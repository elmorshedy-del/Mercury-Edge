from __future__ import annotations

import json
import math
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Any

import psycopg

from paper_engine import (
    canonical_hash,
    compatibility_is_proven,
    d,
    exposure,
    fee_for_fills,
    fills_for_budget,
    load_global,
    load_modes,
    series_rules_before,
    total_cost,
)
from stations import STATIONS

DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_ID = os.environ["PAPER_SESSION_ID"]
ENGINE_VERSION = os.getenv("PAPER_STRATEGY_ENGINE_VERSION", "multi-strategy-v1")
POLL_MS = max(100, int(os.getenv("PAPER_STRATEGY_ENGINE_POLL_MS", "350")))
DEFAULT_DELAY_MS = max(1500, int(os.getenv("PAPER_ENGINE_PROCESS_DELAY_MS", "6000")))
ONE = Decimal("1")
QTY_STEP = Decimal("0.01")
STOP = False


@dataclass
class MarketSpec:
    ticker: str
    floor: Decimal | None
    cap: Decimal | None
    title: str
    subtitle: str


@dataclass
class FullBook:
    yes_bids: dict[Decimal, Decimal]
    no_bids: dict[Decimal, Decimal]
    connection_id: str
    last_seq: int | None
    snapshot_id: int
    snapshot_received_ms: int


def stop(*_: object) -> None:
    global STOP
    STOP = True


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def active_at(market: dict[str, Any], when: datetime) -> bool:
    opened = parse_time(market.get("open_time"))
    closed = parse_time(market.get("close_time") or market.get("expected_expiration_time"))
    return (opened is None or when >= opened) and (closed is None or when <= closed)


def number_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return d(value)
    except Exception:
        return None


def market_spec(raw: dict[str, Any]) -> MarketSpec | None:
    ticker = str(raw.get("ticker") or "")
    if not ticker:
        return None
    return MarketSpec(
        ticker=ticker,
        floor=number_or_none(raw.get("floor_strike")),
        cap=number_or_none(raw.get("cap_strike")),
        title=str(raw.get("title") or ""),
        subtitle=str(raw.get("subtitle") or ""),
    )


def contains(market: MarketSpec, value: Decimal) -> bool:
    return (market.floor is None or value >= market.floor) and (market.cap is None or value <= market.cap)


def dead(market: MarketSpec, confirmed_high: Decimal) -> bool:
    return market.cap is not None and confirmed_high > market.cap


