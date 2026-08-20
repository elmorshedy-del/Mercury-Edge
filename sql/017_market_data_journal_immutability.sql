-- Raw-first information architecture: the Kalshi WebSocket journal is source
-- evidence and must be immutable for the same reason as raw weather captures.
-- Reconstructed books, reaction features and backtests are disposable; the raw
-- exchange messages they depend on are not.

DROP TRIGGER IF EXISTS market_data_journal_immutable ON market_data_journal;
CREATE TRIGGER market_data_journal_immutable
BEFORE UPDATE OR DELETE ON market_data_journal
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();
