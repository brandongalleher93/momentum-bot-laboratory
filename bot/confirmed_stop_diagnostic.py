"""Read-only diagnostic for confirmed and discrepant protective-stop exits."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from bot.event_log import to_json_safe
from bot.security import ensure_private_file


PROTECTIVE_STOP_REASON = "Shadow protective stop reached."
HYPOTHESIS_ID = "CS-001"
HYPOTHESIS_VERSION = "2026-08-26"
VALIDATION_COHORT_START = datetime.fromisoformat("2026-08-27T00:00:00-04:00")


@dataclass(frozen=True)
class StopTrade:
    trade_id: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    raw_pnl: Decimal
    raw_r_multiple: Decimal


@dataclass(frozen=True)
class LedgerEntry:
    trade_id: str
    symbol: str
    entry_time: datetime


def build_confirmed_stop_diagnostic(
    ledger_path: Path,
    audit_path: Path,
    events_path: Path,
    *,
    timezone_name: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a diagnostic without modifying any source artifact."""

    sources = _source_paths(ledger_path, audit_path, events_path)
    before = _fingerprints(sources)

    ledger_entries, trades = _read_ledger(ledger_path)
    audit_rows, audited_trade_count = _read_audit_rows(audit_path)
    if (
        audited_trade_count is not None
        and audited_trade_count != len(ledger_entries)
    ):
        raise ValueError(
            "Execution audit and ledger closed-trade counts do not match: "
            f"{audited_trade_count} audited versus {len(ledger_entries)} in "
            "the ledger."
        )
    events = _read_events(events_path)
    rows = _build_rows(
        trades,
        audit_rows,
        events,
        timezone_name,
        ledger_entries=ledger_entries,
    )

    after_sources = _source_paths(ledger_path, audit_path, events_path)
    after = _fingerprints(after_sources)
    if sources != after_sources or before != after:
        raise RuntimeError(
            "A diagnostic source changed while it was being read; rerun after "
            "the shadow session and audit are idle."
        )

    created = generated_at or datetime.now(timezone.utc)
    if created.tzinfo is None:
        raise ValueError("Diagnostic generation time must be timezone-aware.")

    counts = Counter(row["audit_status"] for row in rows)
    cohorts = {
        status: _summarize_cohort(
            [row for row in rows if row["audit_status"] == status]
        )
        for status in ("confirmed", "discrepant", "unresolved")
    }
    research_cohorts = {
        name: _summarize_research_cohort(
            [row for row in rows if row["research_cohort"] == name]
        )
        for name in ("exploratory", "future_validation")
    }
    return {
        "generated_at": created,
        "diagnostic": "confirmed_stop",
        "diagnostic_version": 1,
        "evidence_boundary": {
            "source_files_modified": False,
            "ledger_rows_selected": len(trades),
            "audit_rows_matched": len(rows),
            "notes": [
                "Only recorded protective-stop exits are included.",
                "Confirmed rows retain raw ledger outcomes.",
                (
                    "Discrepant adjusted outcomes use the existing SIP "
                    "price-path reconstruction and remain estimates."
                ),
                (
                    "Missing event telemetry is reported explicitly and is "
                    "not converted to a negative observation."
                ),
                "This exploratory report does not authorize parameter changes.",
            ],
        },
        "source_fingerprints": before,
        "classification_counts": {
            "confirmed": counts["confirmed"],
            "discrepant": counts["discrepant"],
            "unresolved": counts["unresolved"],
        },
        "cohorts": cohorts,
        "exploratory_primary_comparison_percentage_points": (
            research_cohorts["exploratory"][
                "primary_comparison_percentage_points"
            ]
        ),
        "research_cohorts": research_cohorts,
        "future_validation_assessment": _validation_assessment(
            research_cohorts["future_validation"]
        ),
        "research_plan": _research_plan(),
        "rows": rows,
    }


