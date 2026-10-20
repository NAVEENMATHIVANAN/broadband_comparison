import argparse
import csv
import re
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


def normalize_name(value):
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def backup_database(connection):
    backup_path = BASE / (
        f"broadband_before_bsnl_upsert_{datetime.now():%Y%m%d_%H%M%S}.db"
    )
    backup = sqlite3.connect(str(backup_path))
    try:
        connection.backup(backup)
    finally:
        backup.close()
    return backup_path


def load_candidates():
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Missing CSV: {CSV_PATH}")
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = set(reader.fieldnames or [])

    required = {"plan_name", "price", "speed_mbps", "billing_period"}
    if not required.issubset(headers):
        raise ValueError(f"CSV missing required columns: {sorted(required - headers)}")

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

        parts = [(row.get("offer") or "").strip(), "Billing period: monthly."]
        if review:
            parts.append(
                "REVIEW REQUIRED: eligibility or plan terms must be checked "
                "before recommending this plan to a customer."
            )
        for field in ("billing_period", "review_required"):
            value = (row.get(field) or "").strip()
            if value:
                parts.append(f"{field}: {value}.")

        accepted.append({
            "name": name,
            "name_key": normalize_name(name),
            "price": price,
            "speed": speed,
            "state": (row.get("state") or "").strip() or None,
            "city": (row.get("city") or "").strip() or None,
            "offer": " ".join(part for part in parts if part),
            "source": source,
            "last_updated": (
                (row.get("last_updated") or "").strip()
                or datetime.now().astimezone().isoformat(timespec="seconds")
            ),
            "review": review,
        })

    counts = Counter(p["name_key"] for p in accepted)
    duplicates = [name for name, count in counts.items() if count > 1]
    if duplicates:
        raise ValueError(
            "Duplicate normalized BSNL plan names in CSV; resolve before import: "
            f"{duplicates}"
        )
    return rows, accepted, excluded


def main():
    parser = argparse.ArgumentParser(
        description="Safely upsert BSNL catalogue rows by normalized plan name."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write changes. Without this flag, perform a read-only preview.",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Missing database: {DB_PATH}")
    source_rows, accepted, excluded = load_candidates()

    print(f"Source candidate rows: {len(source_rows)}")
    print(f"Eligible monthly catalogue rows: {len(accepted)}")
    print(f"Rows needing eligibility/terms review: {sum(p['review'] for p in accepted)}")
    print(f"Excluded rows: {sum(excluded.values())}")
    for reason, count in sorted(excluded.items()):
        print(f"  Excluded - {reason}: {count}")

    print("\nEligible records:")
    for p in sorted(accepted, key=lambda item: (item["price"], item["speed"])):
        label = " [REVIEW REQUIRED]" if p["review"] else ""
        print(f"  ₹{p['price']:,.2f} | {p['speed']:g} Mbps | {p['name']}{label}")

    if not args.apply:
        print("\nPREVIEW ONLY. No database changes made. Use --apply to write.")
        return
    if not accepted:
        raise ValueError("No eligible records to import.")

    connection = sqlite3.connect(str(DB_PATH), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(plans)")
        }
        required_db = {
            "id", "provider", "plan_name", "price", "speed_mbps", "state", "city",
            "pincode", "offer", "offer_price", "offer_expiry", "source_url",
            "last_updated", "availability_verified", "availability_checked_at",
            "availability_evidence_url", "official_plan_id",
        }
        if not required_db.issubset(columns):
            raise RuntimeError(
                "Run Stage 44 first. Missing database columns: "
                f"{sorted(required_db - columns)}"
            )

        before = dict(connection.execute(
            "SELECT provider, COUNT(*) FROM plans GROUP BY provider"
        ).fetchall())
        backup_path = backup_database(connection)
        print(f"\nDatabase backup: {backup_path.name}")

        inserted = updated = 0
        connection.execute("BEGIN IMMEDIATE")
        for p in accepted:
            matches = connection.execute("""
                SELECT id, plan_name, official_plan_id
                FROM plans
                WHERE provider = ? AND lower(trim(plan_name)) = ?
            """, (PROVIDER, p["name_key"])).fetchall()

            if len(matches) > 1:
                raise RuntimeError(
                    f"Multiple existing BSNL rows match plan name {p['name']!r}; "
                    "resolve ambiguity manually."
                )

            if matches:
                row = matches[0]
                connection.execute("""
                    UPDATE plans
                    SET plan_name = ?, price = ?, speed_mbps = ?,
                        state = ?, city = ?, offer = ?, source_url = ?,
                        last_updated = ?
                    WHERE id = ?
                """, (
                    p["name"], p["price"], p["speed"], p["state"], p["city"],
                    p["offer"], p["source"], p["last_updated"], row["id"],
                ))
                updated += 1
            else:
                connection.execute("""
                    INSERT INTO plans (
                        provider, plan_name, price, speed_mbps, state, city,
                        pincode, offer, offer_price, offer_expiry, source_url,
                        last_updated, availability_verified,
                        availability_checked_at, availability_evidence_url,
                        official_plan_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?, 0,
                            NULL, NULL, NULL)
                """, (
                    PROVIDER, p["name"], p["price"], p["speed"], p["state"],
                    p["city"], p["offer"], p["source"], p["last_updated"],
                ))
                inserted += 1

        after = dict(connection.execute(
            "SELECT provider, COUNT(*) FROM plans GROUP BY provider"
        ).fetchall())
        for other in ("Airtel Xstream Fiber", "Tata Play Fiber"):
            if before.get(other, 0) != after.get(other, 0):
                raise RuntimeError(f"{other} row count changed unexpectedly.")

        verified = connection.execute("""
            SELECT COUNT(*) FROM plans
            WHERE provider = ? AND availability_verified = 1
        """, (PROVIDER,)).fetchone()[0]

        connection.commit()
        print("\nBSNL upsert completed successfully.")
        print(f"Inserted: {inserted}")
        print(f"Updated: {updated}")
        print("Provider counts:")
        for provider, count in sorted(after.items()):
            print(f"  {provider}: {count}")
        print("BSNL regionally verified rows:", verified)
        print("Existing rows absent from this CSV were retained.")
        print("Regional verification fields were preserved.")
        print(
            "Note: BSNL matching uses normalized plan names because the current "
            "candidate importer does not expose a verified official plan ID."
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
