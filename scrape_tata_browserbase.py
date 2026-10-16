
import csv
import os
import re
from datetime import datetime
from pathlib import Path

from browserbase import Browserbase
from playwright.sync_api import sync_playwright


URL = "https://www.tataplayfiber.com/broadband-plans/chennai"
OUTPUT_FILE = Path("tata_candidates.csv")

PLAN_START = re.compile(r"^(Mega|Lite|Prime):", re.IGNORECASE)

PRICE_PATTERN = re.compile(
    r"₹\s*([\d,]+(?:\.\d+)?)\s*\+\s*GST\s*[xX×]\s*(\d+)\s*Months",
    re.IGNORECASE,
)

SPEED_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*Mbps", re.IGNORECASE)


def extract_plans(page_text):
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]

    # Find the start of each visible plan card.
    starts = [
        index
        for index, line in enumerate(lines)
        if PLAN_START.match(line)
    ]

    plans = []

    for position, start_index in enumerate(starts):
        end_index = (
            starts[position + 1]
            if position + 1 < len(starts)
            else len(lines)
        )

        block = lines[start_index:end_index]
        title = block[0]

        price_match = next(
            (PRICE_PATTERN.search(line) for line in block
             if PRICE_PATTERN.search(line)),
            None,
        )

        speed_match = next(
            (SPEED_PATTERN.search(line) for line in block
             if SPEED_PATTERN.search(line)),
            None,
        )

        # Do not save incomplete or ambiguous plan records.
        if not price_match or not speed_match:
            print(f"Skipped incomplete plan: {title}")
            continue

        price = float(price_match.group(1).replace(",", ""))
        billing_months = int(price_match.group(2))
        speed = float(speed_match.group(1))

        benefits = []
        for line in block:
            if line.lower().startswith("benefits"):
                benefits.append(line.split("-", 1)[-1].strip())

            if "one-time benefit" in line.lower():
                benefits.append(line)

            # Preserve a displayed crossed-out price separately in notes.
            if (
                re.fullmatch(r"₹\s*[\d,]+(?:\.\d+)?", line)
                and "₹" in line
            ):
                benefits.append(f"Other displayed price: {line}")

        offer = (
            f"Promotional price: ₹{price:g} + GST per month "
            f"for {billing_months} months. "
            + "; ".join(dict.fromkeys(benefits))
            + "; Listed on Tata Play Fiber's Chennai page. "
            "Exact address availability not verified."
        )

        plans.append({
            "provider": "Tata Play Fiber",
            "plan_name": title,
            "price": price,
            "speed_mbps": speed,
            "state": "Tamil Nadu",
            "city": "Chennai",
            "pincode": "",
            "offer": offer,
            "offer_price": "",
            "offer_expiry": "",
            "source_url": URL,
            "availability_verified": "false",
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    return plans


def main():
    api_key = os.getenv("BROWSERBASE_API_KEY")
    project_id = os.getenv("BROWSERBASE_PROJECT_ID")

    if not api_key or not project_id:
        raise RuntimeError(
            "Set BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID first."
        )

    client = Browserbase(api_key=api_key)
    browser = None

    try:
        session = client.sessions.create(project_id=project_id)

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(
                session.connect_url
            )
            page = browser.new_page()

            response = page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(5000)

            if response and response.status >= 400:
                raise RuntimeError(
                    f"Tata Play page returned HTTP {response.status}"
                )

            plans = extract_plans(page.locator("body").inner_text())

            browser.close()
            browser = None

        if not plans:
            raise RuntimeError(
                "No complete plans were extracted. Review the page text "
                "before changing the extraction rules."
            )

        fieldnames = [
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
        ]

        with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(plans)

        print(f"Extracted {len(plans)} candidate plans.")
        print(f"Review the file: {OUTPUT_FILE.resolve()}")
        print("These records have NOT been inserted into SQLite.")

        for plan in plans:
            print(
                f"- {plan['plan_name']} | "
                f"₹{plan['price']:g} + GST/month | "
                f"{plan['speed_mbps']:g} Mbps"
            )

    finally:
        if browser is not None:
            browser.close()


if __name__ == "__main__":
    main()
