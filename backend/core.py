import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
from applog import get_logger

log = get_logger("arcgis")

# ---------------------------------------------------------------------------
# 1. Fuentes de datos (servicios ArcGIS públicos)
# ---------------------------------------------------------------------------

COUNTY = "https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/ArcGIS/rest/services"
CITY = "https://gis.miami.gov/gis/rest/services"

OUT = Path(__file__).parent / "out"  # CSVs written by match_condos.py and match_sirs.py

LAYERS = {
    "property": f"{COUNTY}/PaGISView_gdb/FeatureServer/0",
    "recert": f"{CITY}/SmartCities/Recertification_40Years/MapServer/0",
    "ibuild_permits": f"{CITY}/Maps/iBuildPermits/MapServer/0",
    # "permits": f"{CITY}/Building_Permits_Since_2014/FeatureServer/0",
    # "open_violations": f"{COUNTY}/Open_Building_Violations/FeatureServer/0",
    # "closed_violations_5y": f"{COUNTY}/Closed_Building_Violations_(Past_5_years)/FeatureServer/0",
    "flood": f"{COUNTY}/FEMAFloodZone_gdb/FeatureServer/0",
    "shoreline": f"{COUNTY}/Shoreline_gdb/FeatureServer/0",
}

# Licencias: las de la Ciudad están publicadas como CC BY 4.0 (uso comercial
# permitido citando la fuente). Las del condado hay que revisarlas una a una en
# su página del portal (gis-mdc.opendata.arcgis.com) antes de cobrar.
LICENSES = {
    "recert": "Pendiente de verificar (servidor GIS de la Ciudad)",
    "permits": "CC BY 4.0 (Ciudad de Miami)",
    "ibuild_permits": "Pendiente de verificar (servidor GIS de la Ciudad)",
    "flood": "Pendiente de verificar (condado)",
    "shoreline": "Pendiente de verificar (condado)",
}

# Campos que pedimos.
# ni OwnerName ni VIOL_NAME: así ni siquiera llegan a tu computadora.
PROPERTY_FIELDS = [
    "FOLIO",
    "PARENT_FOLIO",
    "CONDO_FLAG",
    "TRUE_SITE_ADDR",
    "TRUE_SITE_UNIT",
    "TRUE_SITE_CITY",
    "TRUE_SITE_ZIP_CODE",
    "DOR_CODE_CUR",
    "DOR_DESC",
    "FLOOR_COUNT",
    "UNIT_COUNT",
    "YEAR_BUILT",
    "BUILDING_ACTUAL_AREA",
    "LEGAL",
    "SUBDIVISION",
]
RECERT_FIELDS = [
    "RecertificateYear",
    "CertificationStatus",
    "RecertificationProcessStatus",
    "Address",
    "BuildingNumber",
    "FolioNumber",
    "YearBuilt",
    "PlanNumber",
    "SubmittedDate",
    "PlanStatus",
    "PlanStatusReason",
    "PlanStatusDate",
    "RequestType",
    "RequestStatus",
    "RequestResult",
    "UpdatedDate",
    "OBJECTID",
]

PERMIT_FIELDS = [
    "PermitNumber",
    "PermitStatus",
    "PermitType",
    "ScopeOfWork",
    "PermitIssuedDate",
]

BAD_PERMIT_STATUSES = ("Hold", "Expired", "Revoked")

FLOOR_IN_NUMBER = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")  # 1204, E-1001, 806A, PH 5801
FLOOR_AND_LETTER = re.compile(r"(\d{1,2})-?([A-Z]{1,2})")  # 43-A, 12B

# First word found in DOR_DESC decides the kind of unit (order matters)
UNIT_TYPE_WORDS = [
    ("COOPERATIVE", "co-op"),
    ("DOCK", "boat dock"),
    ("PARKING", "parking"),
    ("HOTEL", "hotel"),
    ("OFFICE", "office"),
    ("PROFESSIONAL", "office"),
    ("STORE", "retail"),
    ("CONDOMINIUM - RESIDENTIAL", "residential"),
    ("COMMON AREAS", "residential"),
]


