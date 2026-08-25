\set ON_ERROR_STOP on

BEGIN;

INSERT INTO paper_sessions(id,mode,model_version,status,config)
VALUES ('ci-market-immutability','replay','ci','running','{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

INSERT INTO market_data_journal(
  session_id,channel,sid,seq,market_ticker,exchange_ts_ms,
  received_at,received_epoch_ms,received_epoch_ns,received_monotonic_ns,
  raw_text,payload,payload_sha256
) VALUES (
  'ci-market-immutability','trade',1,1,'TEST-MARKET',NULL,
  '2026-08-20T12:00:00Z',1787227200000,1787227200000000000,1000,
  '{"type":"trade","msg":{"market_ticker":"TEST-MARKET"}}',
  '{"type":"trade","msg":{"market_ticker":"TEST-MARKET"}}'::jsonb,
  '7c9be2bc3f333de206d92e5fe290d05c63bedc3b2b2c1fd677d6533b59c463d0'
);

DO $$
BEGIN
  BEGIN
    UPDATE market_data_journal
       SET raw_text='mutated'
     WHERE session_id='ci-market-immutability';
    RAISE EXCEPTION 'expected UPDATE to market_data_journal to fail';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM market_data_journal
     WHERE session_id='ci-market-immutability';
    RAISE EXCEPTION 'expected DELETE from market_data_journal to fail';
  EXCEPTION
    WHEN SQLSTATE '55000' THEN NULL;
  END;
END;
$$;

ROLLBACK;
