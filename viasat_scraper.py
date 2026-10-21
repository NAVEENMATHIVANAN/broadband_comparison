"""Address-based Viasat lookup through Browserbase + Playwright.

This module does not persist submitted addresses or lookup results.
"""
import logging
import os
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
        }

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

    if not plans:
        logger.warning(
            "Viasat plan names appeared, but no priced cards were extracted. "
            "URL=%s; title=%s; body excerpt=%s",
            page.url,
            page.title(),
            body[:5000],
        )

    return plans, body




def _handle_cookie_consent(page):
    """Dismiss the visible OneTrust banner before interacting with Viasat."""

    sdk = page.locator("#onetrust-consent-sdk")
    backdrop = page.locator(".onetrust-pc-dark-filter")

    try:
        if not backdrop.is_visible(timeout=1000):
            return
    except Exception:
        return

    # Prefer rejecting optional cookies when an explicit option is available.
    reject = page.locator("#onetrust-reject-all-handler")
    try:
        if reject.is_visible(timeout=500) and reject.is_enabled():
            reject.click(timeout=3000)
    except Exception:
        pass

    # If the backdrop remains, use the visible Close control in OneTrust.
    try:
        if backdrop.is_visible(timeout=700):
            close_button = sdk.get_by_role(
                "button", name=re.compile(r"^Close$", re.IGNORECASE)
            ).last

            if close_button.is_visible(timeout=1000):
                close_button.click(timeout=4000)
    except Exception as exc:
        logger.warning("Could not click OneTrust Close button: %s", exc)
    _handle_cookie_consent(page)
    # Never continue to the address-confirmation button while blocked.
    try:
        backdrop.wait_for(state="hidden", timeout=5000)
    except Exception as exc:
        raise RuntimeError(
            "Viasat's cookie-consent backdrop remains visible. "
            "Address confirmation was not submitted."
        ) from exc




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
            # address when available, then click the dialog's Continue button.
            confirmation_seen = False
            for _ in range(12):
                try:
                    heading = page.get_by_text(
                        re.compile(r"Please confirm your address", re.I)
                    ).first
                    if heading.is_visible(timeout=500):
                        confirmation_seen = True
                        break
                except Exception:
                    pass
                if re.search(r"/pln(?:[/?#]|$)", page.url, re.I):
                    break
                page.wait_for_timeout(500)

            if confirmation_seen:
                # The screenshot shows Recommended preselected; preserve that
                # choice unless the page indicates otherwise.
                try:
                    recommended = page.get_by_text(
                        re.compile(r"^Recommended$", re.I)
                    ).first
                    if recommended.is_visible(timeout=700):
                        radio = page.locator("input[type='radio']").first
                        if radio.count() and not radio.is_checked():
                            recommended.click(timeout=2500)
                except Exception:
                    logger.info("Could not inspect Recommended radio; keeping site default.")

                continue_button = page.get_by_role(
                    "button", name=re.compile(r"^Continue$", re.I)
                ).last
                continue_button.wait_for(state="visible", timeout=8000)
                continue_button.click(timeout=8000)

            # Wait for the actual plans route. If the site remains on /r/, log
            # page state rather than pretending that the address check completed.
            try:
                page.wait_for_url(re.compile(r".*/pln(?:[/?#].*)?$", re.I), timeout=20000)
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
            plans, body = _extract_plans(page)
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
