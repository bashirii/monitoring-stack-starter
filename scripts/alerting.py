#!/usr/bin/env python3
"""Turns the SMTP and Slack settings in .env into a working Alertmanager setup.

Alertmanager cannot read environment variables, and secrets must not sit in a YAML file. So this script:
  - writes the SMTP password and the Slack webhook URL to files in ./secrets (ignored by git),
  - copies alertmanager/alertmanager.yml to alertmanager/alertmanager.local.yml and fills the two markers
    in it with your email and Slack receivers (no secret goes into that file, only the file paths),
  - writes compose.override.yaml, which Docker Compose loads by itself: it mounts the local copy and the files.

Run it after editing .env, then restart Alertmanager:
    python3 scripts/alerting.py
    docker compose up -d alertmanager

Settings in .env (all optional; leave a group out and that channel stays off):
    SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD     the mail account that sends (Gmail: smtp.gmail.com, 587, an app password)
    ALERT_EMAIL_TO, SMTP_FROM                            who receives (default: SMTP_USER) and the From address (default: SMTP_USER)
    SLACK_WEBHOOK_URL, SLACK_CHANNEL                     an incoming webhook, and the channel name (only used if the webhook allows it)
"""
import argparse
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SMTP_KEYS = ("SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD")
CONTAINER_SECRETS = "/etc/alertmanager/secrets"
# Marks a compose.override.yaml this script wrote, so it is safe to overwrite.
OVERRIDE_HEADER = "# Written by scripts/alerting.py"


def read_env(path):
    env = {}
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def q(value):
    """A YAML-safe quoted string (a JSON string is also a YAML string)."""
    return json.dumps(value)


def write_secret(directory, name, value):
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)  # also closes a folder that already existed with looser rights
    path = directory / name
    # Created owner-only, filled, and only then made readable: there is no moment when the content is readable by others.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value)  # no newline: a trailing one would become part of the password
    # The container reads it as another user (nobody), so the file must be world-readable. The closed folder keeps
    # other users on this machine out; Docker, which mounts it, runs as root (same approach as scripts/secrets.sh).
    path.chmod(0o644)


def email_block(env):
    user = env["SMTP_USER"]
    port = env["SMTP_PORT"]
    lines = [
        f"- to: {q(env.get('ALERT_EMAIL_TO') or user)}",
        f"  from: {q(env.get('SMTP_FROM') or user)}",
        f"  smarthost: {q(env['SMTP_SERVER'] + ':' + port)}",
        f"  auth_username: {q(user)}",
        f"  auth_password_file: {CONTAINER_SECRETS}/smtp_password",
        # Port 587 starts plain and upgrades to TLS (STARTTLS); 465 is TLS from the first byte. Both must be encrypted.
        "  require_tls: true",
        "  send_resolved: true",
        "  html: '{{ template \"zib.email.html\" . }}'",
        "  text: '{{ template \"zib.email.text\" . }}'",
        "  headers:",
        "    subject: '{{ template \"zib.subject\" . }}'",
    ]
    return lines


def slack_block(env):
    lines = [
        "slack_configs:",
        f"  - api_url_file: {CONTAINER_SECRETS}/slack_webhook_url",
    ]
    if env.get("SLACK_CHANNEL"):
        lines.append(f"    channel: {q(env['SLACK_CHANNEL'])}")
    lines += [
        "    send_resolved: true",
        "    short_fields: true",
        "    mrkdwn_in: [text, fallback]",
        "    color: '{{ template \"zib.slack.color\" . }}'",
        "    fallback: '{{ template \"zib.slack.fallback\" . }}'",
        "    title: '{{ template \"zib.slack.title\" . }}'",
        # The title opens this alert in Alertmanager.
        "    title_link: '{{ .ExternalURL }}/#/alerts?silenced=false&inhibited=false&filter=%7Balertname%3D%22{{ .CommonLabels.alertname }}%22%7D'",
        "    text: '{{ template \"zib.slack.text\" . }}'",
        "    fields:",
        "      - title: Severity",
        "        value: '{{ if .CommonLabels.severity }}{{ .CommonLabels.severity | toUpper }}{{ else }}-{{ end }}'",
        "      - title: Service",
        "        value: '{{ if .CommonLabels.job }}{{ .CommonLabels.job }}{{ else }}-{{ end }}'",
        "      - title: Started",
        "        value: '{{ template \"zib.slack.started\" . }}'",
        "      - title: Status",
        "        value: '{{ if eq .Status \"firing\" }}Firing{{ if gt (len .Alerts.Firing) 1 }} ({{ len .Alerts.Firing }} alerts){{ end }}{{ else }}Resolved{{ end }}'",
        "    footer: '{{ template \"zib.slack.footer\" . }}'",
    ]
    return lines


