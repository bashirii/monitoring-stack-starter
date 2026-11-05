#!/usr/bin/env python3
"""Breaks the stack on purpose, step by step, and checks that the right alerts fire and the messages arrive: in Mailpit, and (when set up with scripts/alerting.py)
at your real SMTP server and Slack, which Alertmanager's counters confirm.

The stack must be running. Takes about 15 minutes, because alerts wait out their "for" time.
    python3 scripts/test-alerts.py
"""
import json
import pathlib
import re
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROM, AM, MAIL = "http://127.0.0.1:9090", "http://127.0.0.1:9093", "http://127.0.0.1:8025"
results = []


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


def compose(*args):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


def prom_alerts():
    return {a["labels"]["alertname"]: a["state"] for a in get(PROM + "/api/v1/alerts")["data"]["alerts"]}


def emails():
    return [m["Subject"] for m in get(MAIL + "/api/v1/messages")["messages"]]


def notifications():
    """Messages Alertmanager has sent and failed to send, per channel, from its metrics."""
    sent, failed = {"email": 0, "slack": 0}, {"email": 0, "slack": 0}
    with urllib.request.urlopen(AM + "/metrics", timeout=10) as r:
        for line in r.read().decode().splitlines():
            m = re.match(r'alertmanager_notifications(_failed)?_total\{([^}]*)\} ([0-9.e+]+)', line)
            integration = re.search(r'integration="(\w+)"', m[2]) if m else None
            if integration and integration[1] in sent:
                (failed if m[1] else sent)[integration[1]] += int(float(m[3]))
    return sent, failed


def wait(description, test, timeout):
    start = time.time()
    while time.time() - start < timeout:
        if test():
            took = int(time.time() - start)
            print(f"  ok   {description} (after {took} s)", flush=True)
            results.append((description, True, took))
            return True
        time.sleep(5)
    print(f"  FAIL {description} (not seen within {timeout} s)", flush=True)
    results.append((description, False, timeout))
    return False


def traffic(mode, seconds):
    t = threading.Thread(target=lambda: subprocess.run([sys.executable, str(ROOT / "scripts" / "load.py"), mode, str(seconds)], capture_output=True))
    t.start()
    return t


def main():
    print("start: clear the mailbox and check the baseline")
    sent0, failed0 = notifications()
    urllib.request.urlopen(urllib.request.Request(MAIL + "/api/v1/messages", method="DELETE"), timeout=10)
    wait("only quiet alerts at the start", lambda: not {"TargetDown", "RedisDown", "PostgresDown", "HighErrorRate", "HighLatencyP95", "ProbeFailed"} & {k for k, v in prom_alerts().items() if v == "firing"}, 60)

    print("1. Redis stops")
    compose("stop", "redis")
    wait("RedisDown is firing", lambda: prom_alerts().get("RedisDown") == "firing", 150)
    wait("an email says [FIRING] RedisDown", lambda: any("[FIRING] RedisDown" in s for s in emails()), 120)
    compose("start", "redis")
    wait("RedisDown clears", lambda: "RedisDown" not in prom_alerts(), 150)
    wait("an email says [RESOLVED] RedisDown", lambda: any("[RESOLVED] RedisDown" in s for s in emails()), 150)

    print("2. half of the requests fail for 4 minutes")
    t = traffic("errors", 240)
    wait("HighErrorRate is firing", lambda: prom_alerts().get("HighErrorRate") == "firing", 300)
    wait("an email says [FIRING] HighErrorRate", lambda: any("[FIRING] HighErrorRate" in s for s in emails()), 120)
    t.join()

    print("3. every request takes 0.8 s for 5 minutes")
    t = traffic("slow", 300)
    wait("HighLatencyP95 is firing", lambda: prom_alerts().get("HighLatencyP95") == "firing", 360)
    wait("an email says [FIRING] HighLatencyP95", lambda: any("[FIRING] HighLatencyP95" in s for s in emails()), 120)
    t.join()

    print("4. the service stops")
    compose("stop", "demo-api")
    wait("TargetDown is firing", lambda: prom_alerts().get("TargetDown") == "firing", 150)
    wait("ProbeFailed is firing", lambda: prom_alerts().get("ProbeFailed") == "firing", 150)
    wait("an email says [FIRING] TargetDown", lambda: any("[FIRING] TargetDown" in s for s in emails()), 120)
    compose("start", "demo-api")
    wait("TargetDown clears", lambda: "TargetDown" not in prom_alerts(), 150)

    # Mailpit only proves the first email channel. With a real SMTP server and Slack configured (scripts/alerting.py),
    # Alertmanager's own counters show that those channels accepted the messages too.
    print("5. every channel accepted its messages")
    sent1, failed1 = notifications()
    for channel in ("email", "slack"):
        got, bad = sent1[channel] - sent0[channel], failed1[channel] - failed0[channel]
        ok = bad == 0 and got > 0
        print(f"  {'ok  ' if ok else 'FAIL'} {channel}: {got} sent, {bad} failed")
        results.append((f"{channel} notifications sent with no failure", ok, 0))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
