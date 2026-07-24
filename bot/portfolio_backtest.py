"""Portfolio-level replay assembled from point-in-time candidate history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from bot.backtest import BacktestEngine, BacktestTrade
from bot.config import Settings
from bot.history_store import HistoryStore
from bot.models import Bar
from bot.review import calculate_metrics


@dataclass(frozen=True)
class PortfolioReplayResult:
    run_id: str
    source: str
    symbols: list[str]
    candidate_count: int
    accepted_trades: list[BacktestTrade]
    rejected_trades: list[dict]
    strategy_diagnostics: list[dict]
    metrics: dict
    notes: list[str]


class PortfolioReplayEngine:
    def __init__(self, settings: Settings, store: HistoryStore):
        self.settings, self.store = settings, store

    def run(
        self,
        bars_by_symbol: Mapping[str, Sequence[Bar]],
        *,
        source: str = "reconstructed",
        feed: str = "sip",
        evaluation_start: datetime | None = None,
        evaluation_end: datetime | None = None,
    ) -> PortfolioReplayResult:
        loaded_symbols = {symbol.upper() for symbol, bars in bars_by_symbol.items() if bars}
        candidate_rows = self.store.candidates(
            source=source,
            passed_only=True,
            symbols=loaded_symbols,
            start_time=evaluation_start,
            end_time=evaluation_end,
        )
        candidate_symbols = {row["symbol"] for row in candidate_rows}
        pass_times: dict[str, set[datetime]] = {}
        for row in candidate_rows:
            pass_times.setdefault(row["symbol"], set()).add(datetime.fromisoformat(row["timestamp"]))
        proposed: list[BacktestTrade] = []
        strategy_diagnostics: list[dict] = []
        for symbol in sorted(candidate_symbols & set(bars_by_symbol)):
            bars = [
                bar for bar in bars_by_symbol[symbol]
                if (evaluation_start is None or bar.timestamp >= evaluation_start)
                and (evaluation_end is None or bar.timestamp <= evaluation_end)
            ]
            if bars:
                symbol_result = BacktestEngine(self.settings).run(
                    bars,
                    allowed_decision_times=pass_times.get(symbol, set()),
                )
                proposed.extend(symbol_result.trades)
                grouped: dict[tuple[str, str], dict] = {}
                for detail in symbol_result.rejection_details:
                    key = (detail["stage"], detail["reason"])
                    current = grouped.setdefault(
                        key,
                        {
                            "symbol": symbol,
                            "stage": detail["stage"],
                            "reason": detail["reason"],
                            "count": 0,
                            "representative_timestamp": detail["timestamp"].isoformat(),
                            "actual_values": detail["actual_values"],
                            "expected_values": detail["expected_values"],
                        },
                    )
                    current["count"] += 1
                    if detail["timestamp"].isoformat() > current["representative_timestamp"]:
                        current["representative_timestamp"] = detail["timestamp"].isoformat()
                        current["actual_values"] = detail["actual_values"]
                        current["expected_values"] = detail["expected_values"]
                strategy_diagnostics.extend(grouped.values())
        proposed.sort(key=lambda trade: (trade.entry_time, trade.symbol))
        accepted: list[BacktestTrade] = []
        rejected: list[dict] = []
        eastern = ZoneInfo(self.settings.timezone)
        for trade in proposed:
            day = trade.entry_time.astimezone(eastern).date()
            day_trades = [value for value in accepted if value.entry_time.astimezone(eastern).date() == day]
            realized_loss = abs(sum((value.realized_pnl for value in day_trades if value.realized_pnl < 0), Decimal("0")))
            overlapping = [value for value in accepted if value.entry_time <= trade.entry_time < value.exit_time]
            reason = None
            if len(overlapping) >= self.settings.max_open_positions: reason = "Maximum open positions reached."
            elif realized_loss >= self.settings.max_daily_loss: reason = "Daily loss limit already reached."
            elif self.settings.max_trades_per_day is not None and len(day_trades) >= self.settings.max_trades_per_day: reason = "Maximum trades per day reached."
            if reason: rejected.append({"symbol": trade.symbol, "entry_time": trade.entry_time.isoformat(), "reason": reason})
            else: accepted.append(trade)
        run_id = str(uuid4())
        metrics = calculate_metrics(accepted)
        timestamps = [
            bar.timestamp for bars in bars_by_symbol.values() for bar in bars
            if (evaluation_start is None or bar.timestamp >= evaluation_start)
            and (evaluation_end is None or bar.timestamp <= evaluation_end)
        ]
        notes = [
            "Candidate universe source: " + source,
            f"Loaded symbols: {', '.join(sorted(loaded_symbols)) or 'none'}.",
            f"Scanner-passing symbols: {', '.join(sorted(candidate_symbols)) or 'none'}.",
            "Candidate rows are scoped to the loaded symbols and evaluation window.",
            "Symbol engines use conservative OHLCV execution assumptions.",
            "Portfolio gate enforces chronological open-position, daily-loss, and trade-count limits.",
        ]
        result = PortfolioReplayResult(run_id, source, sorted(candidate_symbols), len(candidate_rows), accepted, rejected, strategy_diagnostics, metrics, notes)
        if timestamps:
            self.store.record_replay({"run_id":run_id,"created_at":datetime.utcnow().isoformat()+"Z","source":source,"feed":feed,"start_time":min(timestamps).isoformat(),"end_time":max(timestamps).isoformat(),"symbol_count":len(result.symbols),"candidate_count":result.candidate_count,"trade_count":len(accepted),"config":self.settings.snapshot(),"metrics":metrics,"notes":notes})
        return result
