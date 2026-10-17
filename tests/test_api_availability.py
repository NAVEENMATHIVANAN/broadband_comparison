
import sqlite3
from contextlib import contextmanager

from fastapi.testclient import TestClient

import main


def make_test_database(path):
    """Create an isolated database for API tests."""
    with sqlite3.connect(path) as con:
        con.execute("""
            CREATE TABLE plans (
                id INTEGER PRIMARY KEY,
                provider TEXT,
                plan_name TEXT,
                price REAL,
                speed_mbps INTEGER,
                state TEXT,
                city TEXT,
                pincode TEXT,
                offer TEXT,
                offer_price REAL,
                offer_expiry TEXT,
                source_url TEXT,
                last_updated TEXT,
                availability_verified INTEGER DEFAULT 0,
                availability_checked_at TEXT,
                availability_evidence_url TEXT
            )
        """)

        con.executemany("""
            INSERT INTO plans (
                provider, plan_name, price, speed_mbps, city,
                offer, source_url, availability_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                "Test ISP", "Unverified plan", 499, 40,
                "Anna Nagar, Chennai", "Intro offer",
                "https://example.com/plan", 0
            ),
            (
                "Test ISP", "Verified plan", 699, 100,
                "Anna Nagar, Chennai", "Discount offer",
                "https://example.com/verified", 1
            ),
            (
                "Test ISP", "Other area plan", 599, 50,
                "Adyar, Chennai", "Other offer",
                "https://example.com/adyar", 1
            ),
        ])


def test_nationwide_catalogue_can_include_unverified_plans(
    monkeypatch, tmp_path
):
    """The API labels nationwide results as a catalogue."""
    monkeypatch.setattr(
        main,
        "get_plans",
        lambda **kwargs: [
            {
                "id": 1,
                "plan_name": "Unverified plan",
                "availability_verified": 0,
            }
        ],
    )

    with TestClient(main.app) as client:
        response = client.get("/plans")

    assert response.status_code == 200
    data = response.json()
    assert data["catalogue_mode"] == "nationwide_catalogue"
    assert len(data["plans"]) == 1
    assert data["plans"][0]["availability_verified"] == 0


def test_regional_search_requests_place_filter(monkeypatch):
    """Regional searches pass the requested place to the data layer."""
    calls = []

    def fake_get_plans(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(main, "get_plans", fake_get_plans)

    with TestClient(main.app) as client:
        response = client.get(
            "/plans",
            params={"place": "Anna Nagar, Chennai"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["catalogue_mode"] == "verified_regional_results"
    assert "Only plans verified" in data["availability_note"]
    assert calls[0]["city"] == "Anna Nagar, Chennai"


def test_regional_offers_exclude_unverified_and_other_places(
    monkeypatch, tmp_path
):
    """Only verified offers matching the requested city are returned."""
    db_path = tmp_path / "test_broadband.db"
    make_test_database(db_path)

    @contextmanager
    def test_connection():
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    monkeypatch.setattr(main, "get_connection", test_connection)

    with TestClient(main.app) as client:
        response = client.get(
            "/offers",
            params={"place": "Anna Nagar, Chennai"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["catalogue_mode"] == "verified_regional_results"
    assert len(data["offers"]) == 1
    assert data["offers"][0]["plan_name"] == "Verified plan"


def test_invalid_pin_code_is_rejected():
    """Legacy PIN input must not accept invalid values."""
    with TestClient(main.app) as client:
        response = client.get("/plans", params={"pincode": "123"})

    assert response.status_code == 400