def event_candidates(conn: psycopg.Connection[Any], series: str, when: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_ticker,rules_hash,raw_payload,captured_at
          FROM settlement_rule_snapshots
         WHERE series_ticker=%s AND event_ticker IS NOT NULL AND captured_at<=%s
         ORDER BY captured_at DESC,id DESC LIMIT 50
        """,
        (series, when),
    ).fetchall()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event_ticker, rules_hash, payload, captured_at in rows:
        ticker = str(event_ticker)
        if ticker in seen:
            continue
        seen.add(ticker)
        raw = payload if isinstance(payload, dict) else {}
        event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
        markets_raw = [m for m in (event.get("markets") or []) if isinstance(m, dict) and active_at(m, when)]
        markets = [spec for spec in (market_spec(m) for m in markets_raw) if spec is not None]
        if not markets:
            continue
        result.append({
            "event_ticker": ticker,
            "rules_hash": str(rules_hash),
            "captured_at": captured_at,
            "mutually_exclusive": bool(event.get("mutually_exclusive")) if event.get("mutually_exclusive") is not None else None,
            "markets": markets,
        })
    return result


def strategy_configs(conn: psycopg.Connection[Any]) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT strategy_code,enabled,paper_trade_enabled,shadow_enabled,config FROM paper_strategy_configs"
    ).fetchall()
    return {
        str(code): {
            "enabled": bool(enabled),
            "paper": bool(paper),
            "shadow": bool(shadow),
            "config": dict(config) if isinstance(config, dict) else {},
        }
        for code, enabled, paper, shadow, config in rows
    }


def reconstruct_book(conn: psycopg.Connection[Any], market_ticker: str, arrival_ms: int) -> FullBook | None:
    snap = conn.execute(
        """
        SELECT id,connection_id::text,seq,received_epoch_ms,raw_text
          FROM market_data_journal
         WHERE session_id=%s AND market_ticker=%s AND channel='orderbook_snapshot'
           AND received_epoch_ms<=%s
         ORDER BY received_epoch_ms DESC,id DESC LIMIT 1
        """,
        (SESSION_ID, market_ticker, arrival_ms),
    ).fetchone()
    if not snap or not snap[1]:
        return None
    snapshot_id = int(snap[0])
    connection_id = str(snap[1])
    fatal = conn.execute(
        """
        SELECT 1 FROM audit_findings
         WHERE session_id=%s AND severity='fatal' AND details->>'connection_id'=%s
           AND detected_at<=to_timestamp(%s/1000.0) LIMIT 1
        """,
        (SESSION_ID, connection_id, arrival_ms),
    ).fetchone()
    if fatal:
        return None
    rows = conn.execute(
        """
        SELECT channel,seq,raw_text FROM market_data_journal
         WHERE session_id=%s AND connection_id=%s::uuid AND market_ticker=%s
           AND id>=%s AND received_epoch_ms<=%s
           AND channel IN ('orderbook_snapshot','orderbook_delta') ORDER BY id ASC
        """,
        (SESSION_ID, connection_id, market_ticker, snapshot_id, arrival_ms),
    ).fetchall()
    yes: dict[Decimal, Decimal] = {}
    no: dict[Decimal, Decimal] = {}
    seen = False
    last_seq: int | None = None
    for channel, seq, raw_text in rows:
        data = json.loads(str(raw_text))
        msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
        if channel == "orderbook_snapshot":
            yes.clear(); no.clear(); seen = True
            for level in msg.get("yes_dollars_fp") or []:
                if isinstance(level, list) and len(level) >= 2:
                    p, q = d(level[0]), d(level[1])
                    if q > 0: yes[p] = q
            for level in msg.get("no_dollars_fp") or []:
                if isinstance(level, list) and len(level) >= 2:
                    raw_p, q = d(level[0]), d(level[1])
                    p = ONE - raw_p  # unified YES-price wire mode -> native NO bid
                    if q > 0: no[p] = q
        elif seen:
            side = str(msg.get("side"))
            raw_p = d(msg.get("price_dollars")); delta = d(msg.get("delta_fp"))
            target = yes if side == "yes" else no if side == "no" else None
            if target is None: continue
            p = raw_p if side == "yes" else ONE - raw_p
            nxt = target.get(p, Decimal(0)) + delta
            if nxt < 0: return None
            if nxt == 0: target.pop(p, None)
            else: target[p] = nxt
        if seq is not None: last_seq = int(seq)
    if not seen:
        return None
    if yes and no and max(yes) + max(no) > ONE:
        return None
    return FullBook(yes, no, connection_id, last_seq, snapshot_id, int(snap[3]))


def asks(book: FullBook, side: str, max_price: Decimal = ONE) -> list[tuple[Decimal, Decimal]]:
    bids = book.no_bids if side == "yes" else book.yes_bids
    result = [(ONE - p, q) for p, q in bids.items() if q > 0 and ONE - p <= max_price]
    return sorted(result, key=lambda x: x[0])


def midpoint(book: FullBook, side: str) -> Decimal | None:
    own = book.yes_bids if side == "yes" else book.no_bids
    side_asks = asks(book, side)
    bid = max(own) if own else None
    ask = side_asks[0][0] if side_asks else None
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal(2)
    return bid if bid is not None else ask


def insert_signal(
    conn: psycopg.Connection[Any], strategy: str, station: str, event_ticker: str,
    when: datetime, epoch_ms: int, weather_id: int | None, signal_class: str,
    key_material: dict[str, Any], evidence: dict[str, Any], approved: bool,
    block_reason: str | None = None,
) -> int:
    state_hash = canonical_hash({"session": SESSION_ID, "strategy": strategy, **key_material})
    row = conn.execute(
        """
        INSERT INTO paper_signals(session_id,station_code,event_ticker,strategy_code,signal_class,
          triggered_at,trigger_epoch_ms,trigger_epoch_ns,weather_event_id,state_hash,evidence,auditor_status,blocked_reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
        ON CONFLICT (session_id,strategy_code,state_hash)
        DO UPDATE SET evidence=EXCLUDED.evidence,auditor_status=EXCLUDED.auditor_status,blocked_reason=EXCLUDED.blocked_reason
        RETURNING id
        """,
        (
            SESSION_ID, station, event_ticker, strategy, signal_class, when, epoch_ms,
            str(epoch_ms * 1_000_000), weather_id, state_hash,
            json.dumps(evidence, separators=(",", ":"), default=str),
            "approved" if approved else "shadow_only", block_reason,
        ),
    ).fetchone()
    if not row: raise RuntimeError("signal insert failed")
    return int(row[0])


def region_stations(region: str) -> list[str]:
    return [s for s, meta in STATIONS.items() if meta.get("region") == region]


def budget_for(conn: psycopg.Connection[Any], mode: dict[str, Any], event: str, region: str, when: datetime, global_cfg: dict[str, Any]) -> Decimal:
    cfg = mode["config"]
    row = conn.execute("SELECT starting_bankroll,cash_balance FROM paper_portfolios WHERE id=%s FOR UPDATE", (mode["portfolio_id"],)).fetchone()
    if not row: return Decimal(0)
    start, cash = d(row[0]), d(row[1])
    reserve = start * d(cfg.get("reserve_pct", 0))
    usable = max(Decimal(0), cash - reserve)
    event_used, region_used, day_used = exposure(
        conn, int(mode["portfolio_id"]), event, region_stations(region), when,
        str(global_cfg.get("portfolio_day_timezone", "America/New_York")),
    )
    return max(Decimal(0), min(
        usable,
        start * d(cfg.get("max_trade_pct", 1)),
        max(Decimal(0), start * d(cfg.get("max_event_pct", 1)) - event_used),
        max(Decimal(0), start * d(cfg.get("max_region_pct", 1)) - region_used),
        max(Decimal(0), start * d(cfg.get("max_daily_deployed_pct", 1)) - day_used),
    ))


def decision_exists(conn: psycopg.Connection[Any], portfolio_id: int, signal_id: int) -> bool:
    return bool(conn.execute("SELECT 1 FROM paper_portfolio_decisions WHERE portfolio_id=%s AND signal_id=%s", (portfolio_id, signal_id)).fetchone())


def record_decision(conn: psycopg.Connection[Any], portfolio_id: int, signal_id: int, decision: str, budget: Decimal, reason: str, details: dict[str, Any], score: Decimal | None = None) -> None:
    conn.execute(
        """
        INSERT INTO paper_portfolio_decisions(portfolio_id,signal_id,decision,requested_budget,score,reason,details)
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (portfolio_id,signal_id) DO NOTHING
        """,
        (portfolio_id, signal_id, decision, budget, score, reason, json.dumps(details, separators=(",", ":"), default=str)),
    )


def place_single_leg(
    conn: psycopg.Connection[Any], *, mode: dict[str, Any], signal_id: int, strategy: str,
    market_ticker: str, side: str, trigger_at: datetime, trigger_ms: int, latency_ms: int,
    budget: Decimal, book: FullBook, side_asks: list[tuple[Decimal, Decimal]], fee_multiplier: Decimal,
    evidence: dict[str, Any], mark_decision: bool = True,
) -> Decimal:
    portfolio_id = int(mode["portfolio_id"])
    if mark_decision and decision_exists(conn, portfolio_id, signal_id): return Decimal(0)
    fills, cost, fee, breakdown = fills_for_budget(side_asks, budget, fee_multiplier)
    if not fills or cost <= 0:
        if mark_decision: record_decision(conn, portfolio_id, signal_id, "skip", budget, "NO_EXECUTABLE_DEPTH", evidence)
        return Decimal(0)
    qty = sum((q for _, q in fills), Decimal(0)); gross = sum((p*q for p,q in fills), Decimal(0))
    avg = gross / qty; worst = max(p for p,_ in fills)
    cash_update = conn.execute(
        "UPDATE paper_portfolios SET cash_balance=cash_balance-%s,updated_at=now() WHERE id=%s AND cash_balance>=%s RETURNING id",
        (cost, portfolio_id, cost),
    ).fetchone()
    if not cash_update:
        if mark_decision: record_decision(conn, portfolio_id, signal_id, "skip", budget, "INSUFFICIENT_CASH_AT_COMMIT", evidence)
        return Decimal(0)
    if mark_decision: record_decision(conn, portfolio_id, signal_id, "trade", budget, "EXECUTABLE", evidence, (ONE-avg)/avg if avg>0 else Decimal(0))
    arrival_ms = trigger_ms + latency_ms
    order = conn.execute(
        """
        INSERT INTO paper_orders(session_id,signal_id,portfolio_id,latency_profile_ms,strategy_code,market_ticker,outcome_side,
          requested_qty,decision_at,simulated_arrival_at,book_seq,status,avg_fill_price,filled_qty,gross_cost,estimated_fee,worst_price,
          book_snapshot,audit,requested_qty_fp,max_price_fp,filled_qty_fp,gross_cost_micros,fee_micros,total_cost_micros,
          state_known_through_epoch_ms,execution_model_version,fee_breakdown)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s/1000.0),%s,'filled',%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
        RETURNING id
        """,
        (
            SESSION_ID, signal_id, portfolio_id, latency_ms, strategy, market_ticker, side, qty, trigger_at, arrival_ms,
            book.last_seq, avg, qty, gross, fee, worst,
            json.dumps({"connection_id":book.connection_id,"snapshot_id":book.snapshot_id,"arrival_ms":arrival_ms,"fills":[[str(p),str(q)] for p,q in fills]}),
            json.dumps(evidence, separators=(",", ":"), default=str),
            int((qty/QTY_STEP).to_integral_value()), int((worst*10000).to_integral_value()), int((qty/QTY_STEP).to_integral_value()),
            int((gross*1_000_000).to_integral_value()), int((fee*1_000_000).to_integral_value()), int((cost*1_000_000).to_integral_value()),
            arrival_ms, ENGINE_VERSION, json.dumps(breakdown, separators=(",", ":")),
        ),
    ).fetchone()
    order_id = int(order[0])
    for idx,(p,q) in enumerate(fills):
        level_fee = d(breakdown[idx]["net_fee"]) if idx < len(breakdown) else Decimal(0)
        conn.execute("INSERT INTO paper_fills(order_id,price,qty,level_index,liquidity,estimated_fee) VALUES (%s,%s,%s,%s,'taker',%s)", (order_id,p,q,idx,level_fee))
    conn.execute(
        """
        INSERT INTO paper_positions(portfolio_id,market_ticker,outcome_side,qty,cost_basis,fees_paid,status,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,'open',now())
        ON CONFLICT (portfolio_id,market_ticker,outcome_side)
        DO UPDATE SET qty=paper_positions.qty+EXCLUDED.qty,cost_basis=paper_positions.cost_basis+EXCLUDED.cost_basis,
                      fees_paid=paper_positions.fees_paid+EXCLUDED.fees_paid,status='open',updated_at=now()
        """,
        (portfolio_id,market_ticker,side,qty,gross,fee),
    )
    return cost


def prior_running_high(conn: psycopg.Connection[Any], station: str, before_id: int) -> Decimal | None:
    row = conn.execute(
        """
        SELECT max(GREATEST(COALESCE(temperature_f,-999),COALESCE(max_temperature_f,-999)))
          FROM live_weather_journal WHERE session_id=%s AND station_code=%s AND id<%s
        """,
        (SESSION_ID, station, before_id),
    ).fetchone()
    return None if not row or row[0] is None or d(row[0]) < -100 else d(row[0])


def prior_omo(conn: psycopg.Connection[Any], station: str, before_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id,temperature_f,raw_payload,first_seen_at,received_epoch_ms
          FROM live_weather_journal
         WHERE session_id=%s AND station_code=%s AND source='IEM_MADIS_OMO' AND id<%s
         ORDER BY id DESC LIMIT 1
        """,
        (SESSION_ID,station,before_id),
    ).fetchone()
    if not row: return None
    return {"id":int(row[0]),"temperature_f":d(row[1]),"raw":row[2],"first_seen_at":row[3],"epoch_ms":int(row[4])}


