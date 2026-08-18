-- PostgreSQL UNIQUE permits multiple NULL event_ticker values. Series-level rule
-- snapshots use event_ticker=NULL, so add a NULL-safe identity index.
CREATE UNIQUE INDEX IF NOT EXISTS settlement_rule_snapshots_identity_idx
  ON settlement_rule_snapshots(series_ticker, COALESCE(event_ticker, ''), rules_hash);
