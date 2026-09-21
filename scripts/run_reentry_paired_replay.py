"""Run baseline and one-entry replays from the completed research workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.config import load_settings
from bot.event_log import to_json_safe
from bot.historical_data import BarCache
from bot.history_store import HistoryStore
from bot.portfolio_backtest import PortfolioReplayEngine, ReplayGuardrails
from bot.security import ensure_private_file
from bot.shadow_paper import shadow_paper_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    manifest_bytes = args.workspace.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not manifest.get("complete") or manifest.get("failures"):
        raise ValueError("Replay workspace is incomplete.")
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
    engine = PortfolioReplayEngine(settings, HistoryStore(args.history))
    common = {
        "execution_bars_by_symbol": execution_by_symbol,
        "execution_trade_paths_by_symbol": trade_paths,
        "source": "captured",
        "feed": manifest["feed"],
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
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
    filtered = [
        row
        for row in candidate.rejected_trades
        if row["reason"].startswith("Replay guardrail: maximum trades")
    ]
    report = {
        "generated_at": datetime.now(timezone.utc),
        "experiment": "one_entry_per_symbol_day",
        "role": "retrospective_exploratory",
        "workspace_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "evaluation_start": start_date,
        "evaluation_end": end_date,
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
            "The candidate was selected after reviewing the shadow sample, so this retrospective result cannot validate it.",
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
