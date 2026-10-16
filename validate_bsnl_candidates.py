import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "bsnl_candidates.csv"
OUTPUT_FILE = BASE_DIR / "bsnl_validation_report.csv"

# Evidence fields are intentionally optional in the source file so older
# candidate CSVs can still be reviewed. Missing evidence blocks import.
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
        number = float(value)
        return number > 0
    except (TypeError, ValueError):
        return False


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


def validate_plan(plan):
    """Validate price, plan quality, and evidence for a specific region."""
    reasons = []

    if str(plan.get("review_required", "")).strip().lower() != "no":
        reasons.append("Manual review is required")

    if str(plan.get("billing_period", "")).strip().lower() != "monthly":
        reasons.append("Monthly billing period is not confirmed")

    if not is_positive_number(plan.get("price")):
        reasons.append("Valid monthly comparison price is missing")

    try:
        speed = float(plan.get("speed_mbps", ""))
        if speed <= 0 or not speed.is_integer():
            reasons.append("Valid whole-number speed is missing")
    except (TypeError, ValueError):
        reasons.append("Valid whole-number speed is missing")

    if not is_verified(plan.get("availability_verified")):
        reasons.append("Regional availability is not verified")

    evidence_url = str(plan.get("availability_evidence_url") or "").strip()
    if not valid_https_url(evidence_url):
        reasons.append("A valid HTTPS regional availability evidence URL is required")

    checked_at = str(plan.get("availability_checked_at") or "").strip()
    if not valid_checked_at(checked_at):
        reasons.append(
            "Availability verification timestamp with timezone is missing/invalid"
        )

    source_url = str(plan.get("source_url") or "").strip()
    if not valid_https_url(source_url):
        reasons.append("A valid HTTPS plan source URL is required")

    city = str(plan.get("city") or "").strip()
    pincode = str(plan.get("pincode") or "").strip()
    if not city and not pincode:
        reasons.append("City or PIN code is missing")
    if pincode and (len(pincode) != 6 or not pincode.isdigit()):
        reasons.append("PIN code must contain six digits")

    result = dict(plan)
    result["availability_evidence_url"] = evidence_url
    result["availability_checked_at"] = checked_at
    result["validation_status"] = (
        "READY_FOR_IMPORT" if not reasons else "BLOCKED"
    )
    result["validation_reasons"] = "; ".join(reasons)
    return result


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Could not find {INPUT_FILE}. Run scrape_bsnl_browserbase.py first."
        )

    with INPUT_FILE.open("r", newline="", encoding="utf-8-sig") as file:
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

    # Preserve source columns and add explicit regional evidence/report columns.
    fieldnames = list(results[0].keys())
    for field in (
        "availability_evidence_url",
        "availability_checked_at",
        "validation_status",
        "validation_reasons",
    ):
        if field not in fieldnames:
            fieldnames.append(field)

    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    ready = sum(row["validation_status"] == "READY_FOR_IMPORT" for row in results)
    blocked = len(results) - ready

    print()
    print("BSNL Candidate Validation")
    print("-------------------------")
    print(f"Candidates checked: {len(results)}")
    print(f"Ready for import: {ready}")
    print(f"Blocked: {blocked}")
    print(f"Report saved to: {OUTPUT_FILE}")
    print()
    print("No plans were inserted into SQLite.")
    print("Missing regional evidence remains blocked; no evidence was invented.")
    print()

    for row in results:
        print(f"{row['plan_name']}: {row['validation_status']}")
        if row["validation_reasons"]:
            print(f"  Reasons: {row['validation_reasons']}")


if __name__ == "__main__":
    main()
