from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256

import psycopg

from raw_journal import RawCapture, canonical_json_bytes, insert_raw_capture
from replay_domain import (
    CURRENT_BENCHMARK_VERSIONS,
    ReplayEventKind,
    ReplayFilter,
    ReplayPolicy,
    build_manifest,
    load_replay_events,
)
from replay_execution import ReplayExecutionConfig, audit_chain_hash, execute_replay, load_market_rows
from replay_hard_state import reconstruct_from_database
from replay_settlement import run_and_persist_replay
from settlement_audit_domain import build_exchange_market_settlement
from settlement_journal import SettlementAuditResult, persist_exchange_market_settlement, persist_settlement_audit_result

DATABASE_URL = os.environ["DATABASE_URL"]
UTC = timezone.utc
SESSION = "ci-step4j-d-source-v1"
DAY = date(2026, 8, 21)
STATION = "KNYC"
EVENT = "KXHIGHNY-26AUG21"
SERIES = "KXHIGHNY"
DEAD_NO_L2 = EVENT + "-B8485"
DEAD_NO_BLIND = EVENT + "-T83"
ALIVE = EVENT + "-B8687"
RULES_OLD = "a" * 64
RULES_FUTURE = "b" * 64
CONNECTION = "11111111-1111-1111-1111-111111111111"


def dt(hour: int, minute: int = 0, second: int = 0, micros: int = 0) -> datetime:
    return datetime(2026, 8, 21, hour, minute, second, micros, tzinfo=UTC)


def epoch_ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def awc_body(raw_ob: str, observed_at: datetime) -> bytes:
    return json.dumps([
        {
            "icaoId": STATION,
            "obsTime": int(observed_at.timestamp()),
            "rawOb": raw_ob,
            "metarType": "METAR",
        }
    ], separators=(",", ":")).encode("utf-8")


