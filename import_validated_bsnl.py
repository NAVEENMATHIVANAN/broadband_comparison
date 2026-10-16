import argparse
import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from database import init_db, save_or_update_plan


BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "bsnl_validation_report.csv"


def positive_number(value):
    try:
        number = float(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def is_verified(value):
    return str(value or "").strip().lower() in {"yes", "true", "1"}


def valid_https_url(value):
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def valid_checked_at(value):
    value = str(value or "").strip()
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def check_import_eligibility(row):
    """Recheck every safety condition immediately before any database write."""
    reasons = []

    if str(row.get("validation_status", "")).strip() != "READY_FOR_IMPORT":
        reasons.append("Validation status is not READY_FOR_IMPORT")
    if str(row.get("review_required", "")).strip().lower() != "no":
        reasons.append("Plan still requires review")
    if str(row.get("billing_period", "")).strip().lower() != "monthly":
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
    if not valid_https_url(row.get("source_url")):
        reasons.append("A valid HTTPS plan source URL is required")
    if not valid_https_url(row.get("availability_evidence_url")):
        reasons.append("A valid HTTPS regional availability evidence URL is required")
    if not valid_checked_at(row.get("availability_checked_at")):
        reasons.append("Availability verification timestamp with timezone is missing/invalid")

    city = str(row.get("city") or "").strip()
    pincode = str(row.get("pincode") or "").strip()
    if not city and not pincode:
        reasons.append("City or PIN code is missing")
    if pincode and (len(pincode) != 6 or not pincode.isdigit()):
        reasons.append("PIN code must contain six digits")

    return reasons


def make_database_plan(row):
    """Map reviewed report data into the Stage 20 plans schema."""
    return {
        "provider": row["provider"].strip(),
        "plan_name": row["plan_name"].strip(),
        "price": float(row["price"]),
        "speed_mbps": int(float(row["speed_mbps"])),
        "state": (row.get("state") or "").strip() or None,
        "city": (row.get("city") or "").strip() or None,
        "pincode": (row.get("pincode") or "").strip() or None,
        "offer": (row.get("offer") or "").strip() or None,
        "offer_price": (
            float(row["offer_price"])
            if positive_number(row.get("offer_price")) is not None
            else None
        ),
        "offer_expiry": (row.get("offer_expiry") or "").strip() or None,
        "source_url": row["source_url"].strip(),
        "last_updated": (row.get("last_updated") or "").strip() or None,
        "availability_verified": True,
        "availability_checked_at": datetime.fromisoformat(
            row["availability_checked_at"].strip().replace("Z", "+00:00")
        ).isoformat(timespec="seconds"),
        "availability_evidence_url": row["availability_evidence_url"].strip(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Preview or import validated BSNL plans with regional evidence."
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save eligible plans after confirmation. Default is preview only."
    )
    parser.add_argument(
        "--input", type=Path, default=INPUT_FILE,
        help="Validation report path (default: bsnl_validation_report.csv)."
    )
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(
            f"{args.input} not found. Run python validate_bsnl_candidates.py first."
        )

    with args.input.open("r", newline="", encoding="utf-8-sig") as file:
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

    print("\nBSNL Import Preview")
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
            f"- {row['plan_name']} | ₹{float(row['price']):g}/month | "
            f"{float(row['speed_mbps']):g} Mbps | "
            f"{row.get('city') or row.get('pincode')}"
        )

    if not args.save:
        print("\nPreview only. SQLite was not modified.")
        print("Use --save only after reviewing the regional evidence.")
        return

    confirmation = input(
        "\nType IMPORT to save these region-verified BSNL plans to SQLite: "
    ).strip()
    if confirmation != "IMPORT":
        print("Import cancelled. SQLite was not modified.")
        return

    init_db()
    imported = 0
    failed = 0
    for row in eligible:
        try:
            save_or_update_plan(make_database_plan(row))
            imported += 1
        except Exception as error:
            failed += 1
            print(f"Save failed for {row.get('plan_name', '(unnamed)')}: {error}")

    print(f"\nImport complete. Imported/updated: {imported}; save failures: {failed}")


if __name__ == "__main__":
    main()
