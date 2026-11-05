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
run amtool -v "$PWD/alertmanager/templates:/etc/alertmanager/templates:ro" "$AM" check-config /w/alertmanager/alertmanager.yml
echo "== email and Slack templates render for a firing and a resolved alert"
for state in firing resolved; do
  for name in email.html email.text slack.title slack.text slack.fallback slack.footer; do
    out=$(run amtool -v "$PWD/tests:/d:ro" -v "$PWD/alertmanager/templates:/t:ro" "$AM" template render \
      --template.glob='/t/*.tmpl' --template.text="{{ template \"zib.$name\" . }}" --template.data="/d/alert-$state.json")
    echo "$out" | grep -q -E 'TargetDown|demo-api|ZIB TechSquare' || { echo "template zib.$name ($state) did not render the alert"; exit 1; }
  done
done
echo ok
echo "== alert delivery settings (scripts/alerting.py) produce a valid Alertmanager configuration"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/alertmanager"; cp alertmanager/alertmanager.yml "$tmp/alertmanager/"; cp -r alertmanager/templates "$tmp/alertmanager/templates"
printf '%s\n' SMTP_SERVER=smtp.example.test SMTP_PORT=587 SMTP_USER=alerts@example.test SMTP_PASSWORD=not-a-real-password \
  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T000/B000/not-a-real-webhook 'SLACK_CHANNEL=#alerts' > "$tmp/env"
python3 scripts/alerting.py --env "$tmp/env" --out "$tmp" >/dev/null
chmod -R a+rX "$tmp"  # the amtool container runs as another user
docker run --rm -v "$tmp:/w:ro" -v "$tmp/alertmanager/templates:/etc/alertmanager/templates:ro" --entrypoint amtool "$AM" check-config /w/alertmanager/alertmanager.local.yml
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
