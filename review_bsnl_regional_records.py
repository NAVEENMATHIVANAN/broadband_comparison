
import csv
import math
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

INPUT_FILE = Path("bsnl_regional_verification.csv")
REPORT_FILE = Path("bsnl_regional_review_report.csv")


def valid_https(value):
    try:
        parsed = urlparse((value or "").strip())
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not any(ch.isspace() for ch in parsed.netloc)
        )
    except (TypeError, ValueError):
        return False


def positive_number(value):
    try:
        number = float(value)
        return math.isfinite(number) and number > 0
    except (TypeError, ValueError):
        return False


def valid_speed(value):
    try:
        number = float(value)
        return math.isfinite(number) and number > 0 and number.is_integer()
    except (TypeError, ValueError):
        return False


def valid_timestamp(value):
    try:
        dt = datetime.fromisoformat(
            (value or "").strip().replace("Z", "+00:00")
        )
        return dt.tzinfo is not None and dt.utcoffset() is not None
    except (TypeError, ValueError, OverflowError):
        return False


def review_record(row):
    issues = []

    for field in ("provider", "plan_name", "state", "city"):
        if not (row.get(field) or "").strip():
            issues.append(f"Missing {field}")

    if not positive_number(row.get("price")):
        issues.append("Price must be positive and numeric")

    if not valid_speed(row.get("speed_mbps")):
        issues.append("Speed must be a positive whole number")

    if (row.get("billing_period") or "").strip().lower() != "monthly":
        issues.append("Monthly billing is not confirmed")

    if (row.get("review_required") or "").strip().lower() != "no":
        issues.append("Plan requires manual review")

    if not valid_https(row.get("source_url")):
        issues.append("Plan source must be an HTTPS URL")

    if not valid_https(row.get("availability_evidence_url")):
        issues.append("Place-specific evidence HTTPS URL is missing or invalid")

    if not valid_timestamp(row.get("availability_checked_at")):
        issues.append("Timezone-aware verification timestamp is missing or invalid")

    verified = (row.get("availability_verified") or "").strip().lower()
    if verified not in {"yes", "true", "1"}:
        issues.append("Availability has not been marked verified")

    # Even a format-valid URL requires a person to inspect the evidence.
    issues.append("Human must confirm evidence genuinely supports this place")

    result = dict(row)
    result["format_check"] = (
        "FORMAT_CHECK_PASSED" if len(issues) == 1 else "BLOCKED"
    )
    result["evidence_review"] = "MANUAL_REVIEW_REQUIRED"
    result["review_notes"] = "; ".join(issues)
    return result


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"{INPUT_FILE} not found. Prepare a regional record first."
        )

    with INPUT_FILE.open(
        "r", newline="", encoding="utf-8-sig"
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    if not rows:
        raise ValueError(f"{INPUT_FILE} contains no records.")

    required = {
        "provider", "plan_name", "price", "speed_mbps", "state", "city",
        "source_url", "availability_verified", "availability_evidence_url",
        "availability_checked_at", "billing_period", "review_required",
    }
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(
            "Missing columns: " + ", ".join(sorted(missing))
        )

    results = [review_record(row) for row in rows]
    fields = list(results[0].keys())

    with REPORT_FILE.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    passed = sum(r["format_check"] == "FORMAT_CHECK_PASSED" for r in results)

    print(f"Records reviewed: {len(results)}")
    print(f"Format checks passed: {passed}")
    print(f"Format checks blocked: {len(results) - passed}")
    print(f"Report saved to: {REPORT_FILE}")
    print("Human evidence review is still required for every record.")
    print("SQLite was not modified.")


if __name__ == "__main__":
    main()