NAME_NOISE = {
    "A",
    "THE",
    "CONDO",
    "CONDOMINIUM",
    "CONDOMINIUMS",
    "OF",
    "NO",
    "AT",
    "ASSN",
    "ASSOCIATION",
    "INC",
}

FUZZY_MIN = 85  # checked by hand: all matches at 85+ were right

NUMBERS = {
    "ONE": "1",
    "TWO": "2",
    "THREE": "3",
    "FOUR": "4",
    "FIVE": "5",
    "I": "1",
    "II": "2",
    "III": "3",
    "IV": "4",
    "V": "5",
}


def fuzzy_name(text):
    """match_name with numbers in one spelling: 'PHASE TWO' / 'PHASE II' -> 'PHASE 2'."""
    return " ".join(NUMBERS.get(w, w) for w in match_name(text).split())


def match_name(text):
    """Condo name without punctuation and filler words: 'GRAND, THE' -> 'GRAND'."""
    words = re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper()).split()
    return " ".join(w for w in words if w not in NAME_NOISE)


TIMEOUT = 60  # seconds to wait for one answer
TRIES = 3  # the City's server sometimes hangs: ask again before giving up
RETRY_WAIT = 2  # seconds before the second try, doubled for each later one


def request_json(method, url, **kwargs):
    """GET or POST an ArcGIS URL and return its JSON, trying again on timeouts and server errors."""
    host = urlparse(url).netloc
    layer = url.split("/services/")[-1]  # 'PaGISView_gdb/FeatureServer/0/query'
    for attempt in range(1, TRIES + 1):
        started = time.monotonic()
        try:
            r = requests.request(method, url, timeout=TIMEOUT, **kwargs)
            if r.status_code < 500:
                r.raise_for_status()  # a 4xx is our mistake: retrying will not help
                data = r.json()
                code = data["error"].get("code") or 0 if "error" in data else None
                if code is None:
                    rows = (
                        f"{len(data['features'])} rows" if "features" in data else "ok"
                    )
                    log.debug(
                        "%s %s: %s in %.1fs",
                        method,
                        layer,
                        rows,
                        time.monotonic() - started,
                    )
                    return data
                if (
                    code < 500
                ):  # our mistake (a wrong field, a bad query): retrying will not help
                    log.error("%s answered with an error: %s", layer, data["error"])
                    raise RuntimeError(data["error"])
                problem = f"ArcGIS error {code}"  # the server's own trouble, sent with HTTP 200
            else:
                problem = f"HTTP {r.status_code}"

        except (requests.Timeout, requests.ConnectionError) as error:
            problem = type(error).__name__
        if attempt < TRIES:
            wait = RETRY_WAIT * 2 ** (attempt - 1)
            log.warning(
                "%s from %s (%s), trying again in %ss", problem, host, layer, wait
            )
            time.sleep(wait)
    log.error("No answer from %s after %s tries (%s): %s", host, TRIES, problem, layer)
    raise RuntimeError(f"No answer from {host} after {TRIES} tries ({problem})")


def layer_info(url):
    return request_json("GET", url, params={"f": "json"})


def safe_fields(url, wanted):
    """Devuelve solo los campos de la lista permitida que existe en la capa"""
    existing = {f["name"] for f in layer_info(url)["fields"]}
    missing = [f for f in wanted if f not in existing]
    if missing:
        print(f" ! no existen en la capa: {missing}")
    return [f for f in wanted if f in existing]


def base_address(r):
    """Building address without the unit number at the end."""
    addr = r["TRUE_SITE_ADDR"] or ""
    unit = r["TRUE_SITE_UNIT"]
    if unit and addr.endswith(" " + unit):
        addr = addr[: -len(unit) - 1]
    return addr


SUFFIXES = {"AVE": "AV"}

STREET_NAMES = {
    "N RIVER DR": "NORTH RIVER DR",
    "S RIVER DR": "SOUTH RIVER DR",
    "S TAMIAMI CANAL DR": "SOUTH TAMIAMI CANAL DR",
}


