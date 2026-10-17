"""Render the building report (HTML and PDF) from the database, through report_db.build_report."""

import sys
from datetime import date
from pathlib import Path

import psycopg
from core import (
    FULL_REVIEW_MIN_UNITS,
    LIGHT_ORDER,
    THIS_YEAR,
    financing_check,
    first_recert_age,
    pretty_name,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape
from report_db import build_report
from weasyprint import HTML

HERE = Path(__file__).parent
OUT = HERE / "out"
MAX_TOWERS = 3  # recertification rows shown before "+ N more"
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
}


RESULT_TITLES = {
    "green": "Green · No recertification issues found",
    "yellow": "Yellow · Items to verify",
    "red": "Red · Recertification problem",
}

# label -> (badge text, badge color, sentence for the overall result)
RECERT_TEXT = {
    "Completed": ("CURRENT", "green", "The recertification is on file and current."),
    "Exempted": ("EXEMPT", "green", "The building is exempt from recertification."),
    "Not due yet": (
        "NOT DUE YET",
        "green",
        "The first recertification is not due yet.",
    ),
    "Not built yet": (
        "NOT BUILT YET",
        "green",
        "The building is not completed yet according to county records, so recertification does not apply yet.",
    ),
    "no record": (
        "NO RECORD",
        "yellow",
        "No recertification record was found, and by our estimate one was already due.",
    ),
    "Completed: overdue": (
        "OVERDUE",
        "yellow",
        "The last recertification is more than 10 years old.",
    ),
    "Pending: city review": (
        "IN CITY REVIEW",
        "yellow",
        "A recertification is in progress and waiting on the City.",
    ),
    "Pending: corrections": (
        "CORRECTIONS REQUESTED",
        "yellow",
        "The City asked for corrections on the recertification.",
    ),
    "Pending: other": ("PENDING", "yellow", "A recertification is pending."),
    "Pending: corrections >1y": (
        "CORRECTIONS > 1 YEAR",
        "red",
        "The City asked for recertification corrections more than a year ago and they are still open.",
    ),
    "Pending: stalled": (
        "STALLED",
        "red",
        "The recertification process is stalled (cancelled, expired or on hold).",
    ),
    "Canceled": ("CANCELED", "red", "The recertification record was canceled."),
    "Pending: enforcement": (
        "ENFORCEMENT",
        "red",
        "The recertification is overdue and the County sent the case to enforcement.",
    ),
    "Closed": (
        "CASE CLOSED",
        "yellow",
        "The County closed the recertification case without a certificate on file. Ask the association why.",
    ),
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


def recert_detail(tower):
    """One sentence with the dates we know for a tower."""
    due = tower.get("next_due")
    label = tower["label"]
    if label == "Not built yet":
        return "No year built on record; the units are listed as vacant."

    if label in ("no record", "Not due yet"):
        return (
            f"Estimated due in {due['year']} ({due['basis']})."
            if due
            else "Year built unknown."
        )
    if label == "Exempted":
        return f"Exempted in the {tower['year_due']} cycle."
    if label.startswith("Completed"):
        return (
            f"{tower['year_due']} cycle completed. Next due: {due['year']} (estimated)."
        )
    if not tower["status_date"]:
        return f"{tower['year_due']} cycle."
    return f"{tower['year_due']} cycle, last update {tower['status_date']}."


def compliance_rows(building):
    """Rows of the compliance table: item, badge, color, detail."""
    rows = []
    towers = list(building["recert_by_tower"].items())
    towers.sort(key=lambda kv: -LIGHT_ORDER.index(kv[1]["light"]))  # worst first
    for address, tower in towers[:MAX_TOWERS]:
        badge, color, _ = RECERT_TEXT[tower["label"]]
        item = (
            "Recertification"
            if len(towers) == 1
            else f"Recertification · {pretty_name(address)}"
        )
        rows.append(
            {
                "item": item,
                "badge": badge,
                "color": color,
                "detail": recert_detail(tower),
            }
        )
    if len(towers) > MAX_TOWERS:
        rows.append(
            {
                "item": f"+ {len(towers) - MAX_TOWERS} more addresses",
                "badge": "",
                "color": "gray",
                "detail": "Shown worst first. Ask us for the full list.",
            }
        )

    floors, built = building["estimate_floors"], building["year_of_construction"]
    dbpr = building["dbpr"]
    if not building["unbuilt"] and (floors is None or floors >= 3):
        if built and THIS_YEAR - built < first_recert_age(building):
            rows.append(
                {
                    "item": "Milestone inspection",
                    "badge": "NOT DUE YET",
                    "color": "green",
                    "detail": f"Due at {first_recert_age(building)} years old (estimated).",
                }
            )
        else:
            rows.append(
                {
                    "item": "Milestone inspection",
                    "badge": "NOT PUBLIC",
                    "color": "gray",
                    "detail": "Results are not public. Ask the association.",
                }
            )
        if dbpr["sirs_reported"]:
            rows.append(
                {
                    "item": "Reserve study (SIRS)",
                    "badge": "REPORTED",
                    "color": "green",
                    "detail": f"Reported to the state (DBPR): {dbpr['sirs_reported']}.",
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


def result_text(tower):
    """Sentence for the overall result, from the worst tower."""
    if tower["label"] == "no record" and not tower.get("next_due"):
        return (
            "No recertification record was found, and the year built is not on record."
        )
    return RECERT_TEXT[tower["label"]][2]


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
                RECERT_TEXT[check["recert_label"]][2]
                + " The review asks about structural inspections and repairs."
            )
        if check["no_sirs"]:
            signals.append(
                "No reserve study (SIRS) in the DBPR registry. The review checks the reserves."
            )
        if check["not_closed"]:
            permits = "permit" if check["not_closed"] == 1 else "permits"
            signals.append(
                f"{check['not_closed']} building {permits} expired, revoked or on hold."
            )
        if check["flood_zone"]:
            signals.append(
                f"High-risk flood zone ({check['flood_zone']}). The review checks the flood insurance."
            )
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


def data_text(dates):
    """Sentence for the footer about how recent the data is; '' when the dates are unknown."""
    if not dates or not dates["local"]:
        return ""
    text = f"City and County records as of {long_date(dates['local'])}"
    if dates["state"]:
        text += f"; State (DBPR) records as of {long_date(dates['state'])}"
    return text + "."


def report_context(building):
    """Display values for the template, computed from the building data."""
    towers = [
        t for t in building["towers"] if t
    ]  # a few condos have no site address on record
    address = ", ".join(pretty_name(t) for t in towers[:2])
    if len(towers) > 2:
        address += f" + {len(towers) - 2} more"
    city = f"Miami, FL {building['zip']}"

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
        "age": f"{THIS_YEAR - built} years old"
        if built
        else "Not built yet"
        if building["unbuilt"]
        else "Unknown",
        "floors": building["estimate_floors"] or "—",
        "units": building["units"],
        "type_text": type_text(building["unit_types"]),
        "coast": f"< {coast['within_miles']} mi"
        if coast and coast["within_miles"]
        else "> 3 mi",
        "coast_text": "Coast or navigable waterway"
        if coast and coast["within_3_miles"]
        else "Not a coastal building",
        "zone": zone,
        "zone_text": zone_text,
        "light": building["recert_light"],
        "result_title": RESULT_TITLES[building["recert_light"]],
        "result_text": result_text(worst),
        "compliance": compliance_rows(building),
        "financing": financing_text(building),
        "lien_search": LIEN_SEARCH.get(building["parent_folio"][:2]),
        "permits_total": permits["total"],
        "permits_recent": [
            permit_line(p) for p in permits["last_12_months"][:MAX_PERMITS]
        ],
        "permits_recent_count": len(permits["last_12_months"]),
        "permits_in_progress": len(permits["in_progress"]),
        "permits_not_closed": len(permits["not_closed"]),
        "questions": building["questions"],
        "generated": long_date(date.today()),
        "data_text": data_text(building.get("data_dates")),
    }


def render_html(building):
    env = Environment(
        loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape()
    )
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
    (OUT / f"report_{folio}.html").write_text(
        html, encoding="utf-8"
    )  # to preview in the browser
    path = OUT / f"report_{folio}.pdf"
    HTML(string=html, base_url=str(HERE)).write_pdf(path)
    print(f"-> {path}")
