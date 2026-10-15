
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

from database import get_plans, get_connection, init_db
from scraper import run_scraper


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Broadband Price Comparison API",
    description="Compare broadband plans in India.",
    version="1.1.0",
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


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

    if pincode and (
        not pincode.isdigit() or len(pincode) != 6
    ):
        raise HTTPException(
            400, "PIN code must contain exactly 6 digits."
        )

    plans = get_plans(
        city=city,
        max_price=max_price,
        pincode=pincode,
    )

    return {
        "count": len(plans),
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
):
    query = """
        SELECT * FROM plans
        WHERE offer IS NOT NULL
          AND TRIM(offer) != ''
    """
    parameters = []

    if city and city.strip():
        query += " AND LOWER(city) = LOWER(?)"
        parameters.append(city.strip())

    query += " ORDER BY price ASC"

    with get_connection() as connection:
        rows = connection.execute(
            query, parameters
        ).fetchall()

    offers = [dict(row) for row in rows]

    return {
        "count": len(offers),
        "city": city,
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
        raise HTTPException(
            500, "The scraping process failed."
        ) from error
