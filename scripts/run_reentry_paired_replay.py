"""Run baseline and one-entry replays from the completed research workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from bot.config import load_settings
from bot.event_log import to_json_safe
from bot.historical_data import BarCache
from bot.history_store import HistoryStore
from bot.portfolio_backtest import PortfolioReplayEngine, ReplayGuardrails
from bot.security import ensure_private_file
from bot.scanner_availability import load_scanner_approvals
from bot.shadow_paper import shadow_paper_settings


def validate_daily_coverage(
    required: dict[str, set[date]],
    available: dict[str, dict[str, set[date]]],
) -> None:
    """Reject missing symbol/dates; do not equate presence with intraday fidelity."""
    if not required or any(not dates for dates in required.values()):
        raise ValueError("Replay requires nonempty symbol/date expectations.")
    for kind in ("minute_bars", "ten_second_bars", "trade_prints"):
        for symbol, dates in required.items():
            missing = dates - available.get(kind, {}).get(symbol, set())
            if missing:
                raise ValueError(
                    f"Missing {kind} symbol/date coverage for {symbol}: {sorted(missing)}"
                )


def fingerprint_files(paths: list[Path]) -> dict[str, str]:
    result = {}
    for path in paths:
        with path.open("rb") as stream:
            result[str(path)] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--role",
        choices=("retrospective_exploratory", "forward_checkpoint", "corrected_exploratory"),
        default="retrospective_exploratory",
    )
    parser.add_argument(
        "--checkpoint-cutoff",
        type=datetime.fromisoformat,
        help="Timezone-aware end cutoff; required for forward checkpoints.",
    )
    parser.add_argument("--scanner-diagnostics", type=Path, nargs="+", required=True,
                        help="Retained scanner decision JSONL files, including relevant archives.")
    args = parser.parse_args()
    approval_hashes = fingerprint_files(args.scanner_diagnostics)
    scanner_approvals = load_scanner_approvals(args.scanner_diagnostics)

    manifest_bytes = args.workspace.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not manifest.get("complete") or manifest.get("failures"):
        raise ValueError("Replay workspace is incomplete.")
    input_paths = sorted({
        Path(value)
        for key in ("paths", "execution_paths", "trade_paths")
        for value in manifest[key]
    })
    input_hashes = fingerprint_files(input_paths)
    root = Path(__file__).resolve().parent.parent
    source_paths = [root / name for name in (
        "bot/backtest.py", "bot/portfolio_backtest.py", "bot/config.py",
        "bot/strategy.py", "bot/risk_manager.py", "bot/historical_data.py",
        "bot/scanner_availability.py", "bot/history_store.py",
        "scripts/run_reentry_paired_replay.py",
    )]
    source_hashes = fingerprint_files(source_paths)
    settings = shadow_paper_settings(load_settings())
    zone = ZoneInfo(settings.timezone)
    start_date = date.fromisoformat(manifest["evaluation_start"])
    end_date = date.fromisoformat(manifest["evaluation_end"])
    evaluation_start = datetime.combine(start_date, time.min, tzinfo=zone).astimezone(
        timezone.utc
    )
    evaluation_end = datetime.combine(end_date, time.max, tzinfo=zone).astimezone(
        timezone.utc
    )
    checkpoint_cutoff = args.checkpoint_cutoff
    if args.role == "forward_checkpoint" or checkpoint_cutoff is not None:
        if checkpoint_cutoff is None or checkpoint_cutoff.tzinfo is None:
            raise ValueError(
                "Forward checkpoint replay requires a timezone-aware cutoff."
            )
        checkpoint_cutoff = checkpoint_cutoff.astimezone(timezone.utc)
        if not evaluation_start <= checkpoint_cutoff <= evaluation_end:
            raise ValueError(
                "Forward checkpoint cutoff is outside the workspace window."
            )
        replay_end = checkpoint_cutoff
    else:
        replay_end = evaluation_end
    bars_by_symbol = {}
    for value in manifest["paths"]:
        path = Path(value)
        bars = BarCache.read(path)
        if bars:
            bars_by_symbol[bars[0].symbol] = bars
    execution_by_symbol = {}
    for value in manifest["execution_paths"]:
        path = Path(value)
        bars = BarCache.read(path)
        if bars:
            execution_by_symbol[bars[0].symbol] = bars
    trade_paths = {
        Path(value).parent.name.upper(): Path(value)
        for value in manifest["trade_paths"]
    }
    expected = set(manifest["symbols"])
    coverage = {
        "minute_bars": sorted(expected - set(bars_by_symbol)),
        "ten_second_bars": sorted(expected - set(execution_by_symbol)),
        "trade_prints": sorted(expected - set(trade_paths)),
    }
    if any(coverage.values()):
        raise ValueError(f"Replay data coverage is incomplete: {coverage}")
    dates_by_symbol = {
        symbol: {date.fromisoformat(value) for value in values}
        for symbol, values in manifest["validation_dates_by_symbol"].items()
    }
    if set(dates_by_symbol) != expected:
        raise ValueError("Expected dates do not cover the workspace symbol set.")
    daily_coverage = {
        label: {
            symbol: {bar.timestamp.astimezone(zone).date() for bar in bars}
            for symbol, bars in series.items()
        }
        for label, series in (
            ("minute_bars", bars_by_symbol),
            ("ten_second_bars", execution_by_symbol),
        )
    }
    daily_coverage["trade_prints"] = {
        symbol: set(
            pd.to_datetime(pd.read_parquet(path, columns=["timestamp"])["timestamp"], utc=True)
            .dt.tz_convert(zone).dt.date
        )
        for symbol, path in trade_paths.items()
    }
    validate_daily_coverage(dates_by_symbol, daily_coverage)
    engine = PortfolioReplayEngine(settings, HistoryStore(args.history))
    common = {
        "execution_bars_by_symbol": execution_by_symbol,
        "execution_trade_paths_by_symbol": trade_paths,
        "source": "captured",
        "scanner_approvals": scanner_approvals,
        "feed": manifest["feed"],
        "evaluation_start": evaluation_start,
        "evaluation_end": replay_end,
        "evaluation_dates_by_symbol": dates_by_symbol,
    }
    print("Running baseline replay…", flush=True)
    baseline = engine.run(
        bars_by_symbol, guardrails=ReplayGuardrails(), **common
    )
    print("Running one-entry replay…", flush=True)
    candidate = engine.run(
        bars_by_symbol,
        guardrails=ReplayGuardrails(max_trades_per_symbol_day=1),
        **common,
    )
    if fingerprint_files(input_paths) != input_hashes or args.workspace.read_bytes() != manifest_bytes:
        raise RuntimeError("Replay input files changed during comparison.")
    if fingerprint_files(args.scanner_diagnostics) != approval_hashes:
        raise RuntimeError("Scanner approval evidence changed during comparison.")
    if fingerprint_files(source_paths) != source_hashes:
        raise RuntimeError("Replay source changed during comparison.")
    filtered = [
        row
        for row in candidate.rejected_trades
        if row["reason"].startswith("Replay guardrail: maximum trades")
    ]
    report = {
        "generated_at": datetime.now(timezone.utc),
        "experiment": "one_entry_per_symbol_day",
        "role": args.role,
        "replay_engine_version": "scanner_availability_v3",
        "scanner_clock": "approval_timestamp_v1",
        "scanner_diagnostics_sha256": approval_hashes,
        "protocol_compatible": False,
        "settings": settings.snapshot(),
        "evaluation_start_timestamp": evaluation_start,
        "evaluation_end_timestamp": replay_end,
        "workspace_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "input_sha256": input_hashes,
        "source_sha256": {
            str(Path(path).relative_to(root)): value
            for path, value in source_hashes.items()
        },
        "evaluation_start": start_date,
        "evaluation_end": replay_end.astimezone(zone).date(),
        "checkpoint_cutoff": checkpoint_cutoff,
        "candidate_source": "captured",
        "feed": manifest["feed"],
        "coverage": coverage,
        "baseline": {
            "run_id": baseline.run_id,
            "candidate_count": baseline.candidate_count,
            "metrics": baseline.metrics,
            "accepted_trades": [asdict(trade) for trade in baseline.accepted_trades],
            "rejection_count": len(baseline.rejected_trades),
        },
        "one_entry_per_symbol_day": {
            "run_id": candidate.run_id,
            "candidate_count": candidate.candidate_count,
            "metrics": candidate.metrics,
            "accepted_trades": [asdict(trade) for trade in candidate.accepted_trades],
            "rejection_count": len(candidate.rejected_trades),
            "later_same_symbol_entries_filtered": len(filtered),
        },
        "comparison": {
            "net_profit_difference": (
                candidate.metrics["net_profit"]
                - baseline.metrics["net_profit"]
            ),
            "max_drawdown_difference": (
                candidate.metrics["max_drawdown"]
                - baseline.metrics["max_drawdown"]
            ),
        },
        "limitations": [
            "The corrected replay engine differs from the frozen protocol. These results are exploratory and require a new predeclared forward comparison.",
            "Coverage checks establish nonempty symbol/date files, not uninterrupted intraday coverage or faithful fills.",
            "Symbol engines generate proposals before portfolio filtering; rejected positions can affect later proposal availability.",
            "Execution uses assumed spreads and configured slippage rather than historical executable quotes.",
            "Replay fidelity must be judged against the observed baseline before interpreting candidate lift.",
            "The fixed forward checkpoint in REENTRY_EXPERIMENT_PLAN.md remains required.",
        ],
    }
    ensure_private_file(args.report)
    args.report.write_text(
        json.dumps(to_json_safe(report), indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            to_json_safe(
                {
                    "baseline": baseline.metrics,
                    "candidate": candidate.metrics,
                    "comparison": report["comparison"],
                    "report": str(args.report),
                }
            )
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
