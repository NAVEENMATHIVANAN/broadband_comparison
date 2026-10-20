import argparse
import csv
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "tata_candidates.csv"
DB_PATH = BASE_DIR / "broadband.db"
PROVIDER = "Tata Play Fiber"
SOURCE_URL = "https://www.tataplayfiber.com/broadband-plans/chennai"


def extract_plan_id(offer):
    match = re.search(r"Official plan ID:\s*([A-Za-z0-9_-]+)", offer or "", re.I)
    return match.group(1).strip() if match else None


def backup_database(connection):
    backup_path = BASE_DIR / (
        f"broadband_before_tata_upsert_{datetime.now():%Y%m%d_%H%M%S}.db"
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

    required = {
        "provider", "plan_name", "price", "speed_mbps", "state", "city",
        "offer", "source_url", "availability_verified", "last_updated",
    }
    missing = required - headers
    if missing:
        raise ValueError(f"Missing CSV columns: {sorted(missing)}")
    if not rows:
        raise ValueError("The Tata CSV contains no records.")

    prepared = []
    ids = []
    for line, row in enumerate(rows, start=2):
        plan_id = extract_plan_id(row.get("offer", ""))
        try:
            price = float(row["price"])
            speed = float(row["speed_mbps"])
        except (TypeError, ValueError):
            raise ValueError(f"Invalid price/speed on CSV line {line}")

        if row["provider"].strip() != PROVIDER:
            raise ValueError(f"Unexpected provider on CSV line {line}")
        if not row["plan_name"].strip() or price <= 0 or speed <= 0:
            raise ValueError(f"Invalid plan name, price, or speed on line {line}")
        if not plan_id:
            raise ValueError(
                f"Missing official plan ID on CSV line {line}; refusing unsafe import."
            )
        if row["source_url"].strip() != SOURCE_URL:
            raise ValueError(f"Unexpected source URL on CSV line {line}")
        if row["availability_verified"].strip().lower() not in {"false", "no", "0"}:
            raise ValueError(
                f"Catalogue row must remain regionally unverified (CSV line {line})."
            )
        if not row["last_updated"].strip():
            raise ValueError(f"Missing last_updated on CSV line {line}")

        ids.append(plan_id)
        prepared.append({
            "plan_id": plan_id,
            "plan_name": row["plan_name"].strip(),
            "price": price,
            "speed_mbps": speed,
            "state": row.get("state", "").strip() or None,
            "city": row.get("city", "").strip() or None,
            "offer": row.get("offer", "").strip() or None,
            "source_url": row["source_url"].strip(),
            "last_updated": row["last_updated"].strip(),
        })

    duplicates = [key for key, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError(f"Duplicate official plan IDs in CSV: {duplicates}")
    return prepared


def main():
    parser = argparse.ArgumentParser(
        description="Safely upsert Tata catalogue records by official plan ID."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write changes. Without this flag, perform a read-only preview.",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Missing database: {DB_PATH}")
    prepared = load_candidates()
    print(f"Validated Tata catalogue rows: {len(prepared)}")
    print(f"Unique official plan IDs: {len({p['plan_id'] for p in prepared})}")
    print("Regional availability: all incoming rows are unverified.")
    if not args.apply:
        print("PREVIEW ONLY. No database changes made. Use --apply to write.")
        return

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
        print(f"Database backup: {backup_path.name}")

        inserted = updated = 0
        connection.execute("BEGIN IMMEDIATE")
        for p in prepared:
            matches = connection.execute("""
                SELECT id, availability_verified
                FROM plans
                WHERE provider = ? AND official_plan_id = ?
            """, (PROVIDER, p["plan_id"])).fetchall()

            if len(matches) > 1:
                raise RuntimeError(
                    f"Multiple database rows match Tata plan ID {p['plan_id']!r}."
                )

            if matches:
                row = matches[0]
                # Only catalogue fields are updated. Regional verification fields,
                # pincode, offer price, and offer expiry are intentionally preserved.
                connection.execute("""
                    UPDATE plans
                    SET plan_name = ?, price = ?, speed_mbps = ?,
                        state = ?, city = ?, offer = ?, source_url = ?,
                        last_updated = ?, official_plan_id = ?
                    WHERE id = ?
                """, (
                    p["plan_name"], p["price"], p["speed_mbps"], p["state"],
                    p["city"], p["offer"], p["source_url"], p["last_updated"],
                    p["plan_id"], row["id"],
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
                            NULL, NULL, ?)
                """, (
                    PROVIDER, p["plan_name"], p["price"], p["speed_mbps"],
                    p["state"], p["city"], p["offer"], p["source_url"],
                    p["last_updated"], p["plan_id"],
                ))
                inserted += 1

        after = dict(connection.execute(
            "SELECT provider, COUNT(*) FROM plans GROUP BY provider"
        ).fetchall())
        for other in ("Airtel Xstream Fiber", "BSNL Bharat Fiber"):
            if before.get(other, 0) != after.get(other, 0):
                raise RuntimeError(f"{other} row count changed unexpectedly.")
        duplicates = connection.execute("""
            SELECT official_plan_id, COUNT(*) AS n
            FROM plans
            WHERE provider = ? AND official_plan_id IS NOT NULL
              AND TRIM(official_plan_id) <> ''
            GROUP BY official_plan_id HAVING COUNT(*) > 1
        """, (PROVIDER,)).fetchall()
        if duplicates:
            raise RuntimeError(f"Duplicate Tata official IDs detected: {duplicates}")

        connection.commit()
        print("\nTata upsert completed successfully.")
        print(f"Inserted: {inserted}")
        print(f"Updated: {updated}")
        print("Provider counts:")
        for provider, count in sorted(after.items()):
            print(f"  {provider}: {count}")
        print("Existing rows absent from this CSV were retained.")
        print("Regional verification fields were preserved.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
