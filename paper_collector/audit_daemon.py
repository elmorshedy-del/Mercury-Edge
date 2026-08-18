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


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "audit_daemon_start", "interval_seconds": INTERVAL_SECONDS}))
    while not STOP:
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "audit_replay.py"],
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )
        print(json.dumps({
            "event": "audit_replay_complete",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }))
        # A failed audit makes readiness red but must not stop evidence capture.
        remaining = max(0.1, INTERVAL_SECONDS - (time.monotonic() - started))
        deadline = time.monotonic() + remaining
        while not STOP and time.monotonic() < deadline:
            time.sleep(min(0.5, deadline - time.monotonic()))
    print(json.dumps({"event": "audit_daemon_stop"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
