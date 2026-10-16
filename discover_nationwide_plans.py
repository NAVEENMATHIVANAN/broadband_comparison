
import csv
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "plan_candidates.csv"

# These are discovery sources, not proof of nationwide availability.
SOURCES = [
    {
        "provider": "Airtel Xstream Fiber",
        "url": "https://www.airtel.in/plans/broadband",
        "region": "",
    },
    {
        "provider": "Tata Play Fiber",
        "url": "https://www.tataplayfiber.com/",
        "region": "",
    },
    {
        "provider": "BSNL Bharat Fiber",
        "url": "https://wsc.cdr.bsnl.co.in/viewAllPlans",
        "region": "",
    },
]

# Additional city pages can be investigated individually.
TATA_CITIES = [
    "chennai",
    "coimbatore",
    "bengaluru",
    "hyderabad",
    "mumbai",
    "delhi",
    "kolkata",
    "pune",
]


def fetch_page(url):
    response = requests.get(
        url,
        headers={
            "User-Agent": "BroadbandComparisonLearningProject/1.0"
        },
        timeout=25,
    )
    response.raise_for_status()
    return response.text


def candidate_text_blocks(html):
    """Collect short blocks that mention both price and speed."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements.
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    price_pattern = re.compile(
        r"(?:₹|Rs\.?\s*)\s*[\d,]+(?:\.\d{1,2})?",
        re.IGNORECASE,
    )
    speed_pattern = re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:Mbps|Gbps)\b",
        re.IGNORECASE,
    )

    results = set()

    # Inspect several common content containers.
    for element in soup.find_all(
        ["article", "li", "tr", "section", "div"]
    ):
        text = " ".join(element.get_text(" ", strip=True).split())

        if not text or len(text) > 1800:
            continue

        if price_pattern.search(text) and speed_pattern.search(text):
            # Keep useful text for review, not as a verified plan.
            results.add(text)

    return sorted(results)


def source_list():
    """Return the discovery URLs without inventing coverage."""
    result = list(SOURCES)

    for city in TATA_CITIES:
        url = (
            "https://www.tataplayfiber.com/online-sales?"
            + urlencode({"city": city})
        )

        result.append({
            "provider": "Tata Play Fiber",
            "url": url,
            "region": city.title(),
        })

    return result


def main():
    candidates = []
    seen = set()

    for source in source_list():
        print(
            f"\nChecking {source['provider']}: "
            f"{source['url']}"
        )

        try:
            html = fetch_page(source["url"])
            blocks = candidate_text_blocks(html)

            print(f"Candidate text blocks: {len(blocks)}")

            for block in blocks:
                key = (
                    source["provider"],
                    source["url"],
                    block,
                )

                if key in seen:
                    continue

                seen.add(key)

                candidates.append({
                    "provider": source["provider"],
                    "page_region": source["region"],
                    "candidate_text": block,
                    "source_url": source["url"],
                    "review_status": "NEEDS_VERIFICATION",
                })

        except requests.RequestException as error:
            print(f"Could not fetch this page: {error}")

        # Keep requests polite and sequential.
        time.sleep(1)

    with OUTPUT_FILE.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        columns = [
            "provider",
            "page_region",
            "candidate_text",
            "source_url",
            "review_status",
        ]

        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(candidates)

    print(f"\nTotal candidate blocks: {len(candidates)}")
    print(f"Review file: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
