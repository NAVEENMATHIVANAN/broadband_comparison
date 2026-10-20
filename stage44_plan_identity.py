
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DB_PATH = Path("broadband.db")
TATA_PROVIDER = "Tata Play Fiber"

def extract_official_id(source_url, offer):
    """Extract a genuine provider plan ID; never generate a replacement."""
    if source_url:
        try:
            params = parse_qs(urlparse(source_url).query)
            for key in ("plan-id", "plan_id", "planId", "planid"):
                values = params.get(key)
                if values and values[0].strip():
                    return values[0].strip()
        except (ValueError, AttributeError):
            pass

    text = offer or ""
    patterns = [
        r"Official\s+plan\s+ID\s*:\s*([A-Za-z0-9_-]+)",
        r"\bplan-id\s*[:=]\s*([A-Za-z0-9_-]+)",
        r"\bplan_id\s*[:=]\s*([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return None


def main():
    if not DB_PATH.is_file():
        raise SystemExit(
            f"Database not found: {DB_PATH.resolve()}. "
            "Run this script from the project root."
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(
        f"broadband_before_stage44_{timestamp}.db"
    )

    # Create a consistent SQLite backup before changing the schema.
    source = sqlite3.connect(str(DB_PATH))
    backup = sqlite3.connect(str(backup_path))
    source.backup(backup)
    backup.close()
    source.close()
    print(f"Database backup: {backup_path}")

    connection = sqlite3.connect(str(DB_PATH), timeout=30)
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        connection.execute("BEGIN IMMEDIATE")

        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(plans)")
        }
        if not columns:
            raise RuntimeError("The plans table does not exist.")

        if "official_plan_id" not in columns:
            connection.execute(
                "ALTER TABLE plans ADD COLUMN official_plan_id TEXT"
            )
            print("Added official_plan_id column.")
        else:
            print("official_plan_id column already exists.")

        before_counts = dict(
            connection.execute("""
                SELECT provider, COUNT(*)
                FROM plans
                GROUP BY provider
            """).fetchall()
        )
        before_total = connection.execute(
            "SELECT COUNT(*) FROM plans"
        ).fetchone()[0]

        tata_rows = connection.execute("""
            SELECT id, source_url, offer
            FROM plans
            WHERE provider = ?
        """, (TATA_PROVIDER,)).fetchall()

        updated = 0
        missing = 0

        for row_id, source_url, offer in tata_rows:
            official_id = extract_official_id(source_url, offer)
            if not official_id:
                missing += 1
                continue

            existing = connection.execute("""
                SELECT official_plan_id
                FROM plans
                WHERE id = ?
            """, (row_id,)).fetchone()[0]

            # Do not overwrite a previously recorded ID with a different one.
            if existing and existing != official_id:
                raise RuntimeError(
                    f"ID conflict for database row {row_id}: "
                    f"existing={existing!r}, extracted={official_id!r}"
                )

            if not existing:
                connection.execute("""
                    UPDATE plans
                    SET official_plan_id = ?
                    WHERE id = ?
                """, (official_id, row_id))
                updated += 1

        # Refuse to create the unique index if real duplicates exist.
        duplicates = connection.execute("""
            SELECT provider, official_plan_id, COUNT(*)
            FROM plans
            WHERE official_plan_id IS NOT NULL
              AND TRIM(official_plan_id) <> ''
            GROUP BY provider, official_plan_id
            HAVING COUNT(*) > 1
        """).fetchall()

        if duplicates:
            details = "; ".join(
                f"{provider}: {plan_id} ({count} rows)"
                for provider, plan_id, count in duplicates
            )
            raise RuntimeError(
                "Duplicate official IDs detected; no migration changes "
                f"were committed. Resolve these first: {details}"
            )

        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_plans_provider_official_plan_id
            ON plans(provider, official_plan_id)
            WHERE official_plan_id IS NOT NULL
              AND TRIM(official_plan_id) <> ''
        """)

        after_counts = dict(
            connection.execute("""
                SELECT provider, COUNT(*)
                FROM plans
                GROUP BY provider
            """).fetchall()
        )
        after_total = connection.execute(
            "SELECT COUNT(*) FROM plans"
        ).fetchone()[0]

        if before_counts != after_counts or before_total != after_total:
            raise RuntimeError(
                "Plan counts changed unexpectedly; rolling back."
            )

        connection.commit()

        print("\nStage 44 migration successful.")
        print("Total plans before/after:", before_total, "/", after_total)
        print("Provider counts:", after_counts)
        print("Tata IDs newly populated:", updated)
        print("Tata rows without extractable IDs:", missing)
        print("Unique index: uq_plans_provider_official_plan_id")
        print("Regional verification values were not modified.")

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
