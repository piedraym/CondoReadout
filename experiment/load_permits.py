"""Load the building permits of one jurisdiction into PostgreSQL."""

import sys

from applog import get_logger
from database import connect, ingest_run
from psycopg.types.json import Jsonb
from step0 import LAYERS, ms_to_date, query

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


# Each jurisdiction publishes its permits in its own way: one function per code
SOURCES = {"01": miami_permits}


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