def omo_temp_c(weather: dict[str, Any]) -> Decimal | None:
    raw = weather.get("raw_payload") if isinstance(weather.get("raw_payload"), dict) else {}
    value = raw.get("temperature_c")
    return number_or_none(value)


def whole_c(value: Decimal) -> Decimal:
    # Python round/bankers behavior is undesirable for an explicit coarse-display model.
    return Decimal(math.floor(float(value) + 0.5)) if value >= 0 else Decimal(math.ceil(float(value) - 0.5))


def c_to_f(value: Decimal) -> Decimal:
    return value * Decimal(9) / Decimal(5) + Decimal(32)


def best_ask_for(conn: psycopg.Connection[Any], ticker: str, side: str, at_ms: int, cap: Decimal = ONE) -> tuple[FullBook,list[tuple[Decimal,Decimal]]] | None:
    book = reconstruct_book(conn,ticker,at_ms)
    if not book: return None
    a = asks(book,side,cap)
    return (book,a) if a else None


def hard_context(conn: psycopg.Connection[Any], weather: dict[str, Any], event: dict[str, Any], series_rules: dict[str, Any] | None) -> tuple[bool,Decimal,list[MarketSpec],list[MarketSpec]]:
    confirmed = weather["max_temperature_f"] or weather["temperature_f"]
    proven = compatibility_is_proven(conn, weather)
    dead_set = [m for m in event["markets"] if dead(m,confirmed)]
    survivors = [m for m in event["markets"] if not dead(m,confirmed)]
    supported = bool(series_rules and series_rules.get("fee_type") in ("quadratic","quadratic_with_maker_fees") and series_rules.get("fee_multiplier") is not None)
    return proven and supported, confirmed, dead_set, survivors


