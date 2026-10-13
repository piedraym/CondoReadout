"""Load the DBPR projects, their match to condos and the SIRS reports into PostgreSQL.

Reads data/Condo_MD.csv and the CSVs that match_condos.py and match_sirs.py write in out/,
so run those two first.
"""

import csv
from datetime import datetime
from pathlib import Path

from applog import get_logger
from database import connect, ingest_run

DATA = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"

log = get_logger("load_dbpr")


def read_csv(path, encoding="utf-8"):
    with path.open(encoding=encoding) as f:
        return list(csv.DictReader(f))


def us_date(text):
    """'10/02/1981' -> date; None when empty."""
    return datetime.strptime(text, "%m/%d/%Y").date() if text else None


def load():
    """Replace the three DBPR tables with what the files have now."""
    projects = [
        (
            r["Project Number"],
            r["File Number"] or None,
            r["Condo Name"],
            r["County"],
            r["Street City State Zip"],
            int(r["Units"]) if r["Units"] else None,
            us_date(r["Recorded Date"]),
            r["Primary Status"] or None,
            r["Secondary Status"] or None,
        )
        for r in read_csv(DATA / "Condo_MD.csv", encoding="latin-1")
    ]
    matches = [
        (r["parent_folio"], r["project_number"], r["method"], False)
        for r in read_csv(OUT / "condo_match.csv")
        if r["project_number"]  # condos without a project have an empty row
    ]
    review = OUT / "condo_review.csv"
    if review.exists():
        matches += [
            (r["parent_folio"], r["Project Number"], "name, other city", True)
            for r in read_csv(review)
        ]
    reports = [
        (
            r["period"],
            r["project_name"] or None,
            r["association_name"] or None,
            r["zip"] or None,
            r["project_number"] or None,
            r["method"] or None,
        )
        for r in read_csv(OUT / "sirs_reports.csv")
    ]

    with connect() as db, ingest_run(db, "dbpr", None) as run:
        with db.cursor() as cur:
            cur.execute("DELETE FROM sirs_report")
            cur.execute("DELETE FROM condo_dbpr")
            cur.execute("DELETE FROM dbpr_project")
            cur.executemany(
                "INSERT INTO dbpr_project VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", projects
            )
            cur.executemany("INSERT INTO condo_dbpr VALUES (%s, %s, %s, %s)", matches)
            cur.executemany(
                "INSERT INTO sirs_report (period, project_name, association_name, zip,"
                " project_number, method) VALUES (%s, %s, %s, %s, %s, %s)",
                reports,
            )
        run.rows = len(projects) + len(matches) + len(reports)
        log.info(
            "%s projects, %s matches to condos (%s to review), %s SIRS reports",
            len(projects), len(matches), sum(m[3] for m in matches), len(reports),
        )


if __name__ == "__main__":
    load()
