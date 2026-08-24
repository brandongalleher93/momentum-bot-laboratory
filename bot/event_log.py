"""Append-only JSONL logs plus flattened CSV reports (decision D-012)."""

from __future__ import annotations

import csv
import json
import re
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from bot.models import DiagnosticResult
from bot.security import ensure_private_directory, ensure_private_file


SENSITIVE_FIELD_NAMES = {
    "account_id",
    "alpaca_api_key",
    "alpaca_secret_key",
    "api_key",
    "api_secret",
    "buying_power",
    "cash",
    "equity",
    "password",
    "secret",
    "secret_key",
    "token",
}
ALPACA_CREDENTIAL_PATTERN = re.compile(r"\b(?:AK|PK)[A-Z0-9]{18,}\b")
LOCAL_USER_PATH_PATTERN = re.compile(r"/(?:Users|home)/[^/\s]+/")


def to_json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return to_json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def redact_sensitive_fields(value: Any) -> Any:
    """Recursively redact credentials and sensitive account values."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).lower() in SENSITIVE_FIELD_NAMES
                else redact_sensitive_fields(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_sensitive_fields(item) for item in value]
    if isinstance(value, str):
        value = ALPACA_CREDENTIAL_PATTERN.sub("[REDACTED CREDENTIAL]", value)
        return LOCAL_USER_PATH_PATTERN.sub("/[USER]/", value)
    return value


class JsonlEventLog:
    """A small thread-safe append-only event store."""

    def __init__(self, path: Path):
        self.path = path
        ensure_private_file(self.path)
        self._lock = threading.Lock()

    def append(self, event: Any) -> None:
        safe_event = redact_sensitive_fields(to_json_safe(event))
        line = json.dumps(safe_event, sort_keys=True, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()


class DiagnosticLogger:
    def __init__(self, log_dir: Path):
        self.events = JsonlEventLog(log_dir / "decision_audit.jsonl")
        self.errors = log_dir / "errors.log"
        self.runtime = log_dir / "runtime.log"
        ensure_private_directory(log_dir)
        ensure_private_file(self.errors)
        ensure_private_file(self.runtime)
        self._lock = threading.Lock()

    def log(self, result: DiagnosticResult) -> None:
        self.events.append(result)
        summary = (
            f"{result.timestamp.isoformat()} {result.severity.value.upper()} "
            f"{result.module} {result.symbol or '-'} {result.reason}\n"
        )
        target = self.errors if result.severity.value == "critical" else self.runtime
        with self._lock, target.open("a", encoding="utf-8") as stream:
            stream.write(summary)


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fieldnames: list[str]) -> None:
    ensure_private_file(path)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: to_json_safe(value) for key, value in row.items()})
