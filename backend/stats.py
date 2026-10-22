"""The queries behind the Stats page, by name. build_stats.py runs them every night and stores
their rows in the stat table; the API only reads that table, so the browser never sends SQL."""

QUERIES = {
    # traffic light of the condos with 11+ units, by jurisdiction
    "lights_by_jurisdiction": """
        SELECT j.name AS jurisdiction, s.light, count(*) AS condos
        FROM condo_status s
        JOIN condo c USING (parent_folio)
        JOIN jurisdiction j ON j.code = s.jurisdiction_code
        WHERE c.units >= 11
        GROUP BY 1, 2
        ORDER BY 1, 2
    """,
    # recertification traffic light of every condo it applies to (all but 1-2 units: houses and duplexes are exempt)
    "recert_lights_by_jurisdiction": """
        SELECT j.name AS jurisdiction, s.light, count(*) AS condos
        FROM condo_status s
        JOIN condo c USING (parent_folio)
        JOIN jurisdiction j ON j.code = s.jurisdiction_code
        WHERE c.units >= 3
        GROUP BY 1, 2
        ORDER BY 1, 2
    """,
    # traffic light of the buildings under the state milestone law (3+ floors; floors unknown: 11+ units)
    "milestone_lights_by_jurisdiction": """
        SELECT j.name AS jurisdiction, s.light, count(*) AS condos
        FROM condo_status s
        JOIN condo c USING (parent_folio)
        JOIN jurisdiction j ON j.code = s.jurisdiction_code
        WHERE c.estimate_floors >= 3 OR (c.estimate_floors IS NULL AND c.units >= 11)
        GROUP BY 1, 2
        ORDER BY 1, 2
    """,
}


# The condos behind one bar segment, listed under the chart when the segment is clicked.
# Same filter as the chart's query; the API passes the segment's area and light as parameters.
CONDO_LIST = """
    SELECT c.parent_folio AS folio, c.name, c.units, min(t.address) AS address
    FROM condo_status s
    JOIN condo c USING (parent_folio)
    JOIN jurisdiction j ON j.code = s.jurisdiction_code
    LEFT JOIN tower t USING (parent_folio)
    WHERE {where} AND j.name = %(jurisdiction)s AND s.light = %(light)s
    GROUP BY 1, 2, 3
    ORDER BY c.units DESC, c.name
"""
LISTS = {
    "recert_lights_by_jurisdiction": CONDO_LIST.format(where="c.units >= 3"),
    "lights_by_jurisdiction": CONDO_LIST.format(where="c.units >= 11"),
    "milestone_lights_by_jurisdiction": CONDO_LIST.format(
        where="(c.estimate_floors >= 3 OR (c.estimate_floors IS NULL AND c.units >= 11))"
    ),
}
