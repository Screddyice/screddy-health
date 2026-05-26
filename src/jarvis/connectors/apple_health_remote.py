"""Apple Health (remote) connector — reads from a Health Auto Export server.

Config at ``~/.openjarvis/connectors/apple_health_remote.json``::

    {"base_url": "https://<tunnel>.trycloudflare.com", "read_token": "sk-read-..."}

The server is ``HealthyApps/health-auto-export-server`` running on NEB;
the iPhone app pushes data via ``POST /api/data`` and this connector pulls
via ``GET /api/metrics/:name`` + ``GET /api/workouts``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry

_DEFAULT_CONFIG_PATH = str(
    DEFAULT_CONFIG_DIR / "connectors" / "apple_health_remote.json"
)

_METRICS = (
    "step_count",
    "active_energy",
    "resting_heart_rate",
    "heart_rate_variability",
    "apple_exercise_time",
)


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating HAE's 'YYYY-MM-DD HH:MM:SS +ZZZZ' form."""
    try:
        return datetime.fromisoformat(value.replace(" ", "T", 1))
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _hae_get(
    base_url: str, token: str, path: str, params: Dict[str, str]
) -> Any:
    resp = httpx.get(
        f"{base_url.rstrip('/')}{path}",
        headers={"api-key": token},
        params=params,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


@ConnectorRegistry.register("apple_health_remote")
class AppleHealthRemoteConnector(BaseConnector):
    """Sync Apple Health data from a remote Health Auto Export server."""

    connector_id = "apple_health_remote"
    display_name = "Apple Health (remote)"
    auth_type = "token"

    def __init__(self, *, config_path: str = _DEFAULT_CONFIG_PATH) -> None:
        self._config_path = Path(config_path)
        self._status = SyncStatus()

    def _load_config(self) -> Dict[str, str]:
        return json.loads(self._config_path.read_text(encoding="utf-8"))

    def is_connected(self) -> bool:
        if not self._config_path.exists():
            return False
        try:
            cfg = self._load_config()
        except Exception:
            return False
        return bool(cfg.get("base_url") and cfg.get("read_token"))

    def disconnect(self) -> None:
        if self._config_path.exists():
            self._config_path.unlink()

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        self._status.state = "syncing"
        try:
            cfg = self._load_config()
            base_url = cfg["base_url"]
            token = cfg["read_token"]

            start = (since or datetime.now(timezone.utc) - timedelta(days=7)).strftime(
                "%Y-%m-%d"
            )
            # Use tomorrow as `to` — HAE interprets `to=YYYY-MM-DD` as 00:00 UTC
            # of that date, so `to=today` would exclude everything recorded today.
            end = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
            params = {"start": start, "end": end}

            # Scalar daily metrics
            for metric in _METRICS:
                yield from self._sync_metric(base_url, token, metric, params)

            # Sleep analysis
            yield from self._sync_sleep(base_url, token, params)

            # Workouts
            yield from self._sync_workouts(base_url, token, params)

            self._status.state = "idle"
            self._status.last_sync = datetime.now()
        except Exception as exc:
            self._status.state = "error"
            self._status.error = str(exc)
            raise

    def sync_status(self) -> SyncStatus:
        return self._status

    # Metrics that should be SUMMED across samples in a day (cumulative).
    _SUM_METRICS = frozenset(
        {"step_count", "active_energy", "apple_exercise_time"}
    )
    # Metrics that should be AVERAGED (instantaneous readings).
    _AVG_METRICS = frozenset(
        {"resting_heart_rate", "heart_rate_variability"}
    )

    def _sync_metric(
        self,
        base_url: str,
        token: str,
        metric: str,
        params: Dict[str, str],
    ) -> Iterator[Document]:
        rows = _hae_get(base_url, token, f"/api/metrics/{metric}", params)
        if not isinstance(rows, list) or not rows:
            return

        # Heart-rate-range rows carry {Min,Avg,Max} per sample — average per day
        if rows and "Avg" in rows[0]:
            by_day: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                date_str = row.get("date")
                if not date_str:
                    continue
                ts = _parse_iso(date_str)
                day = _day_key(ts)
                bucket = by_day.setdefault(
                    day, {"avg": [], "min": [], "max": [], "ts": ts, "units": row.get("units", "bpm")}
                )
                if (a := row.get("Avg")) is not None:
                    bucket["avg"].append(a)
                if (mn := row.get("Min")) is not None:
                    bucket["min"].append(mn)
                if (mx := row.get("Max")) is not None:
                    bucket["max"].append(mx)
            for day, b in sorted(by_day.items()):
                if not b["avg"]:
                    continue
                avg = sum(b["avg"]) / len(b["avg"])
                title = (
                    f"HR avg {avg:.0f} "
                    f"(min {min(b['min']):.0f}, max {max(b['max']):.0f}) bpm"
                )
                yield Document(
                    doc_id=f"apple_health_remote-{metric}-{day}",
                    source="apple_health_remote",
                    doc_type=metric,
                    content=json.dumps({"date": day, "avg": avg}),
                    title=title,
                    timestamp=b["ts"],
                    metadata={"data_type": metric, "day": day},
                )
            return

        # Scalar samples: aggregate to daily totals (or averages) for readability
        by_day_scalar: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            date_str = row.get("date")
            if not date_str:
                continue
            ts = _parse_iso(date_str) if isinstance(date_str, str) else datetime.now()
            day = _day_key(ts)
            qty = row.get("qty")
            if qty is None:
                continue
            bucket = by_day_scalar.setdefault(
                day, {"values": [], "units": row.get("units", ""), "ts": ts}
            )
            bucket["values"].append(float(qty))

        aggregate = "sum" if metric in self._SUM_METRICS else "avg"
        if metric in self._AVG_METRICS:
            aggregate = "avg"

        for day, b in sorted(by_day_scalar.items()):
            total = sum(b["values"]) if aggregate == "sum" else (
                sum(b["values"]) / len(b["values"])
            )
            label = metric.replace("_", " ")
            title = f"{label}: {total:,.0f} {b['units']}".strip()
            yield Document(
                doc_id=f"apple_health_remote-{metric}-{day}",
                source="apple_health_remote",
                doc_type=metric,
                content=json.dumps(
                    {"date": day, aggregate: total, "unit": b["units"]}
                ),
                title=title,
                timestamp=b["ts"],
                metadata={"data_type": metric, "day": day, "aggregate": aggregate},
            )

    def _sync_sleep(
        self, base_url: str, token: str, params: Dict[str, str]
    ) -> Iterator[Document]:
        rows = _hae_get(base_url, token, "/api/metrics/sleep_analysis", params)
        if not isinstance(rows, list):
            return
        for row in rows:
            date_str = row.get("date")
            if not date_str:
                continue
            ts = _parse_iso(date_str) if isinstance(date_str, str) else datetime.now()
            day = _day_key(ts)
            total_h = float(row.get("inBed") or 0)
            core = float(row.get("core") or 0)
            rem = float(row.get("rem") or 0)
            deep = float(row.get("deep") or 0)
            awake = float(row.get("awake") or 0)
            title = (
                f"Sleep: {total_h:.1f}h in bed "
                f"(core {core:.1f}h, REM {rem:.1f}h, deep {deep:.1f}h, awake {awake:.1f}h)"
            )
            yield Document(
                doc_id=f"apple_health_remote-sleep-{day}",
                source="apple_health_remote",
                doc_type="sleep_analysis",
                content=json.dumps(row, default=str),
                title=title,
                timestamp=ts,
                metadata={"data_type": "sleep_analysis", "day": day},
            )

    def _sync_workouts(
        self, base_url: str, token: str, params: Dict[str, str]
    ) -> Iterator[Document]:
        try:
            rows = _hae_get(base_url, token, "/api/workouts", params)
        except httpx.HTTPStatusError:
            return
        if not isinstance(rows, list):
            return
        for row in rows:
            start_str = row.get("start") or row.get("date")
            if not start_str:
                continue
            ts = (
                _parse_iso(start_str)
                if isinstance(start_str, str)
                else datetime.now()
            )
            name = row.get("name") or row.get("type") or "Workout"
            duration_min = row.get("duration") or 0
            distance = row.get("distance")
            parts: List[str] = [f"{name} — {duration_min:.0f}m"]
            if distance:
                parts.append(f"{distance:.2f} {row.get('distanceUnit', 'km')}")
            energy = row.get("activeEnergyBurned") or row.get("energy")
            if energy:
                parts.append(f"{energy:.0f} kcal")
            title = " · ".join(parts)
            workout_id = row.get("id") or row.get("_id") or _day_key(ts)
            yield Document(
                doc_id=f"apple_health_remote-workout-{workout_id}",
                source="apple_health_remote",
                doc_type="workout",
                content=json.dumps(row, default=str),
                title=title,
                timestamp=ts,
                metadata={"data_type": "workout"},
            )