def save_confirmed_stop_diagnostic(
    report: Mapping[str, Any],
    path: Path,
    *,
    source_paths: Sequence[Path],
) -> Path:
    """Write the derived report separately with private file permissions."""

    if path.resolve() in {source.resolve() for source in source_paths}:
        raise ValueError(
            "The diagnostic report path must be separate from every source."
        )
    ensure_private_file(path)
    path.write_text(
        json.dumps(to_json_safe(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _source_paths(
    ledger_path: Path, audit_path: Path, events_path: Path
) -> dict[str, tuple[Path, ...]]:
    paths: dict[str, tuple[Path, ...]] = {
        "ledger": (ledger_path,),
        "execution_audit": (audit_path,),
        "events": (events_path,),
    }
    wal_path = Path(str(ledger_path) + "-wal")
    if wal_path.exists():
        paths["ledger"] = (ledger_path, wal_path)
    return paths


def _fingerprints(
    sources: Mapping[str, Sequence[Path]],
) -> dict[str, list[dict[str, Any]]]:
    fingerprints: dict[str, list[dict[str, Any]]] = {}
    for role, paths in sources.items():
        values = []
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(f"Missing {role} source: {path}")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            values.append(
                {
                    "name": path.name,
                    "bytes": size,
                    "sha256": digest.hexdigest(),
                }
            )
        fingerprints[role] = values
    return fingerprints


def _read_ledger(path: Path) -> tuple[list[LedgerEntry], list[StopTrade]]:
    wal_path = Path(str(path) + "-wal")
    if wal_path.exists() and wal_path.stat().st_size:
        raise RuntimeError(
            "The shadow ledger has a non-empty WAL sidecar. Rerun after the "
            "shadow writer is stopped and SQLite has checkpointed the ledger."
        )
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            rows = connection.execute(
                """
                SELECT trade_id, symbol, entry_time, exit_time, exit_reason,
                       realized_pnl, r_multiple
                FROM shadow_trades
                WHERE status = 'closed'
                ORDER BY entry_time, trade_id
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Unable to read the shadow ledger: {exc}") from exc

    entries = []
    trades = []
    for row in rows:
        entry_time = _aware_datetime(row["entry_time"], "ledger entry_time")
        entries.append(
            LedgerEntry(
                trade_id=str(row["trade_id"]),
                symbol=str(row["symbol"]),
                entry_time=entry_time,
            )
        )
        if row["exit_reason"] != PROTECTIVE_STOP_REASON:
            continue
        exit_time = _aware_datetime(row["exit_time"], "ledger exit_time")
        trades.append(
            StopTrade(
                trade_id=str(row["trade_id"]),
                symbol=str(row["symbol"]),
                entry_time=entry_time,
                exit_time=exit_time,
                raw_pnl=_decimal(row["realized_pnl"], "ledger realized_pnl"),
                raw_r_multiple=_decimal(
                    row["r_multiple"], "ledger r_multiple"
                ),
            )
        )
    return entries, trades


def _read_audit_rows(
    path: Path,
) -> tuple[dict[str, Mapping[str, Any]], int | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Execution audit is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("rows"), list):
        raise ValueError("Execution audit must contain a rows list.")

    rows: dict[str, Mapping[str, Any]] = {}
    for value in payload["rows"]:
        if not isinstance(value, Mapping) or not value.get("trade_id"):
            raise ValueError("Every execution-audit row must have a trade_id.")
        trade_id = str(value["trade_id"])
        if trade_id in rows:
            raise ValueError(f"Duplicate execution-audit trade_id: {trade_id}")
        status = value.get("status")
        if status not in {"confirmed", "discrepant", "unresolved"}:
            raise ValueError(
                f"Execution-audit row {trade_id} has invalid status: {status}"
            )
        rows[trade_id] = value
    ledger_trade_count = payload.get("ledger_trade_count")
    if ledger_trade_count is not None:
        try:
            ledger_trade_count = int(ledger_trade_count)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Execution-audit ledger_trade_count must be an integer."
            ) from exc
    return rows, ledger_trade_count


def _read_events(path: Path) -> list[Mapping[str, Any]]:
    events: list[Mapping[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Event log line {line_number} is not valid JSON."
                ) from exc
            if not isinstance(event, Mapping):
                raise ValueError(
                    f"Event log line {line_number} must contain an object."
                )
            events.append(event)
    return events


def _build_rows(
    trades: Sequence[StopTrade],
    audit_rows: Mapping[str, Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    timezone_name: str,
    *,
    ledger_entries: Sequence[LedgerEntry],
) -> list[dict[str, Any]]:
    zone = ZoneInfo(timezone_name)
    event_index = _event_index(events)
    repeated_entries = _repeated_entry_numbers(ledger_entries, zone)
    results = []
    for trade in trades:
        if trade.trade_id not in audit_rows:
            raise ValueError(
                "Execution audit is missing protective-stop trade "
                f"{trade.trade_id}."
            )
        audit = audit_rows[trade.trade_id]
        if audit.get("exit_reason") != PROTECTIVE_STOP_REASON:
            raise ValueError(
                f"Execution-audit exit reason does not match {trade.trade_id}."
            )
        telemetry = _telemetry(event_index.get(trade.trade_id, ()))
        status = str(audit["status"])
        adjusted_pnl, adjusted_r, adjusted_source = _adjusted_outcome(
            trade, audit
        )
        local_entry = trade.entry_time.astimezone(zone)
        results.append(
            {
                "trade_id": trade.trade_id,
                "symbol": trade.symbol,
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "entry_hour_local": local_entry.strftime("%H:00"),
                "session_date_local": local_entry.date(),
                "holding_seconds": Decimal(
                    str((trade.exit_time - trade.entry_time).total_seconds())
                ),
                "repeated_symbol_entry_number": repeated_entries[trade.trade_id],
                "research_cohort": (
                    "future_validation"
                    if trade.entry_time >= VALIDATION_COHORT_START
                    else "exploratory"
                ),
                "audit_status": status,
                "raw_pnl": trade.raw_pnl,
                "raw_r_multiple": trade.raw_r_multiple,
                "adjusted_pnl": adjusted_pnl,
                "adjusted_r_multiple": adjusted_r,
                "adjusted_outcome_source": adjusted_source,
                "sip_entry_price_difference": _optional_decimal(
                    audit.get("entry_price_difference")
                ),
                "sip_exit_price_difference": _optional_decimal(
                    audit.get("exit_price_difference")
                ),
                **telemetry,
            }
        )
    return results


def _event_index(
    events: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    index: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        trade_id = event.get("trade_id")
        if event.get("event") == "shadow_exit":
            trade = event.get("trade")
            if isinstance(trade, Mapping):
                trade_id = trade.get("trade_id")
        if trade_id:
            index[str(trade_id)].append(event)
    return index


def _repeated_entry_numbers(
    trades: Sequence[LedgerEntry], zone: ZoneInfo
) -> dict[str, int]:
    counts: Counter[tuple[Any, str]] = Counter()
    numbers = {}
    for trade in sorted(trades, key=lambda value: (value.entry_time, value.trade_id)):
        key = (trade.entry_time.astimezone(zone).date(), trade.symbol)
        counts[key] += 1
        numbers[trade.trade_id] = counts[key]
    return numbers


def _telemetry(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    quote_events = [
        event for event in events if event.get("event") == "shadow_position_quote"
    ]
    exit_executions = [
        event.get("execution")
        for event in events
        if event.get("event") == "shadow_exit"
        and isinstance(event.get("execution"), Mapping)
    ]
    telemetry_events = [*quote_events, *exit_executions]
    spreads = _decimal_values(telemetry_events, "spread_percent")
    risk_overruns = [
        *_decimal_values(quote_events, "risk_overrun_at_bid"),
        *_decimal_values(exit_executions, "risk_overrun"),
    ]
    quote_ages = _decimal_values(telemetry_events, "quote_age_seconds")
    anomalies = [
        bool(event["spread_anomaly"])
        for event in telemetry_events
        if "spread_anomaly" in event
    ]
    spread_warning = any(anomalies) if anomalies else None
    risk_overrun = (
        any(value > 0 for value in risk_overruns) if risk_overruns else None
    )
    return {
        "telemetry_available": bool(telemetry_events),
        "position_quote_count": len(quote_events),
        "exit_event_count": len(exit_executions),
        "spread_warning_observed": spread_warning,
        "maximum_spread_percent": max(spreads) if spreads else None,
        "risk_overrun_observed": risk_overrun,
        "primary_marker_observed": _composite_marker(
            spread_warning, risk_overrun
        ),
        "maximum_risk_overrun": max(risk_overruns) if risk_overruns else None,
        "maximum_quote_age_seconds": max(quote_ages) if quote_ages else None,
    }


def _adjusted_outcome(
    trade: StopTrade, audit: Mapping[str, Any]
) -> tuple[Decimal | None, Decimal | None, str]:
    status = audit["status"]
    if status == "confirmed":
        return trade.raw_pnl, trade.raw_r_multiple, "confirmed_raw_ledger"
    reconstruction = audit.get("reconstruction")
    if status == "discrepant" and isinstance(reconstruction, Mapping):
        return (
            _optional_decimal(reconstruction.get("realized_pnl")),
            _optional_decimal(reconstruction.get("r_multiple")),
            "estimated_sip_price_path",
        )
    return None, None, "unresolved"


def _summarize_cohort(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    holding = [row["holding_seconds"] for row in rows]
    adjusted_pnl = [
        row["adjusted_pnl"] for row in rows if row["adjusted_pnl"] is not None
    ]
    adjusted_r = [
        row["adjusted_r_multiple"]
        for row in rows
        if row["adjusted_r_multiple"] is not None
    ]
    known_spread = [
        row["spread_warning_observed"]
        for row in rows
        if row["spread_warning_observed"] is not None
    ]
    known_overrun = [
        row["risk_overrun_observed"]
        for row in rows
        if row["risk_overrun_observed"] is not None
    ]
    known_primary_markers = [
        row["primary_marker_observed"]
        for row in rows
        if row["primary_marker_observed"] is not None
    ]
    return {
        "count": len(rows),
        "raw_net_profit": sum(
            (row["raw_pnl"] for row in rows), Decimal("0")
        ),
        "adjusted_observation_count": len(adjusted_pnl),
        "adjusted_net_profit": sum(adjusted_pnl, Decimal("0")),
        "adjusted_average_r": (
            sum(adjusted_r, Decimal("0")) / len(adjusted_r)
            if adjusted_r
            else None
        ),
        "median_holding_seconds": median(holding) if holding else None,
        "closed_within_30_seconds": sum(value <= 30 for value in holding),
        "closed_within_60_seconds": sum(value <= 60 for value in holding),
        "repeated_symbol_entries": sum(
            row["repeated_symbol_entry_number"] > 1 for row in rows
        ),
        "spread_warning_known_count": len(known_spread),
        "spread_warning_count": sum(known_spread),
        "risk_overrun_known_count": len(known_overrun),
        "risk_overrun_count": sum(known_overrun),
        "primary_marker_known_count": len(known_primary_markers),
        "primary_marker_count": sum(known_primary_markers),
        "primary_marker_rate_percent": (
            Decimal("100")
            * Decimal(sum(known_primary_markers))
            / Decimal(len(known_primary_markers))
            if known_primary_markers
            else None
        ),
        "telemetry_missing_count": sum(
            not row["telemetry_available"] for row in rows
        ),
        "entry_hour_counts": dict(
            sorted(Counter(row["entry_hour_local"] for row in rows).items())
        ),
    }


def _composite_marker(
    spread_warning: bool | None, risk_overrun: bool | None
) -> bool | None:
    if spread_warning is True or risk_overrun is True:
        return True
    if spread_warning is False and risk_overrun is False:
        return False
    return None


def _primary_comparison(
    cohorts: Mapping[str, Mapping[str, Any]],
) -> Decimal | None:
    confirmed = cohorts["confirmed"]["primary_marker_rate_percent"]
    discrepant = cohorts["discrepant"]["primary_marker_rate_percent"]
    if confirmed is None or discrepant is None:
        return None
    return discrepant - confirmed


def _summarize_research_cohort(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    counts = Counter(row["audit_status"] for row in rows)
    cohorts = {
        status: _summarize_cohort(
            [row for row in rows if row["audit_status"] == status]
        )
        for status in ("confirmed", "discrepant", "unresolved")
    }
    return {
        "count": len(rows),
        "classification_counts": {
            "confirmed": counts["confirmed"],
            "discrepant": counts["discrepant"],
            "unresolved": counts["unresolved"],
        },
        "cohorts": cohorts,
        "primary_comparison_percentage_points": _primary_comparison(cohorts),
    }


def _validation_assessment(
    cohort: Mapping[str, Any],
) -> dict[str, Any]:
    counts = cohort["classification_counts"]
    confirmed = cohort["cohorts"]["confirmed"]
    discrepant = cohort["cohorts"]["discrepant"]
    comparison = cohort["primary_comparison_percentage_points"]
    reasons = []
    if cohort["count"] < 20:
        reasons.append("fewer than 20 future protective-stop exits")
    if counts["confirmed"] < 5:
        reasons.append("fewer than five future confirmed stops")
    if counts["discrepant"] < 5:
        reasons.append("fewer than five future discrepant stops")
    if confirmed["primary_marker_known_count"] != counts["confirmed"]:
        reasons.append("confirmed-stop primary-marker telemetry is incomplete")
    if discrepant["primary_marker_known_count"] != counts["discrepant"]:
        reasons.append("discrepant-stop primary-marker telemetry is incomplete")

    if reasons or comparison is None:
        status = "inconclusive"
        if comparison is None and not reasons:
            reasons.append("the primary comparison is unavailable")
    elif comparison >= 20:
        status = "success"
    elif comparison <= 0:
        status = "failure"
    else:
        status = "inconclusive"
        reasons.append(
            "the primary comparison is above zero but below 20 percentage points"
        )
    return {
        "status": status,
        "primary_comparison_percentage_points": comparison,
        "reasons": reasons,
        "parameter_change_authorized": False,
    }


def _research_plan() -> dict[str, Any]:
    return {
        "hypothesis_id": HYPOTHESIS_ID,
        "version": HYPOTHESIS_VERSION,
        "validation_cohort_start": VALIDATION_COHORT_START,
        "status": "predeclared_for_future_validation",
        "hypothesis": (
            "Protective-stop exits classified as discrepant by mature SIP "
            "quotes have a materially higher rate of exit-decision-time spread "
            "anomalies or positive bid-implied risk overrun than confirmed "
            "protective-stop exits."
        ),
        "primary_marker": (
            "spread_warning_observed is true or risk_overrun_observed is true"
        ),
        "primary_comparison": (
            "marker rate for discrepant stops minus marker rate for confirmed "
            "stops"
        ),
        "existing_sample_role": (
            "exploratory only; it may estimate the association but cannot "
            "validate the hypothesis"
        ),
        "future_validation_cohort": (
            "protective-stop entries at or after 2026-08-27T00:00:00-04:00, "
            "using the same frozen strategy and mature SIP classification"
        ),
        "minimum_evaluable_sample": {
            "total_future_stops": 20,
            "confirmed_future_stops": 5,
            "discrepant_future_stops": 5,
        },
        "success": (
            "The primary comparison is at least 20 percentage points in the "
            "future validation cohort."
        ),
        "failure": (
            "The primary comparison is zero or negative after the minimum "
            "evaluable sample is reached."
        ),
        "inconclusive": (
            "The minimum evaluable sample is not reached, telemetry is "
            "missing for either cohort, or the comparison is above zero but "
            "below 20 percentage points."
        ),
        "decision_boundary": (
            "A successful diagnostic supports reviewing the shadow fill model; "
            "it does not authorize a strategy, stop, risk, or trading-window "
            "change."
        ),
    }


def _decimal_values(
    values: Iterable[Mapping[str, Any]], key: str
) -> list[Decimal]:
    result = []
    for value in values:
        parsed = _optional_decimal(value.get(key))
        if parsed is not None:
            result.append(parsed)
    return result


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"Expected a decimal value, received {value!r}.") from exc


def _decimal(value: Any, field: str) -> Decimal:
    parsed = _optional_decimal(value)
    if parsed is None:
        raise ValueError(f"Missing required decimal field: {field}")
    return parsed


def _aware_datetime(value: Any, field: str) -> datetime:
    if value is None:
        raise ValueError(f"Missing required timestamp: {field}")
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp for {field}: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp for {field} must be timezone-aware.")
    return parsed
