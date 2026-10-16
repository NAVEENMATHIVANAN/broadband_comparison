from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import get_plans, get_connection, init_db
from scraper import run_scraper


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Broadband Price Comparison API",
    description="Compare broadband plans in India. Regional results require verified availability.",
    version="1.2.0",
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"title": "Broadband Compare India"},
    )


@app.get("/plans")
def list_plans(
    city: str | None = Query(default=None),
    pincode: str | None = Query(default=None),
    max_price: float | None = Query(default=None, gt=0),
):
    city = city.strip() if city else None
    pincode = pincode.strip() if pincode else None

    if city == "":
        raise HTTPException(400, "City cannot be empty.")

    if pincode and (not pincode.isdigit() or len(pincode) != 6):
        raise HTTPException(400, "PIN code must contain exactly 6 digits.")

    plans = get_plans(city=city, max_price=max_price, pincode=pincode)
    regional_search = bool(city or pincode)

    return {
        "count": len(plans),
        "catalogue_mode": (
            "verified_regional_results" if regional_search
            else "nationwide_catalogue"
        ),
        "availability_note": (
            "Only plans marked as availability-verified for the requested "
            "region are returned."
            if regional_search
            else "Nationwide plan information may include plans whose local "
                 "availability has not been verified."
        ),
        "filters": {
            "city": city,
            "pincode": pincode,
            "max_price": max_price,
        },
        "plans": plans,
    }


@app.get("/offers")
def list_offers(
    city: str | None = Query(default=None),
    pincode: str | None = Query(default=None),
):
    city = city.strip() if city else None
    pincode = pincode.strip() if pincode else None

    if city == "":
        raise HTTPException(400, "City cannot be empty.")
    if pincode and (not pincode.isdigit() or len(pincode) != 6):
        raise HTTPException(400, "PIN code must contain exactly 6 digits.")

    query = """
        SELECT * FROM plans
        WHERE offer IS NOT NULL
          AND TRIM(offer) != ''
    """
    parameters = []

    if city:
        query += " AND availability_verified = 1 AND LOWER(TRIM(city)) = LOWER(TRIM(?))"
        parameters.append(city)
    if pincode:
        if not city:
            query += " AND availability_verified = 1"
        query += " AND TRIM(pincode) = ?"
        parameters.append(pincode)

    query += " ORDER BY price ASC"

    with get_connection() as connection:
        rows = connection.execute(query, parameters).fetchall()

    offers = [dict(row) for row in rows]
    return {
        "count": len(offers),
        "city": city,
        "pincode": pincode,
        "catalogue_mode": (
            "verified_regional_results" if city or pincode
            else "nationwide_catalogue"
        ),
        "offers": offers,
    }


@app.post("/scrape")
def scrape_plans():
    try:
        count = run_scraper()
        return {
            "message": "Scraping process completed.",
            "processed_count": count,
        }
    except Exception as error:
        raise HTTPException(500, "The scraping process failed.") from error


@app.get("/regions")
def list_regions():
    """List only regions attached to plans explicitly marked as verified."""
    with get_connection() as connection:
        rows = connection.execute("""
            SELECT DISTINCT city
            FROM plans
            WHERE availability_verified = 1
              AND city IS NOT NULL
              AND TRIM(city) != ''
            ORDER BY city
        """).fetchall()

        pincode_rows = connection.execute("""
            SELECT DISTINCT pincode
            FROM plans
            WHERE availability_verified = 1
              AND pincode IS NOT NULL
              AND TRIM(pincode) != ''
            ORDER BY pincode
        """).fetchall()

        total = connection.execute(
            "SELECT COUNT(*) FROM plans"
        ).fetchone()[0]

        verified_count = connection.execute("""
            SELECT COUNT(*) FROM plans
            WHERE availability_verified = 1
        """).fetchone()[0]

        verified_city_count = connection.execute("""
            SELECT COUNT(*) FROM plans
            WHERE availability_verified = 1
              AND city IS NOT NULL
              AND TRIM(city) != ''
        """).fetchone()[0]

    return {
        "total_plans": total,
        "availability_verified_plans": verified_count,
        "plans_with_city": verified_city_count,
        "regional_data_complete": total > 0 and verified_count == total,
        "cities": [row["city"] for row in rows],
        "pincodes": [row["pincode"] for row in pincode_rows],
    }