def run_dsn(conn: psycopg.Connection[Any], weather: dict[str, Any], event: dict[str, Any], rules: dict[str, Any], modes: list[dict[str,Any]], global_cfg: dict[str,Any], cfg: dict[str,Any]) -> None:
    ok,confirmed,dead_set,_ = hard_context(conn,weather,event,rules)
    if len(dead_set) < int(cfg.get("min_dead_markets",2)): return
    signal_id = insert_signal(conn,"DSN",weather["station_code"],event["event_ticker"],weather["first_seen_at"],weather["received_epoch_ms"],weather["id"],"hard_state",
        {"weather":weather["id"],"event":event["event_ticker"],"dead":[m.ticker for m in dead_set]},
        {"confirmed_high_f":str(confirmed),"dead_markets":[m.ticker for m in dead_set],"engine":"dsn-v1"},ok,"HARD_STATE_NOT_PROVEN" if not ok else None)
    if not ok: return
    multiplier=d(rules["fee_multiplier"])
    for mode in modes:
        pid=int(mode["portfolio_id"])
        if decision_exists(conn,pid,signal_id): continue
        latency=int(mode["config"].get("execution_latency_ms",100)); at=weather["received_epoch_ms"]+latency
        legs=[]
        max_price=d(cfg.get("max_entry_price",0.97))
        for m in dead_set:
            found=best_ask_for(conn,m.ticker,"no",at,max_price)
            if found: legs.append((m,found[0],found[1],(ONE-found[1][0][0])/found[1][0][0]))
        if not legs:
            record_decision(conn,pid,signal_id,"blocked",Decimal(0),"NO_DSN_DEPTH",{}); continue
        total_budget=budget_for(conn,mode,event["event_ticker"],STATIONS[weather["station_code"]]["region"],weather["first_seen_at"],global_cfg)
        if total_budget<=0:
            record_decision(conn,pid,signal_id,"skip",Decimal(0),"PORTFOLIO_CAP",{}); continue
        weights=sum((max(x[3],Decimal("0.0001")) for x in legs),Decimal(0)); spent=Decimal(0)
        record_decision(conn,pid,signal_id,"trade",total_budget,"DSN_MULTI_LEG",{"legs":[x[0].ticker for x in legs]},max(x[3] for x in legs))
        for m,book,a,roi in sorted(legs,key=lambda x:x[3],reverse=True):
            share=total_budget*max(roi,Decimal("0.0001"))/weights
            spent+=place_single_leg(conn,mode=mode,signal_id=signal_id,strategy="DSN",market_ticker=m.ticker,side="no",trigger_at=weather["first_seen_at"],trigger_ms=weather["received_epoch_ms"],latency_ms=latency,budget=share,book=book,side_asks=a,fee_multiplier=multiplier,evidence={"composite":"DSN","confirmed_high_f":str(confirmed)},mark_decision=False)


def basket_plan(conn: psycopg.Connection[Any], survivors: list[MarketSpec], at_ms: int, multiplier: Decimal, budget: Decimal, min_edge: Decimal) -> tuple[list[tuple[MarketSpec,FullBook,list[tuple[Decimal,Decimal]]]],Decimal] | None:
    legs=[]; max_common=None
    for m in survivors:
        found=best_ask_for(conn,m.ticker,"yes",at_ms,ONE)
        if not found: return None
        book,a=found; depth=sum((q for _,q in a),Decimal(0)); max_common=depth if max_common is None else min(max_common,depth); legs.append((m,book,a))
    if not legs or not max_common or max_common<=0: return None
    hi=int((max_common/QTY_STEP).to_integral_value(rounding=ROUND_FLOOR)); lo=0; best=0
    while lo<=hi:
        mid=(lo+hi)//2; q=QTY_STEP*mid; total=Decimal(0); feasible=True
        for _,_,a in legs:
            remaining=q; fills=[]
            for p,avail in a:
                take=min(avail,remaining)
                if take>0: fills.append((p,take)); remaining-=take
                if remaining<=0: break
            if remaining>0: feasible=False; break
            cost,_,_=total_cost(fills,multiplier); total+=cost
        if feasible and total<=budget and total <= q*(ONE-min_edge): best=mid; lo=mid+1
        else: hi=mid-1
    return None if best<=0 else (legs,QTY_STEP*best)