def norm_address(addr):
    """Upper case, single spaces and the recert layer's street spelling."""
    words = (addr or "").upper().split()
    addr = " ".join(SUFFIXES.get(w, w) for w in words)
    for short, full in STREET_NAMES.items():
        if addr.endswith(" " + short):
            addr = addr[: -len(short)] + full
    return addr


def ms_to_date(ms):
    """ArcGIS date are in milliseconds"""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


def point_query(url, x, y, fields, distance=None):
    """Features of a layer that contain the point (longitude, latitude)."""
    params = {
        "geometry": f"{x},{y}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": ",".join(fields),
        "returnGeometry": "false",
        "f": "json",
    }
    if distance:
        params["distance"] = distance
        params["units"] = "esriSRUnit_StatuteMile"
    data = request_json("GET", f"{url}/query", params=params)
    return [f["attributes"] for f in data["features"]]


# ---------------------------------------------------------------------------
# Recertification traffic light
# ---------------------------------------------------------------------------

THIS_YEAR = date.today().year

# When records share the latest year, the best status wins
PRIORITY = {"Completed": 3, "Exempted": 2, "Pending": 1, "Closed": 0, "Canceled": 0}

# Pending records split by who has to act next
PENDING_GROUPS = {
    "Enforcement": "enforcement",  # County: overdue and sent to enforcement
    "In Review": "city review",
    "Prescreen": "city review",
    "Submitted": "city review",
    "Approved": "city review",
    "Permit Issued": "city review",
    "Final": "city review",
    "Applicant Corrections": "corrections",
    "Prescreen Corrections": "corrections",
    "Applicant Upload": "corrections",
    "Incomplete": "corrections",
    "Cancelled": "stalled",
    "Expired": "stalled",
    "Inactive": "stalled",
    "Hold": "stalled",
}

STUCK_DAYS = 365  # corrections older than this count as stuck

# Recertification schedule. ESTIMATE with the strictest rule (County Sec. 8-11(f)):
# change these when the City confirms which one it applies (its web page says 40).
FIRST_RECERT_AGE = 30
FIRST_RECERT_AGE_COASTAL = 25  # 3+ floors within 3 miles of the coast
RECERT_EVERY = 10


# Fannie Mae / Freddie Mac condo project review (Lender Letter LL-2026-03): from August 3, 2026
# every loan in a project of more than 10 units needs a Full Review (questionnaire 1076 / 476).
FULL_REVIEW_MIN_UNITS = 11
RESERVE_MIN_PERCENT = (
    15  # of the budget, for loan applications from January 4, 2027 (10 before)
)
COMMERCIAL_TYPES = {
    "office",
    "retail",
    "hotel",
    "other commercial",
}  # kinds from unit_type()

LIGHTS = {
    "Completed": "green",
    "Exempted": "green",
    "Completed: overdue": "yellow",
    "Pending: city review": "yellow",
    "Pending: corrections": "yellow",
    "Pending: other": "yellow",
    None: "yellow",  # no record
    "Pending: corrections >1y": "red",
    "Pending: enforcement": "red",
    "Closed": "yellow",  # County: case closed without a certificate
    "Pending: stalled": "red",
    "Canceled": "red",
    "Not due yet": "green",  # no record, but the first recertification is not due
    "Not built yet": "green",
}
LIGHT_ORDER = ["green", "yellow", "red"]

NAME_DROP = {"CONDO", "CONDOMINIUM", "CONDOMINIUMS", "CO-OP", "COOP"}
LOWER_WORDS = {
    "OF",
    "AT",
    "THE",
    "ON",
    "IN",
    "BY",
    "AND",
    "DE",
    "DEL",
    "LA",
    "LAS",
    "LOS",
    "EL",
}
UPPER_WORDS = {
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "N",
    "S",
    "E",
    "W",
    "NE",
    "NW",
    "SE",
    "SW",
}


