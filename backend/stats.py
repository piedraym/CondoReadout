"""The queries behind the Stats page, by name. build_stats.py runs them every night and stores
their rows in the stat table; the API only reads that table, so the browser never sends SQL."""

# Which condos each part of the Dashboard counts, one rule per norm (see the notes in frontend/src/charts.js)
RECERTIFICATION = "c.units >= 3"  # County Sec. 8-11: every condo but houses and duplexes (1-2 units)
MILESTONE = "(c.estimate_floors >= 3 OR (c.estimate_floors IS NULL AND c.units >= 11))"  # state law: 3+ floors
FULL_REVIEW = "c.units >= 11"  # Fannie Mae / Freddie Mac Full Review: more than 10 units

# What a chart groups by inside each area
LIGHT = "s.light"
SIRS = """CASE
    WHEN s.sirs_reported THEN 'reported'
    WHEN EXISTS (SELECT 1 FROM condo_dbpr m WHERE m.parent_folio = c.parent_folio AND NOT m.needs_review)
        THEN 'not_in_registry'
    ELSE 'no_dbpr'
END"""

# When the next recertification is due: overdue, each of the next five years, or later
DUE = """CASE
    WHEN s.next_due_year < extract(year FROM current_date) THEN 'overdue'
    WHEN s.next_due_year > extract(year FROM current_date) + 4 THEN 'later'
    ELSE s.next_due_year::text
END"""


# Count of condos by area and by one value: one row per (jurisdiction, value)
COUNT_BY = """
    SELECT j.name AS jurisdiction, {value} AS {name}, count(*) AS condos
    FROM condo_status s
    JOIN condo c USING (parent_folio)
    JOIN jurisdiction j ON j.code = s.jurisdiction_code
    WHERE {where}
    GROUP BY 1, 2
    ORDER BY 1, 2
"""

QUERIES = {
    # recertification traffic light of every condo it applies to
    "recert_lights_by_jurisdiction": COUNT_BY.format(value=LIGHT, name="light", where=RECERTIFICATION),
    # why the condos are green, yellow or red: the recertification status of their worst tower
    "recert_labels_by_jurisdiction": f"""
        SELECT j.name AS jurisdiction, s.label, s.light, count(*) AS condos
        FROM condo_status s
        JOIN condo c USING (parent_folio)
        JOIN jurisdiction j ON j.code = s.jurisdiction_code
        WHERE {RECERTIFICATION}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2
    """,
    # when the next recertification is due (estimated for towers without a record), condos that have a date
    "recert_due_by_jurisdiction": COUNT_BY.format(
        value=DUE, name="due", where=f"{RECERTIFICATION} AND s.next_due_year IS NOT NULL"
    ),
    # traffic light of the buildings under the state milestone law
    "milestone_lights_by_jurisdiction": COUNT_BY.format(value=LIGHT, name="light", where=MILESTONE),
    # SIRS filed with DBPR, of the buildings under the state milestone law
    "milestone_sirs_by_jurisdiction": COUNT_BY.format(value=SIRS, name="sirs", where=MILESTONE),
    # traffic light of the condos with 11+ units: the Fannie Mae / Freddie Mac Full Review cut
    "lights_by_jurisdiction": COUNT_BY.format(value=LIGHT, name="light", where=FULL_REVIEW),
}

# The condos behind one bar segment, listed under the chart when the segment is clicked.
# Same filter as the chart's query; the API passes the segment's area and value as parameters.
CONDO_LIST = """
    SELECT c.parent_folio AS folio, c.name, c.units, min(t.address) AS address
    FROM condo_status s
    JOIN condo c USING (parent_folio)
    JOIN jurisdiction j ON j.code = s.jurisdiction_code
    LEFT JOIN tower t USING (parent_folio)
    WHERE {where} AND j.name = %(jurisdiction)s AND {value} = %(value)s
    GROUP BY 1, 2, 3
    ORDER BY c.units DESC, c.name
"""
LISTS = {
    "recert_lights_by_jurisdiction": CONDO_LIST.format(value=LIGHT, where=RECERTIFICATION),
    "milestone_lights_by_jurisdiction": CONDO_LIST.format(value=LIGHT, where=MILESTONE),
    "milestone_sirs_by_jurisdiction": CONDO_LIST.format(value=SIRS, where=MILESTONE),
    "lights_by_jurisdiction": CONDO_LIST.format(value=LIGHT, where=FULL_REVIEW),
    "recert_labels_by_jurisdiction": CONDO_LIST.format(value="s.label", where=RECERTIFICATION),
    "recert_due_by_jurisdiction": CONDO_LIST.format(
        value=DUE, where=f"{RECERTIFICATION} AND s.next_due_year IS NOT NULL"
    ),
}
