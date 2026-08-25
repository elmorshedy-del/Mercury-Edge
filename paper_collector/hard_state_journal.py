from __future__ import annotations

"""Append-only persistence for canonical hard-state applications/transitions."""

from hashlib import sha256
import json
from typing import Any

import psycopg

from hard_state_accumulator import HardStateTimeline
from raw_journal import canonical_json_bytes, sha256_hex

HARD_STATE_JOURNAL_VERSION = "hard-state-journal-v1"


def application_id(
    *,
    session_id: str,
    station_code: str,
    climate_date: str,
    accumulator_version: str,
    evidence_id: str,
) -> str:
    raw = "|".join((
        HARD_STATE_JOURNAL_VERSION,
        session_id,
        station_code,
        climate_date,
        accumulator_version,
        evidence_id,
    )).encode("utf-8")
    return f"hard-app:{sha256(raw).hexdigest()[:32]}"


def persist_timeline(
    conn: psycopg.Connection[Any],
    *,
    session_id: str,
    timeline: HardStateTimeline,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Persist one deterministic timeline idempotently without mutation.

    Evidence derivations must already exist. Recomputing the same timeline is an
    idempotent no-op. If the same stable identity produces different bytes, fail
    closed because that indicates hidden non-determinism or versioning failure.
    """
    app_ids: list[str] = []
    state_ids: list[str] = []
    climate_day = timeline.climate_date.isoformat()

    for item in timeline.applications:
        app_id = application_id(
            session_id=session_id,
            station_code=timeline.station_code,
            climate_date=climate_day,
            accumulator_version=timeline.accumulator_version,
            evidence_id=item.evidence_id,
        )
        payload = {
            "journal_version": HARD_STATE_JOURNAL_VERSION,
            "station_code": timeline.station_code,
            "climate_date": climate_day,
            "calendar_version": timeline.calendar_version,
            "accumulator_version": timeline.accumulator_version,
            "application": item.to_dict(),
        }
        canonical = canonical_json_bytes(payload)
        digest = sha256_hex(canonical)
        conn.execute(
            """
            INSERT INTO hard_state_applications(
              application_id,session_id,station_code,climate_date,evidence_id,
              status,reason,known_at,proven_min_f,prior_bound_f,resulting_bound_f,
              evidence_type,accumulator_version,calendar_version,
              application_payload,application_sha256
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s
            )
            ON CONFLICT (application_id) DO NOTHING
            """,
            (
                app_id,
                session_id,
                timeline.station_code,
                timeline.climate_date,
                item.evidence_id,
                item.status.value,
                item.reason,
                item.known_at,
                item.proven_min_f,
                item.prior_bound_f,
                item.resulting_bound_f,
                item.evidence_type,
                timeline.accumulator_version,
                timeline.calendar_version,
                canonical.decode("utf-8"),
                digest,
            ),
        )
        existing = conn.execute(
            "SELECT application_sha256 FROM hard_state_applications WHERE application_id=%s",
            (app_id,),
        ).fetchone()
        if not existing or str(existing[0]) != digest:
            raise RuntimeError("hard-state application collision or non-deterministic recomputation")
        app_ids.append(app_id)

    for state in timeline.states:
        payload = {
            "journal_version": HARD_STATE_JOURNAL_VERSION,
            "accumulator_version": timeline.accumulator_version,
            "state": state.to_dict(),
        }
        canonical = canonical_json_bytes(payload)
        digest = sha256_hex(canonical)
        conn.execute(
            """
            INSERT INTO hard_state_transitions(
              state_id,session_id,station_code,climate_date,
              proven_daily_high_min_f,first_known_at,transition_evidence_id,
              supporting_evidence_ids,state_model_version,calendar_version,
              transition_payload,transition_sha256
            ) VALUES (
              %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s
            )
            ON CONFLICT (state_id) DO NOTHING
            """,
            (
                state.state_id,
                session_id,
                state.station_code,
                state.climate_date,
                state.proven_daily_high_min_f,
                state.first_known_at,
                state.transition_evidence_id,
                json.dumps(list(state.supporting_evidence_ids), separators=(",", ":")),
                state.state_model_version,
                state.calendar_version,
                canonical.decode("utf-8"),
                digest,
            ),
        )
        existing = conn.execute(
            "SELECT transition_sha256 FROM hard_state_transitions WHERE state_id=%s",
            (state.state_id,),
        ).fetchone()
        if not existing or str(existing[0]) != digest:
            raise RuntimeError("hard-state transition collision or non-deterministic recomputation")
        state_ids.append(state.state_id)

    return tuple(app_ids), tuple(state_ids)
