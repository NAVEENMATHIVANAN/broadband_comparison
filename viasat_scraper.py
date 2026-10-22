"""Address-based Viasat lookup through local Playwright.

This module does not persist submitted addresses or lookup results.
"""
import logging
import re
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)
ORDER_URL = "https://buy.viasat.com/"


def _visible_input_inventory(page):
    """Return safe metadata for visible inputs to help diagnose markup changes."""
    try:
        return page.locator("input").evaluate_all(
            "els => els.filter(e => { const r=e.getBoundingClientRect(); "
            "return r.width > 0 && r.height > 0; }).map(e => ({tag:e.tagName, "
            "type:e.type, name:e.name, id:e.id, placeholder:e.placeholder, "
            "ariaLabel:e.getAttribute('aria-label'), autocomplete:e.autocomplete}))"
        )
    except Exception:
        return []


def _find_input(page, patterns):
    """Find visible input using attributes, associated labels, or nearby label text."""
    inputs = page.locator("input")
    try:
        count = min(inputs.count(), 80)
    except Exception:
        count = 0

    for i in range(count):
        field = inputs.nth(i)
        try:
            if not field.is_visible(timeout=300):
                continue
            attrs = field.evaluate("e => ({type:e.type, name:e.name, id:e.id, placeholder:e.placeholder, aria:e.getAttribute('aria-label'), autocomplete:e.autocomplete})")
            joined = " ".join(str(v or "") for v in attrs.values()).lower()
            if attrs.get("type", "").lower() in {"hidden", "submit", "button", "checkbox", "radio"}:
                continue
            if any(re.search(p, joined, re.I) for p in patterns):
                return field
        except Exception:
            continue

    # Try labels explicitly, which is more resilient to changes in input attributes.
    for pattern in patterns:
        try:
            label_text = {
                r"street|address": re.compile(r"street address|address", re.I),
                r"zip|postal": re.compile(r"zip code|postal code|zip", re.I),
                r"apt|unit|suite": re.compile(r"apt.?/?.?unit|suite|unit", re.I),
            }
            for key, label_re in label_text.items():
                if re.search(key, pattern, re.I):
                    loc = page.get_by_label(label_re).first
                    if loc.is_visible(timeout=300):
                        return loc
        except Exception:
            pass
    return None


def _fill(page, patterns, value, required=False):
    if not value:
        return not required
    field = _find_input(page, patterns)
    if field is None:
        return False if required else True
    field.fill(value, timeout=5000)
    return True



def _extract_broadband_label(block):
    """Parse structured broadband-label fields from captured label text."""
    text = re.sub(r"\s+", " ", block).strip()

    def number(pattern):
        match = re.search(pattern, text, re.I)
        return float(match.group(1).replace(",", "")) if match else None

    download = number(
        r"Typical download speeds?\s*:?\s*(\d+(?:\.\d+)?)\s*Mbps"
    )
    upload = number(
        r"Typical upload speeds?\s*:?\s*(\d+(?:\.\d+)?)\s*Mbps"
    )
    latency = number(
        r"Typical latency\s*:?\s*(\d+(?:\.\d+)?)\s*ms"
    )

    fee = number(
        r"Monthly Service Fee\s*:?\s*\$?\s*"
        r"(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
    )

    after = re.search(
        r"Monthly Service Fee\s+will be\s+\$?\s*"
        r"(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
        r"\s+after\s+(\d+)\s+months",
        text,
        re.I,
    )

    data = re.search(
        r"Data included with Monthly Price\s*:?\s*"
        r"(Unlimited|[\d,]+\s*GB)",
        text,
        re.I,
    )

    contract = re.search(
        r"Contract term\s*:?\s*"
        r"(24\s*months|month-to-month|month to month|no contract)",
        text,
        re.I,
    )

    # Parse additional charges from the flattened FCC label text.
    # Viasat's label uses phrases such as "Monthly Lease Fee" and
    # "Charges for Additional Data Usage", so support those exact variants.
    def captured(pattern):
        match = re.search(pattern, text, re.I)
        return re.sub(r"\s+", " ", match.group(1)).strip(" .;:, ") if match else None

    government_taxes = (
        "Varies by location"
        if re.search(
            r"Government Taxes?\s*:?\s*Varies by Location",
            text,
            re.I,
        )
        else None
    )

    admin_raw = captured(
        r"Administrative Cost Recovery Fee\s*:?\s*(\$?\s*\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
    )
    admin_match = re.search(
        r"\$?\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)",
        admin_raw or "",
    )
    admin_fee = float(admin_match.group(1).replace(",", "")) if admin_match else None

    monthly_lease = captured(
        r"Monthly Lease Fee\s*:?\s*(?:\(if applicable\)\s*)?"
        r"(\$?\s*\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
    )
    lifetime_lease = captured(
        r"Lifetime Lease Fee\s*:?\s*(?:\(if applicable\)\s*)?"
        r"(\$?\s*\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
    )
    installation_range = captured(
        r"Installation Fee\s*:?\s*(\$?\s*\d{1,4}(?:,\d{3})*(?:\.\d{2})?\s*-\s*\$?\s*\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
    )
    installation_single = captured(
        r"Installation Fee\s*:?\s*(\$?\s*\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
    )
    installation_fee = installation_range or installation_single
    early_termination_fee = captured(
        r"Early Termination Fee\s*:?\s*(\$?\s*\d{1,4}(?:,\d{3})*(?:\.\d{2})?)\s*/\s*mo"
    )
    additional_data_charges = (
        "None"
        if re.search(
            r"Charges for Additional Data Usage\s*:?\s*None",
            text,
            re.I,
        )
        else captured(
            r"Charges for Additional Data Usage\s*:?\s*(.*?)(?=\s+Network Management Policy\b|$)"
        )
    )

    # Preserve conditional wording in the extracted fee fields.
    if monthly_lease and re.search(
        r"Monthly Lease Fee\s*\$?\s*\d[\d,.]*\s*\(if applicable\)", text, re.I
    ):
        monthly_lease += " (if applicable)"
    if lifetime_lease and re.search(
        r"Lifetime Lease Fee\s*\$?\s*\d[\d,.]*\s*\(if applicable\)", text, re.I
    ):
        lifetime_lease += " (if applicable)"

    return {
        "monthly_service_fee": fee,
        "monthly_fee_after_promo": (
            float(after.group(1).replace(",", "")) if after else None
        ),
        "promo_period_months": int(after.group(2)) if after else None,
        "typical_download_mbps": download,
        "typical_upload_mbps": upload,
        "typical_latency_ms": latency,
        "data_included": data.group(1) if data else None,
        "contract_term": contract.group(1) if contract else None,
        "government_taxes": government_taxes,
        "administrative_cost_recovery_fee": admin_fee,
        "monthly_equipment_lease_fee": monthly_lease,
        "lifetime_lease_fee": lifetime_lease,
        "early_termination_fee": early_termination_fee,
        "installation_fee": installation_fee,
        "additional_data_charges": additional_data_charges,
        "full_label_text": text[:5000],
    }



