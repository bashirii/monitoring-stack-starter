#!/usr/bin/env python3
"""Sends one test alert straight to Alertmanager and checks that every channel accepted it: Mailpit, your SMTP server and Slack.

Needs only Alertmanager (and Mailpit) running, not the whole stack:
    docker compose up -d alertmanager mailpit
    python3 scripts/test-delivery.py

It reads the counters Alertmanager keeps per channel (`alertmanager_notifications_total` and
`alertmanager_notifications_failed_total`). A counter going up with no failure means the mail server answered
"accepted" or Slack answered "ok". It cannot see your inbox or your channel, so look there too: the script prints what to expect.
Takes about 1.5 minutes: the firing message after 10 s, the resolved message after the 1 minute group interval.
"""
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent


def read_env():
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("\"'")
    return env


ENV = read_env()
HOST = ENV.get("BIND_IP", "127.0.0.1")
AM = f"http://{HOST}:{ENV.get('ALERTMANAGER_PORT', '9093')}"
MAIL = f"http://{HOST}:{ENV.get('MAILPIT_PORT', '8025')}"
SMTP_ON = all(ENV.get(k) for k in ("SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"))
SLACK_ON = bool(ENV.get("SLACK_WEBHOOK_URL"))
EXPECTED = {"email": 1 + SMTP_ON, "slack": int(SLACK_ON)}  # Mailpit is always one email


def request(url, data=None, method=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode()


def counters():
    """Sent and failed notifications per channel, from Alertmanager's metrics."""
    sent, failed = {"email": 0, "slack": 0}, {"email": 0, "slack": 0}
    for line in request(AM + "/metrics").splitlines():
        m = re.match(r'alertmanager_notifications(_failed)?_total\{([^}]*)\} ([0-9.e+]+)', line)
        if not m:
            continue
        integration = re.search(r'integration="(\w+)"', m[2])
        if integration and integration[1] in sent:
            (failed if m[1] else sent)[integration[1]] += int(float(m[3]))
    return sent, failed


STARTED = datetime.now(timezone.utc) - timedelta(seconds=1)


def post_alert(resolve):
    """Posts the test alert; with resolve=True its end time is now, which makes Alertmanager send the resolved message."""
    now = datetime.now(timezone.utc)
    request(AM + "/api/v2/alerts", [{
        "labels": {"alertname": "DeliveryTest", "severity": "warning", "job": "delivery-test"},
        "annotations": {"summary": "Test alert from scripts/test-delivery.py",
                        "description": "If you can read this, this channel works. Nothing is wrong with your servers."},
        "startsAt": STARTED.isoformat(),
        "endsAt": (now if resolve else now + timedelta(minutes=10)).isoformat(),
    }])


def mailpit_subjects():
    return [m["Subject"] for m in json.loads(request(MAIL + "/api/v1/messages"))["messages"]]


def redacted_errors():
    out = subprocess.run(["docker", "compose", "logs", "--no-log-prefix", "--tail", "40", "alertmanager"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    lines = [l for l in out.splitlines() if "level=ERROR" in l or "level=WARN" in l]
    # error text can quote the Slack URL: never print it
    return [re.sub(r"https://hooks\.slack\.com/\S+", "<webhook>", l) for l in lines[-6:]]


def wait_for(stage, base_sent, timeout):
    """Waits until each active channel has sent `EXPECTED` more notifications than `base_sent`, or any failed."""
    start = time.time()
    while time.time() - start < timeout:
        sent, failed = counters()
        done = all(sent[c] - base_sent[c] >= n for c, n in EXPECTED.items())
        if done or any(failed[c] > base_failed[c] for c in failed):
            return sent, failed, int(time.time() - start)
        time.sleep(3)
    sent, failed = counters()
    return sent, failed, int(time.time() - start)


def main():
    global base_failed
    try:
        request(AM + "/-/ready")
    except Exception as e:
        sys.exit(f"Alertmanager does not answer at {AM}: {e}\nStart it: docker compose up -d alertmanager mailpit")
    channels = ["Mailpit"] + (["SMTP " + ENV["SMTP_SERVER"]] if SMTP_ON else []) + (["Slack"] if SLACK_ON else [])
    print("Testing channels:", ", ".join(channels))
    if not (SMTP_ON or SLACK_ON):
        print("note: no SMTP or Slack in .env, so only Mailpit is tested")

    try:
        request(MAIL + "/api/v1/messages", method="DELETE")
    except Exception:
        pass
    base_sent, base_failed = counters()
    ok = True

    for stage, timeout in (("firing", 60), ("resolved", 120)):
        post_alert(resolve=stage == "resolved")
        sent, failed, took = wait_for(stage, base_sent, timeout)
        for channel, n in EXPECTED.items():
            if not n:
                continue
            got, bad = sent[channel] - base_sent[channel], failed[channel] - base_failed[channel]
            good = got >= n and bad == 0
            ok &= good
            print(f"  {'ok  ' if good else 'FAIL'} {stage:8} {channel:6} sent {got} of {n}, failed {bad} (after {took} s)")
        base_sent, base_failed = sent, failed
        if stage == "firing":
            subjects = mailpit_subjects()
            hit = any("[FIRING] DeliveryTest" in s for s in subjects)
            ok &= hit
            print(f"  {'ok  ' if hit else 'FAIL'} Mailpit holds the message [FIRING] DeliveryTest")
            print("  waiting for the resolved message (the 1 minute group interval)...", flush=True)
        if not ok:
            break

    if not ok:
        print("\nRecent Alertmanager warnings and errors:")
        for l in redacted_errors():
            print("  " + l)
    else:
        print("\nAlertmanager reports every channel accepted both messages. Now look:")
        if SMTP_ON:
            print(f"  - inbox of {ENV.get('ALERT_EMAIL_TO') or ENV['SMTP_USER']}: '[FIRING] DeliveryTest (warning)' and '[RESOLVED] ...' (check Spam once)")
        if SLACK_ON:
            print(f"  - Slack {ENV.get('SLACK_CHANNEL', '')}: a red 'FIRING' and a green 'RESOLVED' message")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
