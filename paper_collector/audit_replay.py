from __future__ import annotations

import hashlib
import json
import os
import sys
from decimal import Decimal
from typing import Any

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
AUDITOR_VERSION = os.getenv("AUDITOR_VERSION", "python-decimal-v1")
SESSION_ID = os.getenv("AUDIT_SESSION_ID")


class Book:
    def __init__(self) -> None:
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

    def snapshot(self, yes: list[list[str]], no: list[list[str]]) -> None:
        self.yes.clear()
        self.no.clear()
        for price, qty in yes:
            self._set(self.yes, Decimal(price), Decimal(qty))
        for price, qty in no:
            self._set(self.no, Decimal(price), Decimal(qty))
        self.validate()

    def delta(self, side_name: str, price: str, delta: str) -> None:
        side = self.yes if side_name == "yes" else self.no if side_name == "no" else None
        if side is None:
            raise ValueError(f"unknown side: {side_name}")
        p = Decimal(price)
        next_qty = side.get(p, Decimal(0)) + Decimal(delta)
        self._set(side, p, next_qty)
        self.validate()

    def validate(self) -> None:
        # Own-leg pricing: a YES bid x and NO bid y cannot coexist with x+y>1.
        if self.yes and self.no and max(self.yes) + max(self.no) > Decimal("1"):
            raise ValueError(f"crossed binary book: yes={max(self.yes)} no={max(self.no)}")

    def canonical(self) -> dict[str, list[list[str]]]:
        def levels(side: dict[Decimal, Decimal]) -> list[list[str]]:
            return [[format(p, "f"), format(side[p], "f")] for p in sorted(side)]
        return {"yes": levels(self.yes), "no": levels(self.no)}


def state_hash(books: dict[str, Book]) -> str:
    payload = {ticker: books[ticker].canonical() for ticker in sorted(books)}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def hash_chain_update(chain: bytes, row_id: int, payload_sha: str) -> bytes:
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
                SELECT id, channel, sid, seq, market_ticker, raw_text, payload_sha256
                FROM market_data_journal
                WHERE session_id=%s
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        books: dict[str, Book] = {}
        last_seq: dict[int, int] = {}
        sequence_gaps = 0
        integrity_failures = 0
        book_errors = 0
        chain = b""
        findings: list[dict[str, Any]] = []

        for row_id, channel, sid, seq, market_ticker, raw_text, expected_sha in rows:
            actual_sha = hashlib.sha256(raw_text.encode()).hexdigest()
            if actual_sha != expected_sha:
                integrity_failures += 1
                findings.append({"code": "RAW_HASH_MISMATCH", "row_id": row_id, "expected": expected_sha, "actual": actual_sha})
            chain = hash_chain_update(chain, int(row_id), str(expected_sha))

            try:
                data = json.loads(raw_text)
            except Exception as exc:
                integrity_failures += 1
                findings.append({"code": "RAW_JSON_PARSE_FAILURE", "row_id": row_id, "error": repr(exc)})
                continue

            if channel == "subscribed" and isinstance(data.get("msg"), dict) and data["msg"].get("channel") == "orderbook_delta":
                # SID values may be reused on a fresh WebSocket connection. A new
                # subscribed frame is an explicit sequence-generation boundary.
                new_sid = data["msg"].get("sid")
                if new_sid is not None:
                    last_seq.pop(int(new_sid), None)
                continue

            if channel not in ("orderbook_snapshot", "orderbook_delta"):
                continue

            if sid is None or seq is None:
                sequence_gaps += 1
                findings.append({"code": "MISSING_SEQUENCE", "row_id": row_id, "sid": sid, "seq": seq})
            else:
                sid_i, seq_i = int(sid), int(seq)
                prev = last_seq.get(sid_i)
                if prev is not None and seq_i != prev + 1:
                    sequence_gaps += 1
                    findings.append({"code": "SEQUENCE_GAP", "row_id": row_id, "sid": sid_i, "expected": prev + 1, "received": seq_i})
                last_seq[sid_i] = seq_i

            msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
            ticker = str(msg.get("market_ticker") or market_ticker or "")
            if not ticker:
                book_errors += 1
                findings.append({"code": "MISSING_MARKET_TICKER", "row_id": row_id})
                continue

            try:
                if channel == "orderbook_snapshot":
                    book = books.setdefault(ticker, Book())
                    book.snapshot(msg.get("yes_dollars_fp") or [], msg.get("no_dollars_fp") or [])
                else:
                    if ticker not in books:
                        raise ValueError("delta arrived before a reconstructable snapshot")
                    books[ticker].delta(str(msg.get("side")), str(msg.get("price_dollars")), str(msg.get("delta_fp")))
            except Exception as exc:
                book_errors += 1
                findings.append({"code": "BOOK_REPLAY_ERROR", "row_id": row_id, "ticker": ticker, "error": repr(exc)})

        journal_chain = chain.hex()
        final_hash = state_hash(books)
        status = "passed" if not (integrity_failures or sequence_gaps or book_errors) else "failed"
        summary = {
            "books": len(books),
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
            "journal_chain_sha256": journal_chain,
            "final_book_state_sha256": final_hash,
        }, indent=2))
        return 0 if status == "passed" else 2


if __name__ == "__main__":
    sys.exit(run())
