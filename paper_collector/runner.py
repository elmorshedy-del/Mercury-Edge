from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid


def main() -> int:
    env = os.environ.copy()
    env.setdefault("PAPER_SESSION_ID", f"paper-{uuid.uuid4()}")
    child_specs = [
        ("kalshi", "collector.py", True),
        ("awc", "weather_collector.py", True),
        ("omo", "omo_collector.py", True),
        ("rules", "rule_collector.py", True),
        # Auditor failure makes readiness red but should not halt evidence capture.
        ("auditor", "audit_daemon.py", False),
    ]
    children: dict[str, tuple[subprocess.Popen[str], bool]] = {}
    for name, script, critical in child_specs:
        children[name] = (subprocess.Popen([sys.executable, script], env=env, text=True), critical)

    print(json.dumps({
        "event": "paper_runner_start",
        "session_id": env["PAPER_SESSION_ID"],
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
                # Restart non-critical auditor after a short delay. Its output is
                # diagnostic; collection must continue even if an audit process crashes.
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
