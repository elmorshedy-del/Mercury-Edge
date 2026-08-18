from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import time
import uuid

from stations import MARKET_SERIES, OMO_DEFAULT_NETWORKS, WEATHER_STATIONS


def normalize_private_key_env(env: dict[str, str]) -> None:
    """Accept either raw PEM or already-base64 PEM without logging key material."""
    value = env.get("KALSHI_PRIVATE_KEY_PEM_B64", "").strip()
    if not value:
        return
    normalized = value.replace("\\n", "\n")
    if "-----BEGIN " in normalized and "PRIVATE KEY-----" in normalized:
        env["KALSHI_PRIVATE_KEY_PEM_B64"] = base64.b64encode(normalized.encode("utf-8")).decode("ascii")


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

    child_specs = [
        ("kalshi", "collector.py", True),
        ("awc", "weather_collector.py", True),
        ("omo", "omo_collector.py", True),
        ("rules", "rule_collector.py", True),
        # One deterministic engine owns portfolio decisions for every strategy.
        # It is non-critical so evidence capture survives a strategy bug; the
        # supervisor restarts it and the DB uniqueness guards prevent duplicates.
        ("paper_trader", "unified_engine.py", False),
        ("auditor", "audit_daemon.py", False),
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
        print(json.dumps({"event": "paper_runner_stop", "session_id": env["PAPER_SESSION_ID"], "exit_code": exit_code}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
