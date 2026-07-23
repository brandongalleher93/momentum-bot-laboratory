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


def trade_table_rows(trades: Iterable[Any]) -> list[dict[str, Any]]:
    return [to_json_safe(trade) for trade in trades]
