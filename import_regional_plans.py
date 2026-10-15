
import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from database import init_db, save_or_update_plan


BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "regional_plans.csv"


def parse_optional_number(value, field_name):
    """Convert an optional numeric CSV field safely."""
    if value is None or not value.strip():
        return None

    try:
        number = float(value.strip())
    except ValueError as error:
        raise ValueError(
            f"{field_name} must be a valid number."
        ) from error

    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite.")

    return number


def validate_row(row, row_number):
    """Validate one CSV row before it reaches SQLite."""

    def required(field):
        value = (row.get(field) or "").strip()
        if not value:
            raise ValueError(
                f"Row {row_number}: {field} is required."
            )
        return value

    provider = required("provider")
    plan_name = required("plan_name")
    source_url = required("source_url")

    parsed_url = urlparse(source_url)

    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError(
            f"Row {row_number}: source_url must be a valid HTTPS URL."
        )

    verified = (row.get("availability_verified") or "").strip().lower()

    if verified not in ("true", "yes", "1"):
        raise ValueError(
            f"Row {row_number}: regional availability must be "
            "verified before importing."
        )

    city = (row.get("city") or "").strip() or None
    pincode = (row.get("pincode") or "").strip() or None
    state = (row.get("state") or "").strip() or None

    if not city and not pincode:
        raise ValueError(
            f"Row {row_number}: provide a verified city or PIN code."
        )

    if pincode and (
        not pincode.isdigit() or len(pincode) != 6
    ):
        raise ValueError(
            f"Row {row_number}: PIN code must contain six digits."
        )

    price = parse_optional_number(row.get("price"), "price")
    speed = parse_optional_number(
        row.get("speed_mbps"), "speed_mbps"
    )
    offer_price = parse_optional_number(
        row.get("offer_price"), "offer_price"
    )

    if price is None or price <= 0:
        raise ValueError(
            f"Row {row_number}: price must be greater than zero."
        )

    if speed is None or speed <= 0 or not speed.is_integer():
        raise ValueError(
            f"Row {row_number}: speed_mbps must be a positive integer."
        )

    if offer_price is not None and offer_price <= 0:
        raise ValueError(
            f"Row {row_number}: offer_price must be positive."
        )

    if offer_price is not None and not row.get("offer", "").strip():
        raise ValueError(
            f"Row {row_number}: offer_price requires an offer description."
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
        "offer_expiry": (
            (row.get("offer_expiry") or "").strip() or None
        ),
        "source_url": source_url,
        "last_updated": datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds"),
    }


def import_plans():
    if not CSV_PATH.exists():
        print(f"CSV file not found: {CSV_PATH}")
        print("Create regional_plans.csv from the template first.")
        return 0

    init_db()

    imported = 0
    rejected = 0

    with CSV_PATH.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        reader = csv.DictReader(file)

        required_columns = {
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
        }

        if not reader.fieldnames or not required_columns.issubset(
            set(reader.fieldnames)
        ):
            raise ValueError(
                "CSV headers are missing or incorrect. "
                "Use regional_plans_template.csv."
            )

        for row_number, row in enumerate(reader, start=2):
            try:
                plan = validate_row(row, row_number)
                save_or_update_plan(plan)
                imported += 1
                print(
                    f"Row {row_number}: imported "
                    f"{plan['provider']} - {plan['plan_name']}"
                )

            except (ValueError, TypeError) as error:
                rejected += 1
                print(f"Row {row_number}: rejected: {error}")

            except Exception as error:
                rejected += 1
                print(
                    f"Row {row_number}: database error: {error}"
                )

    print(f"\nImported or updated: {imported}")
    print(f"Rejected: {rejected}")

    return imported


if __name__ == "__main__":
    import_plans()
