"""Load the building permits of one jurisdiction into PostgreSQL."""

import sys
from datetime import date, datetime

from applog import get_logger
from core import LAYERS, ms_to_date, query
from database import connect, ingest_run
from psycopg.types.json import Jsonb

log = get_logger("load_permits")

# ProjectName and Banner are never requested: they can carry names of people
MIAMI_FIELDS = [
    "PermitNumber",
    "FOLIO",
    "FULLADDR",
    "PermitType",
    "ScopeOfWork",
    "PermitStatus",
    "PermitIssuedDate",
    "ApplicationNumber",
    "ApplicationType",
]
MIAMI_EXTRA = ["ApplicationNumber", "ApplicationType"]


def miami_permits():
    """Permits of the City of Miami iBuild layer, as rows of permit (without the jurisdiction)."""
    permits = {}
    # rows without a number are applications that never became a permit
    for f in query(LAYERS["ibuild_permits"], "PermitNumber IS NOT NULL", MIAMI_FIELDS):
        p = f["attributes"]
        # the layer has exact duplicate rows: the last one stays
        permits[p["PermitNumber"]] = (
            p["PermitNumber"],
            p["FOLIO"],
            p["FULLADDR"],
            p["PermitType"],
            p["ScopeOfWork"],
            p["PermitStatus"],
            ms_to_date(p["PermitIssuedDate"]),
            Jsonb({key: p[key] for key in MIAMI_EXTRA if p[key] is not None}),
        )
    return permits.values()


# Unincorporated Miami-Dade: the County's permit layer. It only holds permits issued in about the
# last two years and has no status field. OwnerName and the contractor and architect fields are
# never requested.
COUNTY_PERMITS = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/miamidade_permit_data/FeatureServer/0"
COUNTY_FIELDS = [
    "PermitNumber",
    "FolioNumber",
    "PropertyAddress",
    "PermitType",
    "ApplicationTypeDescription",
    "PermitIssuedDate",
    "CoCcDate",
    "LastApprovedInspDate",
    "EstimatedValue",
    "ProcessNumber",
]
COUNTY_TYPES = {  # PermitType code -> words for the report
    "BLDG": "Building",
    "ELEC": "Electrical",
    "PLUM": "Plumbing",
    "MECH": "Mechanical",
    "FIRE": "Fire",
    "LPGX": "Gas",
    "MBLD": "Mobile home",
    "ZIPS": "Zoning",
    "CCUT": "Curb cut",
}


def county_permits():
    """Permits of the County layer for code 30, as rows of permit (without the jurisdiction)."""
    for f in query(COUNTY_PERMITS, "FolioNumber LIKE '30%'", COUNTY_FIELDS):
        p = f["attributes"]
        kind = COUNTY_TYPES.get(p["PermitType"], p["PermitType"] or "Permit")
        work = (p["ApplicationTypeDescription"] or "").strip()
        completed = p["CoCcDate"]  # certificate of occupancy or completion, '05/28/2026'
        yield (
            p["PermitNumber"],
            p["FolioNumber"],
            p["PropertyAddress"],
            f"{kind} · {work}" if work else kind,  # 'Building · RE-ROOF/REPAIR'
            work,
            "Completed" if completed else "Issued",  # the layer does not say whether a permit is still open
            date.fromisoformat(p["PermitIssuedDate"]) if p["PermitIssuedDate"] else None,
            Jsonb(
                {
                    "EstimatedValue": p["EstimatedValue"],
                    "CoCcDate": datetime.strptime(completed, "%m/%d/%Y").date().isoformat() if completed else None,
                    "LastApprovedInspDate": ms_to_date(p["LastApprovedInspDate"]),
                    "ProcessNumber": p["ProcessNumber"],
                }
            ),
        )


# Each jurisdiction publishes its permits in its own way: one function per code
SOURCES = {"01": miami_permits, "30": county_permits}


def load(code):
    """Replace the permits of the jurisdiction with what its source has now."""
    if code not in SOURCES:
        log.error("%s has no permit source yet", code)
        raise SystemExit(1)
    with connect() as db, ingest_run(db, "permits", code) as run:
        rows = [(code, *permit) for permit in SOURCES[code]()]
        with db.cursor() as cur:
            cur.execute("DELETE FROM permit WHERE jurisdiction_code = %s", [code])
            cur.executemany(
                "INSERT INTO permit VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows
            )
        run.rows = len(rows)


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "01")