def run_sbk(conn: psycopg.Connection[Any], weather: dict[str, Any], event: dict[str, Any], rules: dict[str, Any], modes: list[dict[str,Any]], global_cfg: dict[str,Any], cfg: dict[str,Any]) -> None:
    ok,confirmed,_,survivors=hard_context(conn,weather,event,rules)
    if cfg.get("require_mutually_exclusive",True) and event.get("mutually_exclusive") is not True: ok=False
    signal_id=insert_signal(conn,"SBK",weather["station_code"],event["event_ticker"],weather["first_seen_at"],weather["received_epoch_ms"],weather["id"],"hard_state",
        {"weather":weather["id"],"event":event["event_ticker"],"survivors":[m.ticker for m in survivors]},
        {"confirmed_high_f":str(confirmed),"survivors":[m.ticker for m in survivors],"mutually_exclusive":event.get("mutually_exclusive"),"engine":"sbk-v1"},ok,"SBK_PREREQUISITES_UNPROVEN" if not ok else None)
    if not ok or not survivors:return
    multiplier=d(rules["fee_multiplier"]); min_edge=d(cfg.get("min_net_edge",0.01))
    for mode in modes:
        pid=int(mode["portfolio_id"])
        if decision_exists(conn,pid,signal_id):continue
        latency=int(mode["config"].get("execution_latency_ms",100)); at=weather["received_epoch_ms"]+latency
        budget=budget_for(conn,mode,event["event_ticker"],STATIONS[weather["station_code"]]["region"],weather["first_seen_at"],global_cfg)
        plan=basket_plan(conn,survivors,at,multiplier,budget,min_edge)
        if not plan:
            record_decision(conn,pid,signal_id,"skip",budget,"NO_PROFITABLE_COMPLETE_SURVIVOR_BASKET",{});continue
        legs,q=plan; record_decision(conn,pid,signal_id,"trade",budget,"COMPLETE_SURVIVOR_BASKET",{"qty_each":str(q),"legs":[m.ticker for m,_,_ in legs]})
        per_leg=budget/Decimal(len(legs))
        for m,book,a in legs:
            # Cap each leg to exactly q contracts by trimming the ask ladder.
            remaining=q; trimmed=[]
            for p,avail in a:
                take=min(avail,remaining)
                if take>0:trimmed.append((p,take));remaining-=take
                if remaining<=0:break
            exact_cost,_,_=total_cost(trimmed,multiplier)
            place_single_leg(conn,mode=mode,signal_id=signal_id,strategy="SBK",market_ticker=m.ticker,side="yes",trigger_at=weather["first_seen_at"],trigger_ms=weather["received_epoch_ms"],latency_ms=latency,budget=exact_cost,book=book,side_asks=trimmed,fee_multiplier=multiplier,evidence={"composite":"SBK","qty_each":str(q)},mark_decision=False)


def run_hsr(conn: psycopg.Connection[Any], weather: dict[str, Any], event: dict[str, Any], rules: dict[str, Any], modes: list[dict[str,Any]], global_cfg: dict[str,Any], cfg: dict[str,Any]) -> None:
    ok,confirmed,dead_set,survivors=hard_context(conn,weather,event,rules)
    if not ok or not dead_set:return
    signal_id=insert_signal(conn,"HSR",weather["station_code"],event["event_ticker"],weather["first_seen_at"],weather["received_epoch_ms"],weather["id"],"hard_state",
        {"weather":weather["id"],"event":event["event_ticker"]},{"confirmed_high_f":str(confirmed),"engine":"hsr-v1"},True)
    multiplier=d(rules["fee_multiplier"]); min_edge=d(cfg.get("min_net_edge",0.01))
    for mode in modes:
        pid=int(mode["portfolio_id"])
        if decision_exists(conn,pid,signal_id):continue
        latency=int(mode["config"].get("execution_latency_ms",100));at=weather["received_epoch_ms"]+latency
        budget=budget_for(conn,mode,event["event_ticker"],STATIONS[weather["station_code"]]["region"],weather["first_seen_at"],global_cfg)
        best_dead=None
        for m in dead_set:
            found=best_ask_for(conn,m.ticker,"no",at,Decimal("0.99"))
            if found:
                roi=(ONE-found[1][0][0])/found[1][0][0]
                if best_dead is None or roi>best_dead[3]:best_dead=(m,found[0],found[1],roi)
        basket=basket_plan(conn,survivors,at,multiplier,min(budget,Decimal("25")),min_edge) if event.get("mutually_exclusive") is True else None
        basket_roi=None
        if basket:
            legs,q=basket; total=Decimal(0)
            for _,_,a in legs:
                remaining=q; f=[]
                for p,av in a:
                    take=min(av,remaining)
                    if take>0:f.append((p,take));remaining-=take
                    if remaining<=0:break
                c,_,_=total_cost(f,multiplier);total+=c
            basket_roi=(q-total)/total if total>0 else Decimal(0)
        if not best_dead and not basket:
            record_decision(conn,pid,signal_id,"blocked",budget,"NO_HSR_ROUTE",{});continue
        if basket and basket_roi is not None and (not best_dead or basket_roi>best_dead[3]):
            record_decision(conn,pid,signal_id,"trade",budget,"HSR_SBK_ROUTE",{"route":"SBK","roi":str(basket_roi)})
            legs,q=basket
            for m,book,a in legs:
                remaining=q;trim=[]
                for p,av in a:
                    take=min(av,remaining)
                    if take>0:trim.append((p,take));remaining-=take
                    if remaining<=0:break
                exact,_,_=total_cost(trim,multiplier)
                place_single_leg(conn,mode=mode,signal_id=signal_id,strategy="HSR",market_ticker=m.ticker,side="yes",trigger_at=weather["first_seen_at"],trigger_ms=weather["received_epoch_ms"],latency_ms=latency,budget=exact,book=book,side_asks=trim,fee_multiplier=multiplier,evidence={"route":"SBK"},mark_decision=False)
        elif best_dead:
            m,book,a,roi=best_dead; record_decision(conn,pid,signal_id,"trade",budget,"HSR_DBN_ROUTE",{"route":"DBN","market":m.ticker,"roi":str(roi)},roi)
            place_single_leg(conn,mode=mode,signal_id=signal_id,strategy="HSR",market_ticker=m.ticker,side="no",trigger_at=weather["first_seen_at"],trigger_ms=weather["received_epoch_ms"],latency_ms=latency,budget=budget,book=book,side_asks=a,fee_multiplier=multiplier,evidence={"route":"DBN"},mark_decision=False)


