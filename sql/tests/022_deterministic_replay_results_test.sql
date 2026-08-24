\set ON_ERROR_STOP on

BEGIN;

INSERT INTO paper_sessions(id,mode,model_version,status,config)
VALUES ('ci-replay-result-source','paper_live','ci-source-v1','stopped','{}'::jsonb);

INSERT INTO deterministic_replay_results(
  replay_result_id,source_session_id,manifest_id,replay_policy,station_code,
  climate_date,event_ticker,source_input_sha256,version_bundle,
  version_bundle_sha256,execution_config,execution_config_sha256,
  hard_state_output_sha256,execution_output_sha256,settlement_grade,
  settlement_grade_sha256,replay_payload,replay_payload_sha256,replay_model_version
) VALUES (
  'replay-result:ci-one','ci-replay-result-source','manifest:ci','benchmark','KNYC',
  DATE '2026-08-21','KXHIGHNY-26AUG21',repeat('a',64),'{}'::jsonb,
  repeat('b',64),'{}'::jsonb,repeat('c',64),
  repeat('d',64),repeat('e',64),'{}'::jsonb,
  repeat('f',64),'{}'::jsonb,repeat('1',64),'deterministic-replay-result-v1'
);

-- A revised settlement grade for the same source/manifest/config/version must
-- coexist append-only rather than forcing mutation of the earlier result.
INSERT INTO deterministic_replay_results(
  replay_result_id,source_session_id,manifest_id,replay_policy,station_code,
  climate_date,event_ticker,source_input_sha256,version_bundle,
  version_bundle_sha256,execution_config,execution_config_sha256,
  hard_state_output_sha256,execution_output_sha256,settlement_grade,
  settlement_grade_sha256,replay_payload,replay_payload_sha256,replay_model_version
) VALUES (
  'replay-result:ci-two','ci-replay-result-source','manifest:ci','benchmark','KNYC',
  DATE '2026-08-21','KXHIGHNY-26AUG21',repeat('a',64),'{}'::jsonb,
  repeat('b',64),'{}'::jsonb,repeat('c',64),
  repeat('d',64),repeat('e',64),'{"revision":2}'::jsonb,
  repeat('2',64),'{"revision":2}'::jsonb,repeat('3',64),'deterministic-replay-result-v1'
);

DO $$
DECLARE
  n integer;
BEGIN
  SELECT count(*) INTO n
  FROM deterministic_replay_results
  WHERE source_session_id='ci-replay-result-source'
    AND manifest_id='manifest:ci'
    AND execution_config_sha256=repeat('c',64)
    AND replay_model_version='deterministic-replay-result-v1';
  IF n <> 2 THEN
    RAISE EXCEPTION 'expected two append-only settlement revisions, found %', n;
  END IF;
END;
$$;

DO $$
BEGIN
  BEGIN
    UPDATE deterministic_replay_results
       SET replay_payload='{"mutated":true}'::jsonb
     WHERE replay_result_id='replay-result:ci-one';
    RAISE EXCEPTION 'UPDATE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;
END;
$$;

DO $$
BEGIN
  BEGIN
    DELETE FROM deterministic_replay_results
     WHERE replay_result_id='replay-result:ci-one';
    RAISE EXCEPTION 'DELETE unexpectedly succeeded';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;
END;
$$;

ROLLBACK;
