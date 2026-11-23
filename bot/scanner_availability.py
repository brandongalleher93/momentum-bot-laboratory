"""Resolve captured scanner eligibility from retained approval diagnostics."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

ApprovalKey = tuple[str, datetime, str]
ApprovalIndex = dict[ApprovalKey, set[datetime]]


def aware_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Scanner evidence timestamps must be timezone-aware.")
    return parsed


def approval_key(symbol: str, market_time: str, actual: Mapping[str, Any]) -> ApprovalKey:
    return symbol, aware_time(market_time), json.dumps(actual, sort_keys=True)


def load_scanner_approvals(paths: Sequence[Path]) -> ApprovalIndex:
    """Keep repeated approvals; never infer availability from market time."""
    index: ApprovalIndex = {}
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("Expected a diagnostic object.")
                    if event.get("module") != "scanner":
                        continue
                    if not isinstance(event.get("passed"), bool):
                        raise ValueError("Scanner approval status must be boolean.")
                    if not event["passed"]:
                        continue
                    symbol = event["symbol"]
                    actual = event["actual_values"]
                    if not isinstance(symbol, str) or not symbol or not isinstance(actual, dict):
                        raise ValueError("Invalid scanner identity or values.")
                    market = aware_time(event["data_cutoff_time"])
                    decision = aware_time(event["decision_time"])
                    available = aware_time(event["timestamp"])
                    if not market <= decision <= available:
                        raise ValueError("Scanner approval chronology is invalid.")
                    key = approval_key(symbol, event["data_cutoff_time"], actual)
                    index.setdefault(key, set()).add(available)
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        f"Invalid scanner evidence at line {line_number}: {error}"
                    ) from error
    return index


def candidate_approval_times(row: Mapping[str, Any], index: ApprovalIndex) -> set[datetime]:
    key = approval_key(row["symbol"], row["timestamp"], json.loads(row["actual_values_json"]))
    times = index.get(key)
    if not times:
        raise ValueError("Captured scanner row has no matching approval-availability evidence.")
    market = key[1]
    if any(value.utcoffset() is None or value < market for value in times):
        raise ValueError("Scanner availability must be timezone-aware and not precede market time.")
    return times
