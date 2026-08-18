from __future__ import annotations

"""Paper-only strategy engines beyond the original DBN path.

Design goals:
- same immutable weather/L2 evidence for every portfolio mode;
- no real-order path;
- hard-state strategies fail closed on settlement compatibility;
- probabilistic/precision strategies are explicitly labelled paper research;
- multi-leg strategies never pretend Kalshi gives us atomic cross-market fills.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any

import psycopg

from stations import STATIONS

ONE = Decimal("1")
CENT = Decimal("0.01")
TEN_THOUSANDTH = Decimal("0.0001")
QTY_STEP = Decimal("0.01")
ENGINE_VERSION = "all-strategies-paper-v1"


@dataclass
class FullBook:
    yes_bids: dict[Decimal, Decimal]
    no_bids: dict[Decimal, Decimal]
    connection_id: str
    last_seq: int | None
    snapshot_id: int
    snapshot_received_ms: int


@dataclass
class Leg:
    market_ticker: str
    side: str
    trigger_epoch_ms: int
    max_price: Decimal
    label: str
    model_probability: Decimal | None = None


@dataclass
class Bundle:
    strategy_code: str
    signal_class: str
    station: str
    region: str
    event_ticker: str
    weather_id: int
    trigger_at: datetime
    trigger_epoch_ms: int
    legs: list[Leg]
    evidence: dict[str, Any]
    auditor_status: str
    blocked_reason: str | None
    equal_leg_quantity: bool = False
    expected_payout_per_unit: Decimal | None = None
    route_options: dict[str, list[Leg]] | None = None


def d(value: Any) -> Decimal:
    return Decimal(str(value))


def canonical_hash(value: Any) -> str:
    def default(v: Any) -> str:
        if isinstance(v, Decimal):
            return format(v, "f")
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=default).encode()).hexdigest()


def json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def strategy_configs(conn: psycopg.Connection[Any]) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT strategy_code,enabled,paper_trade_enabled,shadow_enabled,config
          FROM paper_strategy_configs
        """
    ).fetchall()
    return {
        str(code): {
            "enabled": bool(enabled),
            "paper_trade_enabled": bool(paper),
            "shadow_enabled": bool(shadow),
            "config": dict(config) if isinstance(config, dict) else {},
        }
        for code, enabled, paper, shadow, config in rows
    }


def compatibility_proven(conn: psycopg.Connection[Any], weather: dict[str, Any]) -> bool:
    key = weather.get("compatibility_rule")
    if weather.get("compatibility_status") != "proven" or not key:
        return False
    row = conn.execute("SELECT status FROM source_compatibility_rules WHERE compatibility_key=%s", (key,)).fetchone()
    return bool(row and str(row[0]) == "proven")


def series_rules_before(conn: psycopg.Connection[Any], series: str, when: datetime) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT rules_hash,fee_type,fee_multiplier,settlement_sources,captured_at
          FROM settlement_rule_snapshots
         WHERE series_ticker=%s AND event_ticker IS NULL AND captured_at<=%s
         ORDER BY captured_at DESC,id DESC LIMIT 1
        """,
        (series, when),
    ).fetchone()
    if not row:
        return None
    return {
        "rules_hash": str(row[0]),
        "fee_type": str(row[1]) if row[1] is not None else None,
        "fee_multiplier": d(row[2]) if row[2] is not None else None,
        "settlement_sources": row[3],
        "captured_at": row[4],
    }


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _active_at(market: dict[str, Any], when: datetime) -> bool:
    opened = _parse_time(market.get("open_time"))
    closed = _parse_time(market.get("close_time") or market.get("expected_expiration_time"))
    return (not opened or when >= opened) and (not closed or when <= closed)


def event_rules_before(conn: psycopg.Connection[Any], series: str, when: datetime) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_ticker,rules_hash,raw_payload,captured_at
          FROM settlement_rule_snapshots
         WHERE series_ticker=%s AND event_ticker IS NOT NULL AND captured_at<=%s
         ORDER BY captured_at DESC,id DESC LIMIT 60
        """,
        (series, when),
    ).fetchall()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ticker, rules_hash, raw_payload, captured_at in rows:
        event_ticker = str(ticker)
        if event_ticker in seen:
            continue
        seen.add(event_ticker)
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
        markets = [m for m in (event.get("markets") or []) if isinstance(m, dict) and _active_at(m, when)]
        if not markets:
            continue
        out.append({
            "event_ticker": event_ticker,
            "rules_hash": str(rules_hash),
            "captured_at": captured_at,
            "mutually_exclusive": bool(event.get("mutually_exclusive")) if event.get("mutually_exclusive") is not None else None,
            "markets": markets,
        })
    return out


def lower_bound(market: dict[str, Any]) -> Decimal | None:
    raw = market.get("floor_strike")
    if raw is None:
        return None
    try:
        return d(raw)
    except Exception:
        return None


def upper_bound(market: dict[str, Any]) -> Decimal | None:
    raw = market.get("cap_strike")
    if raw is None:
        return None
    try:
        return d(raw)
    except Exception:
        return None


