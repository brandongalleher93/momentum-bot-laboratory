"""Portfolio-level replay assembled from point-in-time candidate history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Collection, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from bot.backtest import BacktestEngine, BacktestTrade
from bot.config import Settings
from bot.history_store import HistoryStore
from bot.historical_data import TradeCache, TradeTape, TradeTick
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


@dataclass(frozen=True)
class ReplayGuardrails:
    """Replay-only controls used to test whether repeated entries degrade results."""

    reentry_cooldown_minutes: int = 0
    max_consecutive_losses_per_symbol_day: int | None = None
    max_trades_per_symbol_day: int | None = None

    def __post_init__(self) -> None:
        if self.reentry_cooldown_minutes < 0:
            raise ValueError("Replay cooldown cannot be negative.")
        for value in (
            self.max_consecutive_losses_per_symbol_day,
            self.max_trades_per_symbol_day,
        ):
            if value is not None and value <= 0:
                raise ValueError("Enabled replay guardrail limits must be positive.")

    @property
    def enabled(self) -> bool:
        return any(
            (
                self.reentry_cooldown_minutes,
                self.max_consecutive_losses_per_symbol_day,
                self.max_trades_per_symbol_day,
            )
        )

    def snapshot(self) -> dict:
        return {
            "reentry_cooldown_minutes": self.reentry_cooldown_minutes,
            "max_consecutive_losses_per_symbol_day": (
                self.max_consecutive_losses_per_symbol_day
            ),
            "max_trades_per_symbol_day": self.max_trades_per_symbol_day,
        }


class PortfolioReplayEngine:
    def __init__(self, settings: Settings, store: HistoryStore):
        self.settings, self.store = settings, store

    def run(
        self,
        bars_by_symbol: Mapping[str, Sequence[Bar]],
        *,
        execution_bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None,
        execution_trades_by_symbol: Mapping[str, Sequence[TradeTick]] | None = None,
        execution_trade_paths_by_symbol: Mapping[str, Path] | None = None,
        source: str = "reconstructed",
        feed: str = "sip",
        evaluation_start: datetime | None = None,
        evaluation_end: datetime | None = None,
        evaluation_dates_by_symbol: Mapping[str, Collection[date]] | None = None,
        guardrails: ReplayGuardrails | None = None,
    ) -> PortfolioReplayResult:
        guardrails = guardrails or ReplayGuardrails()
        loaded_symbols = {symbol.upper() for symbol, bars in bars_by_symbol.items() if bars}
        scoped_dates = (
            {
                symbol.upper(): set(dates)
                for symbol, dates in evaluation_dates_by_symbol.items()
            }
            if evaluation_dates_by_symbol is not None
            else None
        )
        candidate_rows = self.store.candidates(
            source=source,
            passed_only=True,
            symbols=loaded_symbols,
            start_time=evaluation_start,
            end_time=evaluation_end,
        )
        eastern = ZoneInfo(self.settings.timezone)
        candidate_rows = [
            row
            for row in candidate_rows
            if self.settings.trade_window_start
            <= datetime.fromisoformat(row["timestamp"]).astimezone(eastern).time()
            <= self.settings.trade_window_end
            and (
                scoped_dates is None
                or datetime.fromisoformat(row["timestamp"])
                .astimezone(eastern)
                .date()
                in scoped_dates.get(row["symbol"], set())
            )
        ]
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
                and (
                    scoped_dates is None
                    or bar.timestamp.astimezone(eastern).date()
                    in scoped_dates.get(symbol, set())
                )
            ]
            if bars:
                engine = BacktestEngine(self.settings)
                symbol_result = engine.run(
                    bars,
                    allowed_decision_times=pass_times.get(symbol, set()),
                    execution_bars=(
                        execution_bars_by_symbol.get(symbol)
                        if execution_bars_by_symbol
                        else None
                    ),
                )
                symbol_trades = list(symbol_result.trades)
                rejection_details = list(symbol_result.rejection_details)
                symbol_execution_bars = (
                    execution_bars_by_symbol.get(symbol)
                    if execution_bars_by_symbol
                    else None
                )
                if symbol_execution_bars:
                    execution_scope = [
                        bar
                        for bar in symbol_execution_bars
                        if (evaluation_start is None or bar.timestamp >= evaluation_start)
                        and (evaluation_end is None or bar.timestamp <= evaluation_end)
                        and (
                            scoped_dates is None
                            or bar.timestamp.astimezone(eastern).date()
                            in scoped_dates.get(symbol, set())
                        )
                    ]
                    if execution_scope:
                        if (
                            execution_trades_by_symbol is not None
                            and symbol in execution_trades_by_symbol
                        ):
                            symbol_execution_trades = [
                                trade
                                for trade in execution_trades_by_symbol[symbol]
                                if (
                                    evaluation_start is None
                                    or trade.timestamp >= evaluation_start
                                )
                                and (
                                    evaluation_end is None
                                    or trade.timestamp <= evaluation_end
                                )
                                and (
                                    scoped_dates is None
                                    or trade.timestamp.astimezone(eastern).date()
                                    in scoped_dates.get(symbol, set())
                                )
                            ]
                        elif (
                            execution_trade_paths_by_symbol is not None
                            and symbol in execution_trade_paths_by_symbol
                        ):
                            symbol_execution_trades = TradeCache.read_tape(
                                execution_trade_paths_by_symbol[symbol]
                            )
                        else:
                            symbol_execution_trades = None
                        micro_result = engine.run_micro(
                            bars,
                            execution_scope,
                            allowed_decision_times=pass_times.get(symbol, set()),
                            execution_trades=symbol_execution_trades,
                        )
                        if isinstance(symbol_execution_trades, TradeTape):
                            symbol_execution_trades.close()
                        del symbol_execution_trades
                        combined = list(micro_result.trades)
                        for trade in symbol_trades:
                            if not any(
                                abs(
                                    (trade.entry_time - micro.entry_time).total_seconds()
                                )
                                < 60
                                for micro in combined
                            ):
                                combined.append(trade)
                        symbol_trades = sorted(
                            combined, key=lambda trade: trade.entry_time
                        )
                        rejection_details.extend(
                            micro_result.rejection_details
                        )
                proposed.extend(symbol_trades)
                grouped: dict[tuple[str, str], dict] = {}
                for detail in rejection_details:
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
        accepted, rejected = self._apply_portfolio_gate(proposed, guardrails, eastern)
        run_id = str(uuid4())
        metrics = calculate_metrics(accepted)
        timestamps = [
            bar.timestamp for bars in bars_by_symbol.values() for bar in bars
            if (evaluation_start is None or bar.timestamp >= evaluation_start)
            and (evaluation_end is None or bar.timestamp <= evaluation_end)
            and (
                scoped_dates is None
                or bar.timestamp.astimezone(eastern).date()
                in scoped_dates.get(bar.symbol, set())
            )
        ]
        execution_symbols = sorted(
            symbol
            for symbol, values in (execution_bars_by_symbol or {}).items()
            if values
        )
        trade_print_symbols = sorted(
            set(
                symbol
                for symbol, values in (execution_trades_by_symbol or {}).items()
                if values
            )
            | set((execution_trade_paths_by_symbol or {}).keys())
        )
        notes = [
            "Candidate universe source: " + source,
            f"Loaded symbols: {', '.join(sorted(loaded_symbols)) or 'none'}.",
            f"Scanner-passing symbols: {', '.join(sorted(candidate_symbols)) or 'none'}.",
            (
                "Candidate rows are scoped to their preselected symbol/date pairs; "
                "other dates are excluded."
                if scoped_dates is not None
                else "Candidate rows are scoped to the loaded symbols and evaluation window."
            ),
            (
                "One-minute bars provide setup context; cached ten-second bars "
                "select the first breakout trigger."
                if execution_bars_by_symbol
                else "Symbol engines use conservative one-minute OHLCV execution assumptions."
            ),
            (
                "Ten-second execution coverage: "
                + (", ".join(execution_symbols) if execution_symbols else "none")
                + ". Symbols without coverage retain one-minute execution assumptions."
            ),
            (
                "Raw trade-print ordering resolves micro entries, protective stops, and targets for: "
                + (", ".join(trade_print_symbols) if trade_print_symbols else "none")
                + "."
                if (
                    execution_trades_by_symbol is not None
                    or execution_trade_paths_by_symbol is not None
                )
                else "Protective stops and targets remain conservative because OHLCV bars do not reveal intrabar trade order."
            ),
            (
                "Replay-only guardrails: "
                f"{guardrails.reentry_cooldown_minutes}-minute symbol re-entry cooldown; "
                f"{guardrails.max_consecutive_losses_per_symbol_day or 'no'} "
                "consecutive-loss limit per symbol/day; "
                f"{guardrails.max_trades_per_symbol_day or 'no'} "
                "trade limit per symbol/day."
                if guardrails.enabled
                else "Replay-only experimental guardrails are disabled."
            ),
            "Portfolio gate enforces chronological open-position, daily-loss, and trade-count limits.",
        ]
        result = PortfolioReplayResult(run_id, source, sorted(candidate_symbols), len(candidate_rows), accepted, rejected, strategy_diagnostics, metrics, notes)
        if timestamps:
            replay_config = self.settings.snapshot()
            replay_config["replay_guardrails"] = guardrails.snapshot()
            self.store.record_replay({"run_id":run_id,"created_at":datetime.now(timezone.utc).isoformat(),"source":source,"feed":feed,"start_time":min(timestamps).isoformat(),"end_time":max(timestamps).isoformat(),"symbol_count":len(result.symbols),"candidate_count":result.candidate_count,"trade_count":len(accepted),"config":replay_config,"metrics":metrics,"notes":notes})
        return result

    def _apply_portfolio_gate(
        self,
        proposed: Sequence[BacktestTrade],
        guardrails: ReplayGuardrails,
        eastern: ZoneInfo,
    ) -> tuple[list[BacktestTrade], list[dict]]:
        accepted: list[BacktestTrade] = []
        rejected: list[dict] = []
        for trade in proposed:
            day = trade.entry_time.astimezone(eastern).date()
            day_trades = [value for value in accepted if value.entry_time.astimezone(eastern).date() == day]
            same_symbol = [value for value in day_trades if value.symbol == trade.symbol]
            realized_loss = abs(
                sum(
                    (
                        value.realized_pnl
                        for value in day_trades
                        if value.exit_time <= trade.entry_time
                        and value.realized_pnl < 0
                    ),
                    Decimal("0"),
                )
            )
            overlapping = [value for value in accepted if value.entry_time <= trade.entry_time < value.exit_time]
            reason = self._guardrail_reason(trade, same_symbol, guardrails)
            if reason is None:
                if len(overlapping) >= self.settings.max_open_positions:
                    reason = "Maximum open positions reached."
                elif realized_loss >= self.settings.max_daily_loss:
                    reason = "Daily loss limit already reached."
                elif (
                    self.settings.max_trades_per_day is not None
                    and len(day_trades) >= self.settings.max_trades_per_day
                ):
                    reason = "Maximum trades per day reached."
            if reason: rejected.append({"symbol": trade.symbol, "entry_time": trade.entry_time.isoformat(), "reason": reason})
            else: accepted.append(trade)
        return accepted, rejected

    @staticmethod
    def _apply_replay_guardrails(
        proposed: Sequence[BacktestTrade],
        guardrails: ReplayGuardrails,
        eastern: ZoneInfo,
    ) -> tuple[list[BacktestTrade], list[dict]]:
        if not guardrails.enabled:
            return list(proposed), []

        kept: list[BacktestTrade] = []
        rejected: list[dict] = []
        by_symbol_day: dict[tuple[object, str], list[BacktestTrade]] = {}

        for trade in proposed:
            key = (
                trade.entry_time.astimezone(eastern).date(),
                trade.symbol,
            )
            prior = by_symbol_day.setdefault(key, [])
            reason = PortfolioReplayEngine._guardrail_reason(
                trade, prior, guardrails
            )

            if reason is not None:
                rejected.append(
                    {
                        "symbol": trade.symbol,
                        "entry_time": trade.entry_time.isoformat(),
                        "reason": reason,
                    }
                )
                continue
            prior.append(trade)
            kept.append(trade)

        return kept, rejected

    @staticmethod
    def _guardrail_reason(
        trade: BacktestTrade,
        prior: Sequence[BacktestTrade],
        guardrails: ReplayGuardrails,
    ) -> str | None:
        if (
            guardrails.max_trades_per_symbol_day is not None
            and len(prior) >= guardrails.max_trades_per_symbol_day
        ):
            return (
                "Replay guardrail: maximum trades per symbol/day reached "
                f"({guardrails.max_trades_per_symbol_day})."
            )
        if (
            guardrails.max_consecutive_losses_per_symbol_day is not None
            and PortfolioReplayEngine._trailing_loss_count(prior)
            >= guardrails.max_consecutive_losses_per_symbol_day
        ):
            return (
                "Replay guardrail: consecutive-loss limit reached for "
                f"this symbol/day ({guardrails.max_consecutive_losses_per_symbol_day})."
            )
        cooldown = timedelta(minutes=guardrails.reentry_cooldown_minutes)
        if (
            cooldown > timedelta(0)
            and prior
            and trade.entry_time < prior[-1].exit_time + cooldown
        ):
            return (
                "Replay guardrail: symbol re-entry cooldown still active "
                f"({guardrails.reentry_cooldown_minutes} minutes)."
            )
        return None

    @staticmethod
    def _trailing_loss_count(trades: Sequence[BacktestTrade]) -> int:
        count = 0
        for trade in reversed(trades):
            if trade.realized_pnl >= 0:
                break
            count += 1
        return count
