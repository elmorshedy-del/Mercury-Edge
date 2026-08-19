-- Step 4B: immutable hard-information journal.
--
-- Raw network captures are append-only. Derived interpretations are also
-- append-only and versioned; a new parser/evidence model writes a new
-- derivation instead of mutating history.

CREATE TABLE IF NOT EXISTS raw_source_journal (
  id bigserial PRIMARY KEY,
  capture_id text NOT NULL UNIQUE,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  source text NOT NULL,
  source_stream text NOT NULL,
  station_code text,
  observed_at timestamptz,
  source_published_at timestamptz,
  first_fetchable_at timestamptz,
  received_at timestamptz NOT NULL,
  received_epoch_ns numeric(20,0) NOT NULL,
  received_monotonic_ns numeric(20,0) NOT NULL,
  transport text NOT NULL,
  sequence_key text,
  content_type text NOT NULL,
  content_encoding text,
  raw_bytes bytea NOT NULL,
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS raw_source_journal_source_time_idx
  ON raw_source_journal(session_id,source,source_stream,received_at,id);
CREATE INDEX IF NOT EXISTS raw_source_journal_station_time_idx
  ON raw_source_journal(station_code,observed_at,received_at,id)
  WHERE station_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS raw_source_journal_payload_hash_idx
  ON raw_source_journal(payload_sha256);

-- Existing parsed weather rows become derived/normalized records linked back to
-- the exact immutable network capture. Nullable preserves old historical rows.
ALTER TABLE live_weather_journal
  ADD COLUMN IF NOT EXISTS raw_source_id bigint REFERENCES raw_source_journal(id) ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS live_weather_journal_raw_source_idx
  ON live_weather_journal(raw_source_id)
  WHERE raw_source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS evidence_derivations (
  evidence_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  station_code text NOT NULL,
  climate_date date NOT NULL,
  evidence_type text NOT NULL,
  trust text NOT NULL CHECK (trust IN ('benchmark_eligible','validation_only','research_only','rejected')),
  integrity_status text NOT NULL CHECK (
    integrity_status IN ('canonical','ambiguous','off_lattice','conflict','incomplete','invalid_window','unknown')
  ),
  proven_min_f integer,
  proven_max_f integer,
  possible_canonical_f jsonb NOT NULL DEFAULT '[]'::jsonb,
  raw_identifier text,
  observed_at timestamptz NOT NULL,
  source_published_at timestamptz,
  first_fetchable_at timestamptz,
  mercury_received_at timestamptz NOT NULL,
  mercury_interpreted_at timestamptz,
  parser_version text NOT NULL,
  evidence_model_version text NOT NULL,
  calendar_version text NOT NULL,
  derivation_payload jsonb NOT NULL,
  derivation_sha256 text NOT NULL CHECK (derivation_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS evidence_derivations_station_day_idx
  ON evidence_derivations(station_code,climate_date,mercury_received_at,evidence_id);
CREATE INDEX IF NOT EXISTS evidence_derivations_version_idx
  ON evidence_derivations(parser_version,evidence_model_version,calendar_version);

-- One evidence item may depend on multiple immutable source captures (required
-- for the future rolling-five-minute MADIS reconstruction).
CREATE TABLE IF NOT EXISTS evidence_source_links (
  evidence_id text NOT NULL REFERENCES evidence_derivations(evidence_id) ON DELETE RESTRICT,
  raw_source_id bigint NOT NULL REFERENCES raw_source_journal(id) ON DELETE RESTRICT,
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  relation text NOT NULL DEFAULT 'input',
  PRIMARY KEY (evidence_id,raw_source_id,relation),
  UNIQUE (evidence_id,ordinal,relation)
);
CREATE INDEX IF NOT EXISTS evidence_source_links_raw_idx
  ON evidence_source_links(raw_source_id,evidence_id);

-- Database-level immutability. Application bugs cannot rewrite or delete the
-- evidentiary record; corrected interpretations are appended under a new
-- version/evidence id.
CREATE OR REPLACE FUNCTION mercury_reject_immutable_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'immutable journal table % does not permit %', TG_TABLE_NAME, TG_OP
    USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS raw_source_journal_immutable ON raw_source_journal;
CREATE TRIGGER raw_source_journal_immutable
BEFORE UPDATE OR DELETE ON raw_source_journal
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();

DROP TRIGGER IF EXISTS evidence_derivations_immutable ON evidence_derivations;
CREATE TRIGGER evidence_derivations_immutable
BEFORE UPDATE OR DELETE ON evidence_derivations
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();

DROP TRIGGER IF EXISTS evidence_source_links_immutable ON evidence_source_links;
CREATE TRIGGER evidence_source_links_immutable
BEFORE UPDATE OR DELETE ON evidence_source_links
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();
