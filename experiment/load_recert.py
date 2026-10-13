"""Load the recertification records of one jurisdiction into PostgreSQL."""

import sys

from applog import get_logger
from database import connect, ingest_run
from psycopg.types.json import Jsonb
from step0 import LAYERS, RECERT_FIELDS, ms_to_date, norm_address, query

log = get_logger("load_recert")

# City of Miami fields that have no column of their own
MIAMI_EXTRA = [
    "BuildingNumber",
    "RecertificationProcessStatus",
    "PlanStatusReason",
    "RequestType",
    "RequestStatus",
    "RequestResult",
]


def miami_records():
    """Records of the City of Miami layer, as rows of recert_record (without the jurisdiction)."""
    for f in query(LAYERS["recert"], "1=1", RECERT_FIELDS):
        r = f["attributes"]
        yield (
            str(r["OBJECTID"]),
            r["FolioNumber"],
            r["Address"],
            norm_address(r["Address"]),
            r["YearBuilt"] or None,
            r["RecertificateYear"],
            r["CertificationStatus"],
            r["PlanNumber"],
            r["PlanStatus"],
            ms_to_date(r["PlanStatusDate"]),
            ms_to_date(r["SubmittedDate"]),
            ms_to_date(r["UpdatedDate"]),
            Jsonb({key: r[key] for key in MIAMI_EXTRA if r[key] is not None}),
        )


# Each jurisdiction publishes its records in its own way: one function per code
SOURCES = {"01": miami_records}


def load(code):
    """Replace the recertification records of the jurisdiction with what its source has now."""
    if code not in SOURCES:
        log.error("%s has no recertification source yet", code)
        raise SystemExit(1)
    with connect() as db, ingest_run(db, "recert", code) as run:
        rows = [(code, *record) for record in SOURCES[code]()]
        with db.cursor() as cur:
            cur.execute("DELETE FROM recert_record WHERE jurisdiction_code = %s", [code])
            cur.executemany(
                "INSERT INTO recert_record VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
        run.rows = len(rows)


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "01")