def event_is_exhaustive_partition(markets: list[dict[str, Any]]) -> bool:
    """Conservative structural check, not a settlement claim.

    Temperature bucket events should contain a lower-tail and upper-tail market.
    We do not infer exact adjacency from undocumented display conventions.
    """
    if len(markets) < 2:
        return False
    has_lower_tail = any(lower_bound(m) is None for m in markets)
    has_upper_tail = any(upper_bound(m) is None for m in markets)
    return has_lower_tail and has_upper_tail


def reconstruct_full_book(conn: psycopg.Connection[Any], session_id: str, ticker: str, arrival_ms: int) -> FullBook | None:
    snap = conn.execute(
        """
        SELECT id,connection_id::text,seq,received_epoch_ms,raw_text,price_mode
          FROM market_data_journal
         WHERE session_id=%s AND market_ticker=%s AND channel='orderbook_snapshot'
           AND received_epoch_ms<=%s
         ORDER BY received_epoch_ms DESC,id DESC LIMIT 1
        """,
        (session_id, ticker, arrival_ms),
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
        (session_id, connection_id, arrival_ms),
    ).fetchone()
    if fatal:
        return None
    rows = conn.execute(
        """
        SELECT channel,seq,raw_text,price_mode
          FROM market_data_journal
         WHERE session_id=%s AND connection_id=%s::uuid AND market_ticker=%s
           AND id>=%s AND received_epoch_ms<=%s
           AND channel IN ('orderbook_snapshot','orderbook_delta')
         ORDER BY id ASC
        """,
        (session_id, connection_id, ticker, snapshot_id, arrival_ms),
    ).fetchall()
    yes: dict[Decimal, Decimal] = {}
    no: dict[Decimal, Decimal] = {}
    seen = False
    last_seq: int | None = None
    for channel, seq, raw_text, price_mode in rows:
        data = json.loads(str(raw_text))
        msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
        unified = str(price_mode or "") == "unified_yes"
        if channel == "orderbook_snapshot":
            yes.clear(); no.clear(); seen = True
            for level in msg.get("yes_dollars_fp") or []:
                if isinstance(level, list) and len(level) >= 2:
                    p, q = d(level[0]), d(level[1])
                    if q > 0: yes[p] = q
            for level in msg.get("no_dollars_fp") or []:
                if isinstance(level, list) and len(level) >= 2:
                    raw_p, q = d(level[0]), d(level[1])
                    p = ONE - raw_p if unified else raw_p
                    if q > 0: no[p] = q
        elif channel == "orderbook_delta" and seen:
            side = str(msg.get("side"))
            raw_p, delta = d(msg.get("price_dollars")), d(msg.get("delta_fp"))
            target = yes if side == "yes" else no if side == "no" else None
            if target is None:
                continue
            p = ONE - raw_p if side == "no" and unified else raw_p
            nxt = target.get(p, Decimal(0)) + delta
            if nxt < 0:
                return None
            if nxt == 0: target.pop(p, None)
            else: target[p] = nxt
        if seq is not None:
            last_seq = int(seq)
    if not seen:
        return None
    if yes and no and max(yes) + max(no) > ONE:
        return None
    return FullBook(yes, no, connection_id, last_seq, snapshot_id, int(snap[3]))


def asks(book: FullBook, side: str, max_price: Decimal = ONE) -> list[tuple[Decimal, Decimal]]:
    opposite = book.no_bids if side == "yes" else book.yes_bids
    result = [(ONE - p, q) for p, q in opposite.items() if q > 0 and ONE - p <= max_price]
    return sorted(result, key=lambda x: x[0])


def best_bid(book: FullBook, side: str) -> Decimal | None:
    source = book.yes_bids if side == "yes" else book.no_bids
    return max(source) if source else None


def best_ask(book: FullBook, side: str) -> Decimal | None:
    levels = asks(book, side)
    return levels[0][0] if levels else None


def implied_mid(book: FullBook, side: str = "yes") -> Decimal | None:
    bid = best_bid(book, side)
    ask = best_ask(book, side)
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return bid if bid is not None else ask


def ceil_increment(value: Decimal, increment: Decimal) -> Decimal:
    if value <= 0:
        return Decimal(0)
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def fee_for_fills(fills: list[tuple[Decimal, Decimal]], multiplier: Decimal) -> tuple[Decimal, list[dict[str, str]]]:
    accumulator = Decimal(0); total = Decimal(0); details: list[dict[str, str]] = []
    for price, qty in fills:
        trade_fee = ceil_increment(multiplier * Decimal("0.07") * qty * price * (ONE - price), TEN_THOUSANDTH)
        balance_change = -(price * qty) - trade_fee
        floored = (balance_change / CENT).to_integral_value(rounding=ROUND_FLOOR) * CENT
        rounding_fee = balance_change - floored
        accumulator += rounding_fee
        rebate = (accumulator / CENT).to_integral_value(rounding=ROUND_FLOOR) * CENT
        accumulator -= rebate
        net = max(Decimal(0), trade_fee + rounding_fee - rebate)
        total += net
        details.append({"trade_fee": format(trade_fee,"f"),"rounding_fee":format(rounding_fee,"f"),"rebate":format(rebate,"f"),"net_fee":format(net,"f")})
    return total, details


