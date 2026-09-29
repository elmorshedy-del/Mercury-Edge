\set ON_ERROR_STOP on
BEGIN;

INSERT INTO paper_sessions(id,mode,model_version,status,config)
VALUES ('h4d-sql-test','replay','h4d-test','running','{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

INSERT INTO raw_source_journal(
  id,capture_id,session_id,source,source_stream,station_code,
  received_at,received_epoch_ns,received_monotonic_ns,transport,
  content_type,raw_bytes,payload_sha256,metadata
) VALUES (
  920001,'raw:h4d-exchange','h4d-sql-test','KALSHI_REST','settled_event_detail','KNYC',
  '2026-08-22T12:00:00Z',1,1,'https_poll','application/json',decode('7b7d','hex'),repeat('a',64),'{}'::jsonb
);

INSERT INTO exchange_market_settlements(
  exchange_settlement_id,session_id,event_ticker,station_code,climate_date,
  raw_source_id,rules_hash,rule_source_name,captured_at,market_results,
  parser_version,settlement_payload,settlement_sha256
) VALUES (
  'exchange-settlement:h4d-v1','h4d-sql-test','KXHIGHNY-26AUG21','KNYC','2026-08-21',
  920001,repeat('b',64),'The Weather Company','2026-08-22T12:00:00Z',
  '[{"market_ticker":"M1","result":"no"},{"market_ticker":"M2","result":"yes"}]'::jsonb,
  'exchange-market-settlement-v1','{"settled":true}'::jsonb,repeat('c',64)
);

INSERT INTO settlement_audit_results(
  audit_id,session_id,exchange_settlement_id,severity,status,finding_code,
  station_code,climate_date,market_ticker,auditor_version,details,audit_payload,audit_sha256
) VALUES (
  'audit:h4d-exchange-pass','h4d-sql-test','exchange-settlement:h4d-v1','info','pass',
  'IMPOSSIBLE_BUCKET_SETTLED_NO','KNYC','2026-08-21','M1','settlement-auditor-v1',
  '{}'::jsonb,'{"audit":true}'::jsonb,repeat('d',64)
);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM settlement_audit_results
    WHERE audit_id='audit:h4d-exchange-pass'
      AND settlement_id IS NULL
      AND validation_id IS NULL
      AND exchange_settlement_id='exchange-settlement:h4d-v1'
  ) THEN
    RAISE EXCEPTION 'exchange settlement source check regression';
  END IF;

  BEGIN
    UPDATE exchange_market_settlements
       SET rule_source_name='mutated'
     WHERE exchange_settlement_id='exchange-settlement:h4d-v1';
    RAISE EXCEPTION 'exchange_market_settlements UPDATE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM exchange_market_settlements
     WHERE exchange_settlement_id='exchange-settlement:h4d-v1';
    RAISE EXCEPTION 'exchange_market_settlements DELETE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;
END $$;

ROLLBACK;
