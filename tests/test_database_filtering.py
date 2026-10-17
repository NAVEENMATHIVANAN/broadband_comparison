
import sqlite3

import pytest

import database


@pytest.fixture
def test_db(monkeypatch, tmp_path):
    """Create an isolated database with verified and unverified plans."""
    db_path = tmp_path / "test_broadband.db"
    monkeypatch.setattr(database, "DATABASE_PATH", db_path)

    database.init_db()

    with sqlite3.connect(db_path) as con:
        con.executemany(
            """
            INSERT INTO plans (
                provider, plan_name, price, speed_mbps,
                state, city, pincode, offer, source_url,
                last_updated, availability_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "Test ISP", "Unverified Anna Nagar", 399, 40,
                    "Tamil Nadu", "Anna Nagar, Chennai", None, None,
                    "https://example.com/unverified",
                    "2026-10-18T10:00:00+05:30", 0,
                ),
                (
                    "Test ISP", "Verified Anna Nagar", 599, 100,
                    "Tamil Nadu", "Anna Nagar, Chennai", None, "Discount",
                    "https://example.com/verified",
                    "2026-10-18T10:00:00+05:30", 1,
                ),
                (
                    "Test ISP", "Verified Adyar", 499, 50,
                    "Tamil Nadu", "Adyar, Chennai", None, "Offer",
                    "https://example.com/adyar",
                    "2026-10-18T10:00:00+05:30", 1,
                ),
            ],
        )

    return db_path


def test_nationwide_catalogue_includes_verified_and_unverified(test_db):
    plans = database.get_plans()

    names = {p["plan_name"] for p in plans}

    assert names == {
        "Unverified Anna Nagar",
        "Verified Anna Nagar",
        "Verified Adyar",
    }


def test_regional_search_excludes_unverified_plans(test_db):
    plans = database.get_plans(city="Anna Nagar, Chennai")

    assert [p["plan_name"] for p in plans] == ["Verified Anna Nagar"]
    assert all(p["availability_verified"] == 1 for p in plans)


def test_city_matching_ignores_case_and_outer_whitespace(test_db):
    plans = database.get_plans(city="  ANNA NAGAR, CHENNAI  ")

    assert [p["plan_name"] for p in plans] == ["Verified Anna Nagar"]


def test_regional_search_does_not_return_another_city(test_db):
    plans = database.get_plans(city="Anna Nagar, Chennai")

    assert all(p["city"] == "Anna Nagar, Chennai" for p in plans)
    assert all(p["plan_name"] != "Verified Adyar" for p in plans)


def test_maximum_price_filter_works_with_regional_search(test_db):
    plans = database.get_plans(
        city="Anna Nagar, Chennai",
        max_price=500,
    )

    assert plans == []


def test_nationwide_maximum_price_filter(test_db):
    plans = database.get_plans(max_price=500)

    assert {p["plan_name"] for p in plans} == {
        "Unverified Anna Nagar",
        "Verified Adyar",
    }
