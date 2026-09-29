from __future__ import annotations

"""Derive Step 4I-B diagnostic events from existing immutable/audited ledgers.

This sweeper is intentionally downstream of trading logic: it observes source
rows, audit findings, portfolio decisions, validation products and settlement
audits and writes a normalized immutable diagnostic ledger. It cannot authorize
or alter a trade.
"""

from datetime import datetime
import json
import re
from typing import Any, Iterable

import psycopg

from asos_evidence import evidence_by_kind, parse_temperature_evidence
from failure_events import HardEdgeFailureEvent, persist_failure_event
from market_calendar import climate_date, six_hour_window_within_climate_day
from stations import STATIONS


def sweep_failure_events(conn: psycopg.Connection[Any], *, session_id: str) -> int:
    events: list[HardEdgeFailureEvent] = []
    events.extend(_asos_events(conn, session_id=session_id))
    events.extend(_elimination_events(conn, session_id=session_id))
    events.extend(_execution_events(conn, session_id=session_id))
    events.extend(_validation_events(conn, session_id=session_id))
    events.extend(_settlement_events(conn, session_id=session_id))
    count = 0
    for event in events:
        persist_failure_event(conn, event=event)
        count += 1
    return count


def _asos_events(conn: psycopg.Connection[Any], *, session_id: str) -> Iterable[HardEdgeFailureEvent]:
    rows = conn.execute(
        """
        SELECT id,raw_source_id,station_code,observed_at,first_seen_at,raw_text
        FROM live_weather_journal
        WHERE session_id=%s AND source='NOAA_AWC'
          AND raw_source_id IS NOT NULL AND raw_text IS NOT NULL
        ORDER BY first_seen_at,id
        """,
        (session_id,),
    ).fetchall()
    for weather_id, raw_source_id, station_raw, observed_at, first_seen_at, raw_text in rows:
        station = str(station_raw)
        timezone_name = (STATIONS.get(station) or {}).get("timezone")
        if not timezone_name or not isinstance(observed_at, datetime) or not isinstance(first_seen_at, datetime):
            continue
        day = climate_date(observed_at, timezone_name)
        items = parse_temperature_evidence(str(raw_text))
        common = dict(
            session_id=session_id,
            station_code=station,
            climate_date=day,
            raw_source_id=int(raw_source_id),
            occurred_at=first_seen_at,
        )
        if not items:
            yield HardEdgeFailureEvent(
                **common,
                stage="source_parse",
                disposition_class="fail_closed",
                reason_code="ASOS_NO_TEMPERATURE_EVIDENCE",
                details={"weather_id": int(weather_id)},
            )
            continue

        off_lattice = [item for item in items if item.integrity_status == "off_lattice"]
        if off_lattice:
            yield HardEdgeFailureEvent(
                **common,
                stage="source_parse",
                disposition_class="integrity_failure",
                reason_code="ASOS_OFF_LATTICE_EVIDENCE",
                details={
                    "weather_id": int(weather_id),
                    "raw_groups": [item.raw_group for item in off_lattice],
                    "kinds": [item.kind for item in off_lattice],
                },
            )
            # Hard-state proof rejects the entire row when any off-lattice item exists.
            continue

        mains = evidence_by_kind(items, "main_temp_c")
        precise = evidence_by_kind(items, "t_group")
        if mains and precise:
            exact = precise[0].exact_canonical_f
            if exact is None or exact not in mains[0].possible_canonical_f:
                yield HardEdgeFailureEvent(
                    **common,
                    stage="evidence",
                    disposition_class="integrity_failure",
                    reason_code="ASOS_MAIN_T_CONFLICT",
                    details={
                        "weather_id": int(weather_id),
                        "main_group": mains[0].raw_group,
                        "t_group": precise[0].raw_group,
                    },
                )
                continue

        sixes = evidence_by_kind(items, "six_hour_max")
        if sixes and precise:
            if sixes[0].proven_min_f is not None and precise[0].proven_min_f is not None and sixes[0].proven_min_f < precise[0].proven_min_f:
                yield HardEdgeFailureEvent(
                    **common,
                    stage="evidence",
                    disposition_class="integrity_failure",
                    reason_code="ASOS_SIX_HOUR_BELOW_CURRENT",
                    details={
                        "weather_id": int(weather_id),
                        "six_hour_group": sixes[0].raw_group,
                        "current_group": precise[0].raw_group,
                    },
                )
                continue

        for item in sixes:
            if not six_hour_window_within_climate_day(observed_at, timezone_name):
                yield HardEdgeFailureEvent(
                    **common,
                    stage="evidence",
                    disposition_class="non_admission",
                    reason_code="ASOS_SIX_HOUR_CROSSES_CLIMATE_DAY",
                    details={"weather_id": int(weather_id), "raw_group": item.raw_group},
                )
        for item in evidence_by_kind(items, "twenty_four_hour_max"):
            yield HardEdgeFailureEvent(
                **common,
                stage="evidence",
                disposition_class="non_admission",
                reason_code="ASOS_24_HOUR_BENCHMARK_DEFERRED",
                details={"weather_id": int(weather_id), "raw_group": item.raw_group},
            )


