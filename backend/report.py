"""Render the building report (HTML and PDF) from the database, through report_db.build_report."""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

import psycopg
from core import (
    FULL_REVIEW_MIN_UNITS,
    LIGHT_ORDER,
    RECERT_TEXT,
    THIS_YEAR,
    financing_check,
    group_where,
    milestone_applies,
    pretty_name,
    recert_rules,
    tower_groups,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape
from report_db import build_report
from weasyprint import HTML

HERE = Path(__file__).parent
OUT = HERE / "out"
MAX_TOWERS = 3  # recertification rows shown before "+ N more" (red and yellow rows are always shown)
MAX_PERMITS = 5  # permits of the last 12 months listed one per line

PERMIT_STATUS = {
    "Final": "finished",
    "Active": "active",
    "Pending": "pending",
    "InAdjustment": "in adjustment",
    "Hold": "on hold",
    "Expired": "expired",
    "Revoked": "revoked",
    "Cancelled": "cancelled",
}

SINGLE_TYPE = {  # condos where every unit is of the same kind
    "residential": "Residential condo",
    "co-op": "Residential co-op",
    "office": "Office condo",
    "retail": "Retail condo",
    "hotel": "Hotel condo",
    "boat dock": "Boat dock condo",
    "parking": "Parking condo",
    "other commercial": "Commercial condo",
    "not built": "Not built yet",
}

# Official paid search of code violations and liens on one property, by jurisdiction (folio prefix)
LIEN_SEARCH = {
    "01": {
        "who": "The City of Miami",
        "fee": "$200",
        "url": "https://www.miami.gov/Services/Building-Permitting/Run-a-Lien-or-Violation-Search",
        "link_text": "miami.gov: Run a Lien or Violation Search",
    },
    "02": {
        "who": "The City of Miami Beach",
        "fee": "$153 for a condo unit",
        "url": "https://lien.miamibeachfl.gov/",
        "link_text": "lien.miamibeachfl.gov: Lien Research System",
    },
}


# How the report names each jurisdiction and its sources (data rules live in the jurisdiction table)
PLACE_TEXT = {
    "01": {
        "place": "Miami, FL",
        "sources": "Miami-Dade County Property Appraiser (year built, units), City of Miami (recertification, "
        "iBuild permits), Florida DBPR (condominium registry, SIRS reports), FEMA flood zones and shoreline "
        "as published by Miami-Dade County.",
        "estimate_note": "Dates marked as estimated follow the county rule and may differ from the City's.",
        "permits_partial": False,  # the City's layer holds every permit
        "records": "City and County records",
    },
    "30": {
        "place": "Unincorporated Miami-Dade, FL",
        "sources": "Miami-Dade County Property Appraiser (year built, units), Miami-Dade County (building "
        "recertification portal, building permits, code violations), Florida DBPR (condominium registry, "
        "SIRS reports), FEMA flood zones and shoreline as published by Miami-Dade County.",
        "estimate_note": "",
        "permits_partial": True,  # the County's layer only holds recent years: the report says since when
        "records": "County records",
        "enforcement": "The County has opened a compliance proceeding because the recertification is overdue.",
    },
    "02": {
        "place": "Miami Beach, FL",
        "sources": "Miami-Dade County Property Appraiser (year built, units), City of Miami Beach (building "
        "recertification and unsafe structure cases from its Civic Access portal), Florida DBPR (condominium "
        "registry, SIRS reports), FEMA flood zones and shoreline as published by Miami-Dade County.",
        "estimate_note": "",
        "permits_partial": False,
        "records": "City and County records",
        # only part of the city's data is loaded: the report says so
        "permits_note": "Miami Beach permits are not loaded yet: look the building up in the City's Civic "
        "Access portal (energovcss.miamibeachfl.gov).",
        "violations_scope": "Only unsafe structure cases in front of the Special Magistrate are included. "
        "Other code cases and liens are not loaded.",
    },
}
OTHER_PLACE = {  # a jurisdiction without its own texts yet: name nobody
    "place": "Miami-Dade County, FL",
    "sources": "Miami-Dade County Property Appraiser (year built, units), Florida DBPR (condominium registry, "
    "SIRS reports), FEMA flood zones and shoreline as published by Miami-Dade County, and local public records.",
    "estimate_note": "Dates marked as estimated follow the county rule and may differ from the local rule.",
    "permits_partial": False,
    "records": "Local and County records",
}

RESULT_TITLES = {
    "green": "Green · Current or not yet due",
    "yellow": "Yellow · Items to verify",
    "red": "Red · Recertification problem",
}


def format_folio(folio):
    """'0132310480001' -> '01-3231-048-0001'."""
    return f"{folio[:2]}-{folio[2:6]}-{folio[6:9]}-{folio[9:]}"


def flood_text(flood):
    """(zone, description) for the FEMA tile."""
    if not flood:
        return "—", "Not determined"
    zone = flood["zone"]
    if zone.startswith("V"):
        text = "Very high risk · coastal waves"
    elif zone.startswith("A"):
        text = "High risk · insurance usually required"
    elif zone == "X" and flood["subtype"] and flood["subtype"].startswith("0.2"):
        text = "Moderate risk"
    elif zone == "X":
        text = "Minimal risk"
    else:
        return zone, "Not determined"
    return zone, text


def permit_line(permit):
    """'Aug 2026 · Mechanical elevators · active'."""
    issued = date.fromisoformat(permit["PermitIssuedDate"]).strftime("%b %Y")
    status = PERMIT_STATUS.get(permit["PermitStatus"], permit["PermitStatus"].lower())
    return {"date": issued, "type": permit["PermitType"].capitalize(), "status": status}


def enforcement_detail(group, building):
    """The County cases behind ENFORCEMENT (code 30), so the realtor can look them up in the County's portal."""
    plans = sorted({t["plan"] for t in group["towers"]})
    if len(plans) == 1:
        text = f"Case {plans[0]} · open since {group['tower']['year_due']}."
    else:
        more = f" + {len(plans) - 2} more" if len(plans) > 2 else ""
        text = f"Cases {', '.join(plans[:2])}{more} · open since {group['tower']['year_due']}."
    cases = (building.get("violations") or {}).get("open") or []
    # the County files an overdue recertification as an unsafe structure case too: count only the others
    unsafe = [c["case_number"] for c in cases if c["case_type"] == "Unsafe Structure" and c["case_number"] not in plans]
    if unsafe:
        cases_word = "case" if len(unsafe) == 1 else "cases"
        text += f" Also {len(unsafe)} other open unsafe structure {cases_word} on the building (e.g. {unsafe[0]})."
    day = (building.get("data_dates") or {}).get("local")
    return f"{text} County records as of {long_date(day)}." if day else text


def recert_detail(group, official, building=None):
    """One sentence with the dates we know for a group of towers; 'estimated' unless the rule is official."""
    tower = group["tower"]
    due = tower.get("next_due")
    label = tower["label"]
    if label == "Pending: enforcement" and building and building["parent_folio"].startswith("30"):
        return enforcement_detail(group, building)
    if label == "Not built yet":
        return "No year built on record; the units are listed as vacant."

    if label in ("no record", "Not due yet"):
        return (
            f"{'Due' if official else 'Estimated due'} in {due['year']} ({due['basis']})."
            if due
            else "Year built unknown."
        )
    if label == "Exempted":
        return f"Exempted in the {tower['year_due']} cycle."
    if label.startswith("Completed"):
        return f"{tower['year_due']} cycle completed. Next due: {due['year']}{'' if official else ' (estimated)'}."
    days = sorted({t["status_date"] for t in group["towers"] if t["status_date"]})
    if not days:
        return f"{tower['year_due']} cycle."
    if len(days) == 1:
        return f"{tower['year_due']} cycle, last update {days[0]}."
    return f"{tower['year_due']} cycle, last updates {days[0]} to {days[-1]}."


def compliance_rows(building):
    """Rows of the compliance table: item, badge, color, detail."""
    rows = []
    all_towers = len(building["recert_by_tower"])
    groups = tower_groups(building["recert_by_tower"])  # worst first
    # red and yellow groups are never hidden; green ones fill the rows left
    shown = [g for g in groups if g["tower"]["light"] != "green" or groups.index(g) < MAX_TOWERS]
    for group in shown:
        badge, color, _ = RECERT_TEXT[group["tower"]["label"]]
        where = group_where(group, all_towers)
        item = (
            "Recertification (milestone)" if all_towers == 1 or not where else f"Recertification (milestone) · {where}"
        )
        rows.append(
            {
                "item": item,
                "badge": badge,
                "color": color,
                "detail": recert_detail(group, recert_rules(building)["official"], building),
            }
        )
    hidden = Counter(RECERT_TEXT[t["label"]][0] for g in groups if g not in shown for t in g["towers"])
    if hidden:
        count = sum(hidden.values())
        what = f"address{'' if count == 1 else 'es'}"
        if len(hidden) == 1:
            item = f"+ {count} more {what}, all {next(iter(hidden))}"
        else:
            item = f"+ {count} more {what} ({', '.join(f'{n} {badge.lower()}' for badge, n in hidden.most_common())})"
        rows.append({"item": item, "badge": "", "color": "gray", "detail": "Ask us for the full list."})

    dbpr = building["dbpr"]
    if milestone_applies(building) != "no":  # also "no" when the building is not built yet
        if dbpr["sirs_reported"]:
            rows.append(
                {
                    "item": "Reserve study (SIRS)",
                    "badge": "REPORTED",
                    "color": "green",
                    "detail": f"Reported to the state (DBPR): {dbpr['sirs_reported']}.",
                }
            )
        elif milestone_applies(building) == "maybe":  # stories unknown: do not claim a SIRS is missing
            registry = "Not found in" if dbpr["projects"] else "Association not identified in"
            rows.append(
                {
                    "item": "Reserve study (SIRS)",
                    "badge": "ASK",
                    "color": "gray",
                    "detail": "Needed if the building has 3 or more stories (not in the records). "
                    f"{registry} the DBPR registry.",
                }
            )
        elif dbpr["projects"]:
            rows.append(
                {
                    "item": "Reserve study (SIRS)",
                    "badge": "NOT IN REGISTRY",
                    "color": "yellow",
                    "detail": "Not found in the DBPR registry (self-reported).",
                }
            )
        else:
            rows.append(
                {
                    "item": "Reserve study (SIRS)",
                    "badge": "UNKNOWN",
                    "color": "gray",
                    "detail": "Association not identified in the DBPR registry.",
                }
            )
    if dbpr["projects"]:
        numbers = ", ".join(p["number"] for p in dbpr["projects"])
        if dbpr["delinquent"]:
            rows.append(
                {
                    "item": "State registration (DBPR)",
                    "badge": "DELINQUENT",
                    "color": "yellow",
                    "detail": f"Likely an unpaid annual fee. Project {numbers}.",
                }
            )
        else:
            rows.append(
                {
                    "item": "State registration (DBPR)",
                    "badge": "RECORDED",
                    "color": "green",
                    "detail": f"Project {numbers}.",
                }
            )
    return rows


def type_text(unit_types):
    """'Residential condo', or '337 residential, 118 retail' when the units differ."""
    kinds = list(unit_types.items())  # most common first
    if len(kinds) == 1:
        return SINGLE_TYPE.get(kinds[0][0], "Condo")
    text = ", ".join(f"{n} {kind}" for kind, n in kinds[:2])
    if len(kinds) > 2:
        text += f", {sum(n for _, n in kinds[2:])} other"
    return text


def result_text(tower, official, place=OTHER_PLACE):
    """Sentence for the overall result, from the worst tower."""
    if tower["label"] == "Pending: enforcement" and place.get("enforcement"):
        return place["enforcement"]
    if tower["label"] == "no record":
        if not tower.get("next_due"):
            why = "the year built is not on record"
        elif official:
            why = "under the recertification rule one was already due"
        else:
            why = "by our estimate one was already due"
        # "not found" is not "not done": the record may exist where the public data does not reach
        return (
            f"No recertification record was found in the {place['records']}, and {why}. "
            "This does not mean it was not done: ask the association for the certificate."
        )
    return RECERT_TEXT[tower["label"]][2]


MAX_CASES = 5  # open cases listed one per line


def violations_text(found, scope=None):
    """Card values: the status and, when the cases are public, the open ones."""
    if found is None:  # built live by core.py, which does not read the cases
        return {"status": "unknown", "jurisdiction": "this municipality"}
    cases = found["open"]
    return {
        "status": found["status"],
        "jurisdiction": found["jurisdiction"],
        "count": len(cases),
        "lines": violation_lines(cases[:MAX_CASES]),
        "more": max(0, len(cases) - MAX_CASES),
        "unsafe": sum(1 for c in cases if c["case_type"] == "Unsafe Structure"),
        "expired": sum(1 for c in cases if c["case_type"] == "Expired Permit"),
        "liens": sum(1 for c in cases if c["lien"]),
        "unit_open": found["unit_open"],
        "scope": scope,  # which cases are loaded, when not all of them
    }


def violation_lines(cases):
    """Open cases for the card: 'Nov 2021 · Unsafe structure · lien'."""
    lines = []
    for case in cases:
        when = date.fromisoformat(case["opened"]).strftime("%b %Y") if case["opened"] else ""
        lines.append({"date": when, "type": case["case_type"].capitalize(), "lien": case["lien"]})
    return lines


def financing_text(building):
    """Box about mortgage financing: whether the lender's Full Review applies and the warning signs."""
    check = financing_check(building)
    units = building["units"]
    signals = []
    if building["unbuilt"]:
        title = "Not built yet."
        text = "Lenders review new projects with their own rules: ask the lender which review applies."
    elif not check["full_review"]:
        title = "Project review may be waived."
        text = (
            f"With {units} units ({FULL_REVIEW_MIN_UNITS - 1} or fewer), "
            "lenders can waive the review of the association."
        )
    else:
        title = "Full Review required."
        text = (
            f"With {units} units (more than {FULL_REVIEW_MIN_UNITS - 1}), every conventional loan in this "
            "building needs the lender's Full Review of the association (questionnaire 1076 / 476)."
        )
        if check["recert_label"]:
            signals.append(
                RECERT_TEXT[check["recert_label"]][2] + " The review asks about structural inspections and repairs."
            )
        if check["no_sirs"]:
            signals.append("No reserve study (SIRS) in the DBPR registry. The review checks the reserves.")
        if check["not_closed"]:
            permits = "permit" if check["not_closed"] == 1 else "permits"
            signals.append(f"{check['not_closed']} building {permits} expired, revoked or on hold.")
        if check["flood_zone"]:
            signals.append(f"High-risk flood zone ({check['flood_zone']}). The review checks the flood insurance.")
        if check["open_cases"]:
            violations = "violation" if check["open_cases"] == 1 else "violations"
            signals.append(f"{check['open_cases']} open code {violations} on the building. The review asks about them.")
        if check["commercial"]:
            share = round(100 * check["commercial"] / units)
            signals.append(
                f"{check['commercial']} of {units} units are commercial ({share}%). "
                "Lenders limit commercial space, measured by floor area."
            )
    return {
        "title": title,
        "text": text,
        "full_review": check["full_review"] and not building["unbuilt"],
        "signals": signals,
    }


def long_date(day):
    """'2026-10-13' or a date -> 'October 13, 2026'."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day.strftime("%B %d, %Y").replace(" 0", " ")


def data_text(dates, records="City and County records"):
    """Sentence for the footer about how recent the data is; '' when the dates are unknown."""
    if not dates or not dates["local"]:
        return ""
    text = f"{records} as of {long_date(dates['local'])}"
    if dates["state"]:
        text += f"; State (DBPR) records as of {long_date(dates['state'])}"
    return text + "."


def report_context(building):
    """Display values for the template, computed from the building data."""
    towers = [t for t in building["towers"] if t]  # a few condos have no site address on record
    address = ", ".join(pretty_name(t) for t in towers[:2])
    if len(towers) > 2:
        address += f" + {len(towers) - 2} more"
    place = PLACE_TEXT.get(building["parent_folio"][:2], OTHER_PLACE)
    official = recert_rules(building)["official"]
    city = f"{place['place']} {building['zip']}"

    worst = max(
        building["recert_by_tower"].values(),
        key=lambda t: LIGHT_ORDER.index(t["light"]),
    )
    built = building["year_of_construction"]
    coast = building["coast"]
    zone, zone_text = flood_text(building["flood"])
    permits = building["permits"]
    return {
        "name": building["name"] or address or "Condominium",
        "address": f"{address}, {city}" if address else city,
        "folio": format_folio(building["parent_folio"]),
        "year_built": built or "—",
        "age": f"{THIS_YEAR - built} years old" if built else "Not built yet" if building["unbuilt"] else "Unknown",
        "floors": building["estimate_floors"] or "—",
        "units": building["units"],
        "type_text": type_text(building["unit_types"]),
        "coast": f"< {coast['within_miles']} mi" if coast and coast["within_miles"] else "> 3 mi",
        "coast_text": "Coast or navigable waterway" if coast and coast["within_3_miles"] else "Not a coastal building",
        "zone": zone,
        "zone_text": zone_text,
        "light": building["recert_light"],
        "result_title": RESULT_TITLES[building["recert_light"]],
        "result_text": result_text(worst, official, place),
        "compliance": compliance_rows(building),
        "financing": financing_text(building),
        "lien_search": LIEN_SEARCH.get(building["parent_folio"][:2]),
        "violations": violations_text(building.get("violations"), place.get("violations_scope")),
        "permits_note": place.get("permits_note"),
        "sources": place["sources"],
        "estimate_note": "" if official else place["estimate_note"],
        "permits_since": (
            date.fromisoformat(permits["since"]).strftime("%B %Y")
            if place.get("permits_partial") and permits.get("since")
            else None
        ),
        "permits_total": permits["total"],
        "permits_recent": [permit_line(p) for p in permits["last_12_months"][:MAX_PERMITS]],
        "permits_recent_count": len(permits["last_12_months"]),
        "permits_in_progress": len(permits["in_progress"]),
        "permits_not_closed": len(permits["not_closed"]),
        "questions": building["questions"],
        "generated": long_date(date.today()),
        "data_text": data_text(building.get("data_dates"), place["records"]),
    }


def render_html(building):
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
    return env.get_template("report.html").render(**report_context(building))


if __name__ == "__main__":
    folio = sys.argv[1] if len(sys.argv) > 1 else "0132310480001"  # The Grand
    try:
        building = build_report(folio)
    except psycopg.Error as error:
        raise SystemExit(f"Could not read the database: {error}")
    if not building:
        raise SystemExit(f"{folio} is not in the database")
    OUT.mkdir(exist_ok=True)
    html = render_html(building)
    (OUT / f"report_{folio}.html").write_text(html, encoding="utf-8")  # to preview in the browser
    path = OUT / f"report_{folio}.pdf"
    HTML(string=html, base_url=str(HERE)).write_pdf(path)
    print(f"-> {path}")