def run_wty(conn: psycopg.Connection[Any], weather: dict[str,Any], event: dict[str,Any], rules: dict[str,Any], modes:list[dict[str,Any]], global_cfg:dict[str,Any], cfg:dict[str,Any]) -> None:
    current=weather["max_temperature_f"] or weather["temperature_f"]; prior=prior_running_high(conn,weather["station_code"],weather["id"])
    if prior is not None and current<=prior:return
    survivors=[m for m in event["markets"] if not dead(m,current)]
    if not survivors or not rules or rules.get("fee_multiplier") is None:return
    lookback=int(cfg.get("pre_signal_lookback_ms",5000)); pre_ms=max(0,weather["received_epoch_ms"]-lookback)
    probs=[]
    for m in survivors:
        b=reconstruct_book(conn,m.ticker,pre_ms)
        if not b:continue
        mid=midpoint(b,"yes")
        if mid is not None and mid>0:probs.append((m,mid))
    total=sum((p for _,p in probs),Decimal(0))
    if total<=0:return
    target,p=max(probs,key=lambda x:x[1]); model_prob=min(ONE,p/total)
    signal_id=insert_signal(conn,"WTY",weather["station_code"],event["event_ticker"],weather["first_seen_at"],weather["received_epoch_ms"],weather["id"],"probabilistic",
        {"weather":weather["id"],"event":event["event_ticker"],"target":target.ticker},{"target":target.ticker,"model_prob":str(model_prob),"model":"pre_signal_survivor_renormalization","engine":"wty-v1"},True)
    multiplier=d(rules["fee_multiplier"]);min_edge=d(cfg.get("min_model_edge",0.03));max_price=d(cfg.get("max_yes_price",0.80))
    for mode in modes:
        pid=int(mode["portfolio_id"])
        if decision_exists(conn,pid,signal_id):continue
        latency=int(mode["config"].get("execution_latency_ms",100));found=best_ask_for(conn,target.ticker,"yes",weather["received_epoch_ms"]+latency,max_price)
        if not found:
            record_decision(conn,pid,signal_id,"blocked",Decimal(0),"NO_WTY_DEPTH",{});continue
        entry=found[1][0][0]; edge=model_prob-entry
        if edge<min_edge:
            record_decision(conn,pid,signal_id,"skip",Decimal(0),"WTY_MODEL_EDGE_TOO_SMALL",{"model_prob":str(model_prob),"ask":str(entry),"edge":str(edge)},edge);continue
        budget=budget_for(conn,mode,event["event_ticker"],STATIONS[weather["station_code"]]["region"],weather["first_seen_at"],global_cfg)
        place_single_leg(conn,mode=mode,signal_id=signal_id,strategy="WTY",market_ticker=target.ticker,side="yes",trigger_at=weather["first_seen_at"],trigger_ms=weather["received_epoch_ms"],latency_ms=latency,budget=budget,book=found[0],side_asks=found[1],fee_multiplier=multiplier,evidence={"model_prob":str(model_prob),"edge":str(edge)})


def momentum_context(conn: psycopg.Connection[Any], weather: dict[str,Any], event:dict[str,Any]) -> dict[str,Any] | None:
    if weather["source"]!="IEM_MADIS_OMO":return None
    precise_c=omo_temp_c(weather); prev=prior_omo(conn,weather["station_code"],weather["id"])
    if precise_c is None or not prev:return None
    prev_raw=prev["raw"] if isinstance(prev["raw"],dict) else {}; prev_c=number_or_none(prev_raw.get("temperature_c"))
    if prev_c is None:return None
    coarse=whole_c(precise_c); prev_coarse=whole_c(prev_c)
    if coarse<=prev_coarse:return None
    coarse_f=c_to_f(coarse); precise_f=c_to_f(precise_c)
    target=next((m for m in event["markets"] if contains(m,coarse_f)),None)
    precise_target=next((m for m in event["markets"] if contains(m,precise_f)),None)
    if not target:return None
    return {"precise_c":precise_c,"precise_f":precise_f,"coarse_c":coarse,"coarse_f":coarse_f,"target":target,"precise_target":precise_target,"prior_omo":prev}


def run_rmo_and_lvp_leg1(conn:psycopg.Connection[Any],weather:dict[str,Any],event:dict[str,Any],rules:dict[str,Any],modes:list[dict[str,Any]],global_cfg:dict[str,Any],rmo_cfg:dict[str,Any],lvp_cfg:dict[str,Any],enabled:set[str])->None:
    if weather["station_code"] not in set(rmo_cfg.get("stations",["KLAX"])):return
    ctx=momentum_context(conn,weather,event)
    if not ctx or not rules or rules.get("fee_multiplier") is None:return
    confirm=int(rmo_cfg.get("confirmation_delay_ms",1000)); pre_book=reconstruct_book(conn,ctx["target"].ticker,weather["received_epoch_ms"]-1)
    post_book=reconstruct_book(conn,ctx["target"].ticker,weather["received_epoch_ms"]+confirm)
    if not pre_book or not post_book:return
    pre=midpoint(pre_book,"yes");post=midpoint(post_book,"yes")
    if pre is None or post is None:return
    momentum=post-pre;min_mom=d(rmo_cfg.get("min_momentum_cents",2))/Decimal(100)
    if momentum<min_mom:return
    mult=d(rules["fee_multiplier"]);max_price=d(rmo_cfg.get("max_yes_price",0.80))
    for strategy,leg in (("RMO","single"),("LVP","leg1")):
        if strategy not in enabled:return_if_false=False
        if strategy not in enabled:continue
        sig=insert_signal(conn,strategy,weather["station_code"],event["event_ticker"],weather["first_seen_at"],weather["received_epoch_ms"],weather["id"],"probabilistic",
            {"weather":weather["id"],"event":event["event_ticker"],"target":ctx["target"].ticker,"leg":leg},{"target":ctx["target"].ticker,"leg":leg,"coarse_c":str(ctx["coarse_c"]),"precise_c":str(ctx["precise_c"]),"momentum":str(momentum),"engine":f"{strategy.lower()}-v1"},True)
        for mode in modes:
            pid=int(mode["portfolio_id"])
            if decision_exists(conn,pid,sig):continue
            latency=confirm+int(mode["config"].get("execution_latency_ms",100));found=best_ask_for(conn,ctx["target"].ticker,"yes",weather["received_epoch_ms"]+latency,max_price)
            if not found:
                record_decision(conn,pid,sig,"blocked",Decimal(0),"NO_MOMENTUM_DEPTH",{});continue
            budget=budget_for(conn,mode,event["event_ticker"],STATIONS[weather["station_code"]]["region"],weather["first_seen_at"],global_cfg)
            place_single_leg(conn,mode=mode,signal_id=sig,strategy=strategy,market_ticker=ctx["target"].ticker,side="yes",trigger_at=weather["first_seen_at"],trigger_ms=weather["received_epoch_ms"],latency_ms=latency,budget=budget,book=found[0],side_asks=found[1],fee_multiplier=mult,evidence={"leg":leg,"momentum":str(momentum)})


