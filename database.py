import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "broadband.db"


def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    """Create the plans table and safely migrate older local databases."""
    connection = get_connection()

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                price REAL,
                speed_mbps INTEGER,
                state TEXT,
                city TEXT,
                pincode TEXT,
                offer TEXT,
                offer_price REAL,
                offer_expiry TEXT,
                source_url TEXT NOT NULL,
                last_updated TEXT NOT NULL,
                availability_verified INTEGER NOT NULL DEFAULT 0,
                availability_checked_at TEXT,
                availability_evidence_url TEXT,
                CHECK (price IS NULL OR price >= 0),
                CHECK (offer_price IS NULL OR offer_price >= 0),
                CHECK (speed_mbps IS NULL OR speed_mbps > 0),
                CHECK (availability_verified IN (0, 1))
            )
        """)

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(plans)"
            ).fetchall()
        }

        migrations = {
            "offer_expiry": "ALTER TABLE plans ADD COLUMN offer_expiry TEXT",
            "availability_verified": (
                "ALTER TABLE plans ADD COLUMN "
                "availability_verified INTEGER NOT NULL DEFAULT 0"
            ),
            "availability_checked_at": (
                "ALTER TABLE plans ADD COLUMN availability_checked_at TEXT"
            ),
            "availability_evidence_url": (
                "ALTER TABLE plans ADD COLUMN availability_evidence_url TEXT"
            ),
        }

        for column, sql in migrations.items():
            if column not in columns:
                connection.execute(sql)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_plans_city
            ON plans(city)
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_plans_price
            ON plans(price)
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_plans_provider
            ON plans(provider)
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_plans_availability
            ON plans(availability_verified)
        """)

        connection.commit()
        print(f"Database initialized: {DATABASE_PATH}")

    finally:
        connection.close()


def save_or_update_plan(plan):
    """Insert/update a plan while preserving verification unless explicitly changed."""
    if (
        not plan.get("provider")
        or not plan.get("plan_name")
        or not plan.get("source_url")
        or not plan.get("last_updated")
        or plan.get("price") is None
        or plan.get("speed_mbps") is None
        or plan["price"] <= 0
        or plan["speed_mbps"] <= 0
    ):
        raise ValueError("Plan failed validation")

    availability_verified = plan.get("availability_verified")
    if availability_verified is not None:
        availability_verified = int(bool(availability_verified))

    if availability_verified == 1:
        if not (plan.get("city") or plan.get("pincode")):
            raise ValueError(
                "A regionally verified plan must include a city or PIN code."
            )
        if not plan.get("availability_evidence_url"):
            raise ValueError(
                "A regionally verified plan must include availability_evidence_url."
            )
        if not plan.get("availability_checked_at"):
            raise ValueError(
                "A regionally verified plan must include availability_checked_at."
            )

    connection = get_connection()

    try:
        existing = connection.execute("""
            SELECT id FROM plans
            WHERE provider = ?
              AND plan_name = ?
              AND source_url = ?
              AND city IS ?
              AND pincode IS ?
        """, (
            plan["provider"],
            plan["plan_name"],
            plan["source_url"],
            plan.get("city"),
            plan.get("pincode"),
        )).fetchone()

        if existing:
            connection.execute("""
                UPDATE plans
                SET price = ?,
                    speed_mbps = ?,
                    state = COALESCE(?, state),
                    offer = COALESCE(?, offer),
                    offer_price = ?,
                    offer_expiry = COALESCE(?, offer_expiry),
                    last_updated = ?,
                    availability_verified = COALESCE(?, availability_verified),
                    availability_checked_at = COALESCE(?, availability_checked_at),
                    availability_evidence_url = COALESCE(?, availability_evidence_url)
                WHERE id = ?
            """, (
                plan["price"],
                plan["speed_mbps"],
                plan.get("state"),
                plan.get("offer"),
                plan.get("offer_price"),
                plan.get("offer_expiry"),
                plan["last_updated"],
                availability_verified,
                plan.get("availability_checked_at"),
                plan.get("availability_evidence_url"),
                existing["id"],
            ))
        else:
            connection.execute("""
                INSERT INTO plans (
                    provider, plan_name, price, speed_mbps,
                    state, city, pincode, offer, offer_price,
                    offer_expiry, source_url, last_updated,
                    availability_verified, availability_checked_at,
                    availability_evidence_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plan["provider"],
                plan["plan_name"],
                plan["price"],
                plan["speed_mbps"],
                plan.get("state"),
                plan.get("city"),
                plan.get("pincode"),
                plan.get("offer"),
                plan.get("offer_price"),
                plan.get("offer_expiry"),
                plan["source_url"],
                plan["last_updated"],
                availability_verified if availability_verified is not None else 0,
                plan.get("availability_checked_at"),
                plan.get("availability_evidence_url"),
            ))

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def get_plans(city=None, max_price=None, pincode=None):
    """
    No location filter: return the nationwide information catalogue.
    With a city or PIN: return only availability-verified regional matches.
    """
    query = "SELECT * FROM plans WHERE 1 = 1"
    parameters = []

    if city:
        query += " AND availability_verified = 1"
        query += " AND LOWER(TRIM(city)) = LOWER(TRIM(?))"
        parameters.append(city.strip())

    if pincode:
        if not city:
            query += " AND availability_verified = 1"
        query += " AND TRIM(pincode) = ?"
        parameters.append(pincode.strip())

    if max_price is not None:
        query += " AND price <= ?"
        parameters.append(max_price)

    query += " ORDER BY price ASC"

    with get_connection() as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [dict(row) for row in rows]


if __name__ == "__main__":
    init_db()
