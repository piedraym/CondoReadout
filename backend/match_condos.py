"""Match DBPR (Florida Department of Business and Professional Regulation) condo projects (Condo_MD.csv) to the
parent folios of the condos in the database, of every loaded jurisdiction. Run it after load_property.py."""

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from core import FUZZY_MIN, fuzzy_name, match_name
from database import connect
from rapidfuzz import fuzz, process

DATA = Path(__file__).parent / "data"
OUT = Path(__file__).parent / "out"

WORDS = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
    "NORTHEAST": "NE",
    "NORTHWEST": "NW",
    "SOUTHEAST": "SE",
    "SOUTHWEST": "SW",
    "STREET": "ST",
    "AVENUE": "AVE",
    "AV": "AVE",
    "DRIVE": "DR",
    "ROAD": "RD",
    "COURT": "CT",
    "PLACE": "PL",
    "TERRACE": "TER",
    "BOULEVARD": "BLVD",
    "LANE": "LN",
    "CIRCLE": "CIR",
}
DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def match_address(text):
    """Street part of an address in one spelling: '1901 NW SOUTH RIVER DRIVE' -> '1901 NW S RIVER DR'."""
    street = (text or "").upper().split(",")[0]  # drop city, state, zip
    street = re.sub(r"#.*", "", street).replace(".", " ")  # drop unit, N.W. -> N W
    street = re.sub(r"\b(\d+)(ST|ND|RD|TH)\b", r"\1", street)  # 15TH -> 15
    street = " ".join(WORDS.get(w, w) for w in street.split())
    return re.sub(r"\b([NS]) ([EW])\b", r"\1\2", street)  # N W -> NW


def loose_address(address):
    """Same address without directions: '6820 W FLAGLER ST' -> '6820 FLAGLER ST'."""
    return " ".join(w for w in address.split() if w not in DIRECTIONS)


# 1. Condos in the database: tower addresses and the name from the legal description
condos = {}
with connect() as db:
    for parent, code, name, zip_code, units in db.execute(
        "SELECT parent_folio, jurisdiction_code, legal_name, zip, units FROM condo"
    ):
        condos[parent] = {
            "code": code,
            "towers": set(),
            "units": units,
            "name": name,
            "zip": zip_code or "",
        }
    for parent, address in db.execute("SELECT parent_folio, address FROM tower"):
        condos[parent]["towers"].add(match_address(address))

# City names DBPR writes for the addresses of each jurisdiction (measured 2026-10-19)
DBPR_CITIES = {
    "01": {"MIAMI", "COCONUT GROVE"},
    "30": {
        "MIAMI",
        "NORTH MIAMI BEACH",
        "NORTH MIAMI",
        "MIAMI LAKES",
        "UNINCORPORATED",
        "KENDALL",
        "HIALEAH",
        "FISHER ISLAND",
    },
}
ZIPS = defaultdict(set)  # jurisdiction -> zips of its condos
for c in condos.values():
    ZIPS[c["code"]].add(c["zip"])

OFFICE = re.compile(r"\b(SUITE|STE|C/O)\b|#")  # management office, not the building


def zip_of(text):
    """Five-digit zip at the end of a DBPR address, or None."""
    found = re.search(r"(\d{5})(-\d{4})?\s*$", text or "")
    return found.group(1) if found else None


def city_like(text, code):
    """DBPR address names a city of the jurisdiction and has one of its zips."""
    parts = [x.strip() for x in (text or "").split(",")]
    return (
        len(parts) >= 3
        and parts[-2].upper() in DBPR_CITIES.get(code, set())
        and zip_of(text) in ZIPS[code]
    )


def first_number(text):
    """Keep the first house number of a range: '3124-26 SW 25 TER' -> '3124 SW 25 TER'."""
    return re.sub(r"^(\d+)\s*(-\s*\d+|,\s*\d+|&\s*\d+)+", r"\1", text.strip())


def name_numbers(name):
    """Numbers in a name, in one spelling: 'RIVERWOOD SEC III #012' -> {'3', '12'}."""
    return {str(int(w)) for w in fuzzy_name(name).split() if w.isdigit()}


def other_section(parent, dbpr_name):
    """Both names have numbers and share none: another section of the complex ('SEC 1' / 'SEC III')."""
    ours, theirs = name_numbers(condos[parent]["name"]), name_numbers(dbpr_name)
    return bool(ours and theirs and not ours & theirs)


# Lookups: key -> set of parent folios
by_address = defaultdict(set)
by_loose = defaultdict(set)
by_name = defaultdict(set)
for parent, c in condos.items():
    for tower in c["towers"]:
        by_address[tower].add(parent)
        by_loose[loose_address(tower)].add(parent)
    if c["name"]:
        by_name[match_name(c["name"])].add(parent)

# 2. DBPR projects in Miami-Dade: name, address, loose address, name with another zip
with (DATA / "Condo_MD.csv").open(encoding="latin-1") as f:
    projects = [r for r in csv.DictReader(f) if r["County"] == "Dade"]


# An address that several DBPR projects give (an office, or the sections of a complex)
# does not say which condo a project is
shared = Counter(
    match_address(first_number(p["Street City State Zip"])) for p in projects
)


