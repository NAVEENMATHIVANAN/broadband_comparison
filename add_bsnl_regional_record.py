
import csv
from pathlib import Path

CANDIDATES = Path("bsnl_candidates.csv")
OUTPUT = Path("bsnl_regional_verification.csv")

FIELDS = [
    "provider", "plan_name", "price", "speed_mbps", "state", "city",
    "offer", "offer_price", "offer_expiry", "source_url",
    "availability_verified", "availability_evidence_url",
    "availability_checked_at", "billing_period", "review_required",
]


def main():
    if not CANDIDATES.exists():
        raise FileNotFoundError(f"Missing {CANDIDATES}")

    with CANDIDATES.open(newline="", encoding="utf-8-sig") as f:
        candidates = list(csv.DictReader(f))

    if not candidates:
        raise ValueError("No BSNL candidates found.")

    for i, plan in enumerate(candidates, start=1):
        print(
            f"{i:2}. {plan.get('plan_name', '').strip()} | "
            f"₹{plan.get('price', '').strip()} | "
            f"{plan.get('speed_mbps', '').strip()} Mbps | "
            f"review_required={plan.get('review_required', 'yes')}"
        )

    try:
        choice = int(input("\nSelect plan number to prepare (0 to cancel): "))
    except ValueError:
        print("Invalid selection.")
        return

    if choice == 0:
        print("Cancelled. No changes made.")
        return
    if not 1 <= choice <= len(candidates):
        print("Selection is outside the available range.")
        return

    candidate = candidates[choice - 1]
    state = input("State name: ").strip()
    city = input("City/area name: ").strip()

    if not state or not city:
        print("State and city/area are required. No changes made.")
        return

    row = {field: candidate.get(field, "") for field in FIELDS}
    row["state"] = state
    row["city"] = city

    # Preparation is not proof of availability.
    row["availability_verified"] = "no"
    row["availability_evidence_url"] = ""
    row["availability_checked_at"] = ""

    if OUTPUT.exists():
        with OUTPUT.open(newline="", encoding="utf-8-sig") as f:
            existing = list(csv.DictReader(f))
    else:
        existing = []

    key = (
        row["provider"].strip().casefold(),
        row["plan_name"].strip().casefold(),
        row["city"].strip().casefold(),
        row["state"].strip().casefold(),
    )
    for old in existing:
        old_key = (
            old.get("provider", "").strip().casefold(),
            old.get("plan_name", "").strip().casefold(),
            old.get("city", "").strip().casefold(),
            old.get("state", "").strip().casefold(),
        )
        if old_key == key:
            print("This plan already has a record for that place.")
            print("No changes made.")
            return

    existing.append(row)

    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(existing)

    print(f"\nPrepared verification record: {candidate.get('plan_name')}")
    print(f"Place: {city}, {state}")
    print(f"Saved to: {OUTPUT}")
    print("Availability remains unverified. SQLite was not modified.")


if __name__ == "__main__":
    main()
