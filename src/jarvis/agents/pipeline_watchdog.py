"""pipeline_watchdog — daily health check of NEB infrastructure Jarvis depends on.

v2: per-metric HAE freshness with watch-off awareness. The v1 single-check
"any rows in 25h" used wrong query params (from/to instead of start/end)
and silently passed against all-time data.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from jarvis.agents._health.metric_fetch import METRIC_REGISTRY
from jarvis.channels import telegram_notify

logger = logging.getLogger(__name__)

USER_SERVICES = [
    "openclaw-gateway",
    "pubsub-puller",
    "webhook-listener",
    "webhook-receiver",
    "webhook-tunnel",
    "cloudflare-tunnel",
    "n8n-openclaw-bridge",
    "hae-tunnel",
]
SYSTEM_SERVICES = ["cloudflared-neb"]
HAE_CONTAINERS = ["hae-mongo", "hae-server"]
DISK_WARN_PCT = 90


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:
        return -1, str(exc)


def check_user_services() -> tuple[list[str], list[str]]:
    issues: list[str] = []
    fixes: list[str] = []
    for svc in USER_SERVICES:
        _, out = _run(["systemctl", "--user", "is-active", f"{svc}.service"])
        if "active" in out:
            continue
        _run(["systemctl", "--user", "restart", f"{svc}.service"], timeout=15)
        _, out2 = _run(["systemctl", "--user", "is-active", f"{svc}.service"])
        if "active" in out2:
            fixes.append(f"{svc} was down — auto-restarted")
        else:
            issues.append(f"{svc} is DOWN (auto-restart failed)")
    return issues, fixes


def check_system_services() -> tuple[list[str], list[str]]:
    issues: list[str] = []
    fixes: list[str] = []
    for svc in SYSTEM_SERVICES:
        _, out = _run(["sudo", "-n", "systemctl", "is-active", f"{svc}.service"])
        if "active" in out:
            continue
        _run(["sudo", "-n", "systemctl", "restart", f"{svc}.service"], timeout=15)
        _, out2 = _run(["sudo", "-n", "systemctl", "is-active", f"{svc}.service"])
        if "active" in out2:
            fixes.append(f"{svc} was down — auto-restarted")
        else:
            issues.append(f"{svc} is DOWN (auto-restart failed — may need sudo)")
    return issues, fixes


def check_hae_containers() -> list[str]:
    issues: list[str] = []
    rc, out = _run(["sudo", "-n", "docker", "ps", "--format", "{{.Names}}\\t{{.Status}}"])
    if rc != 0:
        first = out.splitlines()[0] if out else "no output"
        issues.append(f"docker ps failed: {first}")
        return issues
    running = {line.split("\t")[0]: line for line in out.splitlines() if "\t" in line}
    for c in HAE_CONTAINERS:
        if c not in running:
            issues.append(f"HAE container `{c}` not running")
        elif "Up " not in running[c]:
            issues.append(f"HAE container `{c}` unhealthy: {running[c]}")
    return issues


def _load_hae_config() -> dict:
    path = Path.home() / ".openjarvis" / "connectors" / "apple_health_remote.json"
    return json.loads(path.read_text())


def _is_metric_stale(base_url: str, token: str, metric_id: str, freshness_hours: int) -> tuple[bool, str | None]:
    """Returns (stale, error_message_or_None). Uses HAE start/end query params."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=max(8, freshness_hours // 24 + 1))).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        r = httpx.get(
            f"{base_url.rstrip('/')}/api/metrics/{metric_id}",
            headers={"api-key": token},
            params={"start": start, "end": end},
            timeout=15.0,
        )
        r.raise_for_status()
        rows = r.json() or []
    except Exception as exc:
        return True, f"HAE fetch failed for {metric_id}: {exc}"

    if not rows:
        return True, None
    most_recent = max(rows, key=lambda row: row.get("date", ""))
    most_recent_dt_str = most_recent.get("date", "")
    if not most_recent_dt_str:
        return True, None
    try:
        # Tolerate both Z-suffixed and offset-suffixed timestamps
        dt = datetime.fromisoformat(most_recent_dt_str.replace("Z", "+00:00"))
    except ValueError:
        return True, None
    age_hours = (now - dt.astimezone(timezone.utc)).total_seconds() / 3600
    return (age_hours > freshness_hours), None


def check_hae_freshness() -> list[str]:
    """Per-metric staleness check. Only alerts on full-pipeline failure.

    Partial staleness (some iPhone-only or watch metrics missing) is suppressed —
    the alert only fires when every metric in the registry is stale, i.e. the
    HAE pipeline as a whole has stopped delivering.
    """
    issues: list[str] = []
    try:
        cfg = _load_hae_config()
    except Exception as exc:
        return [f"HAE connector config unreadable: {exc}"]

    base_url = cfg["base_url"]
    token = cfg["read_token"]

    stale_metrics: list[str] = []
    fetch_errors: list[str] = []
    for entry in METRIC_REGISTRY:
        stale, err = _is_metric_stale(base_url, token, entry.id, entry.freshness_hours)
        if err:
            fetch_errors.append(err)
            continue
        if stale:
            stale_metrics.append(entry.id)

    issues.extend(fetch_errors)

    if stale_metrics and len(stale_metrics) == len(METRIC_REGISTRY):
        issues.append("Stale metrics: " + ", ".join(stale_metrics))

    return issues


def check_disk() -> list[str]:
    usage = shutil.disk_usage("/")
    pct = int(usage.used / usage.total * 100)
    if pct >= DISK_WARN_PCT:
        gb_free = usage.free / (1024 ** 3)
        return [f"Disk at {pct}% ({gb_free:.1f} GB free) — above {DISK_WARN_PCT}% threshold"]
    return []


def main() -> int:
    issues: list[str] = []
    fixes: list[str] = []

    i, f = check_user_services()
    issues += i
    fixes += f

    i, f = check_system_services()
    issues += i
    fixes += f

    issues += check_hae_containers()
    issues += check_hae_freshness()
    issues += check_disk()

    if not issues:
        return 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M %Z")
    parts = [f"*Pipeline issues — {now}*", ""]
    for i in issues:
        parts.append(f"• {i}")
    telegram_notify.send("\n".join(parts))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
