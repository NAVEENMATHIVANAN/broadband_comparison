
import argparse
import csv
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
CSV_PATH = BASE / "bsnl_candidates.csv"
DB_PATH = BASE / "broadband.db"
PROVIDER = "BSNL Bharat Fiber"
SOURCE_FALLBACK = "https://wsc.cdr.bsnl.co.in/viewAllPlans"


def truthy(value):
    return str(value or "").strip().lower() in {"yes", "true", "1", "required"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the import after previewing the candidate records.",
    )
    args = parser.parse_args()

    if not CSV_PATH.exists() or not DB_PATH.exists():
        raise FileNotFoundError(
            "Required file missing: bsnl_candidates.csv or broadband.db"
        )

    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = set(reader.fieldnames or [])

    required = {"plan_name", "price", "speed_mbps", "billing_period"}
    if not required.issubset(columns):
        raise ValueError(
            f"CSV missing required columns: {sorted(required - columns)}"
        )

    accepted = []
    excluded = Counter()

    for line, row in enumerate(rows, start=2):
        name = (row.get("plan_name") or "").strip()
        billing = (row.get("billing_period") or "").strip().lower()
        review = truthy(row.get("review_required"))

        try:
            price = float(str(row.get("price") or "").replace(",", "").strip())
            speed = float(str(row.get("speed_mbps") or "").replace(",", "").strip())
        except ValueError:
            excluded["missing_or_invalid_price_or_speed"] += 1
            continue

        if not name or price <= 0 or speed <= 0:
            excluded["missing_name_or_nonpositive_price_or_speed"] += 1
            continue

        if billing not in {"monthly", "month", "1 month"}:
            excluded["billing_not_confirmed_monthly"] += 1
            continue

        source = (row.get("source_url") or "").strip() or SOURCE_FALLBACK
        if not source.startswith("https://"):
            excluded["source_not_https"] += 1
            continue

        offer_parts = [
            (row.get("offer") or "").strip(),
            "Billing period: monthly.",
        ]
        if review:
            offer_parts.append(
                "REVIEW REQUIRED: eligibility or plan terms must be checked "
                "before recommending this plan to a customer."
            )

        # Preserve available candidate details without claiming regional coverage.
        for field in ("billing_period", "review_required"):
            value = (row.get(field) or "").strip()
            if value:
                offer_parts.append(f"{field}: {value}.")

        accepted.append({
            "name": name,
            "price": price,
            "speed": speed,
            "state": (row.get("state") or "").strip() or None,
            "city": (row.get("city") or "").strip() or None,
            "offer": " ".join(part for part in offer_parts if part),
            "source": source,
            "last_updated": (
                row.get("last_updated") or
                datetime.now().astimezone().isoformat(timespec="seconds")
            ),
            "review": review,
        })

    # Detect duplicates by normalized plan name. Do not silently choose
    # between potentially different records with the same name.
    name_counts = Counter(p["name"].casefold() for p in accepted)
    duplicates = [name for name, count in name_counts.items() if count > 1]
    if duplicates:
        print("Duplicate plan names need review:")
        for name in duplicates:
            print("  ", name)
        raise ValueError("Resolve duplicate candidate names before importing.")

    print(f"Source candidate rows: {len(rows)}")
    print(f"Eligible monthly catalogue rows: {len(accepted)}")
    print(f"Rows needing eligibility/terms review: {sum(p['review'] for p in accepted)}")
    print(f"Excluded rows: {sum(excluded.values())}")
    for reason, count in sorted(excluded.items()):
        print(f"  Excluded - {reason}: {count}")

    print("\nEligible records:")
    for p in sorted(accepted, key=lambda item: (item["price"], item["speed"])):
        review_label = " [REVIEW REQUIRED]" if p["review"] else ""
        print(f"  ₹{p['price']:,.2f} | {p['speed']:g} Mbps | {p['name']}{review_label}")

    if not args.apply:
        print("\nPREVIEW ONLY. No database changes were made.")
        print("Review this output, then rerun with --apply to import.")
        return

    if not accepted:
        raise ValueError("No eligible records to import.")

    backup = BASE / (
        f"broadband_before_bsnl_import_{datetime.now():%Y%m%d_%H%M%S}.db"
    )
    shutil.copy2(DB_PATH, backup)
    print(f"\nDatabase backup: {backup.name}")

    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("BEGIN IMMEDIATE")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(plans)")
        }
        required_db = {
            "provider", "plan_name", "price", "speed_mbps", "state", "city",
            "pincode", "offer", "offer_price", "offer_expiry", "source_url",
            "last_updated", "availability_verified",
            "availability_checked_at", "availability_evidence_url",
        }
        if not required_db.issubset(columns):
            raise RuntimeError(
                f"Unexpected plans schema; missing {sorted(required_db - columns)}"
            )

        before = dict(connection.execute(
            "SELECT provider, COUNT(*) FROM plans GROUP BY provider"
        ))

        # Replace only BSNL catalogue rows. Other providers are untouched.
        connection.execute("DELETE FROM plans WHERE provider = ?", (PROVIDER,))

        for p in accepted:
            connection.execute(
                """
                INSERT INTO plans (
                    provider, plan_name, price, speed_mbps,
                    state, city, pincode, offer,
                    offer_price, offer_expiry, source_url, last_updated,
                    availability_verified, availability_checked_at,
                    availability_evidence_url
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?, 0, NULL, NULL)
                """,
                (
                    PROVIDER, p["name"], p["price"], p["speed"],
                    p["state"], p["city"], p["offer"], p["source"],
                    p["last_updated"],
                ),
            )

        after = dict(connection.execute(
            "SELECT provider, COUNT(*) FROM plans GROUP BY provider"
        ))

        for provider in ("Airtel Xstream Fiber", "Tata Play Fiber"):
            if before.get(provider, 0) != after.get(provider, 0):
                raise RuntimeError(f"{provider} row count changed unexpectedly.")

        verified = connection.execute(
            """
            SELECT COUNT(*) FROM plans
            WHERE provider = ? AND availability_verified = 1
            """,
            (PROVIDER,),
        ).fetchone()[0]
        if verified:
            raise RuntimeError("BSNL rows must remain regionally unverified.")

        connection.commit()
        print("\nBSNL catalogue import successful.")
        print("Provider counts:")
        for provider, count in sorted(after.items()):
            print(f"  {provider}: {count}")
        print("BSNL regionally verified rows:", verified)

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