def _extract_broadband_label_blocks(page):
    """Find the separate FCC broadband-label cards rendered below the plan cards."""
    script = r"""
    body => {
      const result = [];
      const seen = new Set();
      const specs = [
        {key: "Essentials", re: /\bViasat\s+Essentials\b/i},
        {key: "Unleashed Max", re: /\bViasat\s+Unleashed\s+Max\b/i},
        {key: "Unleashed", re: /\bViasat\s+Unleashed(?!\s+Max)\b/i}
      ];

      for (const spec of specs) {
        let best = null;
        for (const el of body.querySelectorAll("*")) {
          const text = (el.innerText || "").replace(/\s+/g, " ").trim();
          if (!text || text.length < 100 ||
              !/Fixed Broadband Consumer Disclosure/i.test(text) ||
              !/Monthly Service Fee/i.test(text) ||
              !spec.re.test(text)) continue;

          let parent = el;
          while (parent && parent !== body) {
            const parentText = (parent.innerText || "").replace(/\s+/g, " ").trim();
            if (parentText.length > 6500) break;
            if (parentText.length >= 250 &&
                /Fixed Broadband Consumer Disclosure/i.test(parentText) &&
                /Monthly Service Fee/i.test(parentText) &&
                spec.re.test(parentText)) {
              if (!best || parentText.length < best.length) {
                best = {key: spec.key, text: parentText, length: parentText.length};
              }
              // Continue up only while looking for a potentially smaller match
              parent = parent.parentElement;
            } else {
              parent = parent.parentElement;
            }
          }
        }
        if (best && !seen.has(best.key)) {
          result.push({plan_name: best.key, text: best.text});
          seen.add(best.key);
        }
      }
      return result;
    }
    """
    try:
        return page.locator("body").evaluate(script)
    except Exception as exc:
        logger.warning("Could not inspect Viasat broadband-label cards: %s", exc)
        return []


