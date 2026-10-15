"""Web API: search condos and get their report as HTML or PDF.

Run it from backend/ with:  ../.venv/bin/uvicorn api:app --reload
and open http://127.0.0.1:8000/docs to try every endpoint in the browser.
"""

import re
import time

from applog import get_logger
from database import connect
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from report import HERE, render_html
from report_db import build_report
from search_db import MAX_RESULTS, search
from weasyprint import HTML

log = get_logger("api")

app = FastAPI(
    title="CondoReadout",
    description="Recertification and risk report for condos in the City of Miami.",
)


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
