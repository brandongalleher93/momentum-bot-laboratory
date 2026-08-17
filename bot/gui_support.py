"""Pure helpers used by the Streamlit command center."""

from __future__ import annotations

import json
import re
from dataclasses import fields, replace
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from bot.config import Settings, validate_settings
from bot.event_log import to_json_safe


SAFE_PROFILE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
PROTECTED_FIELDS = {
    "alpaca_api_key",
    "alpaca_secret_key",
    "alpaca_paper",
    "allow_live_trading",
    "log_dir",
    "output_dir",
}


def profile_filename(name: str) -> str:
    cleaned = SAFE_PROFILE_NAME.sub("", name).strip().replace(" ", "_")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("Profile name must contain letters or numbers.")
    return cleaned[:80] + ".json"


def save_profile(directory: Path, name: str, settings: Settings) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / profile_filename(name)
    payload = {"profile_name": name.strip(), "config": settings.snapshot()}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def list_profiles(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json"), key=lambda value: value.stat().st_mtime, reverse=True) if directory.exists() else []


def load_profile(path: Path, base: Settings) -> Settings:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return settings_from_mapping(base, payload.get("config", payload))


def settings_from_mapping(base: Settings, values: Mapping[str, Any]) -> Settings:
    updates: dict[str, Any] = {}
    for field in fields(base):
        name = field.name
        if name in PROTECTED_FIELDS or name not in values:
            continue
        current, raw = getattr(base, name), values[name]
        if isinstance(current, Decimal):
            updates[name] = Decimal(str(raw))
        elif isinstance(current, time):
            updates[name] = time.fromisoformat(str(raw))
        elif isinstance(current, bool):
            updates[name] = raw if isinstance(raw, bool) else str(raw).lower() in {"1", "true", "yes", "on"}
        elif isinstance(current, int):
            updates[name] = int(raw)
        elif current is None:
            updates[name] = _coerce_optional(name, raw)
        else:
            updates[name] = str(raw)
    candidate = replace(base, **updates)
    validate_settings(candidate)
    return candidate


def _coerce_optional(name: str, raw: Any) -> Any:
    if raw is None or str(raw).strip().lower() in {"", "none", "null"}:
        return None
    if name == "daily_equity_drawdown_limit":
        return Decimal(str(raw))
    return int(raw)


def read_jsonl(path: Path, limit: int = 1000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    value["_line"] = line_number
                    rows.append(value)
            except json.JSONDecodeError:
                rows.append({"_line": line_number, "severity": "error", "reason": line.rstrip(), "malformed": True})
    return rows[-limit:]


def recent_decisions(log_dir: Path, limit: int = 500) -> list[dict[str, Any]]:
    rows = read_jsonl(log_dir / "decision_audit.jsonl", limit=limit)
    return sorted(rows, key=lambda row: str(row.get("timestamp", "")), reverse=True)


def config_diff(defaults: Settings, current: Settings) -> list[dict[str, Any]]:
    rows = []
    for field in fields(defaults):
        name = field.name
        if name in {"alpaca_api_key", "alpaca_secret_key"}:
            continue
        old, new = getattr(defaults, name), getattr(current, name)
        if old != new:
            rows.append({"parameter": name, "default": to_json_safe(old), "current": to_json_safe(new)})
    return rows


def active_shadow_protection_rows(settings: Settings) -> list[dict[str, str]]:
    """Describe the settings enforced by the real-time shadow engine."""

    loss_limit = settings.max_consecutive_losses_per_symbol_day
    cooldown = settings.cooldown_after_loss_minutes
    broker_submission = (
        "Enabled — shadow mode blocked"
        if settings.paper_order_submission_enabled
        else "Disabled — no broker orders"
    )
    return [
        {
            "Protection": "Maximum risk per trade",
            "Active shadow setting": _money(settings.max_risk_per_trade),
        },
        {
            "Protection": "Maximum position value",
            "Active shadow setting": _money(settings.max_position_value),
        },
        {
            "Protection": "Daily loss/risk budget",
            "Active shadow setting": _money(settings.max_daily_loss),
        },
        {
            "Protection": "Maximum open positions",
            "Active shadow setting": str(settings.max_open_positions),
        },
        {
            "Protection": "Per-symbol daily loss stop",
            "Active shadow setting": (
                f"Enabled — after {loss_limit} consecutive losses"
                if loss_limit is not None
                else "Disabled"
            ),
        },
        {
            "Protection": "Re-entry cooldown after a loss",
            "Active shadow setting": (
                f"Enabled — {cooldown} minutes"
                if cooldown is not None
                else "Disabled"
            ),
        },
        {
            "Protection": "Three-trade cap per symbol/day",
            "Active shadow setting": "Disabled — replay only",
        },
        {
            "Protection": "Profit target",
            "Active shadow setting": f"{settings.target_r_multiple}R",
        },
        {
            "Protection": "Trading window",
            "Active shadow setting": (
                f"{_clock_time(settings.trade_window_start)}–"
                f"{_clock_time(settings.trade_window_end)} "
                f"{_timezone_label(settings.timezone)}"
            ),
        },
        {
            "Protection": "Scanner source",
            "Active shadow setting": (
                "Delayed SIP full universe before 9:30 AM ET; "
                "SIP market movers afterward"
            ),
        },
        {
            "Protection": "Execution data",
            "Active shadow setting": (
                f"Current {settings.alpaca_data_feed.upper()}"
            ),
        },
        {
            "Protection": "Broker order submission",
            "Active shadow setting": broker_submission,
        },
    ]


def execution_audit_table_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten the persisted SIP audit into a readable dashboard table."""

    rows = report.get("rows", [])
    if not isinstance(rows, list):
        return []
    values: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        reconstruction = row.get("reconstruction")
        if not isinstance(reconstruction, Mapping):
            reconstruction = {}
        values.append(
            {
                "symbol": row.get("symbol"),
                "entry time": row.get("entry_time"),
                "status": row.get("status"),
                "raw P/L": _float_or_none(row.get("raw_pnl")),
                "SIP entry ask": _float_or_none(row.get("sip_entry_ask")),
                "SIP exit bid": _float_or_none(row.get("sip_exit_bid")),
                "estimated SIP-path P/L": _float_or_none(
                    reconstruction.get("realized_pnl")
                ),
                "estimated outcome": reconstruction.get("reason") or "—",
                "explanation": row.get("explanation") or "—",
            }
        )
    status_order = {"discrepant": 0, "unresolved": 1, "confirmed": 2}
    return sorted(
        values,
        key=lambda row: (
            status_order.get(str(row.get("status")), 3),
            str(row.get("entry time", "")),
        ),
        reverse=False,
    )


def _money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _clock_time(value: time) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _timezone_label(value: str) -> str:
    if value == "America/New_York":
        return "ET"
    return value


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def trade_table_rows(trades: Iterable[Any]) -> list[dict[str, Any]]:
    return [to_json_safe(trade) for trade in trades]
