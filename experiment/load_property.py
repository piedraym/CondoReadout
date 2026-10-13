"""Load the condos of one jurisdiction from the Property Appraiser layer into PostgreSQL."""

import sys
from collections import defaultdict

from applog import get_logger
from database import connect, ingest_run
from psycopg.types.json import Jsonb
from step0 import (
    LAYERS,
    PROPERTY_FIELDS,
    base_address,
    query,
    summarize_units,
    unit_type,
)

CHUNK = 100  # parent folios per request

log = get_logger("load_property")


def fetch(code):
    """Units by parent folio and the master of each parent that has one, as ArcGIS features."""
    units = defaultdict(list)
    for f in query(
        LAYERS["property"],
        f"FOLIO LIKE '{code}%' AND PARENT_FOLIO IS NOT NULL",
        PROPERTY_FIELDS,
        geometry=True,
    ):
        if f["attributes"]["FOLIO"] != f["attributes"]["PARENT_FOLIO"]:
            units[f["attributes"]["PARENT_FOLIO"]].append(f)

    masters = {}
    parents = sorted(units)
    for start in range(0, len(parents), CHUNK):
        folios = ",".join(f"'{p}'" for p in parents[start : start + CHUNK])
        for f in query(
            LAYERS["property"], f"FOLIO IN ({folios})", ["FOLIO", "LEGAL"], geometry=True
        ):
            masters[f["attributes"]["FOLIO"]] = f
    return units, masters


def load(code):
    """Replace the condos, towers and units of the jurisdiction with what the layer has now."""
    with connect() as db, ingest_run(db, "property", code) as run:
        units, masters = fetch(code)
        log.info("%s: downloaded %s condos, %s with a master folio", code, len(units), len(masters))
        condos, towers, unit_rows = [], [], []
        for parent, features in units.items():
            rows = [f["attributes"] for f in features]
            master = masters.get(parent)
            # location: the master folio, else the first unit
            point = (master or features[0]).get("geometry") or {}
            b = summarize_units(parent, master and master["attributes"], rows)
            condos.append(
                (
                    parent,
                    code,
                    b["condo_name"],
                    b["name"],
                    b["zip"],
                    b["units"],
                    Jsonb(b["unit_types"]),
                    b["estimate_floors"],
                    b["year_of_construction"],
                    b["unbuilt"],
                    b["master_in_pa"],
                    point.get("x"),
                    point.get("y"),
                )
            )
            for address, count in b["towers"].items():
                towers.append((parent, address, count, b["tower_years"].get(address)))
            for r in rows:
                unit_rows.append(
                    (
                        r["FOLIO"],
                        parent,
                        base_address(r),
                        (r["TRUE_SITE_UNIT"] or "").strip() or None,
                        r["DOR_DESC"],
                        unit_type(r["DOR_DESC"]),
                        r["YEAR_BUILT"] or None,  # 0 = unknown
                    )
                )

        with db.cursor() as cur:
            # towers and units go with their condo (ON DELETE CASCADE)
            cur.execute("DELETE FROM condo WHERE jurisdiction_code = %s", [code])
            cur.executemany(
                "INSERT INTO condo (parent_folio, jurisdiction_code, legal_name, name, zip, units,"
                " unit_types, estimate_floors, year_built, unbuilt, master_in_pa, lon, lat)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                condos,
            )
            cur.executemany("INSERT INTO tower VALUES (%s, %s, %s, %s)", towers)
            cur.executemany("INSERT INTO unit VALUES (%s, %s, %s, %s, %s, %s, %s)", unit_rows)
            cur.execute("UPDATE jurisdiction SET active = true WHERE code = %s", [code])
        run.rows = len(unit_rows)
        log.info("%s: %s condos, %s towers, %s units", code, len(condos), len(towers), len(unit_rows))


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "01")
