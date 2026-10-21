from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from database import get_connection, get_plans, init_db
from scraper import run_scraper
from viasat_router import router as viasat_router

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Broadband Price Comparison API",
    description="Compare plans; regional results require verified availability for the entered place.",
    version="1.3.0")
app.include_router(viasat_router)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@app.on_event("startup")
def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html",
        context={"title": "Broadband Compare India"})

def resolve_place(city, place):
    if city and place and city.strip().casefold() != place.strip().casefold():
        raise HTTPException(400, "Use either city or place, or enter the same value.")
    return (place or city or "").strip() or None

@app.get("/plans")
def list_plans(city: str | None = Query(default=None), place: str | None = Query(default=None),
               pincode: str | None = Query(default=None), max_price: float | None = Query(default=None, gt=0)):
    location = resolve_place(city, place)
    pincode = pincode.strip() if pincode else None
    if pincode and (not pincode.isdigit() or len(pincode) != 6):
        raise HTTPException(400, "PIN code must contain exactly 6 digits.")
    plans = get_plans(city=location, pincode=pincode, max_price=max_price)
    regional = bool(location or pincode)
    return {"count": len(plans), "catalogue_mode": "verified_regional_results" if regional else "nationwide_catalogue",
        "availability_note": "Only plans verified for this city/area or PIN code are returned." if regional else
        "Nationwide catalogue can include plans whose local availability is unverified.",
        "filters": {"city": location, "place": location, "pincode": pincode, "max_price": max_price}, "plans": plans}

@app.get("/offers")
def list_offers(city: str | None = Query(default=None), place: str | None = Query(default=None),
                pincode: str | None = Query(default=None)):
    location = resolve_place(city, place)
    pincode = pincode.strip() if pincode else None
    if pincode and (not pincode.isdigit() or len(pincode) != 6):
        raise HTTPException(400, "PIN code must contain exactly 6 digits.")
    sql = "SELECT * FROM plans WHERE offer IS NOT NULL AND TRIM(offer)!=''"
    args = []
    if location:
        sql += " AND availability_verified=1 AND LOWER(TRIM(city))=LOWER(TRIM(?))"
        args.append(location)
    if pincode:
        if not location: sql += " AND availability_verified=1"
        sql += " AND TRIM(pincode)=?"
        args.append(pincode)
    sql += " ORDER BY price ASC"
    with get_connection() as con:
        offers = [dict(r) for r in con.execute(sql, args).fetchall()]
    return {"count": len(offers), "city": location, "place": location, "pincode": pincode,
        "catalogue_mode": "verified_regional_results" if location or pincode else "nationwide_catalogue",
        "offers": offers}

@app.get("/regions")
def list_regions():
    with get_connection() as con:
        places = con.execute("SELECT DISTINCT city FROM plans WHERE availability_verified=1 AND city IS NOT NULL AND TRIM(city)!='' ORDER BY city").fetchall()
        pins = con.execute("SELECT DISTINCT pincode FROM plans WHERE availability_verified=1 AND pincode IS NOT NULL AND TRIM(pincode)!='' ORDER BY pincode").fetchall()
        total = con.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        verified = con.execute("SELECT COUNT(*) FROM plans WHERE availability_verified=1").fetchone()[0]
    names = [r["city"] for r in places]
    return {"total_plans": total, "availability_verified_plans": verified, "places": names,
        "cities": names, "pincodes": [r["pincode"] for r in pins]}

@app.post("/scrape")
def scrape_plans():
    try:
        return {"message": "Scraping process completed.", "processed_count": run_scraper()}
    except Exception as error:
        raise HTTPException(500, "The scraping process failed.") from error
