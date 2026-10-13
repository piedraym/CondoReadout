-- infoRealtor database. It stores facts from the public sources;
-- the traffic light and the questions are computed in Python when a report is asked for.
-- Running this file again deletes every table and its data: the loaders fill them back.

DROP TABLE IF EXISTS unit, tower, condo, ingest_run, jurisdiction CASCADE;

-- One row per municipality. The code is the first two digits of the folio.
CREATE TABLE jurisdiction (
    code                     text PRIMARY KEY CHECK (code ~ '^[0-9]{2}$'),
    name                     text NOT NULL,
    first_recert_age         integer NOT NULL DEFAULT 30,
    first_recert_age_coastal integer NOT NULL DEFAULT 25,
    recert_every             integer NOT NULL DEFAULT 10,
    active                   boolean NOT NULL DEFAULT false  -- true once its condos are loaded
);

INSERT INTO jurisdiction (code, name) VALUES
    ('01', 'City of Miami'),
    ('30', 'Unincorporated Miami-Dade County');

-- One row per load, to know when each source was last refreshed and whether it failed.
CREATE TABLE ingest_run (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source            text NOT NULL,  -- 'property', 'recert', 'permits'...
    jurisdiction_code text REFERENCES jurisdiction,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    row_count         integer,
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
    tower_address text NOT NULL,
    unit_number   text,
    dor_desc      text,           -- land use from the Property Appraiser
    unit_type     text NOT NULL,  -- 'residential', 'office', 'retail'...
    year_built    integer,
    FOREIGN KEY (parent_folio, tower_address) REFERENCES tower ON DELETE CASCADE
);

CREATE INDEX unit_parent_folio ON unit (parent_folio);
