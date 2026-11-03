#!/usr/bin/env python3
"""Asks Grafana to run every query of every provisioned dashboard and reports panels that fail or return nothing.

Needs the stack running. Usage: python3 scripts/check-dashboards.py
"""
import base64
import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
PASSWORD = (ROOT / "secrets" / "grafana_admin_password").read_text().strip()
BASE = "http://127.0.0.1:3000"
AUTH = "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": AUTH, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


found = call("/api/search?type=dash-db")
print("dashboards in Grafana:", sorted(d["title"] for d in found))
bad = 0
now = int(time.time() * 1000)
for item in found:
    dash = call(f"/api/dashboards/uid/{item['uid']}")["dashboard"]
    for panel in dash["panels"]:
        for target in panel.get("targets", []):
            query = {"refId": target["refId"], "datasource": panel["datasource"], "expr": target["expr"],
                     "instant": bool(target.get("instant")), "range": not target.get("instant"),
                     "intervalMs": 15000, "maxDataPoints": 300}
            result = call("/api/ds/query", {"queries": [query], "from": str(now - 900_000), "to": str(now)})
            res = result["results"][target["refId"]]
            frames = res.get("frames", [])
            if res.get("error"):
                status = "ERROR " + res["error"][:80]
            elif not frames:
                status = "no data"
            else:
                status = f"{len(frames)} series"
            flag = "!!" if status.startswith("ERROR") else "  "
            if status.startswith("ERROR"):
                bad += 1
            print(f"{flag} {dash['title'][:20]:20} | {panel['title'][:42]:42} | {status}")
print("panels with errors:", bad)
sys.exit(1 if bad else 0)