def prior_strategy_signal(conn:psycopg.Connection[Any],strategy:str,station:str,event:str,when:datetime,window_minutes:int,leg:str|None=None)->dict[str,Any]|None:
    rows=conn.execute(
        """
        SELECT id,triggered_at,evidence FROM paper_signals
         WHERE session_id=%s AND strategy_code=%s AND station_code=%s AND event_ticker=%s
           AND triggered_at BETWEEN %s AND %s ORDER BY triggered_at DESC LIMIT 20
        """,
        (SESSION_ID,strategy,station,event,when-timedelta(minutes=window_minutes),when),
    ).fetchall()
    for sid,ts,evidence in rows:
        ev=evidence if isinstance(evidence,dict) else {}
        if leg is None or ev.get("leg")==leg:return {"id":int(sid),"triggered_at":ts,"evidence":ev}
    return None


def run_prv_and_lvp_leg2(conn:psycopg.Connection[Any],weather:dict[str,Any],event:dict[str,Any],rules:dict[str,Any],modes:list[dict[str,Any]],global_cfg:dict[str,Any],prv_cfg:dict[str,Any],lvp_cfg:dict[str,Any],enabled:set[str])->None:
    if weather["station_code"] not in set(prv_cfg.get("stations",["KLAX"])):return
    ctx=momentum_context(conn,weather,event)
    if not ctx or ctx["precise_target"] is None or ctx["precise_target"].ticker==ctx["target"].ticker:return
    # Reversal only when the coarse display points to a strictly higher bucket than precise value.
    if ctx["target"].floor is None or ctx["target"].floor<=ctx["precise_f"]:return
    if not rules or rules.get("fee_multiplier") is None:return
    mult=d(rules["fee_multiplier"]);max_no=d(prv_cfg.get("max_no_price",0.80))
    strategies=[]
    if "PRV" in enabled:strategies.append(("PRV","single",None))
    if "LVP" in enabled:
        prior=prior_strategy_signal(conn,"LVP",weather["station_code"],event["event_ticker"],weather["first_seen_at"],int(lvp_cfg.get("pair_window_minutes",180)),"leg1")
        if prior:strategies.append(("LVP","leg2",prior))
    for strategy,leg,prior in strategies:
        sig=insert_signal(conn,strategy,weather["station_code"],event["event_ticker"],weather["first_seen_at"],weather["received_epoch_ms"],weather["id"],"probabilistic",
            {"weather":weather["id"],"event":event["event_ticker"],"target":ctx["target"].ticker,"leg":leg},{"target":ctx["target"].ticker,"leg":leg,"coarse_f":str(ctx["coarse_f"]),"precise_f":str(ctx["precise_f"]),"paired_signal_id":prior["id"] if prior else None,"engine":f"{strategy.lower()}-v1"},True)
        for mode in modes:
            pid=int(mode["portfolio_id"])
            if decision_exists(conn,pid,sig):continue
            latency=int(mode["config"].get("execution_latency_ms",100));found=best_ask_for(conn,ctx["target"].ticker,"no",weather["received_epoch_ms"]+latency,max_no)
            if not found:
                record_decision(conn,pid,sig,"blocked",Decimal(0),"NO_REVERSAL_DEPTH",{});continue
            budget=budget_for(conn,mode,event["event_ticker"],STATIONS[weather["station_code"]]["region"],weather["first_seen_at"],global_cfg)
            place_single_leg(conn,mode=mode,signal_id=sig,strategy=strategy,market_ticker=ctx["target"].ticker,side="no",trigger_at=weather["first_seen_at"],trigger_ms=weather["received_epoch_ms"],latency_ms=latency,budget=budget,book=found[0],side_asks=found[1],fee_multiplier=mult,evidence={"leg":leg,"paired_signal_id":prior["id"] if prior else None})


def run_hmf(conn:psycopg.Connection[Any],weather:dict[str,Any],event:dict[str,Any],rules:dict[str,Any],modes:list[dict[str,Any]],global_cfg:dict[str,Any],cfg:dict[str,Any])->None:
    if weather["station_code"] not in set(cfg.get("stations",["KLAX"])) or not compatibility_is_proven(conn,weather):return
    current=weather["max_temperature_f"] or weather["temperature_f"]
    prior=prior_strategy_signal(conn,"RMO",weather["station_code"],event["event_ticker"],weather["first_seen_at"],int(cfg.get("pair_window_minutes",240)))
    if not prior:return
    target_ticker=str(prior["evidence"].get("target") or ""); target=next((m for m in event["markets"] if m.ticker==target_ticker),None)
    if not target or not dead(target,current) or not rules or rules.get("fee_multiplier") is None:return
    sig=insert_signal(conn,"HMF",weather["station_code"],event["event_ticker"],weather["first_seen_at"],weather["received_epoch_ms"],weather["id"],"hard_state",
        {"weather":weather["id"],"event":event["event_ticker"],"prior_rmo":prior["id"],"target":target_ticker},{"target":target_ticker,"prior_rmo_signal_id":prior["id"],"confirmed_high_f":str(current),"engine":"hmf-v1"},True)
    mult=d(rules["fee_multiplier"])
    for mode in modes:
        pid=int(mode["portfolio_id"])
        if decision_exists(conn,pid,sig):continue
        latency=int(mode["config"].get("execution_latency_ms",100));found=best_ask_for(conn,target_ticker,"no",weather["received_epoch_ms"]+latency,Decimal("0.99"))
        if not found:
            record_decision(conn,pid,sig,"blocked",Decimal(0),"NO_HMF_DEPTH",{});continue
        budget=budget_for(conn,mode,event["event_ticker"],STATIONS[weather["station_code"]]["region"],weather["first_seen_at"],global_cfg)
        place_single_leg(conn,mode=mode,signal_id=sig,strategy="HMF",market_ticker=target_ticker,side="no",trigger_at=weather["first_seen_at"],trigger_ms=weather["received_epoch_ms"],latency_ms=latency,budget=budget,book=found[0],side_asks=found[1],fee_multiplier=mult,evidence={"prior_rmo_signal_id":prior["id"],"confirmed_high_f":str(current)})