def _extract_plans(page):
    """Wait for Viasat plans to render, then extract plan details."""
    import re
    import time

    body = ""
    plans = []
    seen = set()

    # Wait up to 90 seconds for a plan name to become visible.
    plan_heading = re.compile(
        r"^(?:Viasat\s+)?(?:Essentials|Unleashed(?:\s+Max)?)$",
        re.I,
    )

    deadline = time.monotonic() + 90

    while time.monotonic() < deadline:
        try:
            body = page.locator("body").inner_text(timeout=10000)

            if plan_heading.search(body) or re.search(
                r"(?m)^\s*(?:Viasat\s+)?"
                r"(?:Essentials|Unleashed(?:\s+Max)?)\s*$",
                body,
                re.I,
            ):
                break

        except Exception as exc:
            logger.debug("Waiting for Viasat plans: %s", exc)

        page.wait_for_timeout(2000)

    # Refresh the page text after the wait.
    try:
        body = page.locator("body").inner_text(timeout=15000)
    except Exception:
        body = ""

    if not re.search(
        r"\b(?:Essentials|Unleashed(?:\s+Max)?)\b",
        body,
        re.I,
    ):
        logger.warning(
            "Viasat plans did not render within 90 seconds. "
            "URL=%s; title=%s; body excerpt=%s",
            page.url,
            page.title(),
            body[:5000],
        )
        return [], body

    # Find elements containing plan names and inspect their ancestors.
    try:
        candidates = page.locator("body").evaluate(
            """body => {
                const planName = /Essentials|Unleashed(?:\\s+Max)?/i;
                const price = /\\$\\s*\\d/;
                const results = [];
                const seen = new Set();

                for (const el of body.querySelectorAll("*")) {
                    const text = (el.innerText || "").trim();

                    if (!planName.test(text) || text.length > 2500) {
                        continue;
                    }

                    // Prefer small elements that look like headings.
                    const tag = el.tagName.toLowerCase();
                    const isHeading =
                        /^h[1-6]$/.test(tag) ||
                        el.getAttribute("role") === "heading";

                    if (!isHeading && text.length > 100) {
                        continue;
                    }

                    let parent = el;
                    for (let i = 0; i < 8 && parent; i++, parent = parent.parentElement) {
                        const cardText = (parent.innerText || "").trim();

                        if (
                            cardText.length >= 30 &&
                            cardText.length <= 2500 &&
                            price.test(cardText) &&
                            planName.test(cardText)
                        ) {
                            if (!seen.has(cardText)) {
                                seen.add(cardText);
                                results.push({
                                    heading: text,
                                    text: cardText
                                });
                            }
                            break;
                        }
                    }
                }

                return results;
            }"""
        )
    except Exception as exc:
        logger.warning("Could not inspect Viasat plan elements: %s", exc)
        candidates = []

    def add_plan(heading, block):
        name_match = re.search(
            r"Essentials|Unleashed(?:\s+Max)?",
            heading,
            re.I,
        )
        if not name_match:
            return

        matched_name = re.sub(r"\s+", " ", name_match.group(0)).title()
        if matched_name.lower() == "unleashed max":
            name = "Unleashed Max"
        elif matched_name.lower() == "essentials":
            name = "Essentials"
        else:
            name = "Unleashed"

        # Normalize prices where browser text splits cents, e.g. "$69 99".
        normalized_block = re.sub(
            r"\$(\d{1,4})\s+(\d{2})(?!\d)",
            r"$\1.\2",
            block,
        )

        # Extract monthly fees by their labels, not by price order.
        promo_match = re.search(
            r"Monthly Service Fee\s*\$?\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
            r"\s*/\s*month",
            normalized_block,
            re.I,
        )

        regular_match = re.search(
            r"Monthly Service Fee\s+will\s+be\s+\$?\s*"
            r"(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)"
            r"\s+after\s+\d+\s+months",
            normalized_block,
            re.I,
        )

        # Fallback for plan summaries that say "$39.99/mo".
        if not promo_match:
            promo_match = re.search(
                r"\$(\d{1,4}(?:,\d{3})*\.\d{2})\s*/\s*mo\b",
                normalized_block,
                re.I,
            )

        promo_price = (
            float(promo_match.group(1).replace(",", ""))
            if promo_match else None
        )
        regular_price = (
            float(regular_match.group(1).replace(",", ""))
            if regular_match else None
        )

        # Use normalized text for the remaining extraction logic.
        block = normalized_block

        # Do not invent prices if the labelled monthly fee was not found.
        if promo_price is None:
            logger.warning(
                "Could not identify the promotional monthly fee for %s. Text=%s",
                heading,
                block[:500],
            )
            return

        speeds = re.findall(
            r"\b(\d+(?:\.\d+)?)\s*(Mbps|Gbps)\b",
            block,
            re.I,
        )
        speed_values = sorted({
            float(value) * (1000 if unit.lower() == "gbps" else 1)
            for value, unit in speeds
        })

        cap = re.search(r"\b([\d,]+)\s*GB\b", block, re.I)
        # "Unlimited" is plan-specific and must take precedence over a GB
        # figure accidentally included by a larger ancestor/card container.
        if re.search(r"\bUnlimited\b", block, re.I):
            cap = None

        item = {
            "provider": "Viasat",
            "plan_name": name,
            "promotional_monthly_price": promo_price,
            "regular_monthly_price": regular_price,
            "advertised_speeds_mbps": speed_values,
            "data_allowance_gb": (
                int(cap.group(1).replace(",", "")) if cap else None
            ),
            "plan_details": block[:1600],
            "source_url": page.url,
            "broadband_label": _extract_broadband_label(block),
        }
        item["broadband_label"]["source_url"] = page.url

        key = (
            name,
            promo_price,
            regular_price,
            tuple(speed_values),
            item["data_allowance_gb"],
        )

        if key not in seen:
            plans.append(item)
            seen.add(key)

    for candidate in candidates:
        add_plan(candidate.get("heading", ""), candidate.get("text", ""))

    # The official broadband labels are separate cards below the plan cards.
    # Extract them independently, then merge each label into its matching plan.
    label_blocks = _extract_broadband_label_blocks(page)
    labels_by_name = {}
    for label_block in label_blocks:
        label_name = re.sub(r"\s+", " ", label_block.get("plan_name", "")).strip().lower()
        labels_by_name[label_name] = label_block.get("text", "")

    for plan in plans:
        plan_name = plan.get("plan_name", "")
        label_text = labels_by_name.get(plan_name.lower())
        if not label_text:
            logger.warning(
                "Viasat label diagnostic: url=%s title=%s headings=%s body_excerpt=%s",
                page.url,
                page.title(),
                page.locator("h1, h2, h3, h4").all_inner_texts(),
                page.locator("body").inner_text(timeout=10000)[:8000],
            )
            logger.warning(
                "Viasat page: url=%s | title=%s | body_chars=%s",
                page.url,
                page.title(),
                len(page.locator("body").inner_text()),
            )
            continue

        label_data = _extract_broadband_label(label_text)
        label_data["source_url"] = page.url
        plan["broadband_label"] = label_data
        plan["broadband_label_text"] = label_text[:5000]

        # Use the official label values for fields that should match it.
        if label_data.get("monthly_service_fee") is not None:
            plan["promotional_monthly_price"] = label_data["monthly_service_fee"]
        if label_data.get("monthly_fee_after_promo") is not None:
            plan["regular_monthly_price"] = label_data["monthly_fee_after_promo"]
        if label_data.get("data_included"):
            data_value = label_data["data_included"]
            plan["high_speed_data"] = data_value
            if re.fullmatch(r"[\d,]+\s*GB", data_value, re.I):
                plan["data_allowance_gb"] = int(re.search(r"[\d,]+", data_value).group().replace(",", ""))
        if label_data.get("contract_term"):
            plan["contract_term"] = label_data["contract_term"]
        speeds = []
        if label_data.get("typical_download_mbps") is not None:
            speeds.append(label_data["typical_download_mbps"])
        if label_data.get("typical_upload_mbps") is not None:
            speeds.append(label_data["typical_upload_mbps"])
        if speeds:
            plan["advertised_speeds_mbps"] = speeds

    if not plans:
        logger.warning(
            "Viasat plan names appeared, but no priced cards were extracted. "
            "URL=%s; title=%s; body excerpt=%s",
            page.url,
            page.title(),
            body[:5000],
        )
        # Remove duplicate plan names while preserving the first plan entry.
    unique_plans = {}

    for plan in plans:
        name = " ".join(
            plan.get("plan_name", "").split()
        ).casefold()

        if name and name not in unique_plans:
            unique_plans[name] = plan

    plans = list(unique_plans.values())
    return plans, body