def pretty_name(name):
    """Name for the report: 'THE GRAND FKA VENETIA' -> 'The Grand'."""
    if not name:
        return None
    name = re.split(r"\s+(?:F/K/A|FKA|A/K/A|AKA|N/K/A|NKA|K/A)\b", name.upper())[0]
    words = [w for w in name.split() if w not in NAME_DROP]
    pretty = []
    for i, w in enumerate(words):
        if w == "NO":
            pretty.append("No.")
        elif w in UPPER_WORDS:  # roman numerals and directions
            pretty.append(w)
        elif (
            re.fullmatch(r"\d+(ST|ND|RD|TH)", w) or w in LOWER_WORDS and i > 0
        ):  # 10TH -> 10th
            pretty.append(w.lower())
        else:
            pretty.append(w.capitalize())
    return " ".join(pretty) or None


def days_since(ms):
    """Days from an ArcGIS date (milliseconds) to today, None if missing."""
    if ms is None:
        return None
    return (date.today() - datetime.fromtimestamp(ms / 1000).date()).days


def latest_record(records):
    """Record of the latest cycle; on a tie, the best status."""
    return max(
        records,
        key=lambda r: (
            r["RecertificateYear"] or 0,
            PRIORITY.get(r["CertificationStatus"], -1),
        ),
    )


def recert_label(r):
    """Label of one recert record (a key of LIGHTS), dates must still be in ms."""
    status = r["CertificationStatus"]
    if status == "Completed" and (r["RecertificateYear"] or 0) + 10 < THIS_YEAR:
        return "Completed: overdue"
    if status == "Pending":
        group = PENDING_GROUPS.get(r["PlanStatus"], "other")
        days = days_since(r["PlanStatusDate"])
        if group == "corrections" and days is not None and days > STUCK_DAYS:
            group = "corrections >1y"
        return f"Pending: {group}"
    return status


def query(url, where, fields, geometry=False):
    """Consulta con paginacion, sigue pidiendo mientras queden registros"""
    out, offset = [], 0
    while True:
        params = {
            "where": where,
            "outFields": ",".join(fields),
            "returnGeometry": str(geometry).lower(),
            "outSR": 4326,
            "resultOffset": offset,
            "f": "json",
        }
        data = request_json("POST", f"{url}/query", data=params)
        out.extend(data["features"])

        if not data.get("exceededTransferLimit"):
            return out
        offset += len(data["features"])


def unit_type(dor_desc):
    """Kind of unit from the Property Appraiser's land use: 'STORE : CONDOMINIUM - COMMERCIAL' -> 'retail'."""
    text = dor_desc or ""
    if text.startswith("VACANT"):
        return "not built"
    for word, kind in UNIT_TYPE_WORDS:
        if word in text:
            return kind
    return "other commercial"


def is_street_number(row):
    """True when the unit number is the house number of its own address: unit '2732' at '2732 SW 26 ST'."""
    unit = (row["TRUE_SITE_UNIT"] or "").strip()
    return unit.isdigit() and (row["TRUE_SITE_ADDR"] or "").split()[:1] == [unit]


def estimate_floors(units):
    """Highest floor in the unit numbers ('1204' and '12-B' are floor 12), or None if they do not look like floors."""
    by_number, by_letter, letters = set(), Counter(), set()
    for unit in units:
        unit = (unit or "").strip().upper()
        number = FLOOR_IN_NUMBER.search(unit)
        letter = FLOOR_AND_LETTER.fullmatch(unit)
        if number:
            by_number.add(int(number.group(1)[:-2]))
        elif letter:
            by_letter[int(letter.group(1))] += 1
            letters.add(letter.group(2))
    # '1A'..'61F' can be 61 townhouses: trust them only with several units per floor
    if (
        by_letter
        and len(letters) >= 3
        and sum(by_letter.values()) >= 2 * max(by_letter)
    ):
        by_number.update(by_letter)
    floors = by_number - {0}
    if not floors:
        return None
    top = max(floors)
    # duplexes use the street number as the unit ('2975', '2977'): every unit on "floor 29"
    enough = 3 if top > 5 else min(top, 2)
    return top if len(floors) >= enough else None


def by_count(counts):
    """Items of a Counter, the most common first; on a tie, in alphabetical order."""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def common_year(years):
    """Most common year in a Counter of years; the older one on a tie."""
    return max(years, key=lambda year: (years[year], -year))


