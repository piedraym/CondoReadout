"""Precompute what the Stats page shows: the traffic light of every condo (condo_status) and the
rows of each query of stats.py (stat). nightly.py runs it after the loads; it takes a few minutes."""

from applog import get_logger
from core import LIGHT_ORDER
from database import connect, ingest_run
from psycopg.types.json import Jsonb
from report_db import build_report
from stats import QUERIES

log = get_logger("build_stats")


def condo_statuses(db):
    """One row of condo_status per condo, from the same data as its report."""
    parents = [parent for (parent,) in db.execute("SELECT parent_folio FROM condo ORDER BY parent_folio")]
    for i, parent in enumerate(parents, 1):
        building = build_report(parent)
        worst = max(building["recert_by_tower"].values(), key=lambda t: LIGHT_ORDER.index(t["light"]))
        due = [t["next_due"]["year"] for t in building["recert_by_tower"].values() if t.get("next_due")]
        yield (
            parent,
            parent[:2],
            building["recert_light"],
            worst["label"],
            bool(building["dbpr"]["sirs_reported"]),
            building["dbpr"]["delinquent"],
            len((building.get("violations") or {}).get("open") or []),
            min(due) if due else None,  # the tower that is due first
        )
        if i % 500 == 0:
            log.info("%s of %s condos done", i, len(parents))


def load():
    """Replace condo_status and stat with what the database has now."""
    with connect() as db, ingest_run(db, "stats", None) as run:
        statuses = list(condo_statuses(db))
        with db.cursor() as cur:
            cur.execute("DELETE FROM condo_status")
            cur.executemany("INSERT INTO condo_status VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", statuses)
            cur.execute("DELETE FROM stat")
            for name, sql in QUERIES.items():
                cur.execute(sql)
                columns = [c.name for c in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]
                cur.execute("INSERT INTO stat (name, rows) VALUES (%s, %s)", [name, Jsonb(rows)])
        run.rows = len(statuses) + len(QUERIES)
        log.info("%s condos, %s queries", len(statuses), len(QUERIES))


if __name__ == "__main__":
    load()