def _handle_cookie_consent(page):
    """Handle OneTrust only when its consent overlay is blocking the page."""
    backdrop = page.locator(".onetrust-pc-dark-filter")

    try:
        if not backdrop.is_visible(timeout=1000):
            return
    except Exception:
        return

    # Prefer an explicit reject option if the site provides one.
    reject = page.locator("#onetrust-reject-all-handler")
    try:
        if reject.is_visible(timeout=500) and reject.is_enabled():
            reject.click(timeout=2000)
            backdrop.wait_for(state="hidden", timeout=5000)
            return
    except Exception:
        pass

    # Accept cookies only if that is the available visible control.
    accept = page.locator("#onetrust-accept-btn-handler")
    try:
        if accept.is_visible(timeout=500) and accept.is_enabled():
            accept.click(timeout=2000)
            backdrop.wait_for(state="hidden", timeout=5000)
            return
    except Exception:
        pass

    # Try the known OneTrust close control without failing the lookup
    # if the control is absent or the overlay is already gone.
    close = page.locator(
        "#onetrust-consent-sdk button[aria-label='Close'], "
        "#onetrust-consent-sdk button:has-text('Close')"
    ).last

    try:
        if close.is_visible(timeout=500) and close.is_enabled():
            close.click(timeout=2000)
            backdrop.wait_for(state="hidden", timeout=5000)
            return
    except Exception:
        logger.warning(
            "OneTrust overlay could not be dismissed through its visible controls."
        )

    # Do not force-click through a consent overlay. If it still blocks the
    # page, report the issue clearly instead of continuing blindly.
    try:
        if backdrop.is_visible(timeout=500):
            raise RuntimeError(
                "Viasat cookie-consent overlay is still blocking the page. "
                "Dismiss it in the browser or inspect the current consent controls."
            )
    except RuntimeError:
        raise
    except Exception:
        pass