def building_summary(parent_folio, fields):
    """Junta todas las unidades de un condominio en un resumen de un edificio"""
    all_rows = [
        f["attributes"]
        for f in query(
            LAYERS["property"],
            f"PARENT_FOLIO='{parent_folio}' OR FOLIO='{parent_folio}'",
            fields,
        )
    ]
    master = [r for r in all_rows if r["FOLIO"] == parent_folio]
    rows = [r for r in all_rows if r["FOLIO"] != parent_folio]
    if not rows:
        return None
    return summarize_units(parent_folio, master[0] if master else None, rows)


def summarize_units(parent_folio, master, rows):
    """Summary of one condo from its unit rows and its master row (None if the Property Appraiser has none)."""
    master_legal = master["LEGAL"] if master else None

    folios = [r["FOLIO"] for r in rows]
    # Year per tower: the most common year of its units, so one odd unit does not age the building
    years_by_tower = defaultdict(Counter)
    for r in rows:
        if r["YEAR_BUILT"]:  # 0 = unknown
            years_by_tower[base_address(r)][r["YEAR_BUILT"]] += 1
    tower_years = {tower: common_year(years) for tower, years in years_by_tower.items()}

    # Condominium name: from the first unit whose legal description reads "<NAME> UNIT ..."
    m = next(
        (m for r in rows if (m := re.match(r"^(.*?)\s+UNIT\b", r["LEGAL"] or ""))), None
    )

    # Towers: one parent folio can cover several buildings (one address each)
    # Units without an address (vacant lots, mostly) only make a tower when no unit has one.
    towers = Counter(base_address(r) for r in rows)
    if len(towers) > 1:
        towers.pop("", None)

    return {
        "parent_folio": parent_folio,
        "master_in_pa": bool(master),
        "master_legal": master_legal,
        "towers": dict(by_count(towers)),
        "zip": (rows[0]["TRUE_SITE_ZIP_CODE"] or "")[:5],
        "condo_name": m.group(1) if m else None,
        "name": pretty_name(m.group(1) if m else None),
        "units": len(rows),
        "unit_types": dict(by_count(Counter(unit_type(r["DOR_DESC"]) for r in rows))),
        "estimate_floors": estimate_floors(
            r["TRUE_SITE_UNIT"] for r in rows if not is_street_number(r)
        ),
        "tower_years": tower_years,
        "year_of_construction": min(tower_years.values())
        if tower_years
        else None,  # oldest tower
        # declared but not built: no year, and every unit is taxed as vacant
        "unbuilt": not tower_years
        and all((r["DOR_DESC"] or "").startswith("VACANT") for r in rows),
        "folios": folios,
    }


def recert_records(building, fields):
    """Recertification records of the building, by folio or by tower address."""
    all_folios = [building["parent_folio"]] + building["folios"]
    in_list = ",".join(f"'{f}'" for f in all_folios)
    by_folio = query(LAYERS["recert"], f"FolioNumber IN({in_list})", fields)

    addresses = [norm_address(t) for t in building["towers"]]
    in_list = ",".join(f"'{current_address}'" for current_address in addresses)
    by_address = query(LAYERS["recert"], f"Address IN({in_list})", fields)

    records = {}
    for f in by_folio + by_address:
        r = f["attributes"]
        if r["OBJECTID"] in records:  # found in both ways: keep only one
            continue
        if r["FolioNumber"] == building["parent_folio"]:
            r["matched_on"] = "master"
        elif r["FolioNumber"] in all_folios:
            r["matched_on"] = "unit"
        else:
            r["matched_on"] = "address"
        r["label"] = recert_label(r)
        for key in ("SubmittedDate", "PlanStatusDate"):
            r[key] = ms_to_date(r.get(key))
        records[r["OBJECTID"]] = r
    return list(records.values())