def fill(template, marker, lines):
    """Replaces the comment line holding `marker` with `lines`, indented like the comment."""
    pattern = re.compile(rf"^(?P<indent>[ ]*)# {re.escape(marker)}[ ]*\n", re.M)
    match = pattern.search(template)
    if not match:
        sys.exit(f"error: marker {marker} not found in alertmanager.yml")
    indent = match["indent"]
    body = "".join(f"{indent}{line}\n" for line in lines)
    return template[: match.start()] + body + template[match.end():]


def render(env, root):
    """Returns (enabled channels, config text, compose override text) and writes the secret files."""
    template = (root / "alertmanager" / "alertmanager.yml").read_text()
    enabled, secrets = [], []

    smtp_given = [k for k in SMTP_KEYS if env.get(k)]
    if smtp_given and len(smtp_given) < len(SMTP_KEYS):
        missing = ", ".join(k for k in SMTP_KEYS if not env.get(k))
        sys.exit(f"error: SMTP is half set in .env, missing: {missing}")
    if smtp_given:
        if not env["SMTP_PORT"].isdigit():
            sys.exit("error: SMTP_PORT must be a number such as 587")
        write_secret(root / "secrets", "smtp_password", env["SMTP_PASSWORD"])
        template = fill(template, "@@EXTRA_EMAIL@@", email_block(env))
        enabled.append(f"email via {env['SMTP_SERVER']}:{env['SMTP_PORT']} to {env.get('ALERT_EMAIL_TO') or env['SMTP_USER']}")
        secrets.append("smtp_password")
    else:
        template = template.replace("      # @@EXTRA_EMAIL@@\n", "")

    if env.get("SLACK_WEBHOOK_URL"):
        if not env["SLACK_WEBHOOK_URL"].startswith("https://hooks.slack.com/"):
            sys.exit("error: SLACK_WEBHOOK_URL should start with https://hooks.slack.com/")
        write_secret(root / "secrets", "slack_webhook_url", env["SLACK_WEBHOOK_URL"])
        template = fill(template, "@@SLACK@@", slack_block(env))
        enabled.append("Slack" + (f" channel {env['SLACK_CHANNEL']}" if env.get("SLACK_CHANNEL") else ""))
        secrets.append("slack_webhook_url")
    else:
        template = template.replace("    # @@SLACK@@\n", "")

    override = [
        f"{OVERRIDE_HEADER} from .env. Not committed. Delete it to go back to Mailpit only.",
        "services:",
        "  alertmanager:",
        "    volumes:",
        "      - ./alertmanager/alertmanager.local.yml:/etc/alertmanager/alertmanager.yml:ro",
    ]
    for name in secrets:
        override.append(f"      - ./secrets/{name}:{CONTAINER_SECRETS}/{name}:ro")
    return enabled, template, "\n".join(override) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=str(ROOT / ".env"), help="the settings file (default: .env)")
    parser.add_argument("--out", default=str(ROOT), help="the project folder to write into (default: this repository)")
    args = parser.parse_args()
    root = pathlib.Path(args.out)

    if not pathlib.Path(args.env).exists():
        sys.exit(f"error: {args.env} not found. Copy .env.example to .env and fill in SMTP and Slack.")
    # Never overwrite a user's own override. Check first: render() writes the secret files.
    override_path = root / "compose.override.yaml"
    if override_path.exists() and not override_path.read_text(errors="replace").startswith(OVERRIDE_HEADER):
        sys.exit(f"error: {override_path} exists and was not written by this script, so it is left alone. "
                 "Move it away, or merge by hand the volumes that alertmanager needs.")
    env = read_env(args.env)
    enabled, config, override = render(env, root)
    (root / "alertmanager" / "alertmanager.local.yml").write_text(config)
    override_path.write_text(override)

    print("Alert channels: Mailpit (always)" + "".join(f", {e}" for e in enabled))
    if not enabled:
        print("Nothing to add: set the SMTP or Slack values in .env first.")
    print("Wrote alertmanager/alertmanager.local.yml and compose.override.yaml. Next: docker compose up -d alertmanager")


if __name__ == "__main__":
    main()
