
import csv
import re
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "tata_candidates.csv"
DB_PATH = BASE_DIR / "broadband.db"
BACKUP_PATH = BASE_DIR / (
    f"broadband_before_tata_import_{datetime.now():%Y%m%d_%H%M%S}.db"
)

PROVIDER = "Tata Play Fiber"
SOURCE_URL = "https://www.tataplayfiber.com/broadband-plans/chennai"


def extract_plan_id(offer):
    match = re.search(r"Official plan ID:\s*([^.]+)", offer or "")
    return match.group(1).strip() if match else None


def main():
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Missing CSV: {CSV_PATH}")
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Missing database: {DB_PATH}")

    with CSV_PATH.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    if not rows:
        raise ValueError("The Tata CSV contains no records.")

    required = {
        "provider", "plan_name", "price", "speed_mbps",
        "state", "city", "offer", "source_url",
        "availability_verified", "last_updated",
    }
    missing_columns = required - set(rows[0].keys())
    if missing_columns:
        raise ValueError(f"Missing CSV columns: {sorted(missing_columns)}")

    plan_ids = []
    prepared = []

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
            raise ValueError(f"Missing official plan ID on CSV line {line}")
        if row["source_url"].strip() != SOURCE_URL:
            raise ValueError(f"Unexpected source URL on CSV line {line}")

        # The scraper's records are catalogue listings, not proof of
        # availability at a particular address.
        if row["availability_verified"].strip().lower() not in {
            "false", "no", "0"
        }:
            raise ValueError(
                f"Unexpected regional verification flag on line {line}"
            )

        plan_ids.append(plan_id)
        prepared.append({
            "plan_name": row["plan_name"].strip(),
            "price": price,
            "speed_mbps": speed,
            "state": row.get("state", "").strip() or None,
            "city": row.get("city", "").strip() or None,
            "offer": row.get("offer", "").strip() or None,
            "source_url": row["source_url"].strip(),
            "last_updated": row["last_updated"].strip(),
            "plan_id": plan_id,
        })

    duplicate_ids = [
        plan_id for plan_id, count in Counter(plan_ids).items()
        if count > 1
    ]
    if duplicate_ids:
        raise ValueError(f"Duplicate official plan IDs: {duplicate_ids}")

    print(f"CSV records validated: {len(prepared)}")
    print(f"Unique official plan IDs: {len(set(plan_ids))}")
    print("Regional availability: all unverified")

    # Back up the existing DB before any changes.
    shutil.copy2(DB_PATH, BACKUP_PATH)
    print(f"Database backup created: {BACKUP_PATH.name}")

    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("BEGIN IMMEDIATE")

        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(plans)")
        }
        expected_columns = {
            "id", "provider", "plan_name", "price", "speed_mbps",
            "state", "city", "pincode", "offer", "offer_price",
            "offer_expiry", "source_url", "last_updated",
            "availability_verified", "availability_checked_at",
            "availability_evidence_url",
        }
        if not expected_columns.issubset(columns):
            raise RuntimeError(
                "The plans table schema differs from the expected schema. "
                f"Missing: {sorted(expected_columns - columns)}"
            )

        before = dict(connection.execute(
            "SELECT provider, COUNT(*) FROM plans GROUP BY provider"
        ))

        # Remove only the previous Tata catalogue rows. This operation
        # is rolled back automatically if a later statement fails.
        connection.execute(
            "DELETE FROM plans WHERE provider = ?", (PROVIDER,)
        )

        for plan in prepared:
            connection.execute(
                """
                INSERT INTO plans (
                    provider, plan_name, price, speed_mbps,
                    state, city, pincode, offer,
                    offer_price, offer_expiry, source_url, last_updated,
                    availability_verified, availability_checked_at,
                    availability_evidence_url
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?,
                        0, NULL, NULL)
                """,
                (
                    PROVIDER,
                    plan["plan_name"],
                    plan["price"],
                    plan["speed_mbps"],
                    plan["state"],
                    plan["city"],
                    plan["offer"],
                    plan["source_url"],
                    plan["last_updated"],
                ),
            )

        after = dict(connection.execute(
            "SELECT provider, COUNT(*) FROM plans GROUP BY provider"
        ))

        if after.get(PROVIDER) != len(prepared):
            raise RuntimeError("Tata row count does not match the CSV.")

        for provider in ("Airtel Xstream Fiber", "BSNL Bharat Fiber"):
            if before.get(provider, 0) != after.get(provider, 0):
                raise RuntimeError(
                    f"{provider} row count changed unexpectedly."
                )

        tata_verified = connection.execute(
            """
            SELECT COUNT(*) FROM plans
            WHERE provider = ? AND availability_verified = 1
            """,
            (PROVIDER,),
        ).fetchone()[0]

        if tata_verified != 0:
            raise RuntimeError(
                "Unexpected regionally verified Tata records found."
            )

        connection.commit()

        print("\nImport completed successfully.")
        print("Provider counts:")
        for provider, count in sorted(after.items()):
            print(f"  {provider}: {count}")
        print(f"Tata regional verified records: {tata_verified}")

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