def _capture_broadband_label(page, plan_index):
    """Open a plan's Broadband Facts control and return the actual label text.

    Viasat may render the control as a custom element rather than an <a> or
    <button>. Locate the visible text in the rendered DOM, click its nearest
    interactive ancestor (or the text element itself), then inspect a popup,
    dialog, or inline expansion.
    """
    import time

    # Use DOM text rather than get_by_text() alone: the visible words can be
    # inside nested spans and the clickable element may be a custom component.
    controls = page.locator("body").evaluate(
        r"""body => {
          const matches = [];
          for (const el of body.querySelectorAll('*')) {
            const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
            if (text.toLowerCase() !== 'view broadband facts') continue;
            if ([...el.children].some(c =>
              (c.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase() === 'view broadband facts'
            )) continue;
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue;
            let target = el;
            while (target && target !== body &&
                   !target.matches('a,button,[role="button"],[tabindex],summary')) {
              target = target.parentElement;
            }
            matches.push({
              element: el,
              target: target && target !== body ? target : el,
              tag: el.tagName,
              targetTag: target && target !== body ? target.tagName : el.tagName
            });
          }
          return matches.map((m, index) => ({index, tag:m.tag, targetTag:m.targetTag}));
        }"""
    )
    if len(controls) <= plan_index:
        raise RuntimeError(
            f"Broadband Facts control {plan_index + 1} was not found; "
            f"found {len(controls)} visible control(s)."
        )

    # Click through a locator for the nth visible text occurrence. The JS
    # resolves its interactive ancestor so nested spans do not intercept it.
    popup = None
    before_text = page.locator("body").inner_text(timeout=10000)
    click_result = {"clicked": False, "tag": None}
    try:
        with page.expect_popup(timeout=2500) as popup_info:
            click_result = page.locator("body").evaluate(
                r"""(body, index) => {
                  const found = [];
                  for (const el of body.querySelectorAll('*')) {
                    const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
                    if (text.toLowerCase() !== 'view broadband facts') continue;
                    if ([...el.children].some(c =>
                      (c.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase() === 'view broadband facts'
                    )) continue;
                    const r = el.getBoundingClientRect();
                    if (!r.width || !r.height) continue;
                    let target = el;
                    while (target && target !== body &&
                           !target.matches('a,button,[role="button"],[tabindex],summary')) {
                      target = target.parentElement;
                    }
                    found.push(target && target !== body ? target : el);
                  }
                  const target = found[index];
                  if (!target) return {clicked:false, tag:null};
                  target.click();
                  return {clicked:true, tag:target.tagName};
                }""",
                plan_index,
            )
        popup = popup_info.value
    except PlaywrightTimeoutError:
        # Expected for labels rendered in a dialog or expanded inline.
        pass

    if not click_result.get("clicked"):
        raise RuntimeError(f"Could not click Broadband Facts control {plan_index + 1}.")

    target_page = popup or page
    if popup is not None:
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=15000)
        except PlaywrightTimeoutError:
            pass

    # Wait for actual label content, not merely for the click to complete.
    deadline = time.monotonic() + 12
    label_text = ""
    while time.monotonic() < deadline:
        try:
            dialogs = target_page.get_by_role("dialog")
            if dialogs.count():
                dialog_text = dialogs.last.inner_text(timeout=1000)
                if re.search(r"Typical download speeds?|Fixed Broadband Consumer Disclosure", dialog_text, re.I):
                    label_text = dialog_text
                    break
            body_text = target_page.locator("body").inner_text(timeout=2000)
            if re.search(r"Typical download speeds?|Fixed Broadband Consumer Disclosure", body_text, re.I):
                # For inline expansions, require that the rendered text changed
                # or that the page contains the formal disclosure heading.
                if popup is not None or body_text != before_text or re.search(
                    r"Fixed Broadband Consumer Disclosure", body_text, re.I
                ):
                    label_text = body_text
                    break
        except Exception:
            pass
        target_page.wait_for_timeout(300)

    if not label_text:
        raise RuntimeError(
            f"Clicked Broadband Facts for plan {plan_index + 1}, but no label "
            "content appeared in a popup, dialog, or inline page section."
        )

    if popup is not None:
        try:
            popup.close()
        except Exception:
            pass
    else:
        # Close an in-page modal when a close control exists so the next plan's
        # facts control remains clickable.
        try:
            dialogs = page.get_by_role("dialog")
            if dialogs.count():
                dialog = dialogs.last
                close = dialog.locator(
                    'button[aria-label*="close" i], button:has-text("Close"), '
                    'button:has-text("Done"), [role="button"][aria-label*="close" i]'
                ).first
                if close.count() and close.is_visible(timeout=500):
                    close.click(timeout=1500)
                    dialog.wait_for(state="hidden", timeout=3000)
        except Exception:
            pass

    return label_text




