"""Load the building permits of one jurisdiction into PostgreSQL."""

import re
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl
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


# Unincorporated Miami-Dade: the County's permit layer. It holds permits issued since 2020 and has
# no status field. OwnerName and the contractor and architect fields are
# never requested.
COUNTY_PERMITS = (
    "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/miamidade_permit_data/FeatureServer/0"
)
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


def completion_date(text):
    """'05/28/2026' -> '2026-05-28'; None when empty or not a date (a few permits have a number there)."""
    try:
        return datetime.strptime(text, "%m/%d/%Y").date().isoformat() if text else None
    except ValueError:
        return None


def county_permits():
    """Permits of the County layer for code 30, as rows of permit (without the jurisdiction)."""
    permits = {}
    for f in query(COUNTY_PERMITS, "FolioNumber LIKE '30%'", COUNTY_FIELDS):
        p = f["attributes"]
        kind = COUNTY_TYPES.get(p["PermitType"], p["PermitType"] or "Permit")
        work = (p["ApplicationTypeDescription"] or "").strip()
        completed = p["CoCcDate"]  # certificate of occupancy or completion, '05/28/2026'
        # a permit can come twice if the County refreshes the layer while we read it: the last one stays
        permits[p["PermitNumber"]] = (
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
                    "CoCcDate": completion_date(completed),
                    "LastApprovedInspDate": ms_to_date(p["LastApprovedInspDate"]),
                    "ProcessNumber": p["ProcessNumber"],
                }
            ),
        )
    return permits.values()


# Miami Beach: the Building Department's permits since 2014 (Energov), from a public records request
# (PRR-2026-3800) saved in data/miamibeach_prr/. The export also holds parking rentals, garbage, events
# and records requests: only issued permits of the building trades are kept. Description is free text
# that can name people: it is never read.
MIAMI_BEACH_PERMITS = Path(__file__).parent / "data" / "miamibeach_prr" / "PermitHistoryEnergov.xlsx"
MIAMI_BEACH_SINCE = date(2014, 1, 1)  # the few older dates in the export are typos
MIAMI_BEACH_TRADES = re.compile(
    r"(AC|Building|Electrical|Elevator|Generator|Mechanical|Photovoltaic|Plumbing|Pool|Roofing)"
    r"( Sub-Permit .*| [-–] (Commercial|Residential|Condominium|Phased))|Fire"
)
MIAMI_BEACH_STATUS = {  # theirs -> the City of Miami's words, which the report reads; the rest keep theirs
    "Finaled": "Final",
    "Issued": "Active",
    "Applied": "Pending",
    "Initial": "Pending",
    "Pending": "Pending",
    "Suspended": "Hold",
    "Void": "Cancelled",
    "Cancel": "Cancelled",
    "Abandon": "Cancelled",
}


def miami_beach_permits():
    """Issued building permits of Miami Beach, as rows of permit (without the jurisdiction)."""
    rows = openpyxl.load_workbook(MIAMI_BEACH_PERMITS, read_only=True).active.iter_rows(values_only=True)
    for header in rows:  # a title and a total come first, with empty rows between
        if header and "Parcel" in header:
            break
    column = {name: i for i, name in enumerate(header) if name}
    for row in rows:
        permit = {name: row[i] for name, i in column.items()} if row else {}
        issued = permit.get("Issue Date") and permit["Issue Date"].date()
        if not issued or not MIAMI_BEACH_SINCE <= issued <= date.today():
            continue  # never issued (an application), or a date typed wrong
        if not MIAMI_BEACH_TRADES.fullmatch(permit["Permit Type"] or ""):
            continue  # parking, garbage, events, records requests...
        yield (
            permit["Permit Number"],
            str(permit["Parcel"] or "").strip() or None,  # the condo's folio or a unit's
            permit["Address"],
            permit["Permit Type"].replace("–", "-"),  # 'Roofing - Commercial'
            permit["Work Class"],  # 'Repair', 'New'...
            MIAMI_BEACH_STATUS.get(permit["Permit Status"], permit["Permit Status"]),
            issued,
            Jsonb(
                {
                    "CityStatus": permit["Permit Status"],
                    "ApplyDate": permit["Apply Date"] and permit["Apply Date"].date().isoformat(),
                    "LastInspectionDate": permit["Last Inspection Date"]
                    and permit["Last Inspection Date"].date().isoformat(),
                    "Valuation": permit["Valuation"],
                }
            ),
        )


# Each jurisdiction publishes its permits in its own way: one function per code
SOURCES = {"01": miami_permits, "30": county_permits, "02": miami_beach_permits}


def load(code):
    """Replace the permits of the jurisdiction with what its source has now."""
    if code not in SOURCES:
        log.error("%s has no permit source yet", code)
        raise SystemExit(1)
    with connect() as db, ingest_run(db, "permits", code) as run:
        rows = [(code, *permit) for permit in SOURCES[code]()]
        if code == "02":  # a one-time export: its newest permit tells how old it is
            run.data_date = max(row[7] for row in rows)
        with db.cursor() as cur:
            cur.execute("DELETE FROM permit WHERE jurisdiction_code = %s", [code])
            cur.executemany("INSERT INTO permit VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
        run.rows = len(rows)


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "01")
