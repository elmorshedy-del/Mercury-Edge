\set ON_ERROR_STOP on

INSERT INTO paper_sessions(id,mode,model_version,status,config)
VALUES ('ci-hard-state-timeline','replay','ci','running','{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

INSERT INTO raw_source_journal(
  capture_id,session_id,source,source_stream,station_code,observed_at,
  received_at,received_epoch_ns,received_monotonic_ns,transport,content_type,
  raw_bytes,payload_sha256,metadata
) VALUES (
  'raw:hard-state-ci','ci-hard-state-timeline','NOAA_AWC','metar_json','KPHL',
  '2026-08-18T18:54:00Z','2026-08-18T18:55:00Z',1770000000000000001,
  124,'https_poll','application/json',convert_to('{"rawOb":"KPHL T0311"}','UTF8'),
  'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','{}'::jsonb
) ON CONFLICT (capture_id) DO NOTHING;

INSERT INTO evidence_derivations(
  evidence_id,session_id,station_code,climate_date,evidence_type,trust,
  integrity_status,proven_min_f,proven_max_f,possible_canonical_f,
  raw_identifier,observed_at,mercury_received_at,parser_version,
  evidence_model_version,calendar_version,derivation_payload,derivation_sha256
) VALUES (
  'evidence:hard-state-ci','ci-hard-state-timeline','KPHL','2026-08-18',
  'asos_t_group_current','benchmark_eligible','canonical',88,88,'[88]'::jsonb,
  'T0311','2026-08-18T18:54:00Z','2026-08-18T18:55:00Z',
  'asos-metar-evidence-v1','raw-asos-lattice-v1','lst-climate-calendar-v1',
  '{"proof":88}'::jsonb,'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
) ON CONFLICT (evidence_id) DO NOTHING;

INSERT INTO evidence_source_links(evidence_id,raw_source_id,ordinal,relation)
SELECT 'evidence:hard-state-ci',id,0,'input'
FROM raw_source_journal WHERE capture_id='raw:hard-state-ci'
ON CONFLICT DO NOTHING;

INSERT INTO hard_state_applications(
  application_id,session_id,station_code,climate_date,evidence_id,status,reason,
  known_at,proven_min_f,prior_bound_f,resulting_bound_f,evidence_type,
  accumulator_version,calendar_version,application_payload,application_sha256
) VALUES (
  'hard-app:ci','ci-hard-state-timeline','KPHL','2026-08-18','evidence:hard-state-ci',
  'transition','initial_bound','2026-08-18T18:55:00Z',88,NULL,88,
  'asos_t_group_current','hard-state-accumulator-v1','lst-climate-calendar-v1',
  '{"status":"transition"}'::jsonb,
  'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'
) ON CONFLICT (application_id) DO NOTHING;

INSERT INTO hard_state_transitions(
  state_id,session_id,station_code,climate_date,proven_daily_high_min_f,
  first_known_at,transition_evidence_id,supporting_evidence_ids,
  state_model_version,calendar_version,transition_payload,transition_sha256
) VALUES (
  'hard-state:ci','ci-hard-state-timeline','KPHL','2026-08-18',88,
  '2026-08-18T18:55:00Z','evidence:hard-state-ci','["evidence:hard-state-ci"]'::jsonb,
  'hard-state-accumulator-v1','lst-climate-calendar-v1','{"bound":88}'::jsonb,
  'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
) ON CONFLICT (state_id) DO NOTHING;

DO $$
BEGIN
  BEGIN
    UPDATE hard_state_applications
       SET resulting_bound_f=89
     WHERE application_id='hard-app:ci';
    RAISE EXCEPTION 'hard_state_applications UPDATE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM hard_state_applications WHERE application_id='hard-app:ci';
    RAISE EXCEPTION 'hard_state_applications DELETE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    UPDATE hard_state_transitions
       SET proven_daily_high_min_f=89
     WHERE state_id='hard-state:ci';
    RAISE EXCEPTION 'hard_state_transitions UPDATE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM hard_state_transitions WHERE state_id='hard-state:ci';
    RAISE EXCEPTION 'hard_state_transitions DELETE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;
END;
$$;

SELECT 'hard_state_timeline_ok' AS result;