def total_cost(fills: list[tuple[Decimal, Decimal]], multiplier: Decimal) -> tuple[Decimal, Decimal, list[dict[str, str]]]:
    gross = sum((p*q for p,q in fills), Decimal(0))
    fee, breakdown = fee_for_fills(fills, multiplier)
    return gross + fee, fee, breakdown


def fills_for_budget(levels: list[tuple[Decimal, Decimal]], budget: Decimal, multiplier: Decimal) -> tuple[list[tuple[Decimal, Decimal]], Decimal, Decimal, list[dict[str,str]]]:
    fills: list[tuple[Decimal, Decimal]] = []
    if budget <= 0:
        return fills, Decimal(0), Decimal(0), []
    for price, avail in levels:
        high = int((avail / QTY_STEP).to_integral_value(rounding=ROUND_FLOOR)); lo=0; best=0
        while lo <= high:
            mid=(lo+high)//2
            trial=fills+([(price,QTY_STEP*mid)] if mid else [])
            cost,_,_=total_cost(trial,multiplier)
            if cost<=budget: best=mid; lo=mid+1
            else: high=mid-1
        if best: fills.append((price,QTY_STEP*best))
        cost,_,_=total_cost(fills,multiplier)
        if budget-cost < price*QTY_STEP: break
    cost,fee,breakdown=total_cost(fills,multiplier)
    return fills,cost,fee,breakdown


