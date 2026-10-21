
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from viasat_scraper import lookup_viasat

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class ViasatAddressRequest(BaseModel):
    street_address: str = Field(min_length=4, max_length=250)
    unit: str = Field(default="", max_length=80)
    zipcode: str = Field(min_length=5, max_length=10)


@router.get("/viasat", response_class=HTMLResponse)
def viasat_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="viasat.html",
        context={"title": "Viasat Address-Based Plan Lookup"},
    )


@router.post("/api/viasat/check")
def check_viasat_address(payload: ViasatAddressRequest):
    try:
        return lookup_viasat(
            payload.street_address,
            payload.zipcode,
            payload.unit,
        )

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except RuntimeError as exc:
        logger.exception("Viasat lookup runtime failure")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    except Exception as exc:
        logger.exception("Unexpected Viasat lookup failure")
        raise HTTPException(
            status_code=502,
            detail=f"Viasat lookup failed: {type(exc).__name__}: {exc}",
        ) from exc
