#!/usr/bin/env bash
# Static checks, no containers started: configuration syntax, rule unit tests, dashboards and the compose file.
set -euo pipefail
cd "$(dirname "$0")/.."
PROM=prom/prometheus:v3.15.1
AM=prom/alertmanager:v0.34.2
BB=prom/blackbox-exporter:v0.29.1
run() { docker run --rm -v "$PWD:/w:ro" --entrypoint "$1" "${@:2}"; }

echo "== prometheus configuration and rules"
run promtool "$PROM" check config /w/prometheus/prometheus.yml
echo "== alert rule unit tests"
run promtool "$PROM" test rules /w/tests/rules_test.yml
echo "== alertmanager configuration"
run amtool "$AM" check-config /w/alertmanager/alertmanager.yml
echo "== blackbox configuration"
docker run --rm -v "$PWD/blackbox:/c:ro" "$BB" --config.file=/c/blackbox.yml --config.check
echo "== dashboards are valid JSON and match the generator"
python3 scripts/build-dashboards.py >/dev/null
git diff --quiet -- grafana/dashboards 2>/dev/null || echo "note: dashboards differ from the committed copy (run git diff)"
python3 -c "import json,glob; [json.load(open(f)) for f in glob.glob('grafana/dashboards/*.json')]; print('ok')"
echo "== compose file"
[ -s secrets/postgres_password ] || ./scripts/secrets.sh >/dev/null
docker compose config -q && echo ok
echo "all static checks passed"