def only(parents):
    """The parent folio if exactly one matches, else None (ambiguous or missing)."""
    return next(iter(parents)) if parents and len(parents) == 1 else None


matches = defaultdict(list)  # parent folio -> list of DBPR projects
methods = Counter()
review = []  # same name, but the DBPR address is outside the jurisdiction: check by hand
for p in projects:
    dbpr_address = p["Street City State Zip"]
    address = match_address(first_number(dbpr_address))
    if shared[address] > 1:
        by_addr = by_loose_addr = None
    else:
        by_addr = only(by_address.get(address))
        by_loose_addr = only(by_loose.get(loose_address(address)))
    by_nm = only(by_name.get(match_name(p["Condo Name"])))
    same_zip = by_nm and zip_of(dbpr_address) == condos[by_nm]["zip"]

    if by_nm and (same_zip or by_nm in (by_addr, by_loose_addr)):
        parent, method = by_nm, "name"
    elif by_addr:
        parent, method = by_addr, "address"
    elif by_loose_addr and zip_of(dbpr_address) == condos[by_loose_addr]["zip"]:
        parent, method = by_loose_addr, "loose address"
    elif by_nm and (
        city_like(dbpr_address, condos[by_nm]["code"]) or OFFICE.search(dbpr_address)
    ):
        parent, method = by_nm, "name, other zip"
    else:
        parent, method = None, None
        if by_nm:
            review.append(
                {"parent_folio": by_nm, "pa_name": condos[by_nm]["name"], **p}
            )
    if parent:
        matches[parent].append({**p, "method": method})
        methods[method] += 1


# 3. Fuzzy name pass: leftover projects vs leftover condos in the same zip
used = {m["Project Number"] for found in matches.values() for m in found}
leftover_by_zip = defaultdict(dict)  # zip -> {parent folio: name}
for parent, c in condos.items():
    if parent not in matches and c["name"]:
        leftover_by_zip[c["zip"]][parent] = fuzzy_name(c["name"])

for p in projects:
    if p["Project Number"] in used:
        continue
    choices = leftover_by_zip.get(zip_of(p["Street City State Zip"]))
    if not choices:
        continue
    best = process.extract(
        fuzzy_name(p["Condo Name"]), choices, scorer=fuzz.token_sort_ratio, limit=2
    )
    clear_winner = len(best) == 1 or best[1][1] < best[0][1]
    if (
        best[0][1] >= FUZZY_MIN
        and clear_winner
        and not other_section(best[0][2], p["Condo Name"])
    ):
        _, score, parent = best[0]
        matches[parent].append({**p, "method": f"fuzzy name {score:.0f}"})
        methods["fuzzy name"] += 1


# 4. A condo found by its exact name keeps only those projects. Several projects found only by
# address or a similar name are usually other sections of a complex: too unsure to keep
for parent, found in list(matches.items()):
    named = [m for m in found if m["method"].startswith("name")]
    if named:
        matches[parent] = named
    elif len(found) > 1:
        del matches[parent]
methods = Counter(
    "fuzzy name" if m["method"].startswith("fuzzy") else m["method"]
    for found in matches.values()
    for m in found
)


# 5. Summary and CSVs to review by hand
print(f"{len(condos)} condos, {len(matches)} matched to DBPR: {dict(methods)}")
for code in sorted(ZIPS):
    of_code = [p for p in condos if condos[p]["code"] == code]
    big = [p for p in of_code if condos[p]["units"] >= 11]
    print(
        f"  {code}: {sum(p in matches for p in of_code)} of {len(of_code)} matched,"
        f" {sum(p in matches for p in big)} of {len(big)} with 11+ units"
    )

print(
    f"condos with more than one DBPR project: {sum(len(v) > 1 for v in matches.values())}"
)

missing = [p for p in condos if p not in matches]
for label, minimum in (("11+", 11), ("51+", 51)):
    print(
        f"unmatched with {label} units: {sum(condos[p]['units'] >= minimum for p in missing)}"
    )

delinquent = [
    parent
    for parent, found in matches.items()
    if any(m["Secondary Status"] == "Delinquent" for m in found)
]
print(
    f"condos with a Delinquent DBPR project: {len(delinquent)}"
    f" ({sum(condos[p]['units'] >= 11 for p in delinquent)} with 11+ units)"
)

rows = []
for parent, c in sorted(condos.items(), key=lambda kv: -kv[1]["units"]):
    for m in matches.get(parent) or [None]:
        rows.append(
            {
                "parent_folio": parent,
                "pa_name": c["name"],
                "units": c["units"],
                "towers": " | ".join(sorted(c["towers"])),
                "project_number": m["Project Number"] if m else None,
                "dbpr_name": m["Condo Name"] if m else None,
                "dbpr_address": m["Street City State Zip"] if m else None,
                "dbpr_units": m["Units"] if m else None,
                "dbpr_status": m["Secondary Status"] if m else None,
                "method": m["method"] if m else None,
            }
        )
OUT.mkdir(exist_ok=True)
with (OUT / "condo_match.csv").open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
print(f"-> {OUT / 'condo_match.csv'}")

if review:
    with (OUT / "condo_review.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=review[0].keys())
        writer.writeheader()
        writer.writerows(review)
print(f"name matches to review by hand: {len(review)} -> {OUT / 'condo_review.csv'}")
