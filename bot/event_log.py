"""Append-only JSONL logs plus flattened CSV reports (decision D-012)."""

from __future__ import annotations

import csv
import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from bot.models import DiagnosticResult


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


class JsonlEventLog:
    """A small thread-safe append-only event store."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: Any) -> None:
        line = json.dumps(to_json_safe(event), sort_keys=True, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()


class DiagnosticLogger:
    def __init__(self, log_dir: Path):
        self.events = JsonlEventLog(log_dir / "decision_audit.jsonl")
        self.errors = log_dir / "errors.log"
        self.runtime = log_dir / "runtime.log"
        log_dir.mkdir(parents=True, exist_ok=True)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: to_json_safe(value) for key, value in row.items()})