def recert_by_tower(building, records):
    """Traffic light of each tower, from its latest recertification cycle."""
    by_tower = defaultdict(list)
    for r in records:
        by_tower[norm_address(r["Address"])].append(r)
    result = {}
    for tower in building["towers"]:
        tower_records = by_tower.get(norm_address(tower))
        latest = latest_record(tower_records) if tower_records else None
        label = latest["label"] if latest else None
        result[tower] = {
            "label": label or "no record",
            "light": LIGHTS.get(label, "yellow"),
            "year_due": latest["RecertificateYear"] if latest else None,
            "status_date": latest["PlanStatusDate"] if latest else None,
            "plan": latest["PlanNumber"] if latest else None,
            "year_built": building["tower_years"].get(tower),
        }
    return result


def building_light(towers):
    """The building takes the worst light of its towers."""
    return max((t["light"] for t in towers.values()), key=LIGHT_ORDER.index)


IN_PROGRESS_STATUSES = ("Active", "Pending", "InAdjustment")


def permit_summary(building, fields):
    """Building-level iBuild permits: total, last 12 months, in progress and not closed."""
    rows = query(
        LAYERS["ibuild_permits"],
        f"FOLIO='{building['parent_folio']}' AND PermitNumber IS NOT NULL",
        fields,
    )
    permits = {}
    for f in rows:
        p = f["attributes"]
        p["PermitIssuedDate"] = ms_to_date(p["PermitIssuedDate"])
        permits[p["PermitNumber"]] = p  # the layer has exact duplicate rows
    permits = sorted(
        permits.values(),
        key=lambda p: (p["PermitIssuedDate"] or "", p["PermitNumber"]),
        reverse=True,
    )  # newest first; the number settles ties
    year_ago = (date.today() - timedelta(days=365)).isoformat()
    return {
        "total": len(permits),
        "last_12_months": [
            p for p in permits if (p["PermitIssuedDate"] or "") >= year_ago
        ],
        "in_progress": [
            p for p in permits if p["PermitStatus"] in IN_PROGRESS_STATUSES
        ],
        "not_closed": [p for p in permits if p["PermitStatus"] in BAD_PERMIT_STATUSES],
    }


def building_point(building):
    """Location of the building: master folio, else the first unit."""
    folio = (
        building["parent_folio"] if building["master_in_pa"] else building["folios"][0]
    )
    found = query(LAYERS["property"], f"FOLIO='{folio}'", ["FOLIO"], geometry=True)
    if not found or not found[0].get("geometry"):
        return None
    return found[0]["geometry"]


def flood_zone(point):
    """FEMA flood zone at the building's location."""
    if not point:
        return None
    zones = point_query(
        LAYERS["flood"], point["x"], point["y"], ["FZONE", "ZONESUBTY", "ELEV"]
    )
    if not zones:
        return None
    z = zones[0]
    return {
        "zone": z["FZONE"],
        "subtype": (z["ZONESUBTY"] or "").strip() or None,
        "base_flood_elevation_ft": z["ELEV"]
        if z["ELEV"] not in (None, -9999)
        else None,
    }


COAST_BANDS = (0.25, 0.5, 1, 2, 3)  # miles


def coast_distance(point):
    """Smallest band (miles) with shoreline nearby, and whether it is within 3 miles."""
    if not point:
        return None
    for miles in COAST_BANDS:
        if point_query(
            LAYERS["shoreline"], point["x"], point["y"], ["OBJECTID"], distance=miles
        ):
            return {"within_miles": miles, "within_3_miles": True}
    return {"within_miles": None, "within_3_miles": False}


def recert_rules(building):
    """The jurisdiction's recertification rule from the database, or the County rule as an estimate."""
    return building.get("rules") or {
        "first_age": FIRST_RECERT_AGE,
        "first_age_coastal": FIRST_RECERT_AGE_COASTAL,
        "every": RECERT_EVERY,
        "official": False,
    }


def first_recert_age(building):
    """Age of the first recertification: the coastal age for coastal buildings of 3+ floors."""
    rules = recert_rules(building)
    coastal = (building.get("coast") or {}).get("within_3_miles")
    floors = building["estimate_floors"]
    if coastal and (floors is None or floors >= 3):  # unknown floors: be strict
        return rules["first_age_coastal"]
    return rules["first_age"]


