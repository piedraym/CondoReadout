-- CondoReadout database. It stores facts from the public sources;
-- the traffic light and the questions are computed in Python when a report is asked for.
-- Running this file again deletes every table and its data: the loaders fill them back.

DROP TABLE IF EXISTS stat, condo_status, violation, search_entry, sirs_report, condo_dbpr, dbpr_project, permit, recert_record, condo_location, unit, tower, condo, ingest_run, jurisdiction CASCADE;

-- One row per municipality. The code is the first two digits of the folio.
CREATE TABLE jurisdiction (
    code                     text PRIMARY KEY CHECK (code ~ '^[0-9]{2}$'),
    name                     text NOT NULL,
    first_recert_age         integer NOT NULL DEFAULT 30,
    first_recert_age_coastal integer NOT NULL DEFAULT 25,
    recert_every             integer NOT NULL DEFAULT 10,
    recert_rule_official     boolean NOT NULL DEFAULT false,  -- the rule above applies as written; false = our estimate
    violations_public        boolean,  -- publishes its code cases: true, false, or NULL = not checked yet
    active                   boolean NOT NULL DEFAULT false  -- true once its condos are loaded
);

INSERT INTO jurisdiction (code, name, recert_rule_official, violations_public) VALUES
    ('01', 'City of Miami', false, false),  -- the City's web page still says 40 years
    ('30', 'Unincorporated Miami-Dade County', true, true),  -- County Sec. 8-11(f) applies directly
    ('02', 'City of Miami Beach', true, true);  -- applies Sec. 8-11(f); code cases not loaded yet

-- One row per load, to know when each source was last refreshed and whether it failed.
CREATE TABLE ingest_run (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source            text NOT NULL,  -- 'property', 'recert', 'permits'...
    jurisdiction_code text REFERENCES jurisdiction,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    row_count         integer,
    data_date         date NOT NULL DEFAULT current_date,  -- day the data is from: the load day, or the file's date
    status            text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'failed')),
    error             text
);

-- One row per condominium (parent folio).
CREATE TABLE condo (
    parent_folio      text PRIMARY KEY CHECK (parent_folio ~ '^[0-9]{13}$'),
    jurisdiction_code text NOT NULL REFERENCES jurisdiction,
    legal_name        text,              -- 'THE GRAND FKA VENETIA', from the legal description
    name              text,              -- 'The Grand'
    zip               text,
    units             integer NOT NULL,
    unit_types        jsonb NOT NULL,    -- {"residential": 1046, "retail": 141}
    estimate_floors   integer,           -- NULL when the unit numbers do not show floors
    year_built        integer,           -- oldest tower; NULL when unknown
    unbuilt           boolean NOT NULL,
    master_in_pa      boolean NOT NULL,  -- the parent folio has its own row in the property layer
    lon               double precision,
    lat               double precision,
    loaded_at         timestamptz NOT NULL DEFAULT now()
);

-- One row per building of a condominium: its units share a street address ('' when unknown).
CREATE TABLE tower (
    parent_folio text NOT NULL REFERENCES condo ON DELETE CASCADE,
    address      text NOT NULL,
    units        integer NOT NULL,
    year_built   integer,
    PRIMARY KEY (parent_folio, address)
);

-- One row per unit. No owner names: they are never requested.
CREATE TABLE unit (
    folio         text PRIMARY KEY CHECK (folio ~ '^[0-9]{13}$'),
    parent_folio  text NOT NULL,
    tower_address text,           -- NULL: no address, in a condo whose other units have one
    unit_number   text,
    dor_desc      text,           -- land use from the Property Appraiser
    unit_type     text NOT NULL,  -- 'residential', 'office', 'retail'...
    year_built    integer,
    FOREIGN KEY (parent_folio) REFERENCES condo ON DELETE CASCADE,  -- also units with no tower
    FOREIGN KEY (parent_folio, tower_address) REFERENCES tower ON DELETE CASCADE
);

CREATE INDEX unit_parent_folio ON unit (parent_folio);


-- Flood zone and distance to the coast of each condo, looked up once at its location.
-- It is a separate table with no foreign key, so reloading the condos does not erase it:
-- these lookups are slow and the answers rarely change.
CREATE TABLE condo_location (
    parent_folio       text PRIMARY KEY,
    lon                double precision NOT NULL,  -- the point the lookups were made at
    lat                double precision NOT NULL,
    flood_zone         text,     -- FEMA zone: 'AE', 'X'...; NULL when the point is in no zone
    flood_subtype      text,
    flood_bfe          numeric,  -- base flood elevation in feet, only in some zones
    coast_within_miles numeric,  -- smallest of 0.25, 0.5, 1, 2, 3 with shoreline; NULL = farther than 3
    checked_at         timestamptz NOT NULL DEFAULT now()
);

