from __future__ import annotations

import hashlib
import json
import os
import sys
from decimal import Decimal
from typing import Any

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
AUDITOR_VERSION = os.getenv("AUDITOR_VERSION", "python-decimal-v2")
SESSION_ID = os.getenv("AUDIT_SESSION_ID")
ONE = Decimal("1")


class Book:
    def __init__(self) -> None:
        # Internal representation is always native outcome-leg bid pricing.
        self.yes: dict[Decimal, Decimal] = {}
        self.no: dict[Decimal, Decimal] = {}

    @staticmethod
    def _set(side: dict[Decimal, Decimal], price: Decimal, qty: Decimal) -> None:
        if price < 0 or price > 1:
            raise ValueError(f"price outside [0,1]: {price}")
        if qty < 0:
            raise ValueError(f"negative quantity: {qty}")
        if qty == 0:
            side.pop(price, None)
        else:
            side[price] = qty

    @staticmethod
    def native_price(side_name: str, raw_price: str, price_mode: str | None) -> Decimal:
        price = Decimal(raw_price)
        if side_name == "no" and price_mode == "unified_yes":
            price = ONE - price
        return price

    def snapshot(
        self,
        yes: list[list[str]],
        no: list[list[str]],
        price_mode: str | None,
    ) -> None:
        self.yes.clear()
        self.no.clear()
        for price, qty in yes:
            self._set(self.yes, self.native_price("yes", str(price), price_mode), Decimal(str(qty)))
        for price, qty in no:
            self._set(self.no, self.native_price("no", str(price), price_mode), Decimal(str(qty)))
        self.validate()

    def delta(self, side_name: str, price: str, delta: str, price_mode: str | None) -> None:
        side = self.yes if side_name == "yes" else self.no if side_name == "no" else None
        if side is None:
            raise ValueError(f"unknown side: {side_name}")
        p = self.native_price(side_name, price, price_mode)
        next_qty = side.get(p, Decimal(0)) + Decimal(delta)
        self._set(side, p, next_qty)
        self.validate()

    def validate(self) -> None:
        # Native outcome-leg pricing: YES bid x and NO bid y cannot cross x+y>1.
        if self.yes and self.no and max(self.yes) + max(self.no) > ONE:
            raise ValueError(f"crossed binary book: yes={max(self.yes)} no={max(self.no)}")

    def canonical(self) -> dict[str, list[list[str]]]:
        def levels(side: dict[Decimal, Decimal]) -> list[list[str]]:
            return [[format(p, "f"), format(side[p], "f")] for p in sorted(side)]
        return {"yes": levels(self.yes), "no": levels(self.no)}


def state_hash(books: dict[str, Book]) -> str:
    payload = {ticker: books[ticker].canonical() for ticker in sorted(books)}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def audit_chain_hash(prev: str | None, connection_id: str, received_epoch_ns: int, raw_sha: str) -> str:
    material = f"{prev or ''}|{connection_id}|{received_epoch_ns}|{raw_sha}".encode()
    return hashlib.sha256(material).hexdigest()


def global_chain_update(chain: bytes, row_id: int, payload_sha: str) -> bytes:
    return hashlib.sha256(chain + str(row_id).encode() + b":" + payload_sha.encode()).digest()


