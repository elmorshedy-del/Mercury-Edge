from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid


def main() -> int:
    env = os.environ.copy()
    env.setdefault("PAPER_SESSION_ID", f"paper-{uuid.uuid4()}")
    children = [
        subprocess.Popen([sys.executable, "collector.py"], env=env),
        subprocess.Popen([sys.executable, "weather_collector.py"], env=env),
    ]

    stopping = False

    def terminate(*_: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    try:
        while True:
            for child in children:
                code = child.poll()
                if code is not None:
                    if not stopping:
                        terminate()
                    for other in children:
                        if other is not child and other.poll() is None:
                            try:
                                other.wait(timeout=15)
                            except subprocess.TimeoutExpired:
                                other.kill()
                    return code if code != 0 else 1
            time.sleep(0.25)
    finally:
        terminate()
        for child in children:
            if child.poll() is None:
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()


if __name__ == "__main__":
    raise SystemExit(main())
