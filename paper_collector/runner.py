from __future__ import annotations

import base64
import binascii
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from stations import MARKET_SERIES, OMO_DEFAULT_NETWORKS, WEATHER_STATIONS


PEM_RE = re.compile(
    r"-----BEGIN (?P<label>RSA PRIVATE KEY|PRIVATE KEY)-----"
    r"(?P<body>.*?)"
    r"-----END (?P=label)-----",
    re.DOTALL,
)


def _canonical_pem_bytes(value: str) -> bytes | None:
    normalized = value.strip().strip('"').strip("'").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r\n", "\n")
    match = PEM_RE.search(normalized)
    if not match:
        return None
    label = match.group("label")
    body = "".join(match.group("body").split())
    if not body:
        return None
    wrapped = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN {label}-----\n{wrapped}\n-----END {label}-----\n".encode("utf-8")


def normalize_private_key_env(env: dict[str, str]) -> None:
    """Accept common RSA key encodings without logging key material.

    Input may be raw/escaped PEM, one-line PEM, base64(PEM), or base64(DER).
    Children always receive base64 of a canonical unencrypted PKCS8 PEM.
    """
    raw = env.get("KALSHI_PRIVATE_KEY_PEM_B64", "").strip()
    if not raw:
        return

    candidates: list[tuple[str, bytes]] = []
    pem = _canonical_pem_bytes(raw)
    if pem is not None:
        candidates.append(("pem", pem))

    compact = "".join(raw.strip().strip('"').strip("'").split())
    try:
        decoded = base64.b64decode(compact, validate=True)
        decoded_text = decoded.decode("utf-8", errors="ignore")
        decoded_pem = _canonical_pem_bytes(decoded_text)
        if decoded_pem is not None:
            candidates.append(("pem", decoded_pem))
        candidates.append(("der", decoded))
    except (binascii.Error, ValueError):
        pass

    key = None
    for kind, payload in candidates:
        try:
            key = (
                serialization.load_pem_private_key(payload, password=None)
                if kind == "pem"
                else serialization.load_der_private_key(payload, password=None)
            )
        except (TypeError, ValueError):
            continue
        if key is not None:
            break

    if key is None or not isinstance(key, rsa.RSAPrivateKey):
        raise RuntimeError("Configured Kalshi private key is not a parseable RSA private key")

    canonical = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    env["KALSHI_PRIVATE_KEY_PEM_B64"] = base64.b64encode(canonical).decode("ascii")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in ("/api/health", "/health", "/"):
            self.send_response(404)
            self.end_headers()
            return
        body = b'{"ok":true,"service":"mercury-paper-collector","real_money":false}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def start_health_server() -> ThreadingHTTPServer | None:
    port = int(os.getenv("PORT", "0") or "0")
    if port <= 0:
        return None
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    print(json.dumps({"event": "paper_health_server_start", "port": port}))
    return server


def main() -> int:
    env = os.environ.copy()
    normalize_private_key_env(env)
    env.setdefault("PAPER_SESSION_ID", f"paper-{uuid.uuid4()}")
    env.setdefault("SERIES_TICKERS", ",".join(MARKET_SERIES))
    env.setdefault("WEATHER_STATIONS", ",".join(WEATHER_STATIONS))
    env.setdefault(
        "OMO_STATION_NETWORKS",
        ",".join(f"{station}:{network}" for station, network in OMO_DEFAULT_NETWORKS.items()),
    )
    env.setdefault("IEM_OMO_POLL_SECONDS", "15")
    health_server = start_health_server()

    child_specs = [
        ("kalshi", "collector.py", True),
        ("awc", "weather_collector.py", True),
        ("omo", "omo_collector.py", True),
        ("rules", "rule_collector.py", True),
        ("paper_trader", "unified_engine.py", False),
        ("portfolio_monitor", "portfolio_monitor.py", False),
        ("auditor", "audit_daemon.py", False),
        ("status", "status_reporter.py", False),
    ]
    children: dict[str, tuple[subprocess.Popen[str], bool]] = {}
    for name, script, critical in child_specs:
        children[name] = (subprocess.Popen([sys.executable, script], env=env, text=True), critical)

    print(json.dumps({
        "event": "paper_runner_start",
        "session_id": env["PAPER_SESSION_ID"],
        "market_series_count": len(MARKET_SERIES),
        "weather_station_count": len(WEATHER_STATIONS),
        "omo_priority_count": len(OMO_DEFAULT_NETWORKS),
        "weather_stations": list(WEATHER_STATIONS),
        "omo_priority_stations": list(OMO_DEFAULT_NETWORKS),
        "paper_strategies": ["DBN", "DSN", "SBK", "HSR", "WTY", "RMO", "PRV", "LVP", "HMF"],
        "benchmark_gate": "approved_only",
        "shadow_lab": True,
        "drawdown_monitor": True,
        "children": [{"name": name, "critical": critical} for name, _, critical in child_specs],
    }))

    stopping = False

    def terminate(*_: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for child, _ in children.values():
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    exit_code = 0
    try:
        while True:
            for name, (child, critical) in list(children.items()):
                code = child.poll()
                if code is None:
                    continue
                print(json.dumps({"event": "paper_child_exit", "name": name, "code": code, "critical": critical}))
                if stopping:
                    continue
                if critical:
                    exit_code = code if code != 0 else 1
                    terminate()
                    break
                time.sleep(1)
                script = next(script for child_name, script, _ in child_specs if child_name == name)
                children[name] = (subprocess.Popen([sys.executable, script], env=env, text=True), False)
            if stopping:
                break
            time.sleep(0.25)
    finally:
        terminate()
        for child, _ in children.values():
            if child.poll() is None:
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
        if health_server is not None:
            health_server.shutdown()
            health_server.server_close()
        print(json.dumps({"event": "paper_runner_stop", "session_id": env["PAPER_SESSION_ID"], "exit_code": exit_code}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