def next_recert(building, tower):
    """Year the tower's next recertification is due, estimated."""
    label = tower["label"]
    if label == "Exempted":
        return None
    if label.startswith("Completed"):
        every = recert_rules(building)["every"]
        year, basis = tower["year_due"] + every, f"last recert + {every}"
    elif tower["year_due"]:  # Pending or Canceled: this cycle is still open
        year, basis = tower["year_due"], "open cycle"
    else:  # no record
        built = tower["year_built"] or building["year_of_construction"]
        if not built:
            return None
        age = first_recert_age(building)
        year, basis = built + age, f"year built + {age}"
    return {"year": year, "overdue": year < THIS_YEAR, "basis": basis}


def add_next_due(building):
    """next_due per tower; a tower with no record whose first recertification is in the future turns green."""
    for tower in building["recert_by_tower"].values():
        tower["next_due"] = next_recert(building, tower)
        due = tower["next_due"]
        if tower["label"] != "no record":
            continue
        if building["unbuilt"]:
            tower["label"] = "Not built yet"
        elif due and due["year"] > THIS_YEAR:
            tower["label"] = "Not due yet"
        else:
            continue  # still "no record": keeps its yellow light
        tower["light"] = LIGHTS[tower["label"]]


def dbpr_info(building):
    """DBPR projects, Delinquent status and SIRS reports, from match_condos.py and match_sirs.py."""
    parent = building["parent_folio"]
    with (OUT / "condo_match.csv").open() as f:
        projects = [
            {
                "number": r["project_number"],
                "name": r["dbpr_name"],
                "status": r["dbpr_status"],
            }
            for r in csv.DictReader(f)
            if r["parent_folio"] == parent and r["project_number"]
        ]
    with (OUT / "sirs_by_folio.csv").open() as f:
        sirs = next(
            (
                r["sirs_periods"]
                for r in csv.DictReader(f)
                if r["parent_folio"] == parent
            ),
            None,
        )
    return {
        "projects": projects,
        "delinquent": any(p["status"] == "Delinquent" for p in projects),
        "sirs_reported": sirs,
    }


def financing_check(building):
    """Facts from the public data that the lender's Full Review also looks at (None when there is nothing to report)."""
    worst = max(
        building["recert_by_tower"].values(),
        key=lambda t: LIGHT_ORDER.index(t["light"]),
    )
    floors = building["estimate_floors"]
    zone = (building["flood"] or {}).get("zone") or ""
    return {
        "full_review": building["units"] >= FULL_REVIEW_MIN_UNITS,
        "recert_label": worst["label"] if worst["light"] != "green" else None,
        "no_sirs": (floors is None or floors >= 3)
        and not building["dbpr"]["sirs_reported"],
        "not_closed": len(building["permits"]["not_closed"]),
        "flood_zone": zone
        if zone.startswith(("A", "V"))
        else None,  # high-risk FEMA zones
        "open_cases": len((building.get("violations") or {}).get("open") or []),
        "commercial": sum(
            n for kind, n in building["unit_types"].items() if kind in COMMERCIAL_TYPES
        ),
    }


