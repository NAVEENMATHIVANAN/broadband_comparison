import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from database import init_db, save_or_update_plan


BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "regional_plans.csv"


def parse_optional_number(value, field_name):
    value = (value or "").strip()
    if not value:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a valid number.") from error
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite.")
    return number


def valid_https_url(value):
    parsed = urlparse((value or "").strip())
    return parsed.scheme == "https" and bool(parsed.netloc)


def parse_verified_timestamp(value, row_number):
    value = (value or "").strip()
    if not value:
        raise ValueError(
            f"Row {row_number}: availability_checked_at is required."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"Row {row_number}: availability_checked_at must be ISO 8601 "
            "with a timezone, e.g. 2026-10-16T10:30:00+05:30."
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(
            f"Row {row_number}: availability_checked_at must include a timezone."
        )
    return parsed.isoformat(timespec="seconds")


def validate_row(row, row_number):
    """Validate one regional plan and its explicit availability evidence."""

    def required(field):
        value = (row.get(field) or "").strip()
        if not value:
            raise ValueError(f"Row {row_number}: {field} is required.")
        return value

    provider = required("provider")
    plan_name = required("plan_name")
    source_url = required("source_url")
    evidence_url = required("availability_evidence_url")

    if not valid_https_url(source_url):
        raise ValueError(
            f"Row {row_number}: source_url must be a valid HTTPS URL."
        )
    if not valid_https_url(evidence_url):
        raise ValueError(
            f"Row {row_number}: availability_evidence_url must be a valid HTTPS URL."
        )

    verified = (row.get("availability_verified") or "").strip().lower()
    if verified not in {"true", "yes", "1"}:
        raise ValueError(
            f"Row {row_number}: availability_verified must be true only "
            "when the evidence confirms availability for this region."
        )

    city = (row.get("city") or "").strip() or None
    pincode = (row.get("pincode") or "").strip() or None
    state = (row.get("state") or "").strip() or None

    if not city and not pincode:
        raise ValueError(
            f"Row {row_number}: provide the city or PIN code whose availability was verified."
        )
    if pincode and (not pincode.isdigit() or len(pincode) != 6):
        raise ValueError(f"Row {row_number}: PIN code must contain six digits.")

    price = parse_optional_number(row.get("price"), "price")
    speed = parse_optional_number(row.get("speed_mbps"), "speed_mbps")
    offer_price = parse_optional_number(row.get("offer_price"), "offer_price")

    if price is None or price <= 0:
        raise ValueError(f"Row {row_number}: price must be greater than zero.")
    if speed is None or speed <= 0 or not speed.is_integer():
        raise ValueError(
            f"Row {row_number}: speed_mbps must be a positive integer."
        )
    if offer_price is not None and offer_price <= 0:
        raise ValueError(f"Row {row_number}: offer_price must be positive.")
    if offer_price is not None and not (row.get("offer") or "").strip():
        raise ValueError(
            f"Row {row_number}: offer_price requires an offer description."
        )

    checked_at = parse_verified_timestamp(
        row.get("availability_checked_at"), row_number
    )

    return {
        "provider": provider,
        "plan_name": plan_name,
        "price": price,
        "speed_mbps": int(speed),
        "state": state,
        "city": city,
        "pincode": pincode,
        "offer": (row.get("offer") or "").strip() or None,
        "offer_price": offer_price,
        "offer_expiry": (row.get("offer_expiry") or "").strip() or None,
        "source_url": source_url,
        "last_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "availability_verified": True,
        "availability_checked_at": checked_at,
        "availability_evidence_url": evidence_url,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate regional broadband plans; preview by default."
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Import valid rows after explicit confirmation. Default is preview only."
    )
    parser.add_argument(
        "--csv", type=Path, default=CSV_PATH,
        help="CSV path (default: regional_plans.csv beside this script)."
    )
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"CSV file not found: {args.csv}")
        print("Create regional_plans.csv from regional_plans_template.csv first.")
        return

    with args.csv.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required_columns = {
            "provider", "plan_name", "price", "speed_mbps", "state", "city",
            "pincode", "offer", "offer_price", "offer_expiry", "source_url",
            "availability_verified", "availability_evidence_url",
            "availability_checked_at",
        }
        if not reader.fieldnames or not required_columns.issubset(set(reader.fieldnames)):
            missing = sorted(required_columns - set(reader.fieldnames or []))
            raise ValueError(
                "CSV headers are missing required columns: "
                + ", ".join(missing)
                + ". Use the updated regional_plans_template.csv."
            )
        rows = list(reader)

    eligible = []
    rejected = []
    for row_number, row in enumerate(rows, start=2):
        try:
            eligible.append((row_number, validate_row(row, row_number)))
        except (ValueError, TypeError) as error:
            rejected.append((row_number, str(error)))

    print("\nRegional Plan Import Preview")
    print("----------------------------")
    print(f"Rows checked: {len(rows)}")
    print(f"Valid rows: {len(eligible)}")
    print(f"Rejected rows: {len(rejected)}")
    for row_number, reason in rejected:
        print(f"- Row {row_number}: {reason}")
    for _, plan in eligible:
        print(
            f"+ {plan['provider']} | {plan['plan_name']} | "
            f"₹{plan['price']:g} | {plan['speed_mbps']} Mbps | "
            f"{plan.get('city') or plan.get('pincode')}"
        )

    if not args.save:
        print("\nPreview only. SQLite was not modified.")
        print("Use --save only after reviewing the evidence and preview.")
        return
    if not eligible:
        print("\nNo valid rows to import. SQLite was not modified.")
        return

    confirmation = input(
        "\nType IMPORT to save these region-verified plans to SQLite: "
    ).strip()
    if confirmation != "IMPORT":
        print("Import cancelled. SQLite was not modified.")
        return

    init_db()
    imported = 0
    for row_number, plan in eligible:
        try:
            save_or_update_plan(plan)
            imported += 1
            print(f"Row {row_number}: imported/updated {plan['plan_name']}")
        except Exception as error:
            print(f"Row {row_number}: database save failed: {error}")

    print(f"\nImported or updated: {imported}")
    print(f"Rejected during validation: {len(rejected)}")


if __name__ == "__main__":
    main()
