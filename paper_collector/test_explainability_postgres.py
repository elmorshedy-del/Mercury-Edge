from __future__ import annotations

"""Step 4I-C real-Postgres end-to-end canonical explainability regression."""

import json
import os
from datetime import date, datetime, timezone
from hashlib import sha256

import psycopg

from explainability import canonical_explanation_bytes, explain_order, inspect_raw_source
from failure_event_sweeper import sweep_failure_events
from hard_information_domain import BucketElimination, HardClimateState

UTC = timezone.utc
SESSION = "h4i-e2e-postgres"
STATION = "KPHL"
DAY = date(2026, 8, 18)
EVENT = "KXHIGHPHIL-26AUG18"
MARKET = "KXHIGHPHIL-26AUG18-B86.5"
RULES_HASH = "a" * 64
STATE_ID = "state:h4i-e2e-88"
EV_PRECISE = "evidence:h4i-e2e-precise"
EV_SIX = "evidence:h4i-e2e-six-hour"
ELIM_ID = "elimination:h4i-e2e-86-87"
CONNECTION = "00000000-0000-0000-0000-000000000041"
OBS = datetime(2026, 8, 18, 18, 54, tzinfo=UTC)
PUBLISHED = datetime(2026, 8, 18, 18, 54, 30, tzinfo=UTC)
FETCHABLE = datetime(2026, 8, 18, 18, 54, 31, tzinfo=UTC)
RECEIVED = datetime(2026, 8, 18, 18, 54, 32, tzinfo=UTC)
INTERPRETED = datetime(2026, 8, 18, 18, 54, 32, 100000, tzinfo=UTC)