def _select_label_text_for_plan(text, plan_name):
    """When a capture contains multiple label cards, isolate the requested plan."""
    normalized = re.sub(r"\s+", " ", text).strip()
    # Match the full plan name so Unleashed does not accidentally match Unleashed Max.
    names = ("Essentials", "Unleashed Max", "Unleashed")
    heading_pattern = re.compile(
        r"\b(?:Viasat\s+)?(Essentials|Unleashed\s+Max|Unleashed)\b",
        re.I,
    )
    matches = list(heading_pattern.finditer(normalized))
    if not matches:
        return text

    def canonical(value):
        value = re.sub(r"\s+", " ", value).strip().lower()
        return "unleashed max" if value == "unleashed max" else value

    wanted = canonical(plan_name)
    for index, match in enumerate(matches):
        found = canonical(match.group(1))
        if found != wanted:
            continue
        # Bound the segment at the next different plan heading. This prevents
        # regexes from reading the first plan's fee/speeds for every plan.
        end = len(normalized)
        for later in matches[index + 1:]:
            if canonical(later.group(1)) != wanted:
                end = later.start()
                break
        segment = normalized[match.start():end]
        if re.search(r"Typical download speeds?|Fixed Broadband Consumer Disclosure", segment, re.I):
            return segment
    return text


