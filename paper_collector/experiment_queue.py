from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import psycopg

import strategy_engines as se


def serialize_leg(leg: se.Leg) -> dict[str, Any]:
    return {
        "market_ticker": leg.market_ticker,
        "side": leg.side,
        "trigger_epoch_ms": leg.trigger_epoch_ms,
        "max_price": format(leg.max_price, "f"),
        "label": leg.label,
        "model_probability": None if leg.model_probability is None else format(leg.model_probability, "f"),
    }


def deserialize_leg(payload: dict[str, Any]) -> se.Leg:
    probability = payload.get("model_probability")
    return se.Leg(
        market_ticker=str(payload["market_ticker"]),
        side=str(payload["side"]),
        trigger_epoch_ms=int(payload["trigger_epoch_ms"]),
        max_price=Decimal(str(payload["max_price"])),
        label=str(payload.get("label") or "experiment"),
        model_probability=None if probability is None else Decimal(str(probability)),
    )


def serialize_bundle(bundle: se.Bundle) -> dict[str, Any]:
    return {
        "strategy_code": bundle.strategy_code,
        "signal_class": bundle.signal_class,
        "station": bundle.station,
        "region": bundle.region,
        "event_ticker": bundle.event_ticker,
        "weather_id": bundle.weather_id,
        "trigger_at": bundle.trigger_at.isoformat(),
        "trigger_epoch_ms": bundle.trigger_epoch_ms,
        "legs": [serialize_leg(leg) for leg in bundle.legs],
        "evidence": bundle.evidence,
        "auditor_status": bundle.auditor_status,
        "blocked_reason": bundle.blocked_reason,
        "equal_leg_quantity": bundle.equal_leg_quantity,
        "expected_payout_per_unit": None if bundle.expected_payout_per_unit is None else format(bundle.expected_payout_per_unit, "f"),
        "route_options": None if bundle.route_options is None else {
            name: [serialize_leg(leg) for leg in legs]
            for name, legs in bundle.route_options.items()
        },
    }


def deserialize_bundle(payload: dict[str, Any]) -> se.Bundle:
    trigger_at = datetime.fromisoformat(str(payload["trigger_at"]).replace("Z", "+00:00"))
    if trigger_at.tzinfo is None:
        trigger_at = trigger_at.replace(tzinfo=timezone.utc)
    expected = payload.get("expected_payout_per_unit")
    raw_routes = payload.get("route_options")
    routes = None
    if isinstance(raw_routes, dict):
        routes = {
            str(name): [deserialize_leg(item) for item in items if isinstance(item, dict)]
            for name, items in raw_routes.items()
            if isinstance(items, list)
        }
    return se.Bundle(
        strategy_code=str(payload["strategy_code"]),
        signal_class=str(payload["signal_class"]),
        station=str(payload["station"]),
        region=str(payload["region"]),
        event_ticker=str(payload["event_ticker"]),
        weather_id=int(payload["weather_id"]),
        trigger_at=trigger_at,
        trigger_epoch_ms=int(payload["trigger_epoch_ms"]),
        legs=[deserialize_leg(item) for item in payload.get("legs", []) if isinstance(item, dict)],
        evidence=dict(payload.get("evidence") or {}),
        auditor_status=str(payload.get("auditor_status") or "shadow_only"),
        blocked_reason=str(payload["blocked_reason"]) if payload.get("blocked_reason") is not None else None,
        equal_leg_quantity=bool(payload.get("equal_leg_quantity", False)),
        expected_payout_per_unit=None if expected is None else Decimal(str(expected)),
        route_options=routes,
    )


def enqueue_bundle(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    bundle: se.Bundle,
    signal_id: int,
    strategy_cfg: dict[str, Any],
    fee_multiplier: Decimal,
) -> bool:
    row = conn.execute(
        """
        INSERT INTO paper_experiment_queue(
          signal_id,session_id,strategy_code,bundle,strategy_config,fee_multiplier,status
        ) VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,'queued')
        ON CONFLICT (signal_id) DO NOTHING
        RETURNING signal_id
        """,
        (
            signal_id,
            session_id,
            bundle.strategy_code,
            json.dumps(serialize_bundle(bundle), separators=(",", ":"), default=se.json_default),
            json.dumps(strategy_cfg, separators=(",", ":"), default=se.json_default),
            fee_multiplier,
        ),
    ).fetchone()
    return bool(row)