def h(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def dumps(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def run() -> None:
    url = os.environ["DATABASE_URL"]
    conn = psycopg.connect(url, autocommit=True)
    conn.execute("BEGIN")
    try:
        _seed_canonical_trade(conn)
        trace = explain_order(conn, order_id=_order_id(conn))
        _assert_trace(conn, trace)
        _assert_malformed_source_is_countable_and_non_authorizing(conn)
        print("Step 4I-C real Postgres explainability regression: PASS")
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def _seed_canonical_trade(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO paper_sessions(id,mode,model_version,status,config) VALUES (%s,'replay','h4i-e2e','running','{}'::jsonb)",
        (SESSION,),
    )

    good_raw = b"KPHL 181854Z 31/20 T03110000 10311"
    raw_id = conn.execute(
        """
        INSERT INTO raw_source_journal(
          capture_id,session_id,source,source_stream,station_code,observed_at,
          source_published_at,first_fetchable_at,received_at,received_epoch_ns,
          received_monotonic_ns,transport,content_type,raw_bytes,payload_sha256,metadata
        ) VALUES (%s,%s,'NOAA_AWC','metar_json',%s,%s,%s,%s,%s,%s,%s,'https_poll','text/plain',%s,%s,'{}'::jsonb)
        RETURNING id
        """,
        (
            "capture:h4i-good", SESSION, STATION, OBS, PUBLISHED, FETCHABLE, RECEIVED,
            int(RECEIVED.timestamp() * 1_000_000_000), 1, good_raw, h(good_raw),
        ),
    ).fetchone()[0]

    for evidence_id, evidence_type, raw_identifier, grade in (
        (EV_PRECISE, "asos_t_group_current", "T0311", "H1_CURRENT"),
        (EV_SIX, "asos_six_hour_max", "10311", "H2_SIX_HOUR_MAX"),
    ):
        derivation = {
            "evidence_id": evidence_id,
            "evidence_type": evidence_type,
            "raw_identifier": raw_identifier,
            "grade": grade,
            "proven_min_f": 88,
            "possible_canonical_f": [88],
        }
        conn.execute(
            """
            INSERT INTO evidence_derivations(
              evidence_id,session_id,station_code,climate_date,evidence_type,trust,
              integrity_status,proven_min_f,proven_max_f,possible_canonical_f,
              raw_identifier,observed_at,source_published_at,first_fetchable_at,
              mercury_received_at,mercury_interpreted_at,parser_version,
              evidence_model_version,calendar_version,derivation_payload,derivation_sha256
            ) VALUES (
              %s,%s,%s,%s,%s,'benchmark_eligible','canonical',88,88,'[88]'::jsonb,
              %s,%s,%s,%s,%s,%s,'asos-metar-evidence-v1','raw-asos-lattice-v1',
              'lst-climate-calendar-v1',%s::jsonb,%s
            )
            """,
            (
                evidence_id, SESSION, STATION, DAY, evidence_type, raw_identifier,
                OBS, PUBLISHED, FETCHABLE, RECEIVED, INTERPRETED,
                dumps(derivation), h(dumps(derivation).encode()),
            ),
        )
        conn.execute(
            "INSERT INTO evidence_source_links(evidence_id,raw_source_id,ordinal,relation) VALUES (%s,%s,0,'input')",
            (evidence_id, raw_id),
        )

    state = HardClimateState(
        state_id=STATE_ID,
        station_code=STATION,
        climate_date=DAY,
        proven_daily_high_min_f=88,
        first_known_at=INTERPRETED,
        transition_evidence_id=EV_PRECISE,
        supporting_evidence_ids=(EV_PRECISE, EV_SIX),
        state_model_version="hard-state-accumulator-v1",
        calendar_version="lst-climate-calendar-v1",
    )
    state_payload = state.to_dict()
    conn.execute(
        """
        INSERT INTO hard_state_transitions(
          state_id,session_id,station_code,climate_date,proven_daily_high_min_f,
          first_known_at,transition_evidence_id,supporting_evidence_ids,
          state_model_version,calendar_version,transition_payload,transition_sha256
        ) VALUES (%s,%s,%s,%s,88,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s)
        """,
        (
            STATE_ID, SESSION, STATION, DAY, INTERPRETED, EV_PRECISE,
            dumps([EV_PRECISE, EV_SIX]), state.state_model_version,
            state.calendar_version, dumps(state_payload), h(dumps(state_payload).encode()),
        ),
    )

    elimination = BucketElimination(
        elimination_id=ELIM_ID,
        event_ticker=EVENT,
        market_ticker=MARKET,
        station_code=STATION,
        climate_date=DAY,
        hard_state_id=STATE_ID,
        hard_lower_bound_f=88,
        strike_rule="floor_strike=86;cap_strike=87",
        eliminated=True,
        elimination_model_version="bucket-elimination-v1",
        reason="hard_lower_bound_above_market_cap",
    )
    evidence = {
        "hard_climate_state": state.to_dict(),
        "bucket_elimination": elimination.to_dict(),
        "elimination_context": {
            "event_ticker": EVENT,
            "station_code": STATION,
            "climate_date": DAY.isoformat(),
            "hard_state_id": STATE_ID,
            "transition_evidence_id": EV_PRECISE,
            "event_rules_hash": RULES_HASH,
            "elimination_model_version": "bucket-elimination-v1",
            "dead_market_tickers": [
                "KXHIGHPHIL-26AUG18-T83",
                "KXHIGHPHIL-26AUG18-B84.5",
                MARKET,
            ],
        },
        "event_rules_hash": RULES_HASH,
    }
    signal_id = conn.execute(
        """
        INSERT INTO paper_signals(
          session_id,station_code,event_ticker,strategy_code,signal_class,triggered_at,
          trigger_epoch_ms,trigger_epoch_ns,state_hash,evidence,auditor_status
        ) VALUES (%s,%s,%s,'DBN','hard_state',%s,%s,%s,%s,%s::jsonb,'approved')
        RETURNING id
        """,
        (
            SESSION, STATION, EVENT, INTERPRETED,
            int(INTERPRETED.timestamp() * 1000), int(INTERPRETED.timestamp() * 1_000_000_000),
            "h4i-e2e-signal-state", dumps(evidence),
        ),
    ).fetchone()[0]

    snapshot_raw = dumps({"msg": {"yes_dollars_fp": [["0.28", "10.00"]]}})
    snapshot_id = conn.execute(
        """
        INSERT INTO market_data_journal(
          session_id,channel,sid,seq,market_ticker,received_at,received_epoch_ms,
          received_epoch_ns,received_monotonic_ns,raw_text,payload,payload_sha256,
          connection_id,price_mode,source_message_type
        ) VALUES (
          %s,'orderbook_snapshot',1,900,%s,%s,%s,%s,2,%s,%s::jsonb,%s,%s::uuid,
          'native_binary','orderbook_snapshot'
        ) RETURNING id
        """,
        (
            SESSION, MARKET, INTERPRETED,
            int(INTERPRETED.timestamp() * 1000), int(INTERPRETED.timestamp() * 1_000_000_000),
            snapshot_raw, snapshot_raw, h(snapshot_raw.encode()), CONNECTION,
        ),
    ).fetchone()[0]

    arrival = datetime(2026, 8, 18, 18, 54, 32, 200000, tzinfo=UTC)
    order_id = conn.execute(
        """
        INSERT INTO paper_orders(
          session_id,signal_id,latency_profile_ms,strategy_code,market_ticker,
          outcome_side,requested_qty,decision_at,simulated_arrival_at,book_seq,status,
          avg_fill_price,filled_qty,gross_cost,estimated_fee,worst_price,book_snapshot,
          audit,execution_model_version
        ) VALUES (
          %s,%s,100,'DBN',%s,'no',10,%s,%s,900,'filled',0.72,10,7.20,0.03,0.72,
          %s::jsonb,%s::jsonb,'canonical-dead-no-paper-v1'
        ) RETURNING id
        """,
        (
            SESSION, signal_id, MARKET, INTERPRETED, arrival,
            dumps({
                "connection_id": CONNECTION,
                "snapshot_id": int(snapshot_id),
                "snapshot_received_ms": int(INTERPRETED.timestamp() * 1000),
                "arrival_ms": int(arrival.timestamp() * 1000),
                "asks_used": [["0.72", "10.00"]],
                "l2_only": True,
            }),
            dumps(evidence),
        ),
    ).fetchone()[0]

    settlement_raw = dumps({
        "event_ticker": EVENT,
        "markets": [{"ticker": MARKET, "result": "no"}],
    }).encode()
    settlement_raw_id = conn.execute(
        """
        INSERT INTO raw_source_journal(
          capture_id,session_id,source,source_stream,station_code,received_at,
          received_epoch_ns,received_monotonic_ns,transport,content_type,raw_bytes,
          payload_sha256,metadata
        ) VALUES (%s,%s,'KALSHI_REST','settled_event_detail',%s,%s,%s,3,'https_poll',
                  'application/json',%s,%s,'{}'::jsonb) RETURNING id
        """,
        (
            "capture:h4i-settlement", SESSION, STATION,
            datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
            int(datetime(2026, 8, 19, 12, 0, tzinfo=UTC).timestamp() * 1_000_000_000),
            settlement_raw, h(settlement_raw),
        ),
    ).fetchone()[0]
    exchange_id = "exchange-settlement:h4i-e2e"
    conn.execute(
        """
        INSERT INTO exchange_market_settlements(
          exchange_settlement_id,session_id,event_ticker,station_code,climate_date,
          raw_source_id,rules_hash,rule_source_name,captured_at,market_results,
          parser_version,settlement_payload,settlement_sha256
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,'The Weather Company',%s,%s::jsonb,
                  'exchange-market-settlement-v1',%s::jsonb,%s)
        """,
        (
            exchange_id, SESSION, EVENT, STATION, DAY, settlement_raw_id, RULES_HASH,
            datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
            dumps([{"market_ticker": MARKET, "result": "no"}]),
            dumps({"event_ticker": EVENT}), h(settlement_raw),
        ),
    )
    conn.execute(
        """
        INSERT INTO settlement_audit_results(
          audit_id,session_id,exchange_settlement_id,severity,status,finding_code,
          station_code,climate_date,state_id,elimination_id,order_id,market_ticker,
          auditor_version,details,audit_payload,audit_sha256
        ) VALUES (
          'audit:h4i-e2e',%s,%s,'info','pass','IMPOSSIBLE_BUCKET_SETTLED_NO',
          %s,%s,%s,%s,%s,%s,'settlement-auditor-v1',%s::jsonb,%s::jsonb,%s
        )
        """,
        (
            SESSION, exchange_id, STATION, DAY, STATE_ID, ELIM_ID, order_id, MARKET,
            dumps({"event_ticker": EVENT, "market_result": "no"}),
            dumps({"audit": "pass"}), "b" * 64,
        ),
    )


def _order_id(conn: psycopg.Connection) -> int:
    return int(conn.execute(
        "SELECT id FROM paper_orders WHERE session_id=%s AND market_ticker=%s", (SESSION, MARKET)
    ).fetchone()[0])


def _assert_trace(conn: psycopg.Connection, trace: dict) -> None:
    assert trace["order"]["market_ticker"] == MARKET
    assert trace["order"]["book_snapshot"]["l2_only"] is True
    assert trace["hard_state"]["state_id"] == STATE_ID
    assert trace["hard_state"]["proven_daily_high_min_f"] == 88
    assert trace["elimination"]["elimination_id"] == ELIM_ID
    assert len(trace["newly_dead_market_tickers"]) == 3
    assert [e["evidence_id"] for e in trace["evidence"]] == [EV_PRECISE, EV_SIX]
    assert len(trace["raw_sources"]) == 1
    raw_id = int(trace["raw_sources"][0]["raw_source_id"])
    raw = inspect_raw_source(conn, raw_source_id=raw_id)
    assert raw["utf8_text"] == "KPHL 181854Z 31/20 T03110000 10311"
    assert raw["payload_sha256"] == trace["raw_sources"][0]["payload_sha256"]
    assert trace["evidence"][0]["clocks"]["observed_at"] != trace["evidence"][0]["clocks"]["mercury_received_at"]
    assert trace["settlement_audits"][0]["finding_code"] == "IMPOSSIBLE_BUCKET_SETTLED_NO"
    assert trace["settlement_audits"][0]["exchange_settlement_id"] == "exchange-settlement:h4i-e2e"
    # Explanation identity is deterministic across independent reads.
    second = explain_order(conn, order_id=_order_id(conn))
    assert canonical_explanation_bytes(trace) == canonical_explanation_bytes(second)
    # L2 identity in the explanation resolves to the exact causal snapshot row.
    snapshot = trace["order"]["book_snapshot"]
    db_book = conn.execute(
        "SELECT connection_id::text,seq,market_ticker FROM market_data_journal WHERE id=%s",
        (int(snapshot["snapshot_id"]),),
    ).fetchone()
    assert db_book == (CONNECTION, 900, MARKET)


def _assert_malformed_source_is_countable_and_non_authorizing(conn: psycopg.Connection) -> None:
    bad_raw = b"KPHL 181900Z 31/20 T03100000"
    bad_raw_id = conn.execute(
        """
        INSERT INTO raw_source_journal(
          capture_id,session_id,source,source_stream,station_code,observed_at,
          received_at,received_epoch_ns,received_monotonic_ns,transport,content_type,
          raw_bytes,payload_sha256,metadata
        ) VALUES (%s,%s,'NOAA_AWC','metar_json',%s,%s,%s,%s,4,'https_poll','text/plain',
                  %s,%s,'{}'::jsonb) RETURNING id
        """,
        (
            "capture:h4i-bad", SESSION, STATION,
            datetime(2026, 8, 18, 19, 0, tzinfo=UTC),
            datetime(2026, 8, 18, 19, 0, 1, tzinfo=UTC),
            int(datetime(2026, 8, 18, 19, 0, 1, tzinfo=UTC).timestamp() * 1_000_000_000),
            bad_raw, h(bad_raw),
        ),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO live_weather_journal(
          session_id,station_code,source,report_type,observed_at,first_seen_at,
          received_epoch_ms,received_epoch_ns,received_monotonic_ns,raw_text,
          raw_payload,payload_sha256,raw_source_id
        ) VALUES (%s,%s,'NOAA_AWC','METAR',%s,%s,%s,%s,4,%s,'{}'::jsonb,%s,%s)
        """,
        (
            SESSION, STATION,
            datetime(2026, 8, 18, 19, 0, tzinfo=UTC),
            datetime(2026, 8, 18, 19, 0, 1, tzinfo=UTC),
            int(datetime(2026, 8, 18, 19, 0, 1, tzinfo=UTC).timestamp() * 1000),
            int(datetime(2026, 8, 18, 19, 0, 1, tzinfo=UTC).timestamp() * 1_000_000_000),
            bad_raw.decode(), h(bad_raw), bad_raw_id,
        ),
    )
    sweep_failure_events(conn, session_id=SESSION)
    row = conn.execute(
        """
        SELECT disposition_class,reason_code,raw_source_id
        FROM hard_edge_failure_events
        WHERE session_id=%s AND raw_source_id=%s
        """,
        (SESSION, bad_raw_id),
    ).fetchone()
    assert row == ("integrity_failure", "ASOS_OFF_LATTICE_EVIDENCE", bad_raw_id)
    # The malformed raw record is diagnosable but has no evidence derivation and
    # therefore cannot be part of any hard-state authorization.
    assert conn.execute(
        "SELECT count(*) FROM evidence_source_links WHERE raw_source_id=%s", (bad_raw_id,)
    ).fetchone()[0] == 0


if __name__ == "__main__":
    run()
