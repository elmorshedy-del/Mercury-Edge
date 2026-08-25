\set ON_ERROR_STOP on

INSERT INTO paper_sessions(id,mode,model_version,status,config)
VALUES ('ci-immutable-journal','replay','ci','running','{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

INSERT INTO raw_source_journal(
  capture_id,session_id,source,source_stream,station_code,observed_at,
  received_at,received_epoch_ns,received_monotonic_ns,transport,content_type,
  raw_bytes,payload_sha256,metadata
) VALUES (
  'raw:ci','ci-immutable-journal','NOAA_AWC','metar_json','KPHL',
  '2026-08-18T18:54:00Z','2026-08-18T18:55:00Z',1770000000000000000,
  123,'https_poll','application/json',convert_to('{"rawOb":"KPHL ..."}','UTF8'),
  '1a509322811d739c8ede19ff4094e52ff514c64980c5da8706630bbe0fa35da9','{}'::jsonb
) ON CONFLICT (capture_id) DO NOTHING;

DO $$
BEGIN
  BEGIN
    UPDATE raw_source_journal SET metadata='{"mutated":true}'::jsonb WHERE capture_id='raw:ci';
    RAISE EXCEPTION 'raw_source_journal UPDATE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM raw_source_journal WHERE capture_id='raw:ci';
    RAISE EXCEPTION 'raw_source_journal DELETE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;
END;
$$;

INSERT INTO evidence_derivations(
  evidence_id,session_id,station_code,climate_date,evidence_type,trust,
  integrity_status,proven_min_f,proven_max_f,possible_canonical_f,
  raw_identifier,observed_at,mercury_received_at,parser_version,
  evidence_model_version,calendar_version,derivation_payload,derivation_sha256
) VALUES (
  'evidence:ci','ci-immutable-journal','KPHL','2026-08-18','asos_t_group_current',
  'benchmark_eligible','canonical',88,88,'[88]'::jsonb,'T0311',
  '2026-08-18T18:54:00Z','2026-08-18T18:55:00Z','metar-v1','proof-v1','lst-v1',
  '{"proof":88}'::jsonb,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
) ON CONFLICT (evidence_id) DO NOTHING;

INSERT INTO evidence_source_links(evidence_id,raw_source_id,ordinal,relation)
SELECT 'evidence:ci',id,0,'input' FROM raw_source_journal WHERE capture_id='raw:ci'
ON CONFLICT DO NOTHING;

DO $$
BEGIN
  BEGIN
    UPDATE evidence_derivations SET proven_min_f=89 WHERE evidence_id='evidence:ci';
    RAISE EXCEPTION 'evidence_derivations UPDATE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM evidence_source_links WHERE evidence_id='evidence:ci';
    RAISE EXCEPTION 'evidence_source_links DELETE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;
END;
$$;

SELECT 'immutable_journal_ok' AS result;
