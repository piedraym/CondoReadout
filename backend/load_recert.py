"""Load the recertification records of one jurisdiction into PostgreSQL."""

import io
import re
import sys
from collections import defaultdict
from datetime import datetime

import openpyxl
import requests
from applog import get_logger
from core import LAYERS, RECERT_FIELDS, THIS_YEAR, ms_to_date, norm_address, query
from database import connect, ingest_run
from psycopg.types.json import Jsonb

log = get_logger("load_recert")

# City of Miami fields that have no column of their own
MIAMI_EXTRA = [
    "BuildingNumber",
    "RecertificationProcessStatus",
    "PlanStatusReason",
    "RequestType",
    "RequestStatus",
    "RequestResult",
]


def miami_records():
    """Records of the City of Miami layer, as rows of recert_record (without the jurisdiction)."""
    for f in query(LAYERS["recert"], "1=1", RECERT_FIELDS):
        r = f["attributes"]
        yield (
            str(r["OBJECTID"]),
            r["FolioNumber"],
            r["Address"],
            norm_address(r["Address"]),
            r["YearBuilt"] or None,
            r["RecertificateYear"],
            r["CertificationStatus"],
            r["PlanNumber"],
            r["PlanStatus"],
            ms_to_date(r["PlanStatusDate"]),
            ms_to_date(r["SubmittedDate"]),
            ms_to_date(r["UpdatedDate"]),
            Jsonb({key: r[key] for key in MIAMI_EXTRA if r[key] is not None}),
        )


# Unincorporated Miami-Dade: the County's Building Recertification Portal (RER). Its Export
# button returns every case as an Excel file, without folios, so cases are matched to towers
# by address. The Contact column (owners, associations) is never stored.
COUNTY_PORTAL = "https://www.miamidade.gov/Apps/RER/carbr/Home/"
MIN_CASES = 10_000  # the export has about 37,000: far fewer means an error page

# County case status -> the status words the rules in core use
COUNTY_STATUS = {
    "Issued": "Completed",
    "Exempt": "Exempted",
    "In Progress": "Pending",
    "Closed": "Closed",
    "Revoked": "Canceled",
}

ADDRESS_WORDS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "DRIVE": "DR", "ROAD": "RD",
    "COURT": "CT", "PLACE": "PL", "TERRACE": "TER", "TERR": "TER", "BOULEVARD": "BLVD",
    "LANE": "LN", "CIRCLE": "CIR", "PARKWAY": "PKWY", "HIGHWAY": "HWY",
}  # fmt: skip
STREET_TYPES = {
    "ST", "AVE", "DR", "RD", "CT", "PL", "TER", "BLVD", "LN", "CIR", "PKWY", "HWY",
    "WAY", "TRL", "PATH", "CSWY", "LOOP", "ROW", "PLZ", "SQ", "XING", "ISLE", "CV", "PT", "RUN", "WALK",
}  # fmt: skip


def house_numbers(text):
    """'7323-7353', '3124-26' or '7743,45,47' -> list of (low, high) house numbers."""
    spans = []
    for part in filter(None, re.split(r"[,&]", text)):
        low, _, high = part.partition("-")
        if not low.isdigit() or (high and not high.isdigit()):
            continue
        last = spans[-1][1] if spans else None
        if not high and last and len(low) < len(str(last)):  # 7743,45 -> 7745
            low = str(last)[: len(str(last)) - len(low)] + low
        if high and len(high) < len(low):  # 3124-26 -> 3126
            high = low[: len(low) - len(high)] + high
        spans.append((int(low), int(high or low)))
    return spans


