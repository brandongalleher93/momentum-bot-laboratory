"""Read-only readiness evaluation for the fixed re-entry forward checkpoint."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.event_log import to_json_safe
from bot.security import ensure_private_file


DEFAULT_START_DATE = date(2026, 9, 21)
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_TARGET_TRADES = 50
DEFAULT_MINIMUM_LATER_OPPORTUNITIES = 15
MATURE_AUDIT_STATUSES = {"confirmed", "discrepant"}


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json_immutably(path: Path) -> tuple[dict, str]:
    before = _fingerprint(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    if before != _fingerprint(path):
        raise RuntimeError(f"Input changed while it was being read: {path}")
    return value, before


def _read_closed_trades(
    ledger_path: Path, *, boundary: datetime
) -> tuple[list[dict[str, str]], str]:
    wal_path = Path(f"{ledger_path}-wal")
    if wal_path.exists() and wal_path.stat().st_size:
        raise ValueError(
            "The shadow ledger has a non-empty WAL; retry when it is idle."
        )
    before = _fingerprint(ledger_path)
    uri = ledger_path.resolve().as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as database:
        columns = {
            row[1] for row in database.execute("PRAGMA table_info(shadow_trades)")
        }
        required = {
            "trade_id",
            "symbol",
            "entry_time",
            "exit_time",
            "status",
        }
        if not required.issubset(columns):
            raise ValueError("The shadow ledger has an incompatible schema.")
        rows = database.execute(
            "SELECT trade_id, symbol, entry_time, exit_time "
            "FROM shadow_trades "
            "WHERE status='closed' AND entry_time>=? "
            "ORDER BY entry_time, trade_id",
            (boundary.isoformat(),),
        ).fetchall()
    if wal_path.exists() and wal_path.stat().st_size:
        raise RuntimeError("The shadow ledger gained a non-empty WAL during the check.")
    if before != _fingerprint(ledger_path):
        raise RuntimeError("The shadow ledger changed during the readiness check.")
    trades = [
        {
            "trade_id": trade_id,
            "symbol": symbol,
            "entry_time": entry_time,
            "exit_time": exit_time,
        }
        for trade_id, symbol, entry_time, exit_time in rows
    ]
    for trade in trades:
        entry_time = datetime.fromisoformat(trade["entry_time"])
        if entry_time.tzinfo is None:
            raise ValueError("Ledger entry timestamps must include a timezone.")
        if not trade["exit_time"]:
            raise ValueError("A closed checkpoint trade is missing its exit time.")
    return trades, before


def _cohort_fingerprint(trades: list[dict[str, str]]) -> str:
    payload = json.dumps(trades, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _audit_evidence(
    audit_path: Path | None, cohort: list[dict[str, str]]
) -> dict:
    if audit_path is None:
        return {
            "provided": False,
            "sha256": None,
            "generated_at": None,
            "mature_trades": 0,
            "missing_trade_ids": [trade["trade_id"] for trade in cohort],
            "unresolved_trade_ids": [],
            "complete": False,
        }
    audit, fingerprint = _read_json_immutably(audit_path)
    rows = audit.get("rows")
    if not isinstance(rows, list) or len(rows) != audit.get("ledger_trade_count"):
        raise ValueError(
            "The execution audit does not cover its recorded ledger count."
        )
    identifiers = [row.get("trade_id") for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("The execution audit contains duplicate trade IDs.")
    by_id = {row["trade_id"]: row for row in rows}
    missing = [trade["trade_id"] for trade in cohort if trade["trade_id"] not in by_id]
    unresolved = [
        trade["trade_id"]
        for trade in cohort
        if trade["trade_id"] in by_id
        and by_id[trade["trade_id"]].get("status") not in MATURE_AUDIT_STATUSES
    ]
    mature = len(cohort) - len(missing) - len(unresolved)
    return {
        "provided": True,
        "sha256": fingerprint,
        "generated_at": audit.get("generated_at"),
        "mature_trades": mature,
        "missing_trade_ids": missing,
        "unresolved_trade_ids": unresolved,
        "complete": bool(cohort) and mature == len(cohort),
    }


def _replay_evidence(
    replay_report_path: Path | None,
    *,
    start_date: date,
    checkpoint_end_date: date | None,
    checkpoint_cutoff: datetime | None,
    minimum_later_opportunities: int,
) -> dict:
    if replay_report_path is None:
        return {
            "provided": False,
            "sha256": None,
            "valid_window": False,
            "complete_coverage": False,
            "later_same_symbol_opportunities": None,
            "minimum_later_same_symbol_opportunities": minimum_later_opportunities,
            "minimum_met": False,
            "issues": ["Forward paired replay report is missing."],
        }
    replay, fingerprint = _read_json_immutably(replay_report_path)
    issues: list[str] = []
    window_issues: list[str] = []
    if replay.get("experiment") != "one_entry_per_symbol_day":
        window_issues.append("Replay experiment does not match the declared candidate.")
    if replay.get("role") != "forward_checkpoint":
        window_issues.append("Replay role is not forward_checkpoint.")
    if replay.get("evaluation_start") != start_date.isoformat():
        window_issues.append("Replay start does not match the fixed checkpoint start.")
    expected_end = checkpoint_end_date.isoformat() if checkpoint_end_date else None
    if expected_end is None or replay.get("evaluation_end") != expected_end:
        window_issues.append("Replay end does not match the frozen checkpoint window.")
    replay_cutoff = replay.get("checkpoint_cutoff")
    try:
        parsed_cutoff = datetime.fromisoformat(replay_cutoff)
    except (TypeError, ValueError):
        parsed_cutoff = None
    if parsed_cutoff is not None and parsed_cutoff.tzinfo is None:
        parsed_cutoff = None
    if (
        checkpoint_cutoff is None
        or parsed_cutoff is None
        or parsed_cutoff.astimezone(timezone.utc) != checkpoint_cutoff
    ):
        window_issues.append("Replay cutoff does not match the fiftieth trade exit.")
    issues.extend(window_issues)
    coverage = replay.get("coverage")
    expected_coverage = {"minute_bars", "ten_second_bars", "trade_prints"}
    complete_coverage = (
        isinstance(coverage, dict)
        and expected_coverage.issubset(coverage)
        and all(
            isinstance(coverage[name], list) and not coverage[name]
            for name in expected_coverage
        )
    )
    if not complete_coverage:
        issues.append("Replay data coverage is incomplete or missing.")
    candidate = replay.get("one_entry_per_symbol_day")
    later = (
        candidate.get("later_same_symbol_entries_filtered")
        if isinstance(candidate, dict)
        else None
    )
    if not isinstance(later, int) or later < 0:
        issues.append("Replay later same-symbol opportunity count is invalid.")
        later = None
    return {
        "provided": True,
        "sha256": fingerprint,
        "valid_window": not window_issues,
        "complete_coverage": complete_coverage,
        "later_same_symbol_opportunities": later,
        "minimum_later_same_symbol_opportunities": minimum_later_opportunities,
        "minimum_met": later is not None and later >= minimum_later_opportunities,
        "issues": issues,
    }


def build_forward_checkpoint_readiness(
    ledger_path: Path,
    *,
    audit_path: Path | None = None,
    replay_report_path: Path | None = None,
    start_date: date = DEFAULT_START_DATE,
    timezone_name: str = DEFAULT_TIMEZONE,
    target_trades: int = DEFAULT_TARGET_TRADES,
    minimum_later_opportunities: int = DEFAULT_MINIMUM_LATER_OPPORTUNITIES,
) -> dict:
    """Build a private-safe report without modifying any evidence source."""

    if target_trades <= 0 or minimum_later_opportunities <= 0:
        raise ValueError("Checkpoint thresholds must be positive.")
    zone = ZoneInfo(timezone_name)
    boundary = datetime.combine(start_date, time.min, tzinfo=zone).astimezone(
        timezone.utc
    )
    closed_trades, ledger_sha256 = _read_closed_trades(
        ledger_path, boundary=boundary
    )
    cohort = closed_trades[:target_trades]
    checkpoint_reached = len(cohort) == target_trades
    checkpoint_end_date = None
    checkpoint_cutoff = None
    if checkpoint_reached:
        checkpoint_cutoff = datetime.fromisoformat(cohort[-1]["exit_time"]).astimezone(
            timezone.utc
        )
        checkpoint_end_date = checkpoint_cutoff.astimezone(zone).date()
    audit = _audit_evidence(audit_path, cohort)
    replay = _replay_evidence(
        replay_report_path,
        start_date=start_date,
        checkpoint_end_date=checkpoint_end_date,
        checkpoint_cutoff=checkpoint_cutoff,
        minimum_later_opportunities=minimum_later_opportunities,
    )
    gates = {
        "first_50_closed_trades_frozen": checkpoint_reached,
        "mature_sip_classifications": (
            checkpoint_reached and audit["mature_trades"] == target_trades
        ),
        "exact_forward_replay_window": (
            checkpoint_reached and replay["valid_window"]
        ),
        "complete_replay_coverage": (
            checkpoint_reached and replay["complete_coverage"]
        ),
        "minimum_later_same_symbol_opportunities": (
            checkpoint_reached and replay["minimum_met"]
        ),
    }
    if not checkpoint_reached:
        status = "collecting"
    elif not all(
        gates[name]
        for name in (
            "mature_sip_classifications",
            "exact_forward_replay_window",
            "complete_replay_coverage",
        )
    ):
        status = "checkpoint_evidence_pending"
    elif not gates["minimum_later_same_symbol_opportunities"]:
        status = "inconclusive_insufficient_opportunities"
    else:
        status = "ready_for_evaluation"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment": "one_entry_per_symbol_day",
        "role": "forward_checkpoint_readiness",
        "status": status,
        "decision": None,
        "checkpoint": {
            "timezone": timezone_name,
            "start_date": start_date.isoformat(),
            "boundary_utc": boundary.isoformat(),
            "target_closed_trades": target_trades,
            "observed_closed_trades": len(closed_trades),
            "cohort_closed_trades": len(cohort),
            "remaining_closed_trades": max(target_trades - len(cohort), 0),
            "ignored_later_closed_trades": max(len(closed_trades) - target_trades, 0),
            "checkpoint_end_date": (
                checkpoint_end_date.isoformat() if checkpoint_end_date else None
            ),
            "checkpoint_cutoff": (
                checkpoint_cutoff.isoformat() if checkpoint_cutoff else None
            ),
            "cohort_sha256": _cohort_fingerprint(cohort),
        },
        "sources": {
            "ledger_sha256": ledger_sha256,
            "audit": audit,
            "replay": replay,
        },
        "gates": gates,
        "interpretation": (
            "This report evaluates evidence readiness only. It does not authorize "
            "a strategy, parameter, risk, schedule, or order-submission change."
        ),
    }


def save_forward_checkpoint_readiness(report: dict, path: Path) -> None:
    ensure_private_file(path)
    path.write_text(json.dumps(to_json_safe(report), indent=2) + "\n", encoding="utf-8")