def lookup_viasat(street_address, zipcode, unit=""):
    if not street_address or not street_address.strip() or not zipcode or not zipcode.strip():
        raise ValueError("Street address and ZIP code are required.")

    if not re.fullmatch(r"\d{5}(?:-\d{4})?", zipcode.strip()):
        raise ValueError("Enter a valid 5-digit US ZIP code (ZIP+4 is also accepted).")

    # Launch Chromium locally instead of creating a Browserbase session.
    with sync_playwright() as p:
        browser = None
        context = None

        try:
            browser = p.chromium.launch(
                headless=True,
                timeout=60000,
            )

            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
            )

            page = context.new_page()

            # Continue with the existing Viasat navigation and address flow.

            page.goto(ORDER_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.locator("body").wait_for(timeout=10000)
                page.wait_for_timeout(1500)
            except PlaywrightTimeoutError:
                pass
            _handle_cookie_consent(page)

            if not _fill(page, [r"street|address"], street_address.strip(), required=True):
                raise RuntimeError(
                    "Could not find the street-address field. Visible inputs: "
                    f"{_visible_input_inventory(page)}"
                )
            if not _fill(page, [r"zip|postal"], zipcode.strip(), required=True):
                raise RuntimeError(
                    "Could not find the ZIP-code field. Visible inputs: "
                    f"{_visible_input_inventory(page)}"
                )
            if unit and not _fill(page, [r"apt|unit|suite"], unit.strip()):
                logger.warning("Viasat unit field was not found; continuing without unit information.")

            submitted = False
            for selector in (
                "button[data-testid='navigatorContinueButton']",
                "button:has-text('Find plans')",
                "button:has-text('Check Availability')",
                "button:has-text('See Plans')",
                "button:has-text('Continue')",
                "input[type='submit']",
            ):
                try:
                    button = page.locator(selector).first
                    if button.is_visible(timeout=500) and button.is_enabled():
                        button.click(timeout=5000)
                        submitted = True
                        break
                except Exception:
                    continue
            if not submitted:
                zip_field = _find_input(page, [r"zip|postal"])
                if zip_field is None:
                    raise RuntimeError("Could not submit the Viasat address lookup.")
                zip_field.press("Enter", timeout=3000)
            # Viasat may show an address-confirmation dialog after Find plans.
            # Wait for the SPA to render it, choose the preselected Recommended
            
            # Wait for Viasat's address-confirmation modal.
            confirmation_heading = page.get_by_text(
                re.compile(r"Please confirm your address", re.I)
            ).first

            try:
                confirmation_heading.wait_for(
                    state="visible",
                    timeout=15000,
                )
            except PlaywrightTimeoutError:
                logger.warning(
                    "Address-confirmation modal did not appear. URL=%s",
                    page.url,
                )
            else:
                # Keep the site's preselected Recommended address.
                # Do not click "Edit my address" or select Original.
                recommended = page.get_by_text(
                    re.compile(r"^Recommended$", re.I)
                ).first

                try:
                    recommended.wait_for(state="visible", timeout=3000)
                    logger.info(
                        "Viasat Recommended address is displayed; "
                        "keeping the site-selected address."
                    )
                except PlaywrightTimeoutError:
                    logger.warning(
                        "Recommended label not found; checking the modal's "
                        "Continue button anyway."
                    )

                # Use Viasat's specific modal button, not a generic Continue.
                continue_button = page.locator(
                    "button[data-testid='ConfirmServiceAddressModal-primary-action']"
                )

                continue_button.wait_for(state="visible", timeout=10000)
                continue_button.wait_for(state="attached", timeout=3000)

                if not continue_button.is_enabled():
                    raise RuntimeError(
                        "Viasat's address-confirmation Continue button is disabled."
                    )

                continue_button.click(timeout=10000)

                # Confirm that the modal closed before waiting for plans.
                try:
                    confirmation_heading.wait_for(
                        state="hidden",
                        timeout=10000,
                    )
                except PlaywrightTimeoutError:
                    logger.warning(
                        "Confirmation modal remained visible after Continue."
                    )


            # Wait for the actual plans route. If the site remains on /r/, log
            # page state rather than pretending that the address check completed.
            try:
                page.wait_for_url(re.compile(r".*/pln(?:[/?#].*)?$", re.I), timeout=30000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(2500)
                logger.warning(
                    "Viasat did not reach the plans route. url=%s title=%s text=%s buttons=%s",
                    page.url,
                    page.title(),
                    re.sub(r"\s+", " ", page.locator("body").inner_text(timeout=5000))[:1800],
                    page.locator("button").evaluate_all(
                        "els => els.filter(e => {const r=e.getBoundingClientRect(); return r.width>0 && r.height>0;})"
                        ".map(e => ({text:(e.innerText||'').trim(), id:e.id, testid:e.getAttribute('data-testid'), disabled:e.disabled})).slice(0,30)"
                    ),
                )

            title = page.title()
            final_url = page.url

            
            # Diagnostic: inspect Viasat's broadband-facts controls.
            facts_controls = page.locator("a, button").evaluate_all(
                """els => els
                    .filter(e => /broadband facts|broadband label/i.test(
                        (e.innerText || e.getAttribute('aria-label') || '').trim()
                    ))
                    .map(e => ({
                        tag: e.tagName,
                        text: (e.innerText || '').trim(),
                        href: e.getAttribute('href'),
                        ariaLabel: e.getAttribute('aria-label'),
                        testId: e.getAttribute('data-testid'),
                        role: e.getAttribute('role')
                    }))
                """
            )

            logger.warning(
                "Viasat broadband-facts controls: %s",
                facts_controls,
            )

            logger.warning(
                "Viasat table diagnostic: %s",
                page.locator("table").evaluate_all(
                    """els => els.map(e => ({
                        text: (e.innerText || '').trim().slice(0, 1200),
                        visible: !!(e.getBoundingClientRect().width &&
                                    e.getBoundingClientRect().height)
                    }))"""
                ),
            )
            
            
            
            # Inspect every element containing the broadband-facts text.
            facts_elements = page.locator("body *").evaluate_all(
                """els => els
                    .filter(e => {
                        const text = (e.innerText || '').trim();
                        return text.length < 100 &&
                               /broadband facts/i.test(text) &&
                               !Array.from(e.children).some(
                                   c => /broadband facts/i.test(
                                       (c.innerText || '').trim()
                                   )
                               );
                    })
                    .map(e => ({
                        tag: e.tagName,
                        text: (e.innerText || '').trim(),
                        role: e.getAttribute('role'),
                        ariaLabel: e.getAttribute('aria-label'),
                        testId: e.getAttribute('data-testid'),
                        className: typeof e.className === 'string'
                            ? e.className : '',
                        href: e.getAttribute('href'),
                        type: e.getAttribute('type')
                    }))
                """
            )
            logger.warning(
                "Viasat facts elements: %s",
                facts_elements
            )

            # Inspect embedded frames that may contain the labels.
            logger.warning(
                "Viasat frame URLs: %s",
                [frame.url for frame in page.frames]
            )

            # Inspect links that may point to a broadband label document.
            links = page.locator("a").evaluate_all(
                """els => els.map(e => ({
                    text: (e.innerText || '').trim(),
                    href: e.href
                })).filter(e =>
                    /broadband|label|fact/i.test(e.text + ' ' + e.href)
                )"""
            )
            logger.warning("Viasat broadband-related links: %s", links)

            
            # Inspect the rendered HTML around the broadband facts section.
            html = page.locator("body").inner_html()

            facts_index = html.lower().find("broadband facts")

            logger.warning(
                "Viasat facts HTML index: %s",
                facts_index
            )

            if facts_index >= 0:
                start = max(0, facts_index - 2500)
                end = min(len(html), facts_index + 2500)

                logger.warning(
                    "Viasat facts HTML excerpt: %s",
                    html[start:end]
                )
            else:
                logger.warning(
                    "Viasat facts text is absent from rendered HTML"
                )
                # Temporary diagnostic: inspect the actual rendered plan-card DOM.
                try:
                    page.wait_for_timeout(3000)
                    html_diagnostic = page.evaluate("""
                    () => {
                        const html = document.documentElement.innerHTML;
                        const body = document.body;

                        const terms = [
                            "Viasat Essentials",
                            "Viasat Unleashed",
                            "Broadband facts",
                            "Broadband label",
                            "View Broadband facts",
                            "653 ms",
                            "39.99"
                        ];

                        const results = {};

                        for (const term of terms) {
                            const index = html.toLowerCase().indexOf(term.toLowerCase());
                            results[term] = {
                                found: index !== -1,
                                index: index,
                                excerpt: index !== -1
                                    ? html.slice(Math.max(0, index - 250), index + 700)
                                    : ""
                            };
                        }

                        return {
                            bodyTextLength: body ? body.innerText.length : -1,
                            bodyHTMLLength: body ? body.innerHTML.length : -1,
                            documentHTMLLength: html.length,
                            terms: results
                        };
                    }
                    """)

                    for term, result in html_diagnostic["terms"].items():
                        logger.warning(
                            "VIASAT HTML TERM %s: found=%s index=%s excerpt=%s",
                            term,
                            result["found"],
                            result["index"],
                            result["excerpt"]
                        )

                    diagnostic = page.evaluate("""
                    () => ({
                        url: location.href,
                        readyState: document.readyState,
                        bodyExists: !!document.body,
                        bodyTextLength: document.body
                            ? document.body.innerText.length : -1,
                        bodyHTMLLength: document.body
                            ? document.body.innerHTML.length : -1,
                        htmlLength: document.documentElement
                            ? document.documentElement.innerHTML.length : -1,
                        bodyPreview: document.body
                            ? document.body.innerHTML.slice(0, 800) : "",
                        frames: [...document.querySelectorAll("iframe")].map(frame => ({
                            title: frame.title,
                            src: frame.src
                        }))
                    })
                    """)

                    logger.warning("VIASAT READY STATE: %s", diagnostic["readyState"])
                    logger.warning("VIASAT BODY EXISTS: %s", diagnostic["bodyExists"])
                    logger.warning("VIASAT BODY TEXT LENGTH: %s", diagnostic["bodyTextLength"])
                    logger.warning("VIASAT BODY HTML LENGTH: %s", diagnostic["bodyHTMLLength"])
                    logger.warning("VIASAT HTML LENGTH: %s", diagnostic["htmlLength"])
                    logger.warning("VIASAT BODY PREVIEW: %s", diagnostic["bodyPreview"])
                    logger.warning("VIASAT IFRAME INFO: %s", diagnostic["frames"])
                except Exception as exc:
                    logger.warning("VIASAT DOM DIAGNOSTIC FAILED: %s", exc)
            # Inspect custom elements used by the page.
            custom_elements = page.locator("*").evaluate_all(
                """els => [...new Set(
                    els.map(e => e.tagName.toLowerCase())
                       .filter(name => name.includes('-'))
                )]"""
            )

            logger.warning(
                "Viasat custom elements: %s",
                custom_elements
            )

            # Inspect JavaScript resources loaded by the page.
            scripts = page.locator("script[src]").evaluate_all(
                """els => els.map(e => e.src)"""
            )

            logger.warning(
                "Viasat JavaScript resources: %s",
                scripts
            )
            
            plans, body = _extract_plans(page)
            if plans:
                for index, plan in enumerate(plans):
                    try:
                        label_text = _capture_broadband_label(page, index)
                        # A Viasat page may expose the full label document/body
                        # rather than a plan-specific dialog. Never parse the
                        # first label and reuse its values for every plan.
                        label_text = _select_label_text_for_plan(
                            label_text, plan["plan_name"]
                        )
                        plan["broadband_label"] = _extract_broadband_label(
                            label_text
                        )
                        plan["broadband_label"]["source_url"] = page.url

                        label = plan["broadband_label"]
                        if (
                            label["typical_download_mbps"] is None
                            or label["typical_upload_mbps"] is None
                            or label["typical_latency_ms"] is None
                        ):
                            logger.warning(
                                "Incomplete Broadband Facts for %s",
                                plan["plan_name"],
                            )
                    except Exception as exc:
                        logger.warning(
                            "Could not extract Broadband Facts for %s: %s",
                            plan["plan_name"],
                            exc,
                        )
                        plan["broadband_label"] = {
                            "typical_download_mbps": None,
                            "typical_upload_mbps": None,
                            "typical_latency_ms": None,
                            "extraction_error": str(exc),
                            "source_url": page.url,
                        }
            if plans:
                status = "plans_found"
                message = (
                    "Address-specific plan details were extracted from Viasat. "
                    "Confirm final terms on the official result page."
                )
            else:
                status = "inconclusive"
                message = (
                    "No address-specific plan cards were extracted. "
                    "This does not prove service is unavailable. "
                    "Review the official result page."
                )
            return {
                "provider": "Viasat",
                "status": status,
                "message": message,
                "plans": plans,
                "source_url": final_url,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "page_title": title,
            }
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    logger.warning(
                        "Could not close the Viasat browser context cleanly.",
                        exc_info=True,
                    )

            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    logger.warning(
                        "Could not close the local Chromium browser cleanly.",
                        exc_info=True,
                    )
