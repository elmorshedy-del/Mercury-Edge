# Step 4J-B design note — historical identity compatibility

Status: **LOCKED BEFORE 4J-B IMPLEMENTATION**

A current-version evidence identity created by `hard_state_proof.ProofRecord.evidence_id()` includes the historical `live_weather_journal.weather_id` in addition to the immutable `raw_source_id`, parsed group and software versions.

That means a replay that parses only immutable AWC batch bytes can reproduce the same weather fact but cannot reproduce the exact historical evidence/state id unless it also recovers the original report-row identity.

## Locked rule

For current-version replay only:

- exact `raw_source_journal.raw_bytes` remain the **sole semantic weather input**;
- replay reparses those bytes from scratch and does **not** read `evidence_derivations` or `hard_state_transitions` to construct state;
- `live_weather_journal` may be queried only as an **identity index** to recover the original `weather_id` for a report already proven to exist byte-for-byte inside the referenced immutable raw capture;
- the identity row must match `raw_source_id`, station, `raw_text`, observed timestamp and receipt capture; mismatch/ambiguity fails closed;
- decoded `temperature_f`, `max_temperature_f`, compatibility flags or any prior hard-state fields are never consumed by replay;
- historical derivations/transitions may be compared after reconstruction only as expected outputs.

This preserves exact v1 state-id compatibility without allowing derived weather values to seed replay.

A later evidence-model version should remove database surrogate ids from semantic evidence identity and use only immutable content/provenance identities. That is a versioned migration, not a silent Step 4J change.
