"""Everything the report needs for one condo, read from PostgreSQL instead of the live layers.

build_report returns the same dictionary as core.build_report, so report.py and the
template work with either. The rules (labels, lights, due dates, questions) stay in core.
"""

from datetime import date, datetime, time, timedelta

from core import (
    BAD_PERMIT_STATUSES,
    IN_PROGRESS_STATUSES,
    add_next_due,
    association_questions,
    building_light,
    norm_address,
    recert_by_tower,
    recert_label,
)
from database import connect


def plain(number):
    """Decimal from PostgreSQL as int or float: 10 -> 10, 0.25 -> 0.25; None stays None."""
    if number is None:
        return None
    return int(number) if number == int(number) else float(number)


def to_ms(day):
    """A date as ArcGIS milliseconds (core.recert_label measures the days from them)."""
    return datetime.combine(day, time()).timestamp() * 1000 if day else None


def summary(db, parent_folio):
    """The condo as core.building_summary returns it, or None if it is not in the database."""
    condo = db.execute(
        "SELECT legal_name, name, zip, units, unit_types, estimate_floors, year_built,"
        " unbuilt, master_in_pa FROM condo WHERE parent_folio = %s",
        [parent_folio],
    ).fetchone()
    if not condo:
        return None
    towers = db.execute(
        "SELECT address, units, year_built FROM tower WHERE parent_folio = %s ORDER BY units DESC, address",
        [parent_folio],
    ).fetchall()
    rules = db.execute(
        "SELECT first_recert_age, first_recert_age_coastal, recert_every, recert_rule_official"
        " FROM jurisdiction WHERE code = %s",
        [parent_folio[:2]],
    ).fetchone()
    folios = db.execute("SELECT folio FROM unit WHERE parent_folio = %s ORDER BY folio", [parent_folio]).fetchall()
    return {
        "parent_folio": parent_folio,
        "master_in_pa": condo[8],
        "towers": {address: units for address, units, _ in towers},
        "zip": condo[2],
        "condo_name": condo[0],
        "name": condo[1],
        "units": condo[3],
        "unit_types": dict(sorted(condo[4].items(), key=lambda item: (-item[1], item[0]))),
        "estimate_floors": condo[5],
        "tower_years": {address: year for address, _, year in towers if year},
        "year_of_construction": condo[6],
        "unbuilt": condo[7],
        "folios": [folio for (folio,) in folios],
        "rules": dict(zip(("first_age", "first_age_coastal", "every", "official"), rules)),
    }


def recert_records(db, building):
    """Recertification records of the building, by folio or by tower address (as core.recert_records)."""
    all_folios = [building["parent_folio"]] + building["folios"]
    addresses = [norm_address(tower) for tower in building["towers"] if tower]
    rows = db.execute(
        "SELECT source_id, folio, address, year_built, recert_year, status, plan_number,"
        " plan_status, plan_status_date, submitted_date, extra FROM recert_record"
        " WHERE jurisdiction_code = %s AND (folio = ANY(%s) OR match_address = ANY(%s))"
        " ORDER BY source_id",
        [building["parent_folio"][:2], all_folios, addresses],
    ).fetchall()
    records = []
    for row in rows:
        r = {
            "OBJECTID": row[0],
            "FolioNumber": row[1],
            "Address": row[2],
            "YearBuilt": row[3],
            "RecertificateYear": row[4],
            "CertificationStatus": row[5],
            "PlanNumber": row[6],
            "PlanStatus": row[7],
            "PlanStatusDate": to_ms(row[8]),
            **row[10],
        }
        if r["FolioNumber"] == building["parent_folio"]:
            r["matched_on"] = "master"
        elif r["FolioNumber"] in all_folios:
            r["matched_on"] = "unit"
        else:
            r["matched_on"] = "address"
        r["label"] = recert_label(r)
        r["PlanStatusDate"] = row[8] and row[8].isoformat()
        r["SubmittedDate"] = row[9] and row[9].isoformat()
        records.append(r)
    return records


def permit_summary(db, building):
    """Building-level permits: total, last 12 months, in progress and not closed (as core.permit_summary)."""
    rows = db.execute(
        "SELECT permit_number, status, permit_type, scope, issued_date FROM permit"
        " WHERE jurisdiction_code = %s AND folio = %s"
        " ORDER BY issued_date DESC NULLS LAST, permit_number DESC",
        [building["parent_folio"][:2], building["parent_folio"]],
    ).fetchall()
    permits = [
        {
            "PermitNumber": number,
            "PermitStatus": status,
            "PermitType": permit_type,
            "ScopeOfWork": scope,
            "PermitIssuedDate": issued and issued.isoformat(),
        }
        for number, status, permit_type, scope, issued in rows
    ]
    year_ago = (date.today() - timedelta(days=365)).isoformat()
    # the oldest permit the source holds: some layers only keep recent years
    first = db.execute(
        "SELECT min(issued_date) FROM permit WHERE jurisdiction_code = %s", [building["parent_folio"][:2]]
    ).fetchone()[0]
    return {
        "since": first and first.isoformat(),
        "total": len(permits),
        "last_12_months": [p for p in permits if (p["PermitIssuedDate"] or "") >= year_ago],
        "in_progress": [p for p in permits if p["PermitStatus"] in IN_PROGRESS_STATUSES],
        "not_closed": [p for p in permits if p["PermitStatus"] in BAD_PERMIT_STATUSES],
    }