def _elimination_events(conn: psycopg.Connection[Any], *, session_id: str) -> Iterable[HardEdgeFailureEvent]:
    rows = conn.execute(
        """
        SELECT id,detected_at,station_code,market_ticker,details
        FROM audit_findings
        WHERE session_id=%s AND finding_code='BUCKET_ELIMINATION_FAIL_CLOSED'
        ORDER BY detected_at,id
        """,
        (session_id,),
    ).fetchall()
    for finding_id, detected_at, station_code, market_ticker, details_raw in rows:
        details = _mapping(details_raw)
        reason = str(details.get("reason") or "UNKNOWN")
        yield HardEdgeFailureEvent(
            session_id=session_id,
            stage="elimination",
            disposition_class="fail_closed",
            reason_code=f"BUCKET_ELIMINATION_{_reason_token(reason)}",
            occurred_at=detected_at,
            station_code=str(station_code) if station_code is not None else None,
            event_ticker=str(details.get("event_ticker")) if details.get("event_ticker") else None,
            market_ticker=str(market_ticker) if market_ticker is not None else None,
            state_id=str(details.get("hard_state_id")) if details.get("hard_state_id") else None,
            details={"audit_finding_id": int(finding_id), **details},
        )


def _execution_events(conn: psycopg.Connection[Any], *, session_id: str) -> Iterable[HardEdgeFailureEvent]:
    rows = conn.execute(
        """
        SELECT d.id,d.decided_at,d.decision,d.reason,d.details,
               s.id,s.station_code,s.event_ticker,s.triggered_at,
               p.mode_code
        FROM paper_portfolio_decisions d
        JOIN paper_portfolios p ON p.id=d.portfolio_id
        JOIN paper_signals s ON s.id=d.signal_id
        WHERE p.session_id=%s AND d.decision IN ('skip','blocked')
        ORDER BY d.decided_at,d.id
        """,
        (session_id,),
    ).fetchall()
    for decision_id, decided_at, decision, reason_raw, details_raw, signal_id, station, event_ticker, _, mode_code in rows:
        reason = str(reason_raw or "UNSPECIFIED_EXECUTION_SKIP")
        details = _mapping(details_raw)
        integrity_prefixes = ("MISSING_", "ELIMINATION_", "MARKET_NOT_", "NO_VALID_L2")
        is_fail_closed = str(decision) == "blocked" or reason.startswith(integrity_prefixes)
        disposition = "fail_closed" if is_fail_closed else "economic_skip"
        yield HardEdgeFailureEvent(
            session_id=session_id,
            stage="execution",
            disposition_class=disposition,
            reason_code=_reason_token(reason),
            occurred_at=decided_at,
            station_code=str(station),
            event_ticker=str(event_ticker) if event_ticker else None,
            market_ticker=str(details.get("market_ticker")) if details.get("market_ticker") else None,
            signal_id=int(signal_id),
            elimination_id=str(details.get("elimination_id")) if details.get("elimination_id") else None,
            details={"decision_id": int(decision_id), "mode_code": str(mode_code), "decision": str(decision), **details},
        )


def _validation_events(conn: psycopg.Connection[Any], *, session_id: str) -> Iterable[HardEdgeFailureEvent]:
    rows = conn.execute(
        """
        SELECT validation_id,station_code,climate_date,mercury_received_at,
               raw_source_id,lifecycle,fail_closed_reason,source,source_product_id
        FROM validation_products
        WHERE session_id=%s AND (lifecycle IN ('ambiguous','rejected') OR fail_closed_reason IS NOT NULL)
        ORDER BY mercury_received_at,validation_id
        """,
        (session_id,),
    ).fetchall()
    for validation_id, station, climate_day, received_at, raw_source_id, lifecycle, fail_reason, source, product_id in rows:
        reason = str(fail_reason or f"{lifecycle}_validation_product")
        yield HardEdgeFailureEvent(
            session_id=session_id,
            stage="validation",
            disposition_class="fail_closed",
            reason_code=f"VALIDATION_{_reason_token(reason)}",
            occurred_at=received_at,
            station_code=str(station),
            climate_date=climate_day,
            raw_source_id=int(raw_source_id),
            details={
                "validation_id": str(validation_id),
                "lifecycle": str(lifecycle),
                "source": str(source),
                "source_product_id": str(product_id),
            },
        )


def _settlement_events(conn: psycopg.Connection[Any], *, session_id: str) -> Iterable[HardEdgeFailureEvent]:
    rows = conn.execute(
        """
        SELECT audit_id,created_at,finding_code,station_code,climate_date,
               state_id,elimination_id,order_id,market_ticker,details
        FROM settlement_audit_results
        WHERE session_id=%s AND status='invariant_failure'
        ORDER BY created_at,audit_id
        """,
        (session_id,),
    ).fetchall()
    for audit_id, created_at, finding_code, station, climate_day, state_id, elimination_id, order_id, market_ticker, details_raw in rows:
        details = _mapping(details_raw)
        yield HardEdgeFailureEvent(
            session_id=session_id,
            stage="settlement",
            disposition_class="invariant_failure",
            reason_code=_reason_token(str(finding_code)),
            occurred_at=created_at,
            station_code=str(station),
            climate_date=climate_day,
            event_ticker=str(details.get("event_ticker")) if details.get("event_ticker") else None,
            market_ticker=str(market_ticker) if market_ticker is not None else None,
            state_id=str(state_id) if state_id is not None else None,
            elimination_id=str(elimination_id) if elimination_id is not None else None,
            order_id=int(order_id) if order_id is not None else None,
            details={"settlement_audit_id": str(audit_id), **details},
        )


def _reason_token(value: str) -> str:
    token = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    return token or "UNKNOWN"


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        return dict(decoded) if isinstance(decoded, dict) else {}
    try:
        return dict(value)
    except Exception:
        return {}
