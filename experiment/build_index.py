"""Address index for the search box: one row per address of each City of Miami condo.

Writes out/condo_index.json (the future database table) and out/search.html (a demo page).
"""

import json
import re
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from step0 import LAYERS, base_address, pretty_name, query

HERE = Path(__file__).parent
OUT = HERE / "out"

# 1. All condo units in the City of Miami, grouped by parent folio
units = [
    f["attributes"]
    for f in query(
        LAYERS["property"],
        "FOLIO LIKE '01%' AND PARENT_FOLIO IS NOT NULL",
        ["PARENT_FOLIO", "TRUE_SITE_ADDR", "TRUE_SITE_UNIT", "TRUE_SITE_ZIP_CODE", "LEGAL"],
    )
]
condos = defaultdict(lambda: {"addresses": set(), "units": 0, "name": None, "zip": None})
for u in units:
    c = condos[u["PARENT_FOLIO"]]
    c["units"] += 1
    c["zip"] = c["zip"] or (u["TRUE_SITE_ZIP_CODE"] or "")[:5]
    address = base_address(u).strip()
    if address:
        c["addresses"].add(address)
    if not c["name"]:
        m = re.match(r"^(.*?)\s+UNIT\b", u["LEGAL"] or "")
        c["name"] = pretty_name(m.group(1)) if m else None

# 2. One row per address; a condo without a name is shown by its address
index = []
for parent, c in condos.items():
    for address in c["addresses"]:
        index.append(
            {
                "address": pretty_name(address),
                "name": c["name"] or pretty_name(address),
                "zip": c["zip"],
                "units": c["units"],
                "folio": parent,
            }
        )
index.sort(key=lambda row: (row["address"], row["name"]))

OUT.mkdir(exist_ok=True)
(OUT / "condo_index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")

env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape())
(OUT / "search.html").write_text(env.get_template("search.html").render(index=index), encoding="utf-8")

print(f"{len(condos)} condos, {len(index)} addresses")
print(f"-> {OUT / 'condo_index.json'}")
print(f"-> {OUT / 'search.html'}")
