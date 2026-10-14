"""Connection to PostgreSQL and the bookkeeping every loader shares."""

import os
import time
from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
from applog import get_logger

# another database can be chosen without touching the code: INFOREALTOR_DB="dbname=other"
DB = os.environ.get("INFOREALTOR_DB", "dbname=inforealtor")

log = get_logger("database")


def connect():
    return psycopg.connect(DB)


@contextmanager
def ingest_run(db, source, code):
    """Record one load in ingest_run: 'ok' with its row count, or 'failed' with the error.

    Use it as `with ingest_run(db, "recert", code) as run:` and set run.rows inside.
    If the block fails, its changes to the database are undone and the error goes on.
    """
    known = db.execute("SELECT 1 FROM jurisdiction WHERE code = %s", [code]).fetchone()
    if code is not None and not known:
        log.error("%s is not in the jurisdiction table", code)
        raise SystemExit(1)
    started = time.monotonic()
    run = SimpleNamespace(
        rows=None, data_date=None
    )  # data_date: only when it is not today

    run.id = db.execute(
        "INSERT INTO ingest_run (source, jurisdiction_code) VALUES (%s, %s) RETURNING id",
        [source, code],
    ).fetchone()[0]
    db.commit()  # the run stays recorded even if the load fails
    code = code or "all"  # only for the messages below
    log.info("%s %s: started (run %s)", source, code, run.id)
    try:
        yield run
    except Exception as error:
        db.rollback()
        db.execute(
            "UPDATE ingest_run SET finished_at = now(), status = 'failed', error = %s WHERE id = %s",
            [str(error), run.id],
        )
        db.commit()
        log.exception(
            "%s %s: failed, nothing was changed (run %s)", source, code, run.id
        )
        raise
    db.execute(
        "UPDATE ingest_run SET finished_at = now(), status = 'ok', row_count = %s,"
        " data_date = COALESCE(%s, data_date) WHERE id = %s",
        [run.rows, run.data_date, run.id],
    )
    db.commit()
    log.info(
        "%s %s: saved %s rows in %.0fs (run %s)",
        source,
        code,
        run.rows,
        time.monotonic() - started,
        run.id,
    )
