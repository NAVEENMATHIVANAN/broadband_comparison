
import csv
from pathlib import Path

CANDIDATES_FILE = Path("bsnl_candidates.csv")
OUTPUT_FILE = Path("bsnl_regional_verification.csv")

OUTPUT_COLUMNS = [
    "provider",
    "plan_name",
    "price",
    "speed_mbps",
    "state",
    "city",
    "offer",
    "offer_price",
    "offer_expiry",
    "source_url",
    "availability_verified",
    "availability_evidence_url",
    "availability_checked_at",
    "billing_period",
    "review_required",
]


def main():
    if not CANDIDATES_FILE.exists():
        raise FileNotFoundError(
            f"{CANDIDATES_FILE} not found. Run the BSNL scraper first."
        )

    with CANDIDATES_FILE.open(
        "r", newline="", encoding="utf-8-sig"
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        headers = set(reader.fieldnames or [])

    required = {"provider", "plan_name", "price", "speed_mbps", "source_url"}
    missing = required - headers

    if missing:
        raise ValueError(
            "Candidate CSV is missing columns: "
            + ", ".join(sorted(missing))
        )

    existing = {}
    if OUTPUT_FILE.exists():
        with OUTPUT_FILE.open(
            "r", newline="", encoding="utf-8-sig"
        ) as file:
            for row in csv.DictReader(file):
                key = (
                    row.get("provider", "").strip(),
                    row.get("plan_name", "").strip(),
                    row.get("city", "").strip().casefold(),
                )
                existing[key] = row

    prepared = []
    for candidate in rows:
        key = (
            candidate.get("provider", "").strip(),
            candidate.get("plan_name", "").strip(),
            "",
        )

        # Keep candidate discovery separate from regional approval.
        row = {
            column: candidate.get(column, "")
            for column in OUTPUT_COLUMNS
        }

        # Do not claim regional availability without place-specific evidence.
        row["city"] = ""
        row["state"] = ""
        row["availability_verified"] = "no"
        row["availability_evidence_url"] = ""
        row["availability_checked_at"] = ""

        # Retain review and billing metadata when available.
        row["billing_period"] = candidate.get("billing_period", "")
        row["review_required"] = candidate.get("review_required", "yes")

        # Preserve manually entered regional records for the same plan.
        # Existing records are not automatically marked as verified.
        matches = [
            old for old_key, old in existing.items()
            if old_key[0] == key[0] and old_key[1] == key[1]
        ]

        if matches:
            prepared.extend(matches)
        else:
            prepared.append(row)

    with OUTPUT_FILE.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(prepared)

    print(f"Candidate plans processed: {len(rows)}")
    print(f"Regional verification file: {OUTPUT_FILE}")
    print("Availability has not been automatically verified.")
    print("Review each city/area and its evidence before approving import.")


if __name__ == "__main__":
    main()
