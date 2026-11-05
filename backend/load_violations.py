"""Load the code and building violation cases of one jurisdiction into PostgreSQL."""

import sys
from pathlib import Path

import openpyxl
from applog import get_logger
from core import ms_to_date, query
from database import connect, ingest_run
from psycopg.types.json import Jsonb

log = get_logger("load_violations")

# Unincorporated Miami-Dade: building cases (Building Department) and code compliance cases.
# VIOL_NAME, the violator's name, is never requested.
COUNTY = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services"
BUILDING_CASES = f"{COUNTY}/BuildingViolation_gdb/FeatureServer/0"
BUILDING_LIENS = f"{COUNTY}/BuildingViolationLien/FeatureServer/0"
CODE_CASES = f"{COUNTY}/CCVIOL_gdb/FeatureServer/0"


def county_violations():
    """Building and code cases of code 30, as rows of violation (without the jurisdiction)."""
    liens = {
        f["attributes"]["CASE_NUM"]: f["attributes"]
        for f in query(BUILDING_LIENS, "1=1", ["CASE_NUM", "LIEN_BOOK", "LIEN_PAGE", "LIEN_RECDT"])
    }
    for f in query(
        BUILDING_CASES,
        "FOLIO LIKE '30%'",
        ["CASE_NUM", "CASE_TYPE", "FOLIO", "PROP_ADDR", "PERMIT_NUM", "OPEN_DATE", "CLOSED_DATE", "PROTECTED"],
    ):
        v = f["attributes"]
        lien = liens.get(v["CASE_NUM"])
        yield (
            "building",
            v["CASE_NUM"],
            v["CASE_TYPE"],
            v["FOLIO"],
            " ".join((v["PROP_ADDR"] or "").split()),  # '6960  MIAMI GARDENS DR' -> one space
            "Closed" if v["CLOSED_DATE"] else "Open",
            ms_to_date(v["OPEN_DATE"]),
            ms_to_date(v["CLOSED_DATE"]),
            v["PERMIT_NUM"] or None,
            Jsonb(
                {
                    "protected": v["PROTECTED"],  # 'N' or 'I': meaning to confirm
                    "lien_recorded": ms_to_date(lien["LIEN_RECDT"]) if lien else None,
                    "lien_book_page": f"{lien['LIEN_BOOK']}/{lien['LIEN_PAGE']}" if lien else None,
                }
            ),
        )
    seen = set()  # a code case has one row per problem; the same problem can repeat
    for f in query(
        CODE_CASES,
        "FOLIO LIKE '30%'",
        ["CASE_NUM", "CASE_DATE", "STAT_DESC", "FOLIO", "ADDRESS", "PROBLEM_DESC", "LAST_ACTV", "LN_RECDATE"],
    ):
        v = {key: value.strip() if isinstance(value, str) else value for key, value in f["attributes"].items()}
        key = (v["CASE_NUM"], v["PROBLEM_DESC"] or "")
        if key in seen:
            continue
        seen.add(key)
        yield (
            "code",
            v["CASE_NUM"],
            v["PROBLEM_DESC"] or "",
            v["FOLIO"],  # the layer pads it with spaces: stripped above
            v["ADDRESS"],
            v["STAT_DESC"],  # 'Open', 'Closed', 'Lien', 'Referred to Internal Compliance'...
            ms_to_date(v["CASE_DATE"]),
            None,  # the layer has no closing date
            None,
            Jsonb({"last_action": v["LAST_ACTV"], "lien_recorded": v["LN_RECDATE"] or None}),
        )


# Miami Beach: every code case of the Building Department since 2014, from a public records request
# (PRR-2026-3800) saved in data/miamibeach_prr/. Each case carries the folio of the condo or of a unit.
# The Description column is free text that can name people: it is never read.
MIAMI_BEACH_CASES = Path(__file__).parent / "data" / "miamibeach_prr" / "ViolationHistoryEnergovSummaryRpt.xlsx"
MIAMI_BEACH_CLOSED = {"Closed", "Void", "Canceled"}
MIAMI_BEACH_CASE_TYPES = {"Unsafe Structures": "Unsafe Structure"}  # theirs -> ours; the rest keep their name


def miami_beach_violations():
    """Miami Beach code cases, as rows of violation (without the jurisdiction)."""
    rows = openpyxl.load_workbook(MIAMI_BEACH_CASES, read_only=True).active.iter_rows(values_only=True)
    for header in rows:  # a title and a total come first, with empty rows between
        if header and "Parcel" in header:
            break
    column = {name: i for i, name in enumerate(header) if name}
    for row in rows:
        case = {name: row[i] for name, i in column.items()} if row else {}
        if not case.get("Case Number"):
            continue  # the empty rows between cases
        opened, closed = (case[key] and case[key].date() for key in ("Opened Date", "Closed Date"))
        yield (
            "building",
            case["Case Number"],
            MIAMI_BEACH_CASE_TYPES.get(case["Case Type"], case["Case Type"].replace("–", "-")),
            str(case["Parcel"] or "").strip() or None,  # the condo's folio or a unit's
            case["Address"],
            "Closed" if closed or case["Status"] in MIAMI_BEACH_CLOSED else "Open",
            opened,
            closed,
            None,
            Jsonb({"case_status": case["Status"]}),  # e.g. 'Special Magistrate', 'Fine Owed'
        )


# Each jurisdiction publishes its cases in its own way: one function per code.
# The City of Miami does not publish them, so it has no entry.
SOURCES = {"30": county_violations, "02": miami_beach_violations}


def load(code):
    """Replace the violation cases of the jurisdiction with what its sources have now."""
    if code not in SOURCES:
        log.error("%s has no violation source", code)
        raise SystemExit(1)
    with connect() as db, ingest_run(db, "violations", code) as run:
        rows = [(code, *case) for case in SOURCES[code]()]
        if code == "02":  # a one-time export: its newest case date tells how old it is
            run.data_date = max(day for row in rows for day in (row[7], row[8]) if day)
        with db.cursor() as cur:
            cur.execute("DELETE FROM violation WHERE jurisdiction_code = %s", [code])
            cur.executemany("INSERT INTO violation VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
        run.rows = len(rows)


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "30")
