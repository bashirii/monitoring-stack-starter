#!/usr/bin/env python3
"""Writes the two Grafana dashboards as JSON files into grafana/dashboards/.

The dashboards are code: change a query here, run this script, commit the JSON. Run it with
    python3 scripts/build-dashboards.py
"""
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "grafana" / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}


class Board:
    def __init__(self, uid, title, tags):
        self.uid, self.title, self.tags = uid, title, tags
        self.panels, self.y, self.x, self.next_id = [], 0, 0, 1

    def _place(self, w, h):
        if self.x + w > 24:
            self.x, self.y = 0, self.y + h
        pos = {"x": self.x, "y": self.y, "w": w, "h": h}
        self.x += w
        return pos

    def row(self, title):
        self.x, self.y = 0, self.y + (self.panels and self.panels[-1]["gridPos"]["h"] or 0)
        self.panels.append({"id": self.next_id, "type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.next_id += 1
        self.y += 1

    def stat(self, title, expr, unit="short", w=4, thresholds=None, description=""):
        steps = thresholds or [{"color": "green", "value": None}]
        self.panels.append({
            "id": self.next_id, "type": "stat", "title": title, "description": description,
            "datasource": DS, "gridPos": self._place(w, 4),
            "targets": [{"expr": expr, "refId": "A", "instant": True}],
            "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "none", "textMode": "auto"},
        })
        self.next_id += 1

    def series(self, title, queries, unit="short", w=12, h=8, description="", stack=False, minimum=None, maximum=None):
        defaults = {"unit": unit, "custom": {"lineWidth": 1, "fillOpacity": 10, "showPoints": "never",
                                              "stacking": {"mode": "normal" if stack else "none"}}}
        if minimum is not None:
            defaults["min"] = minimum
        if maximum is not None:
            defaults["max"] = maximum
        self.panels.append({
            "id": self.next_id, "type": "timeseries", "title": title, "description": description,
            "datasource": DS, "gridPos": self._place(w, h),
            "targets": [{"expr": e, "legendFormat": legend, "refId": chr(65 + i)} for i, (e, legend) in enumerate(queries)],
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        })
        self.next_id += 1

    def save(self, filename):
        board = {"uid": self.uid, "title": self.title, "tags": self.tags, "timezone": "browser", "schemaVersion": 39,
                 "version": 1, "refresh": "15s", "time": {"from": "now-30m", "to": "now"},
                 "panels": self.panels, "templating": {"list": []}, "annotations": {"list": []}}
        (OUT / filename).write_text(json.dumps(board, indent=2) + "\n")


OK, WARN, BAD = "green", "orange", "red"

# ------------------------------------------------------------------ service overview
b = Board("service-overview", "Service overview", ["starter", "service"])
b.row("Is it working? Look here first")
b.stat("Targets up", 'sum(up == 1) or vector(0)', w=4, description="Scrape targets that answer. Should equal the number of targets.")
b.stat("Targets down", 'count(up == 0) or vector(0)', w=4, thresholds=[{"color": OK, "value": None}, {"color": BAD, "value": 1}])
b.stat("Firing alerts", 'count(ALERTS{alertstate="firing", alertname!="Watchdog"}) or vector(0)', w=4,
       thresholds=[{"color": OK, "value": None}, {"color": BAD, "value": 1}], description="Alerts firing now, not counting the always-on Watchdog.")
b.stat("Requests per second", 'sum(rate(http_requests_total{job="demo-api"}[1m]))', unit="reqps", w=4)
b.stat("Error ratio (5xx)", 'job:http_errors:ratio_rate5m{job="demo-api"}', unit="percentunit", w=4,
       thresholds=[{"color": OK, "value": None}, {"color": WARN, "value": 0.01}, {"color": BAD, "value": 0.05}])
b.stat("Latency p95", 'job:http_request_duration_seconds:p95_5m{job="demo-api"}', unit="s", w=4,
       thresholds=[{"color": OK, "value": None}, {"color": WARN, "value": 0.25}, {"color": BAD, "value": 0.5}])

b.row("The service (demo-api): rate, errors, duration")
b.series("Requests per second by route", [('sum by (path) (rate(http_requests_total{job="demo-api"}[1m]))', "{{path}}")], unit="reqps", stack=True)
b.series("Responses by status", [('sum by (status) (rate(http_requests_total{job="demo-api"}[1m]))', "{{status}}")], unit="reqps", stack=True)
b.series("Error ratio, 5xx over all requests", [('sum(rate(http_requests_total{job="demo-api",status=~"5.."}[1m])) / sum(rate(http_requests_total{job="demo-api"}[1m]))', "error ratio")], unit="percentunit", minimum=0)
b.series("Latency percentiles", [
    ('histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket{job="demo-api"}[1m])))', "p50"),
    ('histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{job="demo-api"}[1m])))', "p95"),
    ('histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{job="demo-api"}[1m])))', "p99"),
], unit="s", minimum=0)

b.row("Seen from outside, and its dependencies")
b.series("HTTP check: success (1) or failure (0)", [("probe_success", "{{instance}}")], w=8, h=7, minimum=0, maximum=1)
b.series("HTTP check: time to answer", [("probe_duration_seconds", "{{instance}}")], unit="s", w=8, h=7, minimum=0)
b.series("Targets that answer (1) or not (0)", [("up", "{{job}}")], w=8, h=7, minimum=0, maximum=1)
b.series("Redis: clients and memory", [("redis_connected_clients", "connected clients"), ("redis_memory_used_bytes / 1024 / 1024", "memory used (MiB)")], w=8, h=7)
b.series("Redis: commands per second", [("rate(redis_commands_processed_total[1m])", "commands")], unit="ops", w=8, h=7, minimum=0)
b.series("PostgreSQL: connections and commits", [("sum(pg_stat_activity_count)", "connections"), ("rate(pg_stat_database_xact_commit{datname='app'}[1m])", "commits per second")], w=8, h=7)
b.save("service-overview.json")

# ------------------------------------------------------------------ host and containers
h = Board("host-and-containers", "Host and containers", ["starter", "host"])
h.row("The machine")
h.stat("CPU use", '100 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100', unit="percent", w=6,
       thresholds=[{"color": OK, "value": None}, {"color": WARN, "value": 70}, {"color": BAD, "value": 85}])
h.stat("Memory use", '(1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100', unit="percent", w=6,
       thresholds=[{"color": OK, "value": None}, {"color": WARN, "value": 75}, {"color": BAD, "value": 90}])
h.stat("Root disk free", '(node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100', unit="percent", w=6,
       thresholds=[{"color": BAD, "value": None}, {"color": WARN, "value": 15}, {"color": OK, "value": 30}])
h.stat("Uptime", "time() - node_boot_time_seconds", unit="s", w=6)
h.series("CPU by mode", [('avg by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[1m])) * 100', "{{mode}}")], unit="percent", stack=True, minimum=0)
h.series("Memory", [("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "used"), ("node_memory_MemAvailable_bytes", "available")], unit="bytes", stack=True)
h.series("Disk free by mount", [('(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs|ramfs"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs|ramfs"}) * 100', "{{mountpoint}}")], unit="percent", minimum=0, maximum=100)
h.series("Network", [('sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*|br-.*|docker.*"}[1m]))', "received"), ('sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|br-.*|docker.*"}[1m]))', "sent")], unit="Bps")
h.row("Containers")
h.series("CPU by container", [('sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[1m])) * 100', "{{name}}")], unit="percent", minimum=0)
h.series("Memory by container", [('container_memory_working_set_bytes{name!=""}', "{{name}}")], unit="bytes", minimum=0)
h.series("Container restarts in the last hour", [('changes(container_start_time_seconds{name!=""}[1h])', "{{name}}")], minimum=0)
h.series("Memory use against the limit", [('container_memory_working_set_bytes{name!=""} / (container_spec_memory_limit_bytes{name!=""} > 0) * 100', "{{name}}")], unit="percent", minimum=0, maximum=100)
h.save("host-and-containers.json")
print("wrote", sorted(p.name for p in OUT.glob("*.json")))
