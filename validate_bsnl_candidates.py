
import csv
from pathlib import Path


INPUT_FILE = Path("bsnl_candidates.csv")
OUTPUT_FILE = Path("bsnl_validation_report.csv")

REQUIRED_COLUMNS = {
    "provider",
    "plan_name",
    "price",
    "speed_mbps",
    "state",
    "city",
    "pincode",
    "availability_verified",
    "billing_period",
    "review_required",
}


def is_positive_number(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def is_verified(value):
    return str(value or "").strip().lower() in {
        "yes", "true", "1"
    }


def validate_plan(plan):
    reasons = []

    # 1. Check whether manual review is still required.
    if str(plan.get("review_required", "")).strip().lower() != "no":
        reasons.append("Manual review is required")

    # 2. Only accept a clearly identified monthly price.
    if plan.get("billing_period", "").strip().lower() != "monthly":
        reasons.append("Monthly billing period is not confirmed")

    if not is_positive_number(plan.get("price")):
        reasons.append("Valid monthly comparison price is missing")

    # 3. Check advertised speed.
    if not is_positive_number(plan.get("speed_mbps")):
        reasons.append("Valid speed is missing")

    # 4. Require evidence of regional availability.
    if not is_verified(plan.get("availability_verified")):
        reasons.append("Regional availability is not verified")

    # 5. Require a city or PIN code for regional listings.
    city = str(plan.get("city") or "").strip()
    pincode = str(plan.get("pincode") or "").strip()

    if not city and not pincode:
        reasons.append("City or PIN code is missing")

    if pincode and (
        len(pincode) != 6 or not pincode.isdigit()
    ):
        reasons.append("PIN code must contain six digits")

    if reasons:
        status = "BLOCKED"
    else:
        status = "READY_FOR_IMPORT"

    result = dict(plan)
    result["validation_status"] = status
    result["validation_reasons"] = "; ".join(reasons)

    return result


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Could not find {INPUT_FILE}. "
            "Run scrape_bsnl_browserbase.py first."
        )

    with INPUT_FILE.open(
        "r", newline="", encoding="utf-8-sig"
    ) as file:
        reader = csv.DictReader(file)

        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])

        if missing:
            raise ValueError(
                "The candidate CSV is missing these columns: "
                + ", ".join(sorted(missing))
            )

        candidates = list(reader)

    if not candidates:
        raise ValueError(
            "The candidate CSV contains no plans to validate."
        )

    results = [validate_plan(plan) for plan in candidates]

    fieldnames = list(results[0].keys())

    with OUTPUT_FILE.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    ready = sum(
        row["validation_status"] == "READY_FOR_IMPORT"
        for row in results
    )
    blocked = len(results) - ready

    print()
    print("BSNL Candidate Validation")
    print("-------------------------")
    print(f"Candidates checked: {len(results)}")
    print(f"Ready for import: {ready}")
    print(f"Blocked: {blocked}")
    print(f"Report saved to: {OUTPUT_FILE.resolve()}")
    print()
    print("No plans were inserted into SQLite.")
    print()

    for row in results:
        print(f"{row['plan_name']}: {row['validation_status']}")

        if row["validation_reasons"]:
            print(f"  Reasons: {row['validation_reasons']}")


if __name__ == "__main__":
    main()