def association_questions(building):
    """Questions for the buyer to ask the association, based on what the data shows or lacks."""
    questions = [
        "Request a written confirmation of no open code violations, unsafe structure cases or liens.",
        "Are any special assessments approved, planned or under discussion?",
    ]
    by_label = defaultdict(list)  # towers that are not green, grouped by status
    for tower, t in building["recert_by_tower"].items():
        if t["light"] != "green":
            by_label[t["label"]].append(tower)
    for label, towers in by_label.items():
        if len(towers) > 3:
            where = f"{len(towers)} addresses"
        else:
            where = (
                ", ".join(t for t in towers if t) or "The building"
            )  # no site address on record

        if label == "no record":
            questions.append(
                f"{where}: request the recertification certificate (no record found in the public records)."
            )
        else:
            questions.append(
                f"{where}: what is the status of the recertification ({label}) and when will it be completed?"
            )

    floors = building["estimate_floors"]
    built = building["year_of_construction"]
    if building["unbuilt"]:
        questions.append(
            "The building is not completed yet: ask the developer for the expected completion date "
            "and the estimated association budget and reserves."
        )
    elif (
        floors is None or floors >= 3
    ):  # milestone and SIRS apply to 3+ floors; unknown: be strict
        if built is None or THIS_YEAR - built >= first_recert_age(building):
            questions.append(
                "Request the latest milestone inspection report and any repairs it requires."
            )
        cases = (building.get("violations") or {}).get("open") or []
        if cases:
            unsafe = [c for c in cases if c["case_type"] == "Unsafe Structure"]
            example = (unsafe or cases)[0]["case_number"]
            questions.append(
                f"The County lists {len(cases)} open code violations on the building"
                f"{f', {len(unsafe)} of them unsafe structure' if unsafe else ''} (e.g. case {example}): "
                "what are they, what will it cost to correct them, and is there a lien?"
            )
        if building["dbpr"]["sirs_reported"]:
            questions.append(
                "Request a copy of the Structural Integrity Reserve Study (SIRS) and the reserve funding plan."
            )
        else:
            questions.append(
                "Request the Structural Integrity Reserve Study (SIRS): it is not in the DBPR registry."
            )

    if building["dbpr"]["delinquent"]:
        questions.append(
            "Is the association current on its annual fee to DBPR? It is listed as delinquent."
        )
    in_progress = building["permits"]["in_progress"]
    if in_progress:
        questions.append(
            f"What work is in progress under the {len(in_progress)} active permits, "
            "what does it cost and how will it be paid for?"
        )
    not_closed = building["permits"]["not_closed"]
    if not_closed:
        questions.append(
            f"Was the work under the {len(not_closed)} expired, revoked or on-hold permits completed "
            f"and inspected (e.g. {not_closed[0]['PermitNumber']})?"
        )

    zone = (building["flood"] or {}).get("zone") or ""
    if zone.startswith(("A", "V")):  # high-risk FEMA zones
        questions.append(
            "Request the building's Elevation Certificate and the cost of the master flood insurance policy."
        )

    if financing_check(building)["full_review"] and not building["unbuilt"]:
        questions += [
            (
                "Request the completed condo questionnaire for lenders (Fannie Mae 1076 / Freddie Mac 476) "
                "before making an offer: every conventional loan in this building needs it."
            ),
            (
                f"What share of the annual budget goes to reserves (lenders require at least {RESERVE_MIN_PERCENT}% "
                "from January 2027), and when was the last reserve study done?"
            ),
            (
                "Are critical repairs pending, and what do they cost per unit? How many owners are more than "
                "60 days behind on dues, and is the association in any litigation?"
            ),
        ]
    return questions


def build_report(parent_folio):
    """Everything the report needs for one condo, or None if the folio has no units."""
    fields = safe_fields(LAYERS["property"], PROPERTY_FIELDS)
    building = building_summary(parent_folio, fields)
    if not building:
        return None
    recert_fields = safe_fields(LAYERS["recert"], RECERT_FIELDS)
    building["recert"] = recert_records(building, recert_fields)
    building["recert_by_tower"] = recert_by_tower(building, building["recert"])
    permit_fields = safe_fields(LAYERS["ibuild_permits"], PERMIT_FIELDS)
    building["permits"] = permit_summary(building, permit_fields)
    point = building_point(building)
    building["flood"] = flood_zone(point)
    building["coast"] = coast_distance(point)
    add_next_due(building)  # needs coast; may turn "no record" into "Not due yet"
    building["recert_light"] = building_light(building["recert_by_tower"])
    building["dbpr"] = dbpr_info(building)
    building["questions"] = association_questions(building)
    return building


if __name__ == "__main__":
    folio = sys.argv[1] if len(sys.argv) > 1 else "0132310480001"  # The Grand
    try:
        building = build_report(folio)
    except RuntimeError as error:
        raise SystemExit(f"Could not build the report: {error}")
    if not building:
        raise SystemExit("Sin resultados para ese folio")
    building["folios"] = building["folios"][:5]  # only to keep the printout short
    print(json.dumps(building, indent=2, ensure_ascii=False))