def process_weather(conn:psycopg.Connection[Any],weather:dict[str,Any])->None:
    meta=STATIONS.get(weather["station_code"]); current=weather["max_temperature_f"] or weather["temperature_f"]
    if not meta or current is None:return
    configs=strategy_configs(conn); enabled={code for code,c in configs.items() if c["enabled"] and c["paper"]}
    if not enabled:return
    rules=series_rules_before(conn,meta["series"],weather["first_seen_at"])
    if not rules:return
    events=event_candidates(conn,meta["series"],weather["first_seen_at"]); modes=load_modes(conn); global_cfg=load_global(conn)
    for event in events:
        if "DSN" in enabled:run_dsn(conn,weather,event,rules,modes,global_cfg,configs["DSN"]["config"])
        if "SBK" in enabled:run_sbk(conn,weather,event,rules,modes,global_cfg,configs["SBK"]["config"])
        if "HSR" in enabled:run_hsr(conn,weather,event,rules,modes,global_cfg,configs["HSR"]["config"])
        if "WTY" in enabled:run_wty(conn,weather,event,rules,modes,global_cfg,configs["WTY"]["config"])
        if "RMO" in enabled or "LVP" in enabled:run_rmo_and_lvp_leg1(conn,weather,event,rules,modes,global_cfg,configs.get("RMO",{}).get("config",{}),configs.get("LVP",{}).get("config",{}),enabled)
        if "PRV" in enabled or "LVP" in enabled:run_prv_and_lvp_leg2(conn,weather,event,rules,modes,global_cfg,configs.get("PRV",{}).get("config",{}),configs.get("LVP",{}).get("config",{}),enabled)
        if "HMF" in enabled:run_hmf(conn,weather,event,rules,modes,global_cfg,configs["HMF"]["config"])


def ensure_state(conn:psycopg.Connection[Any])->None:
    conn.execute("INSERT INTO paper_engine_state(session_id,component,last_weather_id) VALUES (%s,'strategy_engine',0) ON CONFLICT DO NOTHING",(SESSION_ID,));conn.commit()


def weather_from_row(row:tuple[Any,...])->dict[str,Any]:
    return {"id":int(row[0]),"station_code":str(row[1]),"source":str(row[2]),"report_type":str(row[3]),"observed_at":row[4],"first_seen_at":row[5],"received_epoch_ms":int(row[6]),"received_epoch_ns":int(row[7]),"temperature_f":d(row[8]) if row[8] is not None else None,"max_temperature_f":d(row[9]) if row[9] is not None else None,"compatibility_status":str(row[10]),"compatibility_rule":str(row[11]) if row[11] is not None else None,"raw_payload":row[12] if isinstance(row[12],dict) else {}}


def main()->int:
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    print(json.dumps({"event":"strategy_engine_start","session_id":SESSION_ID,"engine_version":ENGINE_VERSION}))
    with psycopg.connect(DATABASE_URL) as conn:
        ensure_state(conn)
        while not STOP:
            global_cfg=load_global(conn);delay=max(1500,int(global_cfg.get("paper_engine_process_delay_ms",DEFAULT_DELAY_MS)))
            state=conn.execute("SELECT last_weather_id FROM paper_engine_state WHERE session_id=%s AND component='strategy_engine'",(SESSION_ID,)).fetchone();last=int(state[0]) if state else 0
            cutoff=time.time_ns()//1_000_000-delay
            rows=conn.execute(
                """
                SELECT id,station_code,source,report_type,observed_at,first_seen_at,received_epoch_ms,received_epoch_ns,
                       temperature_f,max_temperature_f,compatibility_status,compatibility_rule,raw_payload
                  FROM live_weather_journal WHERE session_id=%s AND id>%s AND received_epoch_ms<=%s ORDER BY id ASC LIMIT 60
                """,(SESSION_ID,last,cutoff)).fetchall()
            if not rows:
                time.sleep(POLL_MS/1000);continue
            for row in rows:
                weather=weather_from_row(row)
                try:
                    with conn.transaction():
                        process_weather(conn,weather)
                        conn.execute("UPDATE paper_engine_state SET last_weather_id=%s,updated_at=now() WHERE session_id=%s AND component='strategy_engine'",(weather["id"],SESSION_ID))
                except Exception as exc:
                    conn.rollback()
                    with conn.transaction():
                        conn.execute("INSERT INTO audit_findings(session_id,severity,component,finding_code,station_code,details) VALUES (%s,'error','strategy_engine','STRATEGY_WEATHER_FAILED',%s,%s::jsonb)",(SESSION_ID,weather["station_code"],json.dumps({"weather_id":weather["id"],"error":repr(exc)})))
                        conn.execute("UPDATE paper_engine_state SET last_weather_id=%s,updated_at=now() WHERE session_id=%s AND component='strategy_engine'",(weather["id"],SESSION_ID))
            conn.commit()
    print(json.dumps({"event":"strategy_engine_stop","session_id":SESSION_ID}));return 0


if __name__=="__main__":raise SystemExit(main())
