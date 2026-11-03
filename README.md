# monitoring-stack-starter

A small, complete monitoring and alerting stack for Docker servers, with a demo service you can break on purpose. You run one command, and you get dashboards, alerts that reach an inbox, and a way to prove the alerts work.

It answers four questions about a server:

1. **Is it up?** Every target is scraped every 15 seconds, and an HTTP check asks the service from outside.
2. **Is it working?** Request rate, error ratio and latency per route.
3. **Is the machine healthy?** CPU, memory, disk and every container.
4. **Will someone find out?** Alerts go to email (Slack is one block away), and a `Watchdog` alert tells you when alerting itself has broken.

Everything here was written for this repository and run end to end. The alert rules have unit tests, and the alerts were fired for real (see [What was tested](#what-was-tested)).

## What is inside

| Part | What it does |
|------|--------------|
| **Prometheus** | Collects the numbers, stores them for 15 days, evaluates the alert rules |
| **Alertmanager** | Groups alerts, routes them, sends email, and keeps a service that is down from also paging for its errors |
| **Grafana** | Two dashboards, written as code: *Service overview* and *Host and containers* |
| **node-exporter** | Machine metrics: CPU, memory, disk, network |
| **cAdvisor** | Container metrics: CPU, memory, restarts, out-of-memory kills |
| **redis_exporter**, **postgres_exporter** | Health of the two databases the demo runs with |
| **Blackbox exporter** | Asks the service from outside: does `/health` answer, how fast |
| **demo-api** | A tiny FastAPI service with routes that fail or run slowly on purpose |
| **Mailpit** | Catches the alert emails so you can read them without a mail server |

## Quick start

You need Docker with Compose, and `openssl`. About 3 GB of images are pulled the first time.

```bash
git clone https://github.com/bashirii/monitoring-stack-starter.git
cd monitoring-stack-starter
cp .env.example .env            # optional: change ports
./scripts/secrets.sh            # creates random passwords in ./secrets and prints the Grafana login once
docker compose up -d --build
```

Then open (all on this machine only):

| Page | Address |
|------|---------|
| Grafana | <http://127.0.0.1:3000> (user `admin`, the password printed by `secrets.sh`) |
| Prometheus | <http://127.0.0.1:9090> |
| Alertmanager | <http://127.0.0.1:9093> |
| Alert emails (Mailpit) | <http://127.0.0.1:8025> |
| The demo service | <http://127.0.0.1:8000> |

Give it a minute: Grafana opens on *Service overview*. Send some traffic so the panels move:

```bash
python3 scripts/load.py ok 60
```

## See the alerts work

Break something and watch.

```bash
docker compose stop redis                 # RedisDown fires after about a minute, an email arrives
docker compose start redis                # it resolves, a second email arrives
python3 scripts/load.py errors 240        # half of the requests fail: HighErrorRate
python3 scripts/load.py slow 300          # every request takes 0.8 s: HighLatencyP95
docker compose stop demo-api              # TargetDown and ProbeFailed
```

`python3 scripts/test-alerts.py` does all of this for you, in order, and checks that each alert fired and each email arrived. It takes about 15 minutes, because alerts wait out their `for:` time before they fire.

## The alerts

| Alert | Fires when | Severity | First move |
|-------|-----------|----------|------------|
| `Watchdog` | Always. Goes to no one. Send it to a heartbeat service in production: when the heartbeat stops, alerting is broken | none | |
| `TargetDown` | A scrape target has not answered for 1 minute | critical | `docker compose ps`, then the container's logs |
| `ProbeFailed` | The outside HTTP check has failed for 1 minute | critical | Is the service up? Is a proxy or firewall in the way? |
| `RedisDown`, `PostgresDown` | The exporter reaches no database for 30 seconds | critical | The database container, its logs, its disk |
| `HighErrorRate` | More than 5% of requests return 5xx, over 5 minutes, for 1 minute | critical | The service's logs for the failing route |
| `HighLatencyP95` | 95% of requests take over 0.5 s, for 2 minutes | warning | Slow query, slow dependency, or too little CPU |
| `HostHighCpu` / `HostHighMemory` | CPU above 85% for 10 minutes, memory above 90% for 5 | warning | Which container? Look at *Host and containers* |
| `HostDiskAlmostFull` | Less than 15% free for 5 minutes | warning | Logs, images, old backups |
| `HostDiskFillsIn24h` | At the rate of the last 6 hours the disk is full within a day | warning | The same, before it becomes an outage |
| `ContainerKilledForMemory` | The kernel killed a container for memory | critical | Raise its memory limit, or fix the leak |
| `CertificateExpiresSoon` | An https target's certificate ends in under 14 days | warning | Renew it. Needs an https target in the blackbox job |

The thresholds are starting points. Change them in `prometheus/rules/alerts.yml`, run `./scripts/check.sh`, and reload Prometheus with a signal (the web reload and shutdown endpoints are left switched off on purpose, because they have no login):

```bash
docker compose kill -s HUP prometheus
```

## Use it for your own services

1. **Make the service expose metrics.** Any Prometheus client library will do. `demo-api/app.py` shows the two metrics that matter most for a web service: a request counter and a duration histogram, labelled by route and status.
2. **Add it to `prometheus/prometheus.yml`** as a new job, on the same Docker network.
3. **Add an outside check** by adding its address to the `blackbox-http` targets.
4. **Reuse the dashboards.** The queries in `scripts/build-dashboards.py` use `job="demo-api"`: change the job name, run the script, and commit the JSON.
5. **Run `./scripts/check.sh`.** It validates the configuration and runs the rule unit tests, with nothing started.

## Slack, a real mail server and the Watchdog

- **Email:** `alertmanager/alertmanager.yml` points at the Mailpit container. Put your SMTP server in `smtp_smarthost` and keep its password in a file (`smtp_auth_password_file`), never in the YAML.
- **Slack:** the receiver is written and commented out in `alertmanager.yml`. Create an incoming webhook, put its URL in a file, mount the file, and enable the block. The URL is read from the file and is never stored in the configuration.
- **Watchdog:** it fires all the time, so it is routed to a "blackhole" receiver. In production, point it at a heartbeat service (for example healthchecks.io) that alerts you when the heartbeats stop.

## Security notes

- Every published port is bound to `127.0.0.1`. To reach Grafana from another machine, put a reverse proxy with HTTPS and a login in front of it. Do not publish Prometheus, Alertmanager or Mailpit to the internet: they have no login.
- Prometheus runs without its web reload and shutdown endpoints (`--web.enable-lifecycle` is off), so a web page you visit cannot stop it or reload it by sending a request to `127.0.0.1:9090`. Reload with `docker compose kill -s HUP prometheus`.
- Alertmanager and Mailpit have web pages and an API with no login. They are bound to `127.0.0.1` and hold only demo data here. Before you bind them to another address, add an authenticating reverse proxy, or give Alertmanager a `--web.config.file` with `basic_auth_users`, and stop publishing Mailpit's port (remove the service when you use a real mail server).
- Browsers can reach `127.0.0.1` from any web page you open. That is the reason for the three points above; keep the stack off a machine you use to browse untrusted sites if the data in it matters.
- The passwords are random files in `./secrets` (ignored by git). The compose file reads them as Docker secrets.
- `node-exporter` and `cAdvisor` read the host's filesystem read-only. That is how they see the machine, and it is why they should not be exposed.
- The demo service runs as a non-root user with a read-only filesystem and no extra capabilities.

## What was tested

Run on 2026-11-03 on Arch Linux with Docker 29.9.2 and Compose 5.6.1.

| Component | Version |
|-----------|---------|
| Prometheus | 3.15.1 |
| Alertmanager | 0.34.2 |
| Grafana OSS | 13.0.2 |
| node_exporter | 1.12.1 |
| cAdvisor | 0.60.6 |
| redis_exporter | 1.96.0 |
| postgres_exporter | 0.20.1 |
| Blackbox exporter | 0.29.1 |
| Mailpit | 1.33.0 |
| Redis / PostgreSQL | 7 / 17 |

**Static checks** (`./scripts/check.sh`, nothing started): the Prometheus, Alertmanager and Blackbox configuration are valid, the nine rule unit tests pass, the dashboards are valid JSON, and the compose file parses. To make sure the tests can fail, two rules were broken on purpose in a scratch copy (the error-rate threshold raised to 90%, the `TargetDown` expression changed): the tests failed both times.

**The stack running:** all 9 scrape targets were up within about a minute of `docker compose up`, and no rule had an evaluation error. `scripts/check-dashboards.py` ran every query of both dashboards through Grafana: 28 panels and 34 queries, no errors, and every panel showed data once traffic was sent.

**The alerts, fired for real** (`scripts/test-alerts.py`, 13 of 13 checks passed, about 15 minutes in all):

| Step | What was done | Alert fired after | Email |
|------|---------------|-------------------|-------|
| 1 | Redis stopped, then started | `RedisDown` 60 s | `[FIRING]` after 10 s, `[RESOLVED]` 25 s after the alert cleared |
| 2 | Half of the requests fail for 4 minutes | `HighErrorRate` 90 s | `[FIRING]` after 10 s |
| 3 | Every request takes 0.8 s for 5 minutes | `HighLatencyP95` 210 s | `[FIRING]` after 10 s |
| 4 | The demo service stopped, then started | `TargetDown` 80 s, `ProbeFailed` firing in the same window | `[FIRING]` after 10 s; `TargetDown` cleared 15 s after the restart |

Times include the rule's own `for:` wait and up to one 15-second scrape. The emails were read in Mailpit.

![Service overview during the error test](docs/img/service-overview-incident.jpg)

*The Service overview dashboard while half of the requests fail: one alert firing, a 49% error ratio.*

![An alert email in Mailpit](docs/img/alert-email.jpg)

*The email Alertmanager sent for `HighErrorRate`.*

**Not tested:** Slack delivery (the block is written but was not run), a real SMTP server, the `CertificateExpiresSoon` alert (it needs an https target; only the rule syntax is checked), the host alerts on a live machine (CPU, memory, disk and out-of-memory are covered by the unit tests, not provoked for real), routing `Watchdog` to a heartbeat service, running on a remote server behind a reverse proxy, other operating systems, and upgrades between versions.

**Known limits:** cAdvisor lists every container on the host, not only this stack's, so the container panels show all of them. `HostDiskFillsIn24h` stays quiet for the first 6 hours of a new stack by design. The demo service's port is published only so you can send it traffic.

## Layout

```text
compose.yaml                 the whole stack
.env.example                 ports and retention
prometheus/                  scrape jobs, alert rules, recording rules
alertmanager/                routing, email, inhibition
blackbox/                    the outside HTTP check
grafana/                     datasource and dashboards, provisioned as code
demo-api/                    the service that gets monitored
tests/rules_test.yml         unit tests for the alert rules
scripts/                     secrets, static checks, load, alert test, dashboard check, dashboard generator
```

## Licence

MIT, see `LICENSE`.
