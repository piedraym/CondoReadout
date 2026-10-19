"""Web API: search condos and get their report as HTML or PDF.

Run it from backend/ with:  ../.venv/bin/uvicorn api:app --reload
and open http://127.0.0.1:8000/docs to try every endpoint in the browser.

On the server it also serves the web pages (frontend/dist) and asks for the shared password
in APP_PASSWORD; with no APP_PASSWORD (on your Mac) it asks for none.
"""

import base64
import os
import re
import secrets
import time
from pathlib import Path

from applog import get_logger
from database import connect
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from report import HERE, render_html
from report_db import build_report
from search_db import MAX_RESULTS, search
from weasyprint import HTML

log = get_logger("api")

PASSWORD = os.environ.get("APP_PASSWORD")  # None: no password, as on your Mac
OPEN_PATHS = {"/health"}  # the server's health check calls it without a password
FRONTEND = (
    Path(__file__).parent.parent / "frontend" / "dist"
)  # built by `npm run build`


app = FastAPI(
    title="CondoReadout",
    description="Recertification and risk report for Miami-Dade condos.",
)


def password_ok(header):
    """True when the request carries the shared password (any user name is accepted)."""
    if not header or not header.startswith("Basic "):
        return False
    try:
        _, _, given = base64.b64decode(header[6:]).decode().partition(":")
    except ValueError:  # not valid base64 or not text
        return False
    return secrets.compare_digest(given.encode(), PASSWORD.encode())


@app.middleware("http")
async def require_password(request, call_next):
    """Ask for the shared password with the browser's own login box, unless APP_PASSWORD is unset."""
    if PASSWORD and request.url.path not in OPEN_PATHS:
        if not password_ok(request.headers.get("authorization")):
            return Response(
                "Password required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="CondoReadout"'},
            )
    return await call_next(request)


def clean_folio(folio):
    """'01-3231-048-0001' or '0132310480001' -> '0132310480001'; 404 when it is not a folio."""
    digits = re.sub(r"\D", "", folio)
    if len(digits) != 13:
        raise HTTPException(404, f"{folio} is not a folio")
    return digits


def building_or_404(folio):
    """Everything the report needs, or 404 if the condo is not in the database."""
    building = build_report(folio)
    if not building:
        raise HTTPException(404, f"{folio} is not in the database")
    return building


@app.get("/health")
def health():
    """Whether the database answers, and the last good load of each source."""
    with connect() as db:
        loads = db.execute(
            "SELECT DISTINCT ON (source, jurisdiction_code) source, jurisdiction_code,"
            " finished_at, data_date FROM ingest_run WHERE status = 'ok'"
            " ORDER BY source, jurisdiction_code, id DESC"
        ).fetchall()
    return {
        "database": "ok",
        "loads": [
            {"source": s, "jurisdiction": j, "finished_at": f, "data_date": d}
            for s, j, f, d in loads
        ],
    }


@app.get("/search")
def search_condos(
    q: str = Query(..., min_length=1, description="Address or name, as typed"),
    limit: int = Query(MAX_RESULTS, ge=1, le=50),
):
    """Condos whose address or name matches what was typed, best first."""
    return search(q, limit)


@app.get("/report/{folio}", response_class=HTMLResponse)
def report_html(folio: str):
    """The report of one condo as a web page."""
    folio = clean_folio(folio)
    return render_html(building_or_404(folio))


@app.get("/report/{folio}/pdf")
def report_pdf(folio: str):
    """The report of one condo as a PDF file."""
    started = time.monotonic()
    folio = clean_folio(folio)
    html = render_html(building_or_404(folio))
    pdf = HTML(string=html, base_url=str(HERE)).write_pdf()
    log.info("PDF report %s in %.1fs", folio, time.monotonic() - started)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="report_{folio}.pdf"'},
    )


# The web pages, at the end so that the routes above come first. While developing, Vite serves
# them instead (npm run dev), so this only happens after `npm run build`.
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
