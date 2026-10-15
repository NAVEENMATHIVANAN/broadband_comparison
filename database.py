
import sqlite3
from pathlib import Path

# Store the database beside this Python file.
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "broadband.db"


def get_connection():
    """Create a connection to the SQLite database."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    """Create the plans table if it does not exist."""

    with get_connection() as connection:
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

                CHECK (price IS NULL OR price >= 0),
                CHECK (offer_price IS NULL OR offer_price >= 0),
                CHECK (speed_mbps IS NULL OR speed_mbps > 0)
            )
        """)

        # Improve filtering by city and price.
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

    print(f"Database initialized: {DATABASE_PATH}")


def get_plans(city=None, max_price=None):
    """Return plans with optional city and price filters."""

    query = "SELECT * FROM plans WHERE 1 = 1"
    parameters = []

    if city:
        query += " AND LOWER(city) = LOWER(?)"
        parameters.append(city.strip())

    if max_price is not None:
        query += " AND price <= ?"
        parameters.append(max_price)

    query += " ORDER BY price ASC"

    with get_connection() as connection:
        rows = connection.execute(
            query, parameters
        ).fetchall()

    return [dict(row) for row in rows]


if __name__ == "__main__":
    init_db()
