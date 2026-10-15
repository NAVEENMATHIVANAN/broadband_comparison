
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from database import get_plans, get_connection, init_db
from scraper import run_scraper


app = FastAPI(
    title="Broadband Price Comparison API",
    description=(
        "Compare broadband plans and offers "
        "from Indian internet service providers."
    ),
    version="1.0.0",
)


@app.on_event("startup")
def startup():
    """Initialize the database when the API starts."""
    init_db()


@app.get("/", response_class=HTMLResponse)
def home():
    """Temporary landing page until the frontend is built."""
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Broadband Comparison API</title>
    </head>
    <body>
        <h1>Broadband Price Comparison</h1>
        <p>The backend is running successfully.</p>
        <p><a href="/docs">Open API documentation</a></p>
        <p><a href="/plans">View stored plans</a></p>
    </body>
    </html>
    """


@app.get("/plans")
def list_plans(
    city: str | None = Query(default=None, min_length=1),
    max_price: float | None = Query(default=None, gt=0),
):
    """Return broadband plans with optional filters."""

    if city is not None:
        city = city.strip()

        if not city:
            raise HTTPException(
                status_code=400,
                detail="City cannot be empty.",
            )

    try:
        plans = get_plans(
            city=city,
            max_price=max_price,
        )
        return {
            "count": len(plans),
            "filters": {
                "city": city,
                "max_price": max_price,
            },
            "plans": plans,
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve broadband plans.",
        ) from error


@app.get("/offers")
def list_offers(
    city: str | None = Query(default=None, min_length=1),
):
    """Return recorded promotional offers."""

    if city is not None:
        city = city.strip()

        if not city:
            raise HTTPException(
                status_code=400,
                detail="City cannot be empty.",
            )

    query = """
        SELECT *
        FROM plans
        WHERE offer IS NOT NULL
          AND TRIM(offer) != ''
    """
    parameters = []

    if city:
        query += " AND LOWER(city) = LOWER(?)"
        parameters.append(city)

    query += " ORDER BY price ASC"

    connection = get_connection()

    try:
        rows = connection.execute(
            query, parameters
        ).fetchall()

        offers = [dict(row) for row in rows]

        return {
            "count": len(offers),
            "city": city,
            "offers": offers,
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve broadband offers.",
        ) from error

    finally:
        connection.close()


@app.post("/scrape")
def scrape_plans():
    """Manually trigger the existing scraper."""

    try:
        processed = run_scraper()

        return {
            "message": "Scraping process completed.",
            "processed_count": processed,
        }

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail="The scraping process failed.",
        ) from error