def insert_awc(
    conn: psycopg.Connection,
    *,
    raw_ob: str,
    observed_at: datetime,
    received_at: datetime,
    monotonic_ns: int,
) -> int:
    body = awc_body(raw_ob, observed_at)
    capture = RawCapture(
        session_id=SESSION,
        source="NOAA_AWC",
        source_stream="metar_json_batch",
        raw_bytes=body,
        station_code=STATION,
        observed_at=observed_at,
        first_fetchable_at=received_at,
        received_at=received_at,
        received_epoch_ns=epoch_ns(received_at),
        received_monotonic_ns=monotonic_ns,
        transport="https_poll",
        content_type="application/json",
        metadata={"live_causal": True},
    )
    raw_id = insert_raw_capture(conn, capture)
    report_hash = sha256(raw_ob.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO live_weather_journal(
          session_id,station_code,source,report_type,observed_at,source_received_at,
          first_seen_at,received_epoch_ms,received_epoch_ns,received_monotonic_ns,
          temperature_f,max_temperature_f,raw_text,raw_payload,payload_sha256,
          compatibility_status,compatibility_rule,raw_source_id
        ) VALUES (
          %s,%s,'NOAA_AWC','METAR',%s,%s,%s,%s,%s,%s,
          NULL,NULL,%s,%s::jsonb,%s,'proven','replay-fixture-v1',%s
        )
        """,
        (
            SESSION, STATION, observed_at, received_at, received_at,
            epoch_ns(received_at) // 1_000_000, epoch_ns(received_at), monotonic_ns,
            raw_ob,
            json.dumps({"rawOb": raw_ob}, separators=(",", ":")),
            report_hash,
            raw_id,
        ),
    )
    return raw_id


def markets() -> list[dict]:
    return [
        {
            "ticker": DEAD_NO_BLIND,
            "floor_strike": None,
            "cap_strike": 83,
            "strike_type": "less",
            "open_time": dt(0).isoformat(),
            "close_time": dt(23, 59).isoformat(),
        },
        {
            "ticker": DEAD_NO_L2,
            "floor_strike": 84,
            "cap_strike": 85,
            "strike_type": "between",
            "open_time": dt(0).isoformat(),
            "close_time": dt(23, 59).isoformat(),
        },
        {
            "ticker": ALIVE,
            "floor_strike": 86,
            "cap_strike": 87,
            "strike_type": "between",
            "open_time": dt(0).isoformat(),
            "close_time": dt(23, 59).isoformat(),
        },
    ]


def insert_rule_snapshot(
    conn: psycopg.Connection,
    *,
    captured_at: datetime,
    rules_hash: str,
    market_rows: list[dict],
) -> int:
    payload = {"event": {"event_ticker": EVENT, "markets": market_rows}}
    row = conn.execute(
        """
        INSERT INTO settlement_rule_snapshots(
          session_id,series_ticker,event_ticker,captured_at,rules_hash,
          settlement_sources,fee_type,fee_multiplier,raw_payload
        ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,'quadratic',1,%s::jsonb)
        RETURNING id
        """,
        (
            SESSION, SERIES, EVENT, captured_at, rules_hash,
            json.dumps([{"name": "NWS Daily Climate Report"}], separators=(",", ":")),
            json.dumps(payload, separators=(",", ":")),
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])


def insert_l2_snapshot(conn: psycopg.Connection) -> int:
    received = dt(18, 0, 0, 50_000)
    raw_text = json.dumps(
        {
            "msg": {
                "market_ticker": DEAD_NO_L2,
                "yes_dollars_fp": [["0.20", "2"]],
                "no_dollars_fp": [["0.80", "2"]],
            }
        },
        separators=(",", ":"),
    )
    digest = sha256(raw_text.encode("utf-8")).hexdigest()
    ns = epoch_ns(received)
    chain = audit_chain_hash(None, CONNECTION, ns, digest)
    row = conn.execute(
        """
        INSERT INTO market_data_journal(
          session_id,channel,sid,seq,market_ticker,exchange_ts_ms,received_at,
          received_epoch_ms,received_epoch_ns,received_monotonic_ns,raw_text,
          payload,payload_sha256,connection_id,prev_chain_hash,chain_hash,
          price_mode,source_message_type
        ) VALUES (
          %s,'orderbook_snapshot',1,1,%s,NULL,%s,%s,%s,3000,%s,%s::jsonb,%s,
          %s::uuid,NULL,%s,NULL,'orderbook_snapshot'
        ) RETURNING id
        """,
        (
            SESSION, DEAD_NO_L2, received, ns // 1_000_000, ns, raw_text,
            raw_text, digest, CONNECTION, chain,
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])


def insert_archive_madis(conn: psycopg.Connection) -> int:
    body = b'{"station":"KNYC","T":305.35,"TSS":0,"note":"historical archive only"}'
    capture = RawCapture(
        session_id=SESSION,
        source="MADIS",
        source_stream="madis_omo_archive",
        raw_bytes=body,
        station_code=STATION,
        observed_at=dt(17, 30),
        first_fetchable_at=None,
        received_at=dt(20),
        received_epoch_ns=epoch_ns(dt(20)),
        received_monotonic_ns=4000,
        transport="archive_import",
        content_type="application/json",
        metadata={"live_causal": False, "historical_observation": True},
    )
    return insert_raw_capture(conn, capture)


def persist_later_exchange_settlement(conn: psycopg.Connection) -> tuple[str, datetime]:
    captured_at = dt(23)
    body = json.dumps(
        {"event_ticker": EVENT, "markets": [{"ticker": DEAD_NO_L2, "result": "no"}]},
        separators=(",", ":"),
    ).encode("utf-8")
    raw = RawCapture(
        session_id=SESSION,
        source="KALSHI_REST",
        source_stream="settled_event_json",
        raw_bytes=body,
        station_code=STATION,
        observed_at=None,
        received_at=captured_at,
        received_epoch_ns=epoch_ns(captured_at),
        received_monotonic_ns=5000,
        transport="https_poll",
        content_type="application/json",
        metadata={"live_causal": True},
    )
    raw_id = insert_raw_capture(conn, raw)
    settlement = build_exchange_market_settlement(
        event_ticker=EVENT,
        station_code=STATION,
        climate_date=DAY,
        source_record_id=f"raw_source_journal:{raw_id}",
        source_payload_sha256=raw.payload_sha256,
        rules_hash=RULES_OLD,
        rule_source_name="NWS Daily Climate Report",
        captured_at=captured_at,
        market_results=((DEAD_NO_L2, "no"),),
    )
    persist_exchange_market_settlement(
        conn,
        session_id=SESSION,
        settlement=settlement,
        raw_source_id=raw_id,
    )
    persist_settlement_audit_result(
        conn,
        result=SettlementAuditResult(
            session_id=SESSION,
            severity="info",
            status="pass",
            finding_code="FIXTURE_EXCHANGE_SETTLEMENT_CAPTURED",
            station_code=STATION,
            climate_date=DAY,
            exchange_settlement_id=settlement.exchange_settlement_id,
            details={"purpose": "4J-D independent later audit source"},
        ),
    )
    return settlement.exchange_settlement_id, captured_at


def source_fingerprint(conn: psycopg.Connection) -> str:
    payload = {}
    queries = {
        "raw": "SELECT id,payload_sha256 FROM raw_source_journal WHERE session_id=%s ORDER BY id",
        "weather": "SELECT id,raw_source_id,payload_sha256 FROM live_weather_journal WHERE session_id=%s ORDER BY id",
        "market": "SELECT id,payload_sha256,chain_hash FROM market_data_journal WHERE session_id=%s ORDER BY id",
        "rules": "SELECT id,rules_hash FROM settlement_rule_snapshots WHERE session_id=%s ORDER BY id",
        "exchange": "SELECT exchange_settlement_id,settlement_sha256 FROM exchange_market_settlements WHERE session_id=%s ORDER BY exchange_settlement_id",
        "audit": "SELECT audit_id,audit_sha256 FROM settlement_audit_results WHERE session_id=%s ORDER BY audit_id",
        "signals": "SELECT id,state_hash FROM paper_signals WHERE session_id=%s ORDER BY id",
        "orders": "SELECT id,execution_model_version FROM paper_orders WHERE session_id=%s ORDER BY id",
    }
    for key, query in queries.items():
        rows = conn.execute(query, (SESSION,)).fetchall()
        payload[key] = [[str(value) if value is not None else None for value in row] for row in rows]
    return sha256(canonical_json_bytes(payload)).hexdigest()


def main() -> None:
    conn = psycopg.connect(DATABASE_URL)
    try:
        conn.execute(
            "INSERT INTO paper_sessions(id,mode,model_version,status,config) VALUES (%s,'paper_live','ci-step4j-d-v1','stopped','{}'::jsonb)",
            (SESSION,),
        )

        # Causal first proof: exact T-group proves 87F at Mercury receipt 18:00Z.
        first_raw = "KNYC 211751Z 18005KT 10SM CLR 31/20 A3000 RMK AO2 T03060200"
        first_raw_id = insert_awc(
            conn,
            raw_ob=first_raw,
            observed_at=dt(17, 51),
            received_at=dt(18),
            monotonic_ns=1000,
        )

        # Physically older/lower observation arrives an hour later. Replay must
        # not backdate it and must not lower the already-proven 87F state.
        older_late_raw = "KNYC 211700Z 18005KT 10SM CLR 30/20 A3000 RMK AO2 T03000200"
        insert_awc(
            conn,
            raw_ob=older_late_raw,
            observed_at=dt(17),
            received_at=dt(19),
            monotonic_ns=2000,
        )

        old_rule_id = insert_rule_snapshot(
            conn,
            captured_at=dt(17, 45),
            rules_hash=RULES_OLD,
            market_rows=markets(),
        )
        future_markets = markets()
        future_markets[1] = {**future_markets[1], "cap_strike": 100}
        future_rule_id = insert_rule_snapshot(
            conn,
            captured_at=dt(18, 30),
            rules_hash=RULES_FUTURE,
            market_rows=future_markets,
        )
        assert future_rule_id > old_rule_id

        l2_id = insert_l2_snapshot(conn)
        archive_id = insert_archive_madis(conn)
        exchange_id, settlement_time = persist_later_exchange_settlement(conn)

        events = load_replay_events(
            conn,
            source_session_id=SESSION,
            replay_filter=ReplayFilter(station_code=STATION, event_ticker=EVENT, climate_date=DAY),
        )
        archive_events = [event for event in events if event.source_id == f"raw_source_journal:{archive_id}"]
        assert len(archive_events) == 1
        assert archive_events[0].kind is ReplayEventKind.RAW_SOURCE
        assert archive_events[0].benchmark_admissible is False
        assert archive_events[0].live_causal is False
        assert archive_events[0].available_at == dt(20)

        cfg = ReplayExecutionConfig(
            starting_bankroll=Decimal("10"),
            starting_cash=Decimal("10"),
            execution_latency_ms=100,
            fee_multiplier=Decimal("1"),
            max_no_price=Decimal("1"),
            max_trade_pct=Decimal("0.50"),
            max_event_pct=Decimal("1"),
            max_region_pct=Decimal("1"),
            max_daily_deployed_pct=Decimal("1"),
            allocation="best_edge_first",
        )
        filt = ReplayFilter(station_code=STATION, event_ticker=EVENT, climate_date=DAY)

        before = source_fingerprint(conn)
        replay_count_before = conn.execute(
            "SELECT count(*) FROM deterministic_replay_results WHERE source_session_id=%s",
            (SESSION,),
        ).fetchone()[0]
        assert replay_count_before == 0

        one = run_and_persist_replay(
            conn,
            source_session_id=SESSION,
            replay_filter=filt,
            execution_config=cfg,
        )
        two = run_and_persist_replay(
            conn,
            source_session_id=SESSION,
            replay_filter=filt,
            execution_config=cfg,
        )

        assert one.replay_result_id == two.replay_result_id
        assert one.canonical_output_sha256 == two.canonical_output_sha256
        assert one.hard_state.output_sha256 == two.hard_state.output_sha256
        assert one.execution.output_sha256 == two.execution.output_sha256
        assert one.settlement.output_sha256 == two.settlement.output_sha256

        # The late lower AWC fact never backdates or reduces the hard state.
        bounds = [state.proven_daily_high_min_f for state in one.hard_state.timeline.states]
        assert bounds == [87], bounds
        state = one.hard_state.timeline.states[0]
        assert state.first_known_at == dt(18)
        assert f"raw_source_journal:{first_raw_id}" in {
            record_id
            for evidence in one.hard_state.evidence
            for record_id in evidence.source_record_ids
        }

        # The only transition must use the rule snapshot that was actually
        # available at 18:00, never the 18:30 future revision.
        assert len(one.hard_state.eliminations) == 1
        elim = one.hard_state.eliminations[0]
        assert elim.rule_snapshot_id == old_rule_id
        assert elim.rule_rules_hash == RULES_OLD
        assert DEAD_NO_BLIND in elim.dead_market_tickers
        assert DEAD_NO_L2 in elim.dead_market_tickers
        assert ALIVE not in elim.dead_market_tickers

        trades = [decision for decision in one.execution.decisions if decision.decision == "trade"]
        blocked = [decision for decision in one.execution.decisions if decision.decision == "blocked"]
        assert len(trades) == 1, [decision.to_dict() for decision in one.execution.decisions]
        assert trades[0].market_ticker == DEAD_NO_L2
        assert trades[0].snapshot_id == l2_id
        assert trades[0].connection_id == CONNECTION
        assert trades[0].guaranteed_profit > 0
        assert one.execution.ending_cash >= 0
        assert any(
            decision.market_ticker == DEAD_NO_BLIND
            and decision.reason == "NO_VALID_L2_AT_SIMULATED_ARRIVAL"
            for decision in blocked
        )

        # Settlement is later and grades the already-made decision; it cannot
        # influence the execution. Build a second causal world ending just
        # before settlement and verify the decision objects are identical.
        pre_settlement_events = tuple(event for event in events if event.available_at < settlement_time)
        pre_manifest = build_manifest(
            source_session_id=SESSION,
            versions=CURRENT_BENCHMARK_VERSIONS,
            policy=ReplayPolicy.BENCHMARK,
            replay_filter=filt,
            events=pre_settlement_events,
        )
        pre_hard = reconstruct_from_database(conn, manifest=pre_manifest, events=pre_settlement_events)
        pre_exec = execute_replay(
            manifest=pre_manifest,
            hard_state=pre_hard,
            market_rows=load_market_rows(conn, session_id=SESSION),
            config=cfg,
        )
        assert pre_hard.timeline.states == one.hard_state.timeline.states
        assert pre_hard.eliminations == one.hard_state.eliminations
        assert pre_exec.decisions == one.execution.decisions

        assert one.settlement.status == "pass"
        assert one.settlement.unsettled_trade_count == 0
        assert one.settlement.realized_pnl is not None
        assert one.settlement.realized_pnl > 0
        assert one.settlement.trade_settlements[0].exchange_settlement_id == exchange_id
        assert one.settlement.trade_settlements[0].finding_code == "IMPOSSIBLE_BUCKET_SETTLED_NO"
        assert trades[0].simulated_arrival_at < settlement_time

        # Replay-native explanation reaches immutable weather bytes, exact old
        # rules, causal L2 and the later exchange settlement source.
        traces = one.explanation["decision_traces"]
        trade_trace = next(item for item in traces if item["decision"]["decision"] == "trade")
        assert trade_trace["rule_snapshot"]["rule_snapshot_id"] == old_rule_id
        assert trade_trace["rule_snapshot"]["rules_hash"] == RULES_OLD
        assert trade_trace["market_snapshot"]["market_data_journal_id"] == l2_id
        assert trade_trace["settlement_source"]["exchange_settlement_id"] == exchange_id
        assert any(raw["raw_source_id"] == first_raw_id for raw in trade_trace["raw_sources"])
        assert all(len(raw["payload_sha256"]) == 64 for raw in trade_trace["raw_sources"])
        assert len(one.explanation["trace_sha256"]) == 64

        # Source facts and live paper P&L remain untouched. Replay writes only
        # its own immutable derivation row, and rerunning is idempotent.
        after = source_fingerprint(conn)
        assert after == before
        replay_rows = conn.execute(
            "SELECT replay_result_id,replay_payload_sha256 FROM deterministic_replay_results WHERE source_session_id=%s",
            (SESSION,),
        ).fetchall()
        assert len(replay_rows) == 1
        assert str(replay_rows[0][0]) == one.replay_result_id
        assert len(str(replay_rows[0][1])) == 64

        print(
            "4J-D real Postgres replay PASS:",
            "state=87@18:00Z",
            "archive_leak=blocked",
            "future_rule_leak=blocked",
            "missing_l2_proxy=blocked",
            "settlement_late_only=verified",
            f"realized_pnl={one.settlement.realized_pnl}",
            f"replay_result_id={one.replay_result_id}",
        )
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    main()
