-- The original uniqueness key predates explicit WebSocket connection IDs. SIDs
-- and sequence numbers can be reused after reconnect, and an identical fresh
-- snapshot must never be silently discarded. Make the evidence journal
-- connection-aware.
ALTER TABLE market_data_journal
  DROP CONSTRAINT IF EXISTS market_data_journal_session_id_sid_seq_payload_sha256_key;

CREATE UNIQUE INDEX IF NOT EXISTS market_data_journal_ws_identity_idx
  ON market_data_journal(session_id, connection_id, sid, seq, payload_sha256)
  WHERE connection_id IS NOT NULL AND sid IS NOT NULL AND seq IS NOT NULL;

CREATE INDEX IF NOT EXISTS market_data_journal_chain_idx
  ON market_data_journal(session_id, connection_id, id);
