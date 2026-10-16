
import csv
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path

from browserbase import Browserbase
from playwright.sync_api import sync_playwright


PAGE_URL = "https://wsc.cdr.bsnl.co.in/viewAllPlans"
OUTPUT_FILE = Path("bsnl_candidates.csv")

FIELDNAMES = [
    "provider",
    "plan_name",
    "price",
    "speed_mbps",
    "state",
    "city",
    "pincode",
    "offer",
    "offer_price",
    "offer_expiry",
    "source_url",
    "availability_verified",
    "last_updated",
    "offer_id",
    "offer_code",
    "data_cap_gb",
    "post_quota_speed_mbps",
    "billing_period",
    "displayed_price_raw",
    "price_note",
    "billing_options",
    "review_required",
]

SPECIAL_PLAN_MARKERS = (
    "TREASURY",
    "INCOMING-ONLY",
    "FEDFINA",
    "JUBILANT",
    "MOU",
    "RURAL",
    "GHAR KA WIFI",
    "TAC-MEMBER",
    "CSPDCL",
    "VIDHYA-MITRAM",
    "AIRFIBRE",
    "BHARAT NET",
    "TEMPORARY",
)


def clean_html(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"</p\s*>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_price(value):
    """Return a numeric price, or None if the value is unclear."""
    if value is None:
        return None

    cleaned = str(value).replace(",", "").replace("₹", "").strip()
    match = re.search(r"\d+(?:\.\d+)?", cleaned)

    if not match:
        return None

    try:
        price = float(match.group())
        return price if price > 0 else None
    except ValueError:
        return None


def extract_speed_mbps(description):
    match = re.search(
        r"(?:Speed\s*:\s*)?(?:Up\s*to\s*)?"
        r"(\d+(?:\.\d+)?)\s*(Gbps|Mbps)\b",
        description,
        flags=re.I,
    )

    if not match:
        return None

    speed = float(match.group(1))

    if match.group(2).lower() == "gbps":
        speed *= 1000

    return speed


def extract_post_quota_speed_mbps(description):
    """
    Extract the last speed in the Fup_Speed section.

    Example:
    Fup_Speed: 10 Mbps till 20GB and 2 Mbps beyond
    Result: 2 Mbps
    """
    match = re.search(r"Fup[_\s]*Speed\s*:\s*", description, flags=re.I)

    if not match:
        return None

    section = description[match.end():]

    # Stop if another labelled field starts.
    next_field = re.search(
        r"\b(?:Voice|OTT|Static\s*IP|Installation|Security|"
        r"Data[_\s]*Limit|Speed)\s*:",
        section,
        flags=re.I,
    )

    if next_field:
        section = section[:next_field.start()]

    speeds = re.findall(
        r"(\d+(?:\.\d+)?)\s*(Gbps|Mbps)\b",
        section,
        flags=re.I,
    )

    if not speeds:
        return None

    value, unit = speeds[-1]
    speed = float(value)

    if unit.lower() == "gbps":
        speed *= 1000

    return speed


def extract_data_cap_gb(description):
    match = re.search(
        r"\btill\s*(\d+(?:\.\d+)?)\s*(GB|TB)\b",
        description,
        flags=re.I,
    )

    if not match:
        return None

    amount = float(match.group(1))

    if match.group(2).upper() == "TB":
        amount *= 1000

    return amount


def get_billing_options(item):
    """Read available billing variants from the official API response."""
    extension = item.get("offerExtMap") or {}
    options = extension.get("dppOfferDtoList") or []

    if isinstance(options, dict):
        options = options.get("dppOfferDtoList", [])

    return options if isinstance(options, list) else []


def describe_billing_options(options):
    descriptions = []

    for option in options:
        name = str(option.get("offerName") or "Unnamed option").strip()
        amount = parse_price(option.get("mrcDisplay"))

        if amount is None:
            amount = parse_price(option.get("lease"))

        if amount is None:
            descriptions.append(name)
        else:
            descriptions.append(f"{name}: Rs {amount:g}")

    return " | ".join(descriptions)


def find_monthly_price(options):
    """
    Use a price only when the API explicitly identifies a monthly option
    or clearly specifies a one-month billing term.
    """
    monthly_options = []

    for option in options:
        name = str(option.get("offerName") or "").strip().lower()
        months = option.get("leaseOfMonth")

        explicit_monthly = "monthly" in name

        try:
            one_month_term = int(months) == 1
        except (TypeError, ValueError):
            one_month_term = False

        if explicit_monthly or one_month_term:
            amount = parse_price(option.get("mrcDisplay"))

            if amount is None:
                amount = parse_price(option.get("lease"))

            if amount is not None:
                monthly_options.append(
                    (str(option.get("defaultFlag", "")).upper(), amount)
                )

    if not monthly_options:
        return None

    # Prefer the option marked as the default, if one exists.
    monthly_options.sort(key=lambda row: row[0] != "Y")
    prices = {amount for _, amount in monthly_options}

    # If monthly options disagree, do not guess which price to use.
    if len(prices) > 1:
        return None

    return monthly_options[0][1]


def determine_billing_period(name, options, monthly_price):
    if monthly_price is not None:
        return "monthly"

    text = " ".join(
        [name]
        + [str(option.get("offerName") or "") for option in options]
    ).lower()

    if "annual" in text or "yearly" in text:
        return "annual_or_other"

    if "biannual" in text or "biennial" in text:
        return "multi_year_or_other"

    if "triennial" in text:
        return "multi_year_or_other"

    if options:
        return "unclear"

    return "unclear"


def parse_offers(api_result):
    if api_result.get("http_status") != 200:
        raise RuntimeError(
            f"BSNL API returned HTTP {api_result.get('http_status')}"
        )

    payload = api_result.get("body", {})

    if str(payload.get("code")) != "200":
        raise RuntimeError(
            f"Unexpected BSNL API response code: {payload.get('code')}"
        )

    offers = payload.get("data", {}).get("offerList", [])

    if not isinstance(offers, list) or not offers:
        raise RuntimeError("No offers returned; CSV was not generated.")

    candidates = []
    skipped = 0

    for item in offers:
        name = str(item.get("offerName") or "").strip()
        description = clean_html(item.get("brief"))
        speed = extract_speed_mbps(description)

        if not name or speed is None or speed <= 0:
            skipped += 1
            print(f"Skipped incomplete plan: {name or '(unnamed)'}")
            continue

        options = get_billing_options(item)
        monthly_price = find_monthly_price(options)
        displayed_price = parse_price(item.get("displayRentPrice"))

        billing_period = determine_billing_period(
            name, options, monthly_price
        )

        # Never place a potentially annual or multi-month price in the
        # monthly comparison field.
        comparable_price = (
            monthly_price if billing_period == "monthly" else None
        )

        data_cap = extract_data_cap_gb(description)
        post_quota_speed = extract_post_quota_speed_mbps(description)

        upper_name = name.upper()
        special_plan = any(
            marker in upper_name for marker in SPECIAL_PLAN_MARKERS
        )

        review_reasons = []

        if special_plan:
            review_reasons.append("possible special or restricted eligibility")

        if comparable_price is None:
            review_reasons.append("monthly price not confirmed")

        if displayed_price is None:
            review_reasons.append("displayed price unavailable")

        if data_cap is not None and data_cap < 100:
            review_reasons.append("unusually low data threshold; verify source")

        
        offer_parts = [description] if description else []
        offer_parts.append(
            "Regional availability has not been verified."
        )

        if review_reasons:
            offer_parts.append("Review required: " + "; ".join(review_reasons))

        candidates.append({
            "provider": "BSNL Bharat Fiber",
            "plan_name": name,
            "price": comparable_price if comparable_price is not None else "",
            "speed_mbps": speed,
            "state": "",
            "city": "",
            "pincode": "",
            "offer": " ".join(offer_parts),
            "offer_price": "",
            "offer_expiry": "",
            "source_url": PAGE_URL,
            "availability_verified": "false",
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "offer_id": item.get("offerId", ""),
            "offer_code": item.get("offerCode", ""),
            "data_cap_gb": data_cap if data_cap is not None else "",
            "post_quota_speed_mbps": (
                post_quota_speed if post_quota_speed is not None else ""
            ),
            "billing_period": billing_period,
            "displayed_price_raw": (
                displayed_price if displayed_price is not None else ""
            ),
            "price_note": (
                "Monthly price from billing option; GST treatment unverified"
                if comparable_price is not None
                else "Not safe to use as a monthly comparison price"
            ),
            "billing_options": describe_billing_options(options),
            "review_required": "yes" if review_reasons else "no",
        })

    # Deduplicate by official offer ID.
    unique = {}

    for candidate in candidates:
        key = candidate["offer_id"] or (
            candidate["plan_name"],
            candidate["price"],
            candidate["speed_mbps"],
        )
        unique[key] = candidate

    return list(unique.values()), skipped


def main():
    api_key = os.getenv("BROWSERBASE_API_KEY")
    project_id = os.getenv("BROWSERBASE_PROJECT_ID")

    if not api_key or not project_id:
        raise RuntimeError(
            "Set BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID "
            "in PowerShell first."
        )

    client = Browserbase(api_key=api_key)
    session = client.sessions.create(project_id=project_id)
    print(f"Browserbase session created: {session.id}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            session.connect_url,
            timeout=60000,
        )

        try:
            page = browser.new_page()
            print(f"Opening official BSNL page: {PAGE_URL}")
            page.goto(PAGE_URL, wait_until="commit", timeout=90000)

            result = page.evaluate(
                """async () => {
                    const response = await fetch(
                        "/ecare/bsnl/offer/list",
                        {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                                "Accept": "application/json"
                            },
                            body: JSON.stringify({
                                offerType: 2,
                                salesCatgCode: "Fiber_Combo"
                            })
                        }
                    );

                    let body;
                    try {
                        body = await response.json();
                    } catch (error) {
                        body = {parseError: String(error)};
                    }

                    return {
                        http_status: response.status,
                        body: body
                    };
                }"""
            )
        finally:
            browser.close()

    plans, skipped = parse_offers(result)

    with OUTPUT_FILE.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(plans)

    monthly_count = sum(
        1 for plan in plans if plan["billing_period"] == "monthly"
    )
    review_count = sum(
        1 for plan in plans if plan["review_required"] == "yes"
    )

    print()
    print(f"Candidate plans extracted: {len(plans)}")
    print(f"Incomplete plans skipped: {skipped}")
    print(f"Confirmed monthly-price candidates: {monthly_count}")
    print(f"Candidates requiring review: {review_count}")
    print(f"CSV saved to: {OUTPUT_FILE.resolve()}")
    print("No plans were inserted into SQLite.")
    print()

    for plan in plans:
        price = plan["price"]
        if price != "":
            price_text = f"₹{float(price):g}/month"
        else:
            raw = plan["displayed_price_raw"]
            price_text = (
                f"raw displayed ₹{raw}; period unconfirmed"
                if raw != ""
                else "monthly price unconfirmed"
            )

        print(
            f"- {plan['plan_name']} | {price_text} | "
            f"{plan['speed_mbps']:g} Mbps | "
            f"review: {plan['review_required']}"
        )


if __name__ == "__main__":
    main()
