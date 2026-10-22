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
}