def latest_prior_weather(conn: psycopg.Connection[Any], session_id: str, weather: dict[str, Any], source: str | None = None) -> dict[str, Any] | None:
    clauses=["session_id=%s","station_code=%s","id<%s","temperature_f IS NOT NULL"]
    params:list[Any]=[session_id,weather["station_code"],weather["id"]]
    if source:
        clauses.append("source=%s"); params.append(source)
    row=conn.execute(
        f"SELECT id,temperature_f,max_temperature_f,first_seen_at,received_epoch_ms,source FROM live_weather_journal WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone()
    if not row: return None
    return {"id":int(row[0]),"temperature_f":d(row[1]),"max_temperature_f":d(row[2]) if row[2] is not None else None,"first_seen_at":row[3],"received_epoch_ms":int(row[4]),"source":str(row[5])}


def insert_signal(conn: psycopg.Connection[Any], session_id: str, bundle: Bundle) -> int:
    state_hash=canonical_hash({"strategy":bundle.strategy_code,"weather_id":bundle.weather_id,"event":bundle.event_ticker,"legs":[(l.market_ticker,l.side,l.label,l.trigger_epoch_ms) for l in bundle.legs],"evidence":bundle.evidence})
    row=conn.execute(
        """
        INSERT INTO paper_signals(session_id,station_code,event_ticker,strategy_code,signal_class,triggered_at,trigger_epoch_ms,trigger_epoch_ns,weather_event_id,state_hash,evidence,auditor_status,blocked_reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
        ON CONFLICT (session_id,strategy_code,state_hash)
        DO UPDATE SET evidence=EXCLUDED.evidence,auditor_status=EXCLUDED.auditor_status,blocked_reason=EXCLUDED.blocked_reason
        RETURNING id
        """,
        (session_id,bundle.station,bundle.event_ticker,bundle.strategy_code,bundle.signal_class,bundle.trigger_at,bundle.trigger_epoch_ms,str(bundle.trigger_epoch_ms*1_000_000),bundle.weather_id,state_hash,json.dumps(bundle.evidence,separators=(",",":"),default=json_default),bundle.auditor_status,bundle.blocked_reason),
    ).fetchone()
    if not row: raise RuntimeError("generic paper signal insert failed")
    return int(row[0])


def exposure(conn: psycopg.Connection[Any], portfolio_id:int, event_ticker:str, region_stations:list[str], when:datetime, day_tz:str)->tuple[Decimal,Decimal,Decimal]:
    e=conn.execute("SELECT COALESCE(sum(o.total_cost_micros),0) FROM paper_orders o JOIN paper_signals s ON s.id=o.signal_id WHERE o.portfolio_id=%s AND o.status IN ('filled','partial') AND s.event_ticker=%s",(portfolio_id,event_ticker)).fetchone()
    r=conn.execute("SELECT COALESCE(sum(o.total_cost_micros),0) FROM paper_orders o JOIN paper_signals s ON s.id=o.signal_id WHERE o.portfolio_id=%s AND o.status IN ('filled','partial') AND s.station_code=ANY(%s)",(portfolio_id,region_stations)).fetchone()
    day=conn.execute("SELECT COALESCE(sum(total_cost_micros),0) FROM paper_orders WHERE portfolio_id=%s AND status IN ('filled','partial') AND (decision_at AT TIME ZONE %s)::date=(%s AT TIME ZONE %s)::date",(portfolio_id,day_tz,when,day_tz)).fetchone()
    return d(e[0])/1_000_000,d(r[0])/1_000_000,d(day[0])/1_000_000


def portfolio_budget(conn:psycopg.Connection[Any], mode:dict[str,Any], bundle:Bundle, global_cfg:dict[str,Any], strategy_cfg:dict[str,Any])->Decimal:
    row=conn.execute("SELECT starting_bankroll,cash_balance FROM paper_portfolios WHERE id=%s FOR UPDATE",(mode["portfolio_id"],)).fetchone()
    start,cash=d(row[0]),d(row[1]); cfg=mode["config"]
    reserve=start*d(cfg.get("reserve_pct",0)); usable=max(Decimal(0),cash-reserve)
    region_stations=[s for s,m in STATIONS.items() if m.get("region")==bundle.region]
    eu,ru,du=exposure(conn,int(mode["portfolio_id"]),bundle.event_ticker,region_stations,bundle.trigger_at,str(global_cfg.get("portfolio_day_timezone","America/New_York")))
    cap=min(
        usable,
        start*d(cfg.get("max_trade_pct",1)),
        max(Decimal(0),start*d(cfg.get("max_event_pct",1))-eu),
        max(Decimal(0),start*d(cfg.get("max_region_pct",1))-ru),
        max(Decimal(0),start*d(cfg.get("max_daily_deployed_pct",1))-du),
    )
    strategy_cap=start*d(strategy_cfg.get("max_portfolio_pct",1))
    return max(Decimal(0),min(cap,strategy_cap))


def _record_decision(conn:psycopg.Connection[Any],portfolio_id:int,signal_id:int,decision:str,budget:Decimal,score:Decimal|None,reason:str,details:dict[str,Any])->bool:
    row=conn.execute("INSERT INTO paper_portfolio_decisions(portfolio_id,signal_id,decision,requested_budget,score,reason,details) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (portfolio_id,signal_id) DO NOTHING RETURNING id",(portfolio_id,signal_id,decision,budget,score,reason,json.dumps(details,separators=(",",":"),default=json_default))).fetchone()
    return bool(row)


def _insert_order(conn:psycopg.Connection[Any],session_id:str,portfolio_id:int,signal_id:int,strategy:str,leg:Leg,book:FullBook,latency_ms:int,fills:list[tuple[Decimal,Decimal]],cost:Decimal,fee:Decimal,breakdown:list[dict[str,str]],audit:dict[str,Any])->Decimal:
    qty=sum((q for _,q in fills),Decimal(0)); gross=sum((p*q for p,q in fills),Decimal(0)); avg=gross/qty; worst=max(p for p,_ in fills)
    arrival_ms=leg.trigger_epoch_ms+latency_ms; arrival_at=datetime.fromtimestamp(arrival_ms/1000,tz=timezone.utc)
    qty_fp=int((qty/QTY_STEP).to_integral_value()); max_fp=int((leg.max_price*10000).to_integral_value()); gross_micro=int((gross*1_000_000).to_integral_value()); fee_micro=int((fee*1_000_000).to_integral_value()); cost_micro=int((cost*1_000_000).to_integral_value())
    row=conn.execute(
        """
        INSERT INTO paper_orders(session_id,signal_id,portfolio_id,latency_profile_ms,strategy_code,market_ticker,outcome_side,requested_qty,decision_at,simulated_arrival_at,book_seq,status,avg_fill_price,filled_qty,gross_cost,estimated_fee,worst_price,book_snapshot,audit,requested_qty_fp,max_price_fp,filled_qty_fp,gross_cost_micros,fee_micros,total_cost_micros,state_known_through_epoch_ms,execution_model_version,fee_breakdown)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'filled',%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING id
        """,
        (session_id,signal_id,portfolio_id,latency_ms,strategy,leg.market_ticker,leg.side,qty,datetime.fromtimestamp(leg.trigger_epoch_ms/1000,tz=timezone.utc),arrival_at,book.last_seq,avg,qty,gross,fee,worst,json.dumps({"connection_id":book.connection_id,"snapshot_id":book.snapshot_id,"snapshot_received_ms":book.snapshot_received_ms,"asks_used":[[format(p,"f"),format(q,"f")] for p,q in fills]},separators=(",",":")),json.dumps(audit,separators=(",",":"),default=json_default),qty_fp,max_fp,qty_fp,gross_micro,fee_micro,cost_micro,arrival_ms,ENGINE_VERSION,json.dumps(breakdown,separators=(",",":"))),
    ).fetchone()
    order_id=int(row[0])
    for i,(p,q) in enumerate(fills):
        level_fee=d(breakdown[i]["net_fee"]) if i<len(breakdown) else Decimal(0)
        conn.execute("INSERT INTO paper_fills(order_id,price,qty,level_index,liquidity,estimated_fee) VALUES (%s,%s,%s,%s,'taker',%s)",(order_id,p,q,i,level_fee))
    conn.execute("INSERT INTO paper_positions(portfolio_id,market_ticker,outcome_side,qty,cost_basis,fees_paid,status,updated_at) VALUES (%s,%s,%s,%s,%s,%s,'open',now()) ON CONFLICT (portfolio_id,market_ticker,outcome_side) DO UPDATE SET qty=paper_positions.qty+EXCLUDED.qty,cost_basis=paper_positions.cost_basis+EXCLUDED.cost_basis,fees_paid=paper_positions.fees_paid+EXCLUDED.fees_paid,status='open',updated_at=now()",(portfolio_id,leg.market_ticker,leg.side,qty,gross,fee))
    conn.execute("UPDATE paper_portfolios SET cash_balance=cash_balance-%s,updated_at=now() WHERE id=%s AND cash_balance>=%s",(cost,portfolio_id,cost))
    return cost


def execute_bundle(conn:psycopg.Connection[Any],session_id:str,bundle:Bundle,signal_id:int,modes:list[dict[str,Any]],global_cfg:dict[str,Any],strategy_cfg:dict[str,Any],fee_multiplier:Decimal)->None:
    if bundle.auditor_status not in ("approved","shadow_only"):
        return
    for mode in modes:
        portfolio_id=int(mode["portfolio_id"])
        if conn.execute("SELECT 1 FROM paper_portfolio_decisions WHERE portfolio_id=%s AND signal_id=%s",(portfolio_id,signal_id)).fetchone():
            continue
        if not strategy_cfg.get("paper_trade_enabled",False):
            _record_decision(conn,portfolio_id,signal_id,"skip",Decimal(0),None,"STRATEGY_PAPER_TRADE_DISABLED",{"strategy":bundle.strategy_code}); continue
        budget=portfolio_budget(conn,mode,bundle,global_cfg,strategy_cfg.get("config",{}))
        if budget<=0:
            _record_decision(conn,portfolio_id,signal_id,"skip",Decimal(0),None,"PORTFOLIO_CAP_REACHED",{"strategy":bundle.strategy_code}); continue
        latency=max(0,int(mode["config"].get("execution_latency_ms",100)))
        inter_leg=max(0,int(strategy_cfg.get("config",{}).get("multi_leg_inter_order_ms",5)))

        # HSR chooses its route independently for each mode/latency.
        selected_legs=bundle.legs
        route_name=None
        if bundle.route_options:
            route_scores:list[tuple[Decimal,str,list[Leg]]]=[]
            for name,legs in bundle.route_options.items():
                unit_cost=Decimal(0); payout=Decimal(len(legs)) if name=="dsn" else Decimal(1); valid=True
                for idx,leg in enumerate(legs):
                    book=reconstruct_full_book(conn,session_id,leg.market_ticker,leg.trigger_epoch_ms+latency+idx*inter_leg)
                    levels=asks(book,leg.side,leg.max_price) if book else []
                    if not levels: valid=False; break
                    one=[(levels[0][0],Decimal(1))]
                    c,_,_=total_cost(one,fee_multiplier); unit_cost+=c
                if valid and unit_cost>0 and payout>unit_cost:
                    route_scores.append(((payout-unit_cost)/unit_cost,name,legs))
            if not route_scores:
                _record_decision(conn,portfolio_id,signal_id,"blocked",budget,None,"HSR_NO_EXECUTABLE_ROUTE",{}); continue
            route_scores.sort(key=lambda x:x[0],reverse=True); score,route_name,selected_legs=route_scores[0]
        else:
            score=None

        book_levels:list[tuple[Leg,FullBook,list[tuple[Decimal,Decimal]]]]=[]
        for idx,leg in enumerate(selected_legs):
            book=reconstruct_full_book(conn,session_id,leg.market_ticker,leg.trigger_epoch_ms+latency+idx*inter_leg)
            levels=asks(book,leg.side,leg.max_price) if book else []
            if not book or not levels:
                book_levels=[]; break
            book_levels.append((leg,book,levels))
        if not book_levels:
            _record_decision(conn,portfolio_id,signal_id,"blocked",budget,score,"NO_VALID_L2_AT_SIMULATED_ARRIVAL",{"latency_ms":latency}); continue

        executions:list[tuple[Leg,FullBook,list[tuple[Decimal,Decimal]],Decimal,Decimal,list[dict[str,str]]]]=[]
        if bundle.equal_leg_quantity and len(book_levels)>1:
            max_qty=min(sum(q for _,q in levels) for _,_,levels in book_levels)
            hi=int((max_qty/QTY_STEP).to_integral_value(rounding=ROUND_FLOOR)); lo=0; best=0
            while lo<=hi:
                mid=(lo+hi)//2; qty=QTY_STEP*mid; aggregate=Decimal(0); valid=True
                for _,_,levels in book_levels:
                    remaining=qty; fills=[]
                    for p,avail in levels:
                        take=min(avail,remaining)
                        if take>0: fills.append((p,take)); remaining-=take
                        if remaining<=0: break
                    if remaining>0: valid=False; break
                    c,_,_=total_cost(fills,fee_multiplier); aggregate+=c
                if valid and aggregate<=budget: best=mid; lo=mid+1
                else: hi=mid-1
            if best<=0:
                _record_decision(conn,portfolio_id,signal_id,"skip",budget,score,"INSUFFICIENT_MULTI_LEG_DEPTH",{}); continue
            target=QTY_STEP*best
            for leg,book,levels in book_levels:
                remaining=target; fills=[]
                for p,avail in levels:
                    take=min(avail,remaining)
                    if take>0: fills.append((p,take)); remaining-=take
                    if remaining<=0: break
                cost,fee,breakdown=total_cost(fills,fee_multiplier); executions.append((leg,book,fills,cost,fee,breakdown))
        else:
            per_leg=budget/Decimal(len(book_levels))
            for leg,book,levels in book_levels:
                fills,cost,fee,breakdown=fills_for_budget(levels,per_leg,fee_multiplier)
                if fills: executions.append((leg,book,fills,cost,fee,breakdown))
        if len(executions)!=len(book_levels):
            _record_decision(conn,portfolio_id,signal_id,"skip",budget,score,"BUNDLE_FOK_NOT_SATISFIED",{"legs":len(book_levels),"fillable":len(executions)}); continue
        if not _record_decision(conn,portfolio_id,signal_id,"trade",budget,score,"EXECUTABLE_PAPER_STRATEGY",{"strategy":bundle.strategy_code,"route":route_name,"legs":len(executions),"non_atomic_multi_leg":len(executions)>1}):
            continue
        for leg,book,fills,cost,fee,breakdown in executions:
            _insert_order(conn,session_id,portfolio_id,signal_id,bundle.strategy_code,leg,book,latency,fills,cost,fee,breakdown,{"confidence_class":bundle.signal_class,"auditor_status":bundle.auditor_status,"route":route_name,"multi_leg_inter_order_ms":inter_leg,"real_money":False})


def _bundle(strategy:str,signal_class:str,weather:dict[str,Any],meta:dict[str,str],event:dict[str,Any],legs:list[Leg],evidence:dict[str,Any],approved:bool,reason:str|None=None,**kwargs:Any)->Bundle:
    return Bundle(strategy,signal_class,weather["station_code"],meta.get("region",weather["station_code"]),event["event_ticker"],weather["id"],weather["first_seen_at"],weather["received_epoch_ms"],legs,evidence,"approved" if approved else "shadow_only",reason,**kwargs)


def construct_hard_bundles(weather:dict[str,Any],meta:dict[str,str],event:dict[str,Any],proven:bool,configs:dict[str,dict[str,Any]])->list[Bundle]:
    high=weather.get("max_temperature_f") or weather.get("temperature_f")
    if high is None: return []
    high=d(high); dead=[m for m in event["markets"] if upper_bound(m) is not None and high>upper_bound(m)]
    survivors=[m for m in event["markets"] if m not in dead]
    out:list[Bundle]=[]; base={"confirmed_high_f":high,"proven_compatibility":proven,"event_rules_hash":event["rules_hash"],"dead_markets":[m.get("ticker") for m in dead],"survivors":[m.get("ticker") for m in survivors]}
    dbn_cfg=configs.get("DBN",{}).get("config",{})
    if configs.get("DBN",{}).get("enabled"):
        for m in dead:
            ticker=str(m.get("ticker") or "")
            if ticker: out.append(_bundle("DBN","hard_state",weather,meta,event,[Leg(ticker,"no",weather["received_epoch_ms"],d(dbn_cfg.get("max_entry_price",0.97)),"dead_bucket")],{**base,"market":ticker},proven,"SETTLEMENT_COMPATIBILITY_UNVERIFIED" if not proven else None))
    if configs.get("DSN",{}).get("enabled") and len(dead)>=int(configs["DSN"].get("config",{}).get("min_dead_markets",2)):
        cfg=configs["DSN"]["config"]; legs=[Leg(str(m.get("ticker")),"no",weather["received_epoch_ms"],d(cfg.get("max_entry_price",0.97)),"dead_set") for m in dead if m.get("ticker")]
        out.append(_bundle("DSN","hard_state",weather,meta,event,legs,{**base,"construction":"all_dead_no"},proven,"SETTLEMENT_COMPATIBILITY_UNVERIFIED" if not proven else None))
    exhaustive=event.get("mutually_exclusive") is True and event_is_exhaustive_partition(event["markets"])
    if configs.get("SBK",{}).get("enabled") and survivors:
        legs=[Leg(str(m.get("ticker")),"yes",weather["received_epoch_ms"],ONE,"survivor") for m in survivors if m.get("ticker")]
        approved=proven and exhaustive
        out.append(_bundle("SBK","hard_state",weather,meta,event,legs,{**base,"mutually_exclusive":event.get("mutually_exclusive"),"exhaustive_partition_check":exhaustive},approved,"SBK_REQUIRES_PROVEN_EXHAUSTIVE_PARTITION" if not approved else None,equal_leg_quantity=True,expected_payout_per_unit=ONE))
    if configs.get("HSR",{}).get("enabled") and dead:
        cfg=configs["HSR"].get("config",{}); dbn=[Leg(str(m.get("ticker")),"no",weather["received_epoch_ms"],d(cfg.get("max_entry_price",0.97)),"route_dbn") for m in dead if m.get("ticker")]
        best_dbn=dbn[:1]
        dsn=dbn
        sbk=[Leg(str(m.get("ticker")),"yes",weather["received_epoch_ms"],ONE,"route_sbk") for m in survivors if m.get("ticker")] if exhaustive else []
        routes={"dbn":best_dbn,"dsn":dsn};
        if sbk: routes["sbk"]=sbk
        out.append(_bundle("HSR","hard_state",weather,meta,event,[],{**base,"routes":list(routes),"exhaustive_partition_check":exhaustive},proven,"SETTLEMENT_COMPATIBILITY_UNVERIFIED" if not proven else None,route_options=routes))
    return out


def construct_wty(conn:psycopg.Connection[Any],session_id:str,weather:dict[str,Any],meta:dict[str,str],event:dict[str,Any],cfg:dict[str,Any])->Bundle|None:
    high=weather.get("max_temperature_f") or weather.get("temperature_f")
    if high is None:return None
    survivors=[m for m in event["markets"] if upper_bound(m) is None or d(high)<=upper_bound(m)]
    if not survivors:return None
    lookback=int(cfg.get("pre_signal_lookback_ms",5000)); pre_ms=weather["received_epoch_ms"]-lookback
    probs:list[tuple[Decimal,dict[str,Any]]]=[]
    for m in survivors:
        ticker=str(m.get("ticker") or ""); book=reconstruct_full_book(conn,session_id,ticker,pre_ms) if ticker else None
        mid=implied_mid(book,"yes") if book else None
        if mid is not None and mid>0: probs.append((mid,m))
    if not probs:return None
    total=sum((p for p,_ in probs),Decimal(0)); probs.sort(key=lambda x:x[0],reverse=True); prior,mkt=probs[0]; model=prior/total if total>0 else prior; ticker=str(mkt.get("ticker")); max_price=d(cfg.get("max_yes_price",0.80));
    leg=Leg(ticker,"yes",weather["received_epoch_ms"],max_price,"winner_transfer",model)
    return _bundle("WTY","probabilistic",weather,meta,event,[leg],{"model":"pre_signal_survivor_renormalization","pre_signal_probability":prior,"renormalized_probability":model,"survivor_count":len(probs),"confirmed_high_f":d(high)},True)


def _c_from_f(f:Decimal)->Decimal:return (f-32)*Decimal(5)/Decimal(9)
def _f_from_c(c:Decimal)->Decimal:return c*Decimal(9)/Decimal(5)+32

def construct_rmo(conn:psycopg.Connection[Any],session_id:str,weather:dict[str,Any],meta:dict[str,str],event:dict[str,Any],cfg:dict[str,Any])->Bundle|None:
    if weather.get("source")!="IEM_MADIS_OMO" or weather.get("temperature_f") is None:return None
    allowed=set(cfg.get("stations",["KLAX"]));
    if weather["station_code"] not in allowed:return None
    prior=latest_prior_weather(conn,session_id,weather,"IEM_MADIS_OMO")
    if not prior:return None
    current=d(weather["temperature_f"]); previous=d(prior["temperature_f"])
    c_now=_c_from_f(current); c_prev=_c_from_f(previous); coarse_now=c_now.to_integral_value(rounding=ROUND_HALF_UP); coarse_prev=c_prev.to_integral_value(rounding=ROUND_HALF_UP)
    if coarse_now<=coarse_prev:return None
    delay=int(cfg.get("confirmation_delay_ms",1000)); min_move=d(cfg.get("min_momentum_cents",2))/100; best:tuple[Decimal,str]|None=None
    for m in event["markets"]:
        ticker=str(m.get("ticker") or "");
        if not ticker:continue
        before=reconstruct_full_book(conn,session_id,ticker,weather["received_epoch_ms"]-1); after=reconstruct_full_book(conn,session_id,ticker,weather["received_epoch_ms"]+delay)
        b0=best_bid(before,"yes") if before else None; b1=best_bid(after,"yes") if after else None
        if b0 is None or b1 is None:continue
        move=b1-b0
        if move>=min_move and (best is None or move>best[0]):best=(move,ticker)
    if not best:return None
    leg=Leg(best[1],"yes",weather["received_epoch_ms"]+delay,d(cfg.get("max_yes_price",0.80)),"momentum_confirmation")
    return _bundle("RMO","probabilistic",weather,meta,event,[leg],{"coarse_c_before":coarse_prev,"coarse_c_after":coarse_now,"precise_f":current,"momentum":best[0],"confirmation_delay_ms":delay,"prior_weather_id":prior["id"]},True)


def construct_prv(weather:dict[str,Any],meta:dict[str,str],event:dict[str,Any],cfg:dict[str,Any])->list[Bundle]:
    if weather.get("source")!="IEM_MADIS_OMO" or weather.get("temperature_f") is None:return []
    if weather["station_code"] not in set(cfg.get("stations",["KLAX"])):return []
    precise=d(weather["temperature_f"]); coarse_c=_c_from_f(precise).to_integral_value(rounding=ROUND_HALF_UP); coarse_f=_f_from_c(coarse_c); out=[]
    for m in event["markets"]:
        lo=lower_bound(m); ticker=str(m.get("ticker") or "")
        if ticker and lo is not None and coarse_f>=lo and precise<lo:
            out.append(_bundle("PRV","precision_hypothesis",weather,meta,event,[Leg(ticker,"no",weather["received_epoch_ms"],d(cfg.get("max_no_price",0.80)),"coarse_cross_not_precise_cross")],{"precise_f":precise,"coarse_c":coarse_c,"coarse_implied_f":coarse_f,"threshold_f":lo,"rule":"coarse_cross_not_precise_cross"},True))
    return out


def linked_signal(conn:psycopg.Connection[Any],session_id:str,station:str,event_ticker:str,strategy:str,before:datetime,window_minutes:int)->dict[str,Any]|None:
    row=conn.execute("SELECT id,triggered_at,evidence FROM paper_signals WHERE session_id=%s AND station_code=%s AND event_ticker=%s AND strategy_code=%s AND triggered_at<%s AND triggered_at>=%s ORDER BY triggered_at DESC LIMIT 1",(session_id,station,event_ticker,strategy,before,before-timedelta(minutes=window_minutes))).fetchone()
    return {"id":int(row[0]),"triggered_at":row[1],"evidence":row[2]} if row else None


def execute_extra_strategies(conn:psycopg.Connection[Any],session_id:str,weather:dict[str,Any],modes:list[dict[str,Any]],global_cfg:dict[str,Any])->int:
    meta=STATIONS.get(weather["station_code"])
    if not meta:return 0
    configs=strategy_configs(conn); series=meta["series"]; rules=series_rules_before(conn,series,weather["first_seen_at"])
    if not rules or rules.get("fee_type") not in ("quadratic","quadratic_with_maker_fees") or rules.get("fee_multiplier") is None:return 0
    fee_multiplier=d(rules["fee_multiplier"]); proven=compatibility_proven(conn,weather); events=event_rules_before(conn,series,weather["first_seen_at"]); created=0
    for event in events:
        bundles=construct_hard_bundles(weather,meta,event,proven,{k:v for k,v in configs.items() if k!="DBN"})
        if configs.get("WTY",{}).get("enabled"):
            b=construct_wty(conn,session_id,weather,meta,event,configs["WTY"]["config"])
            if b:bundles.append(b)
        rmo=None
        if configs.get("RMO",{}).get("enabled"):
            rmo=construct_rmo(conn,session_id,weather,meta,event,configs["RMO"]["config"])
            if rmo:bundles.append(rmo)
        prvs=construct_prv(weather,meta,event,configs.get("PRV",{}).get("config",{})) if configs.get("PRV",{}).get("enabled") else []
        bundles.extend(prvs)
        # LVP/HMF phase-1 is entered prospectively when RMO fires; phase-2 is added only on an independent later trigger.
        if rmo and configs.get("LVP",{}).get("enabled"):
            l=rmo.legs[0]; bundles.append(_bundle("LVP","sequential_hypothesis",weather,meta,event,[Leg(l.market_ticker,l.side,l.trigger_epoch_ms,l.max_price,"lvp_phase1_rmo")],{"phase":1,"source_strategy":"RMO","rmo_evidence":rmo.evidence},True))
        if rmo and configs.get("HMF",{}).get("enabled"):
            l=rmo.legs[0]; bundles.append(_bundle("HMF","sequential_hypothesis",weather,meta,event,[Leg(l.market_ticker,l.side,l.trigger_epoch_ms,l.max_price,"hmf_phase1_rmo")],{"phase":1,"source_strategy":"RMO","rmo_evidence":rmo.evidence},True))
        if prvs and configs.get("LVP",{}).get("enabled"):
            prior=linked_signal(conn,session_id,weather["station_code"],event["event_ticker"],"LVP",weather["first_seen_at"],int(configs["LVP"]["config"].get("pair_window_minutes",180)))
            if prior:
                for p in prvs:
                    leg=p.legs[0]; bundles.append(_bundle("LVP","sequential_hypothesis",weather,meta,event,[Leg(leg.market_ticker,leg.side,leg.trigger_epoch_ms,leg.max_price,"lvp_phase2_prv")],{"phase":2,"prior_lvp_signal_id":prior["id"],"prv_evidence":p.evidence},True))
        if proven and configs.get("HMF",{}).get("enabled"):
            prior=linked_signal(conn,session_id,weather["station_code"],event["event_ticker"],"HMF",weather["first_seen_at"],int(configs["HMF"]["config"].get("pair_window_minutes",240)))
            if prior:
                high=weather.get("max_temperature_f") or weather.get("temperature_f")
                if high is not None:
                    dead=[m for m in event["markets"] if upper_bound(m) is not None and d(high)>upper_bound(m)]
                    if dead:
                        m=dead[-1]; ticker=str(m.get("ticker") or "")
                        if ticker: bundles.append(_bundle("HMF","hard_state_sequence",weather,meta,event,[Leg(ticker,"no",weather["received_epoch_ms"],d(configs["HMF"]["config"].get("max_no_price",0.97)),"hmf_phase2_hidden_max")],{"phase":2,"prior_hmf_signal_id":prior["id"],"confirmed_high_f":d(high),"compatibility_rule":weather.get("compatibility_rule")},True))
        for bundle in bundles:
            cfg=configs.get(bundle.strategy_code)
            if not cfg or not cfg.get("enabled"):continue
            signal_id=insert_signal(conn,session_id,bundle); execute_bundle(conn,session_id,bundle,signal_id,modes,global_cfg,cfg,fee_multiplier); created+=1
    return created