def split_address(address):
    """'7323 - 7353 NW 56 ST 101-H' -> ([(7323, 7353)], 'NW 56 ST'); the unit after the street type is dropped."""
    text = (address or "").upper().replace(".", " ")
    text = re.sub(r"(\d)\s*([-,&])\s*(\d)", r"\1\2\3", text)  # '7323 - 7353' -> '7323-7353'
    text = re.sub(r"\b(\d+)(ST|ND|RD|TH)\b", r"\1", text)  # 15TH -> 15
    words = [ADDRESS_WORDS.get(w, w) for w in re.sub(r"#.*", "", text).split()]
    words = " ".join(words).replace("N E ", "NE ").replace("N W ", "NW ").replace("S E ", "SE ").replace("S W ", "SW ").split()
    numbers = house_numbers(words.pop(0)) if words and words[0][0].isdigit() else []
    street = []
    for w in words:
        street.append(w)
        if w in STREET_TYPES:
            break
    return numbers, " ".join(street)


def county_cases():
    """Every case of the County portal, as dicts with the Excel's column names."""
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (CondoReadout nightly load)"
    # the export needs a search in the same session first; it then returns every case
    session.get(COUNTY_PORTAL + "BrSearchResults", params={"RecertificationYear": THIS_YEAR}, timeout=120).raise_for_status()
    answer = session.post(COUNTY_PORTAL + "BrDownload", timeout=300)
    answer.raise_for_status()
    if not answer.content.startswith(b"PK"):  # an .xlsx file is a zip
        raise RuntimeError("the County portal did not return an Excel file")
    rows = openpyxl.load_workbook(io.BytesIO(answer.content), read_only=True).active.iter_rows(values_only=True)
    header = next(rows)
    cases = [dict(zip(header, row)) for row in rows]
    if len(cases) < MIN_CASES:
        raise RuntimeError(f"the County export has only {len(cases)} cases")
    return cases


def county_records():
    """County cases matched to the towers of code-30 condos: one row per case and tower it covers."""
    towers = defaultdict(list)  # street -> [(house number, tower address)]
    with connect() as db:
        for (address,) in db.execute(
            "SELECT t.address FROM tower t JOIN condo c USING (parent_folio) WHERE c.jurisdiction_code = '30'"
        ):
            numbers, street = split_address(address)
            if numbers:
                towers[street].append((numbers[0][0], address))
    cases = county_cases()
    for case in cases:
        numbers, street = split_address(case["PropertyAddress"])
        covered = {
            address
            for number, address in towers.get(street, ())
            if any(low <= number <= high for low, high in numbers)
        }
        enforcement = case["EnforcementStatus"] == "Yes"
        year = str(case["RecertificationYear"] or "")
        issued = case["RecertDate"]
        for address in sorted(covered):
            yield (
                f"{case['CaseNumber']}@{address}",  # a case covering three towers is stored three times
                None,  # the export has no folio
                address,  # the tower's address, so the report finds it like a City record
                norm_address(address),
                None,
                int(year) if year.isdigit() else None,
                COUNTY_STATUS.get(case["CaseStatus"], case["CaseStatus"]),
                case["CaseNumber"],
                "Enforcement" if enforcement else case["CaseStatus"],
                datetime.strptime(issued, "%d-%b-%y").date() if issued else None,
                None,
                None,
                Jsonb({"CaseAddress": case["PropertyAddress"], "BuildingNumber": case["BuildingNumber"],
                       "CaseStatus": case["CaseStatus"], "EnforcementStatus": case["EnforcementStatus"]}),
            )  # fmt: skip


# Each jurisdiction publishes its records in its own way: one function per code
SOURCES = {"01": miami_records, "30": county_records}


def load(code):
    """Replace the recertification records of the jurisdiction with what its source has now."""
    if code not in SOURCES:
        log.error("%s has no recertification source yet", code)
        raise SystemExit(1)
    with connect() as db, ingest_run(db, "recert", code) as run:
        rows = [(code, *record) for record in SOURCES[code]()]
        with db.cursor() as cur:
            cur.execute("DELETE FROM recert_record WHERE jurisdiction_code = %s", [code])
            cur.executemany(
                "INSERT INTO recert_record VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
        run.rows = len(rows)


if __name__ == "__main__":
    load(sys.argv[1] if len(sys.argv) > 1 else "01")
