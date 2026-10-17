"""Load the code and building violation cases of one jurisdiction into PostgreSQL."""

import sys

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


# Each jurisdiction publishes its cases in its own way: one function per code.
# The City of Miami does not publish them, so it has no entry.
SOURCES = {"30": county_violations}


def load(code):
    """Replace the violation cases of the jurisdiction with what its sources have now."""
    if code not in SOURCES:
        log.error("%s has no violation source", code)
        raise SystemExit(1)
    with connect() as db, ingest_run(db, "violations", code) as run:
        rows = [(code, *case) for case in SOURCES[code]()]
        with db.cursor() as cur:
            cur.execute("DELETE FROM violation WHERE jurisdiction_code = %s", [code])
            cur.executemany(
                "INSERT INTO violation VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", rows
            )
        run.rows = len(rows)


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "30")