def location(db, building):
    """Flood zone and coast distance as core.flood_zone and core.coast_distance return them."""
    row = db.execute(
        "SELECT flood_zone, flood_subtype, flood_bfe, coast_within_miles FROM condo_location WHERE parent_folio = %s",
        [building["parent_folio"]],
    ).fetchone()
    if not row:  # not looked up yet: run load_location.py
        return None, None
    zone, subtype, bfe, miles = row
    flood = zone and {
        "zone": zone,
        "subtype": subtype,
        "base_flood_elevation_ft": plain(bfe),
    }
    coast = {"within_miles": plain(miles), "within_3_miles": miles is not None}
    return flood, coast


def dbpr_info(db, building):
    """DBPR projects, Delinquent status and SIRS reports (as core.dbpr_info)."""
    projects = [
        {"number": number, "name": name, "status": status or ""}
        for number, name, status in db.execute(
            "SELECT p.project_number, p.name, p.secondary_status FROM condo_dbpr m"
            " JOIN dbpr_project p USING (project_number)"
            " WHERE m.parent_folio = %s AND NOT m.needs_review ORDER BY p.project_number",
            [building["parent_folio"]],
        )
    ]
    periods = [
        period
        for (period,) in db.execute(
            "SELECT DISTINCT s.period FROM sirs_report s"
            " JOIN condo_dbpr m USING (project_number)"
            " WHERE m.parent_folio = %s AND NOT m.needs_review",
            [building["parent_folio"]],
        )
    ]
    return {
        "projects": projects,
        "delinquent": any(p["status"] == "Delinquent" for p in projects),
        "sirs_reported": " + ".join(sorted(periods)) or None,
    }


def violations(db, building):
    """Open building and code cases, with a status: 'public', 'not_public', 'unavailable' or 'unknown'.

    Whether a jurisdiction publishes its cases is decided in jurisdiction.violations_public;
    'unavailable' means it publishes them but there is no good load yet.
    """
    code = building["parent_folio"][:2]
    name, public = db.execute("SELECT name, violations_public FROM jurisdiction WHERE code = %s", [code]).fetchone()
    found = {"status": None, "jurisdiction": name, "open": [], "unit_open": 0}
    if public is None:
        found["status"] = "unknown"
    elif not public:
        found["status"] = "not_public"
    elif not db.execute(
        "SELECT 1 FROM ingest_run WHERE source = 'violations' AND status = 'ok' AND jurisdiction_code = %s LIMIT 1",
        [code],
    ).fetchone():
        found["status"] = "unavailable"
    else:
        found["status"] = "public"
        found["open"] = [
            {
                "case_number": number,
                "case_type": case_type,
                "source": source,
                "opened": opened and opened.isoformat(),
                "lien": status == "Lien" or bool(extra.get("lien_recorded")),
            }
            for number, case_type, source, opened, status, extra in db.execute(
                "SELECT case_number, case_type, source, opened_date, status, extra FROM violation"
                " WHERE jurisdiction_code = %s AND folio = %s AND status <> 'Closed'"
                " ORDER BY case_type = 'Unsafe Structure' DESC, opened_date DESC NULLS LAST, case_number",
                [code, building["parent_folio"]],
            )
        ]
        found["unit_open"] = db.execute(
            "SELECT count(*) FROM violation v JOIN unit u ON u.folio = v.folio"
            " WHERE v.jurisdiction_code = %s AND u.parent_folio = %s AND v.status <> 'Closed'",
            [code, building["parent_folio"]],
        ).fetchone()[0]
    return found


def data_dates(db, building):
    """Day of the data behind the report: {'local': ..., 'state': ...} as ISO dates, None if never loaded."""
    latest = dict(
        db.execute(
            # the last good load of each source
            "SELECT DISTINCT ON (source) source, data_date FROM ingest_run WHERE status = 'ok'"
            " AND (jurisdiction_code = %s OR jurisdiction_code IS NULL) ORDER BY source, id DESC",
            [building["parent_folio"][:2]],
        ).fetchall()
    )
    # the sources the jurisdiction has (Miami Beach has no permits yet); the report is as old as the oldest
    local = [latest[source] for source in ("property", "recert", "permits") if latest.get(source)]
    return {
        "local": min(local).isoformat() if local else None,
        "state": latest["dbpr"].isoformat() if latest.get("dbpr") else None,
    }


def build_report(parent_folio):
    """Everything the report needs for one condo, or None if the condo is not in the database."""
    with connect() as db:
        building = summary(db, parent_folio)
        if not building:
            return None
        building["recert"] = recert_records(db, building)
        building["recert_by_tower"] = recert_by_tower(building, building["recert"])
        building["permits"] = permit_summary(db, building)
        building["flood"], building["coast"] = location(db, building)
        add_next_due(building)  # needs coast; may turn "no record" into "Not due yet"
        building["recert_light"] = building_light(building["recert_by_tower"])
        building["dbpr"] = dbpr_info(db, building)
        building["violations"] = violations(db, building)
        building["questions"] = association_questions(building)
        building["data_dates"] = data_dates(db, building)
    return building
