 
import argparse
import csv
from pathlib import Path

from database import init_db, save_or_update_plan


INPUT_FILE = Path("bsnl_validation_report.csv")


def positive_number(value):
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def is_verified(value):
    return str(value or "").strip().lower() in {
        "yes", "true", "1"
    }


def check_import_eligibility(row):
    """Recheck critical requirements immediately before importing."""
    reasons = []

    if row.get("validation_status") != "READY_FOR_IMPORT":
        reasons.append("Validation status is not READY_FOR_IMPORT")

    if str(row.get("review_required", "")).lower().strip() != "no":
        reasons.append("Plan still requires review")

    if str(row.get("billing_period", "")).lower().strip() != "monthly":
        reasons.append("Monthly billing period is not confirmed")

    price = positive_number(row.get("price"))
    speed = positive_number(row.get("speed_mbps"))

    if price is None:
        reasons.append("Valid monthly price is missing")

    if speed is None or not speed.is_integer():
        reasons.append("Valid whole-number speed is missing")

    if not is_verified(row.get("availability_verified")):
        reasons.append("Regional availability is not verified")

    if not str(row.get("provider", "")).strip():
        reasons.append("Provider is missing")

    if not str(row.get("plan_name", "")).strip():
        reasons.append("Plan name is missing")

    source_url = str(row.get("source_url", "")).strip()

    if not source_url.startswith("https://"):
        reasons.append("A valid HTTPS source URL is required")

    city = str(row.get("city") or "").strip()
    pincode = str(row.get("pincode") or "").strip()

    if not city and not pincode:
        reasons.append("City or PIN code is missing")

    if pincode and (
        len(pincode) != 6 or not pincode.isdigit()
    ):
        reasons.append("PIN code must contain six digits")

    return reasons


def make_database_plan(row):
    """Map the validated CSV fields to the existing plans table."""
    return {
        "provider": row["provider"].strip(),
        "plan_name": row["plan_name"].strip(),
        "price": float(row["price"]),
        "speed_mbps": int(float(row["speed_mbps"])),
        "state": row.get("state") or None,
        "city": row.get("city") or None,
        "pincode": row.get("pincode") or None,
        "offer": row.get("offer") or "",
        "offer_price": (
            float(row["offer_price"])
            if positive_number(row.get("offer_price")) is not None
            else None
        ),
        "offer_expiry": row.get("offer_expiry") or None,
        "source_url": row["source_url"].strip(),
        "last_updated": row.get("last_updated") or None,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Preview or import validated BSNL plans."
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save eligible plans to SQLite. Without this, preview only.",
    )
    args = parser.parse_args()

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"{INPUT_FILE} not found. Run "
            "python validate_bsnl_candidates.py first."
        )

    with INPUT_FILE.open(
        "r", newline="", encoding="utf-8-sig"
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    if not rows:
        print("No candidate records found. Nothing to import.")
        return

    eligible = []
    blocked = []

    for row in rows:
        reasons = check_import_eligibility(row)

        if reasons:
            blocked.append((row.get("plan_name", "(unnamed)"), reasons))
        else:
            eligible.append(row)

    print()
    print("BSNL Import Preview")
    print("-------------------")
    print(f"Candidates checked: {len(rows)}")
    print(f"Eligible for import: {len(eligible)}")
    print(f"Blocked: {len(blocked)}")

    if blocked:
        print("\nBlocked plans:")
        for name, reasons in blocked:
            print(f"- {name}: {'; '.join(reasons)}")

    if not eligible:
        print("\nNo eligible plans. SQLite was not modified.")
        return

    print("\nEligible plans:")
    for row in eligible:
        print(
            f"- {row['plan_name']} | "
            f"₹{float(row['price']):g}/month | "
            f"{float(row['speed_mbps']):g} Mbps | "
            f"{row.get('city') or row.get('pincode')}"
        )

    if not args.save:
        print(
            "\nPreview only. SQLite was not modified.\n"
            "After reviewing the evidence, use --save to import."
        )
        return

    confirmation = input(
        "\nType IMPORT to save these plans to SQLite: "
    ).strip()

    if confirmation != "IMPORT":
        print("Import cancelled. SQLite was not modified.")
        return

    init_db()

    imported = 0

    for row in eligible:
        save_or_update_plan(make_database_plan(row))
        imported += 1

    print(f"\nImport complete. Processed {imported} eligible plans.")


if __name__ == "__main__":
    main()
