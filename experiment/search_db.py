"""The search box: find condos by address or name in PostgreSQL.

Same rules as the demo page (templates/search.html): every typed word must start a word of the
address or the name, directions (N, SW...) must match whole, and the results show first the
addresses that start with what was typed, then the names, then the rest.
"""

import re
import sys

from database import connect
from step0 import pretty_name

MAX_RESULTS = 8

# The same spelling the Property Appraiser uses: "North" -> "n", "Street" -> "st"
WORDS = {
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
    "street": "st",
    "avenue": "ave",
    "av": "ave",
    "drive": "dr",
    "road": "rd",
    "court": "ct",
    "place": "pl",
    "terrace": "ter",
    "boulevard": "blvd",
    "lane": "ln",
    "circle": "cir",
}
DIRECTIONS = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}


def tokens(text):
    """Words in one spelling: '1717 North Bayshore Drive' -> ['1717', 'n', 'bayshore', 'dr']."""
    words = re.sub(r"[.,#%_\\]", " ", (text or "").lower()).split()
    return [WORDS.get(w) or re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", w) for w in words]


def entries(parent_folio, condo, towers):
    """Rows of search_entry for one condo: one per address, or one by name if it has none."""
    rows = []
    for address in towers:  # summarize_units leaves "" only when the condo has no address
        shown = pretty_name(address) or condo["name"] or parent_folio
        name = condo["name"] or pretty_name(address)
        name_words = " " + " ".join(tokens(name)) + " "
        words = " " + " ".join(tokens(shown)) + name_words
        rows.append(
            (parent_folio, shown, name, condo["zip"], condo["units"], condo["unbuilt"], words, name_words)
        )
    return rows


def search(query, limit=MAX_RESULTS):
    """Condos that match what was typed, best first, as dicts for the search box."""
    words = tokens(query)
    if not words:
        return []
    # every typed word must start a word; a direction must be the whole word
    where = " AND ".join("words LIKE %s" for _ in words)
    params = [f"% {w}{' ' if w in DIRECTIONS else ''}%" for w in words]
    start = " " + " ".join(words) + "%"
    sql = (
        "SELECT address, name, zip, units, unbuilt, parent_folio FROM search_entry"
        f" WHERE {where} ORDER BY CASE"
        "   WHEN words LIKE %s THEN 0"  # the address starts with it
        "   WHEN name_words LIKE %s OR regexp_replace(name_words, '^ the ', ' ') LIKE %s THEN 1"
        "   ELSE 2 END, address COLLATE \"C\", name COLLATE \"C\" LIMIT %s"  # C: plain character order
    )
    with connect() as db:
        rows = db.execute(sql, [*params, start, start, start, limit]).fetchall()
    return [
        {"address": a, "name": n, "zip": z, "units": u, "unbuilt": b, "folio": f}
        for a, n, z, u, b, f in rows
    ]


if __name__ == "__main__":
    for row in search(" ".join(sys.argv[1:])):
        status = "  Not built yet" if row["unbuilt"] else ""
        print(f"{row['address']:<32} {row['name'] or '':<32} {row['units']:>5} units  {row['folio']}{status}")