def resolve_session(conn: psycopg.Connection[Any]) -> str:
    if SESSION_ID:
        return SESSION_ID
    row = conn.execute(
        "SELECT id FROM paper_sessions WHERE mode='paper_live' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("no paper session found; set AUDIT_SESSION_ID")
    return str(row[0])


def run() -> int:
    with psycopg.connect(DATABASE_URL) as conn:
        session_id = resolve_session(conn)

        # Freeze a consistent journal prefix even if the live collector continues writing.
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            rows = conn.execute(
                """
                SELECT id, channel, sid, seq, market_ticker, raw_text, payload_sha256,
                       connection_id::text, received_epoch_ns, prev_chain_hash,
                       chain_hash, price_mode
                FROM market_data_journal
                WHERE session_id=%s
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        books: dict[str, Book] = {}
        last_seq: dict[tuple[str, int], int] = {}
        last_connection_chain: dict[str, str] = {}
        snapshot_seen: set[tuple[str, str]] = set()
        invalid_connections: set[str] = set()
        sequence_gaps = 0
        integrity_failures = 0
        book_errors = 0
        chain = b""
        findings: list[dict[str, Any]] = []

        for (
            row_id, channel, sid, seq, market_ticker, raw_text, expected_sha,
            connection_id, received_epoch_ns, prev_chain_hash, stored_chain_hash,
            price_mode,
        ) in rows:
            actual_sha = hashlib.sha256(raw_text.encode()).hexdigest()
            if actual_sha != expected_sha:
                integrity_failures += 1
                findings.append({
                    "code": "RAW_HASH_MISMATCH", "row_id": row_id,
                    "expected": expected_sha, "actual": actual_sha,
                })
            chain = global_chain_update(chain, int(row_id), str(expected_sha))

            # New v1.1 WebSocket rows are chained by connection. This detects
            # deletion, insertion, reordering, or mutation independent of DB IDs.
            if connection_id:
                expected_prev = last_connection_chain.get(connection_id)
                if (prev_chain_hash or None) != (expected_prev or None):
                    integrity_failures += 1
                    findings.append({
                        "code": "CHAIN_PREDECESSOR_MISMATCH",
                        "row_id": row_id,
                        "connection_id": connection_id,
                        "expected_prev": expected_prev,
                        "stored_prev": prev_chain_hash,
                    })
                expected_chain = audit_chain_hash(
                    prev_chain_hash,
                    connection_id,
                    int(received_epoch_ns),
                    str(expected_sha),
                )
                if stored_chain_hash != expected_chain:
                    integrity_failures += 1
                    findings.append({
                        "code": "CHAIN_HASH_MISMATCH",
                        "row_id": row_id,
                        "connection_id": connection_id,
                        "expected": expected_chain,
                        "stored": stored_chain_hash,
                    })
                if stored_chain_hash:
                    last_connection_chain[connection_id] = stored_chain_hash

            try:
                data = json.loads(raw_text)
            except Exception as exc:
                integrity_failures += 1
                findings.append({"code": "RAW_JSON_PARSE_FAILURE", "row_id": row_id, "error": repr(exc)})
                continue

            if channel not in ("orderbook_snapshot", "orderbook_delta"):
                continue

            conn_key = connection_id or "legacy"
            if sid is None or seq is None:
                sequence_gaps += 1
                invalid_connections.add(conn_key)
                findings.append({"code": "MISSING_SEQUENCE", "row_id": row_id, "sid": sid, "seq": seq})
            else:
                sid_i, seq_i = int(sid), int(seq)
                sequence_key = (conn_key, sid_i)
                prev = last_seq.get(sequence_key)
                if prev is not None and seq_i != prev + 1:
                    sequence_gaps += 1
                    invalid_connections.add(conn_key)
                    findings.append({
                        "code": "SEQUENCE_GAP", "row_id": row_id,
                        "connection_id": connection_id, "sid": sid_i,
                        "expected": prev + 1, "received": seq_i,
                    })
                last_seq[sequence_key] = seq_i

            msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
            ticker = str(msg.get("market_ticker") or market_ticker or "")
            if not ticker:
                book_errors += 1
                invalid_connections.add(conn_key)
                findings.append({"code": "MISSING_MARKET_TICKER", "row_id": row_id})
                continue

            try:
                if channel == "orderbook_snapshot":
                    book = books.setdefault(ticker, Book())
                    book.snapshot(
                        msg.get("yes_dollars_fp") or [],
                        msg.get("no_dollars_fp") or [],
                        price_mode,
                    )
                    snapshot_seen.add((conn_key, ticker))
                else:
                    if (conn_key, ticker) not in snapshot_seen:
                        raise ValueError("delta arrived before snapshot in this connection generation")
                    if ticker not in books:
                        raise ValueError("delta arrived before a reconstructable snapshot")
                    books[ticker].delta(
                        str(msg.get("side")),
                        str(msg.get("price_dollars")),
                        str(msg.get("delta_fp")),
                        price_mode,
                    )
            except Exception as exc:
                book_errors += 1
                invalid_connections.add(conn_key)
                findings.append({
                    "code": "BOOK_REPLAY_ERROR", "row_id": row_id,
                    "connection_id": connection_id, "ticker": ticker,
                    "error": repr(exc),
                })

        journal_chain = chain.hex()
        final_hash = state_hash(books)
        status = "passed" if not (integrity_failures or sequence_gaps or book_errors) else "failed"
        summary = {
            "books": len(books),
            "connections": len({row[7] for row in rows if row[7]}),
            "invalid_connections": sorted(invalid_connections),
            "findings": findings[:200],
            "findings_truncated": max(0, len(findings) - 200),
        }

        previous = conn.execute(
            """
            SELECT journal_chain_sha256, final_book_state_sha256
            FROM paper_replay_runs
            WHERE session_id=%s AND auditor_version=%s AND journal_rows=%s AND status='passed'
            ORDER BY id DESC LIMIT 1
            """,
            (session_id, AUDITOR_VERSION, len(rows)),
        ).fetchone()
        if previous and (previous[0] != journal_chain or previous[1] != final_hash):
            status = "failed"
            book_errors += 1
            summary["determinism_failure"] = {
                "previous_journal_chain": previous[0],
                "current_journal_chain": journal_chain,
                "previous_book_hash": previous[1],
                "current_book_hash": final_hash,
            }

        replay_id = conn.execute(
            """
            INSERT INTO paper_replay_runs(
              session_id, auditor_version, finished_at, status, journal_rows,
              integrity_failures, sequence_gaps, book_errors,
              journal_chain_sha256, final_book_state_sha256, summary
            ) VALUES (%s,%s,now(),%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            RETURNING id
            """,
            (
                session_id, AUDITOR_VERSION, status, len(rows), integrity_failures,
                sequence_gaps, book_errors, journal_chain, final_hash,
                json.dumps(summary, separators=(",", ":")),
            ),
        ).fetchone()[0]
        conn.commit()

        print(json.dumps({
            "replay_id": replay_id,
            "session_id": session_id,
            "status": status,
            "journal_rows": len(rows),
            "integrity_failures": integrity_failures,
            "sequence_gaps": sequence_gaps,
            "book_errors": book_errors,
            "invalid_connections": sorted(invalid_connections),
            "journal_chain_sha256": journal_chain,
            "final_book_state_sha256": final_hash,
        }, indent=2))
        return 0 if status == "passed" else 2


if __name__ == "__main__":
    sys.exit(run())
