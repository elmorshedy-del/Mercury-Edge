from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

INTERVAL_SECONDS = max(30.0, float(os.getenv("AUDIT_INTERVAL_SECONDS", "300")))
STOP = False


def stop(*_: object) -> None:
    global STOP
    STOP = True


def _run_child(script: str, event_name: str) -> None:
    proc = subprocess.run(
        [sys.executable, script],
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )
    print(json.dumps({
        "event": event_name,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }))


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "audit_daemon_start", "interval_seconds": INTERVAL_SECONDS}))
    while not STOP:
        started = time.monotonic()
        _run_child("audit_replay.py", "audit_replay_complete")
        # Explainability/fail-closed diagnostics are downstream of benchmark
        # trading. A sweep failure is surfaced but must never stop evidence capture.
        _run_child("diagnostic_sweep.py", "hard_edge_diagnostic_sweep_process_complete")
        remaining = max(0.1, INTERVAL_SECONDS - (time.monotonic() - started))
        deadline = time.monotonic() + remaining
        while not STOP and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))
    print(json.dumps({"event": "audit_daemon_stop"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
