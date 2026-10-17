"""Look up the flood zone and the distance to the coast of each condo and save them in PostgreSQL."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from applog import get_logger
from core import coast_distance, flood_zone
from database import connect

WORKERS = 8  # condos looked up at the same time
BATCH = 100  # condos saved per commit, so an interrupted run keeps its progress

log = get_logger("load_location")

PENDING = """
    SELECT c.parent_folio, c.lon, c.lat
    FROM condo c
    LEFT JOIN condo_location l USING (parent_folio)
    WHERE c.jurisdiction_code = %s
      AND c.lon IS NOT NULL
      AND (l.parent_folio IS NULL OR l.lon <> c.lon OR l.lat <> c.lat)
    ORDER BY c.parent_folio
"""

SAVE = """
    INSERT INTO condo_location
        (parent_folio, lon, lat, flood_zone, flood_subtype, flood_bfe, coast_within_miles)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (parent_folio) DO UPDATE SET
        lon = EXCLUDED.lon,
        lat = EXCLUDED.lat,
        flood_zone = EXCLUDED.flood_zone,
        flood_subtype = EXCLUDED.flood_subtype,
        flood_bfe = EXCLUDED.flood_bfe,
        coast_within_miles = EXCLUDED.coast_within_miles,
        checked_at = now()
"""


def look_up(condo):
    """Row for condo_location, or None if the servers did not answer."""
    parent, lon, lat = condo
    point = {"x": lon, "y": lat}
    try:
        flood = flood_zone(point) or {}
        coast = coast_distance(point)
    except (RuntimeError, requests.RequestException) as error:
        log.warning("%s: no location data (%s)", parent, error)
        return None
    return (
        parent,
        lon,
        lat,
        flood.get("zone"),
        flood.get("subtype"),
        flood.get("base_flood_elevation_ft"),
        coast["within_miles"],
    )


def load(code):
    """Look up the condos of the jurisdiction that have no location data, or that moved."""
    started = time.monotonic()
    with connect() as db:
        pending = db.execute(PENDING, [code]).fetchall()
        if not pending:
            log.info(
                "%s: every condo already has its flood zone and coast distance", code
            )
            return
        log.info(
            "%s: looking up flood zone and coast distance of %s condos",
            code,
            len(pending),
        )
        run = db.execute(
            "INSERT INTO ingest_run (source, jurisdiction_code) VALUES ('location', %s) RETURNING id",
            [code],
        ).fetchone()[0]
        db.commit()

        saved = failed = 0
        with ThreadPoolExecutor(WORKERS) as pool:
            for start in range(0, len(pending), BATCH):
                rows = list(pool.map(look_up, pending[start : start + BATCH]))
                found = [r for r in rows if r]
                with db.cursor() as cur:
                    cur.executemany(SAVE, found)
                db.commit()
                saved += len(found)
                failed += len(rows) - len(found)
                log.info("%s: %s of %s condos done", code, saved + failed, len(pending))

        db.execute(
            "UPDATE ingest_run SET finished_at = now(), status = %s, row_count = %s, error = %s WHERE id = %s",
            [
                "failed" if failed else "ok",
                saved,
                f"{failed} condos without an answer: run it again" if failed else None,
                run,
            ],
        )
    log.info(
        "%s: saved %s condos, %s failed, in %.0fs (run %s)",
        code,
        saved,
        failed,
        time.monotonic() - started,
        run,
    )


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "01")