-- Recertification records, all buildings of the jurisdiction (not only condos).
-- Every jurisdiction fills the same columns from its own source; what does not fit goes in extra.
-- A record belongs to a condo by its folio (the parent's or a unit's) or by its address.
CREATE TABLE recert_record (
    jurisdiction_code text NOT NULL REFERENCES jurisdiction,
    source_id         text NOT NULL,  -- the record's id in its source (OBJECTID in Miami)
    folio             text,
    address           text,           -- as the source writes it
    match_address     text,           -- in one spelling, to compare with the towers
    year_built        integer,
    recert_year       integer,        -- year the recertification is due
    status            text,           -- 'Completed', 'Pending', 'Exempted', 'Canceled'
    plan_number       text,
    plan_status       text,
    plan_status_date  date,
    submitted_date    date,
    updated_date      date,
    extra             jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (jurisdiction_code, source_id)
);

CREATE INDEX recert_record_folio ON recert_record (folio);
CREATE INDEX recert_record_match_address ON recert_record (match_address);

-- Building permits, all properties of the jurisdiction. No project names: they are never requested.
CREATE TABLE permit (
    jurisdiction_code text NOT NULL REFERENCES jurisdiction,
    permit_number     text NOT NULL,
    folio             text,
    address           text,
    permit_type       text,  -- 'MECHANICAL ELEVATORS', 'ROOFING'...
    scope             text,
    status            text,  -- 'Final', 'Active', 'Expired', 'Revoked', 'Hold'...
    issued_date       date,
    extra             jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (jurisdiction_code, permit_number)
);

CREATE INDEX permit_folio ON permit (folio);

-- Condominium projects registered with the State (DBPR file Condo_MD.csv), every county in the file.
-- The managing entity columns of the file are not loaded.
CREATE TABLE dbpr_project (
    project_number   text PRIMARY KEY,
    file_number      text,
    name             text NOT NULL,
    county           text,
    address          text,   -- street, city, state and zip as DBPR writes them
    units            integer,
    recorded_date    date,
    primary_status   text,
    secondary_status text    -- 'Recorded', 'Delinquent'...
);

-- Which DBPR projects belong to which condo, found by match_condos.py.
-- No foreign key to condo, so reloading the condos does not erase the matches.
CREATE TABLE condo_dbpr (
    parent_folio   text NOT NULL,
    project_number text NOT NULL REFERENCES dbpr_project,
    method         text,              -- 'name', 'address', 'fuzzy name 91'...
    needs_review   boolean NOT NULL,  -- same name but another city: not used until checked by hand
    PRIMARY KEY (parent_folio, project_number)
);

-- SIRS reports filed with DBPR in Miami-Dade, one row per report, matched to a project when possible.
CREATE TABLE sirs_report (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period           text NOT NULL,  -- 'before July 2025' or 'July 2025+'
    project_name     text,
    association_name text,
    zip              text,
    project_number   text REFERENCES dbpr_project,  -- NULL when no project was found
    method           text                            -- 'exact' or 'fuzzy'
);

CREATE INDEX sirs_report_project_number ON sirs_report (project_number);


-- The search box: one row per address of each condo (by name only when it has no address).
-- Filled by load_property.py with the same words the search uses (see search_db.py).
CREATE TABLE search_entry (
    parent_folio text NOT NULL REFERENCES condo ON DELETE CASCADE,
    address      text NOT NULL,  -- as shown: '1717 N Bayshore Dr'
    name         text,           -- as shown: 'The Grand'
    zip          text,
    units        integer NOT NULL,
    unbuilt      boolean NOT NULL,  -- every unit still vacant: shown as "Not built yet"
    words        text NOT NULL,  -- ' 1717 n bayshore dr the grand ': address and name, one spelling
    name_words   text NOT NULL   -- ' the grand '
);


-- Code and building violation cases, all properties of the jurisdiction (not only condos).
-- One row per case and problem: a code case can list several problems.
-- The violator's name is never requested.
CREATE TABLE violation (
    jurisdiction_code text NOT NULL REFERENCES jurisdiction,
    source            text NOT NULL,  -- 'building' (building code) or 'code' (code compliance)
    case_number       text NOT NULL,
    case_type         text NOT NULL,  -- 'Unsafe Structure', 'Expired Permit'... or the code problem
    folio             text,
    address           text,
    status            text,           -- 'Open', 'Closed', 'Lien'...
    opened_date       date,
    closed_date       date,
    permit_number     text,           -- the expired permit, in 'Expired Permit' cases
    extra             jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (jurisdiction_code, source, case_number, case_type)
);

CREATE INDEX violation_folio ON violation (folio);


-- What the Stats page shows, computed by build_stats.py after the nightly loads.
-- The traffic light of each condo: the report computes it in Python, so it is stored here for SQL.
CREATE TABLE condo_status (
    parent_folio      text PRIMARY KEY REFERENCES condo(parent_folio) ON DELETE CASCADE,
    jurisdiction_code text NOT NULL REFERENCES jurisdiction(code),
    light             text NOT NULL CHECK (light IN ('green', 'yellow', 'red')),
    label             text NOT NULL,  -- label of the worst tower, e.g. 'Pending: enforcement', 'no record'
    sirs_reported     boolean NOT NULL,
    dbpr_delinquent   boolean NOT NULL,
    open_violations   integer NOT NULL,
    next_due_year     integer  -- year the first tower is due (estimated without a record); NULL when no date
);

-- The result of each named query of stats.py, as JSON rows.
CREATE TABLE stat (
    name        text PRIMARY KEY,
    rows        jsonb NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now()
);


