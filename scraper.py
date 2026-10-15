
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from database import init_db, save_or_update_plan


AIRTEL_URL = "https://www.airtel.in/plans/broadband"
PROVIDER = "Airtel Xstream Fiber"


def extract_price(text):
    """Extract an INR price from text."""
    match = re.search(
        r"(?:₹|Rs\.?\s*)([\d,]+(?:\.\d{1,2})?)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    return float(match.group(1).replace(",", ""))


def extract_speed(text):
    """Extract speed in Mbps, including values written in Gbps."""
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(Gbps|Mbps|mbps)",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    speed = float(match.group(1))
    unit = match.group(2).lower()

    if unit == "gbps":
        speed *= 1000

    return int(speed)


def scrape_airtel():
    """Fetch and parse Airtel broadband plan tables."""

    print(f"Fetching plans from {AIRTEL_URL}")

    try:
        response = requests.get(
            AIRTEL_URL,
            headers={
                "User-Agent": (
                    "BroadbandComparisonLearningProject/1.0"
                )
            },
            timeout=20,
        )
        response.raise_for_status()

    except requests.RequestException as error:
        print(f"Network error: {error}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    scraped_plans = []

    # Examine HTML tables rather than guessing from arbitrary text.
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])

            if len(cells) < 2:
                continue

            values = [
                cell.get_text(" ", strip=True)
                for cell in cells
            ]

            row_text = " | ".join(values)

            # Ignore headings and rows without price and speed.
            price = extract_price(row_text)
            speed = extract_speed(row_text)

            if price is None or speed is None:
                continue

            # Require an INR price and an explicit speed unit.
            if price <= 0 or speed <= 0:
                continue

            # Avoid treating a table heading as a plan.
            if not any(
                re.search(r"₹|Rs\.?", value, re.IGNORECASE)
                for value in values
            ):
                continue

            if not any(
                re.search(r"\b(?:Mbps|Gbps)\b", value, re.IGNORECASE)
                for value in values
            ):
                continue

            plan_name = f"Airtel ₹{price:g} / {speed} Mbps plan"

            # Store benefits as descriptive text, not as a price.
            offer_parts = [
                value for value in values
                if value and value not in ("-", "—", "–")
                and not re.search(
                    r"₹|Rs\.?|\d+(?:\.\d+)?\s*(?:Mbps|Gbps)",
                    value,
                    re.IGNORECASE,
                )
            ]

            offer = "; ".join(offer_parts) or None

            scraped_plans.append({
                "provider": PROVIDER,
                "plan_name": plan_name,
                "price": price,
                "speed_mbps": speed,
                "state": None,
                "city": None,
                "pincode": None,
                "offer": offer,
                "offer_price": None,
                "offer_expiry": None,
                "source_url": AIRTEL_URL,
                "last_updated": datetime.now(
                    timezone.utc
                ).isoformat(timespec="seconds"),
            })

    # Remove duplicate rows found in the same page.
    unique_plans = {}

    for plan in scraped_plans:
        key = (
            plan["provider"],
            plan["plan_name"],
            plan["price"],
            plan["speed_mbps"],
        )
        unique_plans[key] = plan

    result = list(unique_plans.values())

    if not result:
        print(
            "No valid plan rows were found in HTML tables. "
            "Existing database records were not changed."
        )
        print(
            "Inspect the page HTML next. If plans are rendered "
            "by JavaScript, evaluate Steel.dev."
        )
        return []

    return result


def run_scraper():
    """Save only validated plans."""

    init_db()
    plans = scrape_airtel()

    if not plans:
        return 0

    saved_count = 0

    for plan in plans:
        try:
            save_or_update_plan(plan)
            saved_count += 1
            print(
                f"Processed: {plan['plan_name']} "
                f"at ₹{plan['price']:.2f}"
            )
        except Exception as error:
            print(
                f"Could not save {plan['plan_name']}: {error}"
            )

    print(f"Successfully processed {saved_count} plans.")
    return saved_count


if __name__ == "__main__":
    run_scraper()
