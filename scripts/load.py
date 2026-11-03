#!/usr/bin/env python3
"""Sends traffic to the demo API so the dashboards move and the alerts can fire.

    python3 scripts/load.py ok 60        healthy traffic for 60 seconds
    python3 scripts/load.py errors 240   half of the requests fail (HighErrorRate)
    python3 scripts/load.py slow 240     every request takes 0.8 s (HighLatencyP95)
"""
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
MODES = {
    "ok": ["/work"],
    "errors": ["/work", "/error"],
    "slow": ["/slow"],
}


def worker(paths, stop):
    i = 0
    while not stop.is_set():
        path = paths[i % len(paths)]
        i += 1
        try:
            urllib.request.urlopen(BASE + path, timeout=10).read()
        except urllib.error.HTTPError:
            pass  # the failing route fails on purpose
        except OSError:
            time.sleep(1)
        time.sleep(0.05)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    stop = threading.Event()
    threads = [threading.Thread(target=worker, args=(MODES[mode], stop), daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=15)
    print(f"sent {mode} traffic for {seconds} s")


if __name__ == "__main__":
    main()
