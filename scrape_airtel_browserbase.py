
import argparse
import os
import re
import csv
from datetime import datetime, timezone
from pathlib import Path

from browserbase import Browserbase
from playwright.sync_api import sync_playwright

from database import init_db, save_or_update_plan


BASE_DIR = Path(__file__).resolve().parent
SOURCE_URL = "https://www.airtel.in/plans/broadband"
PROVIDER = "Airtel Xstream Fiber"
OUTPUT_FILE = BASE_DIR / "airtel_candidates.csv"


def extract_plans(page_text):
    """Extract candidate Airtel plans from visible page text."""

    lines = [
        line.strip()
        for line in page_text.splitlines()
        if line.strip()
    ]

    # Match monthly prices explicitly labelled before GST.
    price_pattern = re.compile(
        r"₹\s*([\d,]+(?:\.\d+)?)\s*/\s*m\s*\+\s*GST",
        re.IGNORECASE,
    )

    # Start a new block whenever a plan price appears.
    plan_starts = []

    for index, line in enumerate(lines):
        match = price_pattern.search(line)
        if match:
            plan_starts.append((index, match))

    candidates = []

    for position, (start_index, price_match) in enumerate(plan_starts):
        
        # Stop at the next price or at the end of the page.
        next_price_index = (
            plan_starts[position + 1][0]
            if position + 1 < len(plan_starts)
            else len(lines)
        )

        # Airtel plan cards end with a Select button.
        end_index = next_price_index

        for candidate_index in range(
            start_index + 1, next_price_index
        ):
            if lines[candidate_index].strip().lower() == "select":
                end_index = candidate_index + 1
                break


        block = lines[start_index:end_index]
        price = float(price_match.group(1).replace(",", ""))

        # Locate an explicit speed in the current plan block.
        speed = None

        for line in block:
            speed_match = re.search(
                r"\b(\d+(?:\.\d+)?)\s*(Mbps|Gbps)\b",
                line,
                re.IGNORECASE,
            )

            if speed_match:
                value = float(speed_match.group(1))
                unit = speed_match.group(2).lower()

                if unit == "gbps":
                    value *= 1000

                if value > 0 and value.is_integer():
                    speed = int(value)
                    break

        # Reject incomplete plan blocks.
        if price <= 0 or speed is None:
            print("Skipped incomplete plan block:", block[:4])
            continue

        # Keep advertised benefits without interpreting them as prices.
        benefits = []
        index = 0

        while index < len(block):
            line = block[index].strip()
            lower_line = line.lower()

            # Combine "Unlimited" and "internet" into one benefit.
            if (
                lower_line == "unlimited"
                and index + 1 < len(block)
                and block[index + 1].strip().lower() == "internet"
            ):
                benefits.append("Unlimited internet")
                index += 2
                continue

            # Ignore interface labels.
            if lower_line not in {
                "",
                "speed",
                "view details",
                "select",
                "new",
                "unlimited",
                "internet",
            }:
                benefits.append(line)

            index += 1

        offer = " | ".join(dict.fromkeys(benefits)) or None

        candidates.append({
            "provider": PROVIDER,
            "plan_name": f"Airtel ₹{price:g} / {speed} Mbps plan",
            "price": price,
            "speed_mbps": speed,
            "state": None,
            "city": None,
            "pincode": None,
            "offer": offer,
            "offer_price": None,
            "offer_expiry": None,
            "source_url": SOURCE_URL,
            "last_updated": datetime.now(
                timezone.utc
            ).isoformat(timespec="seconds"),
        })

    # Prevent duplicate price/speed combinations in one run.
    unique = {}

    for plan in candidates:
        key = (
            plan["provider"],
            plan["price"],
            plan["speed_mbps"],
        )
        unique[key] = plan

    return list(unique.values())


def fetch_airtel_page():
    api_key = os.getenv("BROWSERBASE_API_KEY")
    project_id = os.getenv("BROWSERBASE_PROJECT_ID")

    if not api_key or not project_id:
        raise RuntimeError(
            "Set BROWSERBASE_API_KEY and "
            "BROWSERBASE_PROJECT_ID first."
        )

    client = Browserbase(api_key=api_key)
    session = client.sessions.create(project_id=project_id)

    print("Browserbase session created:", session.id)

    try:
        # Browser cleanup must happen before Playwright stops.
        with sync_playwright() as playwright:
            browser = None

            try:
                browser = playwright.chromium.connect_over_cdp(
                    session.connect_url
                )

                if not browser.contexts:
                    raise RuntimeError(
                        "No browser context was returned."
                    )

                context = browser.contexts[0]
                page = (
                    context.pages[0]
                    if context.pages
                    else context.new_page()
                )

                response = page.goto(
                    SOURCE_URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                if response and response.status >= 400:
                    raise RuntimeError(
                        f"ISP page returned HTTP {response.status}"
                    )

                page.wait_for_timeout(2500)

                print("Page title:", page.title())
                print("Final URL:", page.url)

                return page.locator("body").inner_text(
                    timeout=15000
                )

            finally:
                if browser is not None:
                    browser.close()

    finally:
        # The browser connection is closed before Playwright exits.
        # Browserbase manages termination of non-persistent sessions.
        print("Browser inspection finished.")


def write_candidates(plans):
    columns = [
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
        "last_updated",
    ]

    with OUTPUT_FILE.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(plans)

    print(f"Candidate CSV saved to: {OUTPUT_FILE}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save extracted candidates to SQLite after inspection.",
    )
    args = parser.parse_args()

    page_text = fetch_airtel_page()
    plans = extract_plans(page_text)

    print(f"\nCandidate plans extracted: {len(plans)}")

    for plan in plans:
        print(
            f"{plan['plan_name']} | "
            f"Monthly advertised price before GST: "
            f"₹{plan['price']:g} | "
            f"{plan['speed_mbps']} Mbps"
        )
        if plan["offer"]:
            print("  Benefits:", plan["offer"])

    write_candidates(plans)

    if not args.save:
        print(
            "\nDry run only: database unchanged. "
            "Review airtel_candidates.csv before using --save."
        )
        return

    if not plans:
        raise RuntimeError(
            "No plans extracted. Database was not updated."
        )

    # This scraper does not infer regional availability.
    # It also does not delete older plans when extraction fails.
    init_db()

    for plan in plans:
        save_or_update_plan(plan)

    print(f"\nSaved {len(plans)} candidate plan records.")


if __name__ == "__main__":
    main()
