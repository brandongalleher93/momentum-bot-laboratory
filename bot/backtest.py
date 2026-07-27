"""Conservative completed-bar backtester for one pre-screened symbol at a time."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, IO, Optional, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.exits import ExitManager
from bot.historical_data import TradeTape, TradeTick
from bot.models import Bar, PositionRecord, Quote, RiskSnapshot
from bot.risk_manager import RiskManager
from bot.strategy import BullFlagStrategy, MicroPullbackStrategy


@dataclass(frozen=True)
class BacktestTrade:
    trade_id: str
    symbol: str
    entry_time: datetime
    entry_price: Decimal
    quantity: int
    stop_price: Decimal
    target_price: Decimal
    initial_risk: Decimal
    exit_time: datetime
    exit_price: Decimal
    exit_reason: str
    realized_pnl: Decimal
    r_multiple: Decimal
    ambiguous_same_bar: bool
    setup_id: str
    config_version: str
    parameter_profile: str
    decision_register_version: str


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    trades: list[BacktestTrade]
    setup_rejections: int
    entry_opportunities: int
    ambiguous_same_bar_count: int
    rejection_details: list[dict[str, Any]]
    notes: list[str]


class BacktestEngine:
    """Runs against OHLCV bars without pretending to know intrabar ordering."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.diagnostics = DiagnosticFactory(settings)
        self.strategy = BullFlagStrategy(settings, self.diagnostics)
        self.micro_strategy = MicroPullbackStrategy(settings, self.diagnostics)
        self.risk = RiskManager(settings, self.diagnostics)
        self.exits = ExitManager(settings, self.diagnostics)

    def run(
        self,
        bars: Sequence[Bar],
        *,
        allowed_decision_times: set[datetime] | None = None,
        execution_bars: Sequence[Bar] | None = None,
    ) -> BacktestResult:
        self._validate_bars(bars)
        if execution_bars:
            self._validate_bars(execution_bars)
            if execution_bars[0].symbol != bars[0].symbol:
                raise ValueError("Context and execution bars must use the same symbol.")
        symbol = bars[0].symbol
        eastern = ZoneInfo(self.settings.timezone)
        by_date: dict[object, list[Bar]] = {}
        for bar in bars:
            by_date.setdefault(bar.timestamp.astimezone(eastern).date(), []).append(bar)
        execution_by_date: dict[object, list[Bar]] = {}
        for bar in execution_bars or ():
            execution_by_date.setdefault(
                bar.timestamp.astimezone(eastern).date(), []
            ).append(bar)

        trades: list[BacktestTrade] = []
        rejected = 0
        opportunities = 0
        ambiguous_count = 0
        rejection_details: list[dict[str, Any]] = []
        for session_bars in by_date.values():
            session_trades, session_rejected, session_opportunities, session_details = self._run_session(
                session_bars,
                allowed_decision_times=allowed_decision_times,
                execution_bars=execution_by_date.get(
                    session_bars[0].timestamp.astimezone(eastern).date()
                ),
            )
            trades.extend(session_trades)
            rejected += session_rejected
            opportunities += session_opportunities
            rejection_details.extend(session_details)
            ambiguous_count += sum(1 for trade in session_trades if trade.ambiguous_same_bar)

        return BacktestResult(
            symbol=symbol,
            trades=trades,
            setup_rejections=rejected,
            entry_opportunities=opportunities,
            ambiguous_same_bar_count=ambiguous_count,
            rejection_details=rejection_details,
            notes=[
                "Input is treated as a pre-screened single-symbol candidate stream.",
                (
                    "Ten-second bars select the first observed breakout trigger; "
                    "one-minute bars provide setup context and subsequent management."
                    if execution_bars
                    else "One-minute bars use conservative stop-first resolution when sequence is unknown."
                ),
                "Scanner-universe survivorship and point-in-time symbol membership require a separate dataset audit.",
            ],
        )

    def run_micro(
        self,
        context_bars: Sequence[Bar],
        execution_bars: Sequence[Bar],
        *,
        allowed_decision_times: set[datetime] | None = None,
        execution_trades: Sequence[TradeTick] | TradeTape | None = None,
    ) -> BacktestResult:
        """Run ten-second setups behind minute-level scanner/context approval."""

        self._validate_bars(context_bars)
        self._validate_bars(execution_bars)
        if context_bars[0].symbol != execution_bars[0].symbol:
            raise ValueError("Context and execution bars must use the same symbol.")
        if isinstance(execution_trades, TradeTape):
            if execution_trades.symbol != context_bars[0].symbol:
                raise ValueError("Execution trades must match the backtest symbol.")
        elif execution_trades:
            self._validate_trades(execution_trades, symbol=context_bars[0].symbol)
        symbol = context_bars[0].symbol
        eastern = ZoneInfo(self.settings.timezone)
        context_by_date: dict[object, list[Bar]] = {}
        execution_by_date: dict[object, list[Bar]] = {}
        for bar in context_bars:
            context_by_date.setdefault(
                bar.timestamp.astimezone(eastern).date(), []
            ).append(bar)
        for bar in execution_bars:
            execution_by_date.setdefault(
                bar.timestamp.astimezone(eastern).date(), []
            ).append(bar)
        trades_by_date: dict[object, list[TradeTick]] = {}
        if execution_trades is not None and not isinstance(
            execution_trades, TradeTape
        ):
            for trade in execution_trades:
                trades_by_date.setdefault(
                    trade.timestamp.astimezone(eastern).date(), []
                ).append(trade)

        trades: list[BacktestTrade] = []
        rejected = 0
        opportunities = 0
        details: list[dict[str, Any]] = []
        for day, session_execution in execution_by_date.items():
            session_context = context_by_date.get(day, [])
            (
                session_trades,
                session_rejected,
                session_opportunities,
                session_details,
            ) = self._run_micro_session(
                session_context,
                session_execution,
                allowed_decision_times=allowed_decision_times,
                execution_trades=(
                    execution_trades
                    if isinstance(execution_trades, TradeTape)
                    else trades_by_date.get(day)
                ),
            )
            trades.extend(session_trades)
            rejected += session_rejected
            opportunities += session_opportunities
            details.extend(session_details)
        return BacktestResult(
            symbol=symbol,
            trades=trades,
            setup_rejections=rejected,
            entry_opportunities=opportunities,
            ambiguous_same_bar_count=sum(
                1 for trade in trades if trade.ambiguous_same_bar
            ),
            rejection_details=details,
            notes=[
                "Minute-level reconstructed scanner approval gates ten-second setup evaluation.",
                "Completed ten-second bars detect micro-pullback and reversal breakouts.",
                (
                    "Raw trade prints determine chronological entries, stops, and targets; "
                    "completed-minute bars drive indicator exits."
                    if execution_trades
                    else "Protective stop/target checks use ten-second OHLCV; completed-minute bars drive indicator exits."
                ),
            ],
        )

    def _run_micro_session(
        self,
        context_bars: Sequence[Bar],
        execution_bars: Sequence[Bar],
        *,
        allowed_decision_times: set[datetime] | None,
        execution_trades: Sequence[TradeTick] | TradeTape | None,
    ) -> tuple[list[BacktestTrade], int, int, list[dict[str, Any]]]:
        trades: list[BacktestTrade] = []
        position: Optional[PositionRecord] = None
        entry_setup_id: Optional[str] = None
        entry_time: Optional[datetime] = None
        realized_loss = Decimal("0")
        rejected = 0
        opportunities = 0
        details: list[dict[str, Any]] = []
        used_breakout_levels: set[Decimal] = set()
        last_context_cutoff: Optional[datetime] = None
        chronological_trades = (
            execution_trades
            if execution_trades is not None
            and not isinstance(execution_trades, TradeTape)
            else ()
        )
        trades_by_bar_end: dict[datetime, list[TradeTick]] = {}
        for trade in chronological_trades:
            bucket_start = trade.timestamp.replace(
                second=(trade.timestamp.second // 10) * 10,
                microsecond=0,
            )
            trades_by_bar_end.setdefault(
                bucket_start + timedelta(seconds=10), []
            ).append(trade)

        for index, event_bar in enumerate(execution_bars):
            available = execution_bars[:index]
            event_trades: list[TradeTick] | None = None
            context_available = [
                bar for bar in context_bars if bar.timestamp <= event_bar.timestamp
            ]
            context_cutoff = (
                context_available[-1].timestamp if context_available else None
            )

            if position is not None:
                event_trades = (
                    execution_trades.trades_for_bar(event_bar)
                    if isinstance(execution_trades, TradeTape)
                    else trades_by_bar_end.get(event_bar.timestamp, [])
                )
                closed = (
                    self._maybe_close_position_from_trades(
                        position,
                        event_trades,
                        setup_id=entry_setup_id or "unknown",
                        entry_time=entry_time or position.opened_at,
                    )
                    if execution_trades is not None
                    else self._maybe_close_micro_position(
                        position,
                        event_bar,
                        setup_id=entry_setup_id or "unknown",
                        entry_time=entry_time or position.opened_at,
                        entry_bar=False,
                    )
                )
                if (
                    closed is None
                    and context_cutoff is not None
                    and context_cutoff != last_context_cutoff
                    and len(context_available) >= self.settings.ema_period
                ):
                    try:
                        decision = self.exits.evaluate(
                            position, context_available, event_bar.timestamp
                        )
                    except ValueError:
                        decision = None
                    if decision is not None and decision.should_exit:
                        closed = self._close(
                            position,
                            event_bar,
                            event_bar.close,
                            decision.reason,
                            False,
                            setup_id=entry_setup_id or "unknown",
                            entry_time=entry_time or position.opened_at,
                        )
                last_context_cutoff = context_cutoff
                if closed is not None:
                    trades.append(closed)
                    if closed.realized_pnl < 0:
                        realized_loss += abs(closed.realized_pnl)
                    position = None
                    entry_setup_id = None
                    entry_time = None
                continue

            if len(available) < 4:
                continue
            decision_time = available[-1].timestamp
            if not self._scanner_gate_open(
                decision_time, allowed_decision_times
            ):
                continue
            setup_result = self.micro_strategy.detect_setup(
                event_bar.symbol, available, decision_time
            )
            if setup_result.value is None:
                rejected += 1
                details.append(
                    self._detail_from_diagnostic(
                        event_bar.symbol,
                        decision_time,
                        setup_result.diagnostic,
                    )
                )
                continue
            setup = setup_result.value
            if setup.breakout_level in used_breakout_levels:
                continue
            event_trades = (
                execution_trades.trades_for_bar(event_bar)
                if isinstance(execution_trades, TradeTape)
                else trades_by_bar_end.get(event_bar.timestamp, [])
            )
            trigger_trade = (
                self._first_trigger_trade(event_trades, setup.trigger_price)
                if execution_trades is not None
                else None
            )
            trigger_reached = (
                trigger_trade is not None
                if execution_trades is not None
                else event_bar.high >= setup.trigger_price
            )
            if not trigger_reached:
                details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": "ten_second_entry_trigger",
                        "reason": "Micro breakout trigger was not reached on the next ten-second bar.",
                        "actual_values": {"next_bar_high": event_bar.high},
                        "expected_values": {"minimum_high": setup.trigger_price},
                    }
                )
                continue
            opportunities += 1
            assumed_ask = (
                max(setup.trigger_price, trigger_trade.price)
                if trigger_trade is not None
                else max(setup.trigger_price, event_bar.open)
            )
            trigger_time = (
                trigger_trade.timestamp
                if trigger_trade is not None
                else event_bar.timestamp
            )
            assumed_spread = min(
                self.settings.max_spread_percent / Decimal("2"),
                Decimal("0.001"),
            )
            quote = Quote(
                symbol=event_bar.symbol,
                timestamp=trigger_time,
                bid=assumed_ask * (Decimal("1") - assumed_spread),
                ask=assumed_ask,
            )
            entry_result = self.strategy.check_entry(setup, quote)
            if entry_result.value is None or not entry_result.value.triggered:
                details.append(
                    self._detail_from_diagnostic(
                        event_bar.symbol,
                        decision_time,
                        entry_result.diagnostic,
                    )
                )
                continue
            plan_result = self.strategy.create_trade_plan(
                setup, entry_result.value
            )
            if plan_result.value is None:
                details.append(
                    self._detail_from_diagnostic(
                        event_bar.symbol,
                        decision_time,
                        plan_result.diagnostic,
                    )
                )
                continue
            plan = plan_result.value
            risk_result = self.risk.evaluate(
                plan,
                RiskSnapshot(
                    realized_loss=realized_loss,
                    trades_taken_today=len(trades),
                    open_positions=0,
                    active_entry_orders=0,
                ),
                trigger_time,
            )
            if not risk_result.approval.approved:
                details.append(
                    self._detail_from_diagnostic(
                        event_bar.symbol,
                        decision_time,
                        risk_result.diagnostic,
                    )
                )
                continue
            slipped_entry = assumed_ask * (
                Decimal("1")
                + self.settings.backtest_entry_slippage_bps / Decimal("10000")
            )
            if slipped_entry > plan.maximum_entry_price:
                details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": "backtest_execution",
                        "reason": "Slipped micro entry exceeded the protected maximum entry price.",
                        "actual_values": {"slipped_entry": slipped_entry},
                        "expected_values": {
                            "maximum_entry_price": plan.maximum_entry_price
                        },
                    }
                )
                continue
            actual_risk_per_share = slipped_entry - plan.stop_price
            actual_target = slipped_entry + (
                self.settings.target_r_multiple * actual_risk_per_share
            )
            trade_id = str(uuid4())
            position = PositionRecord(
                trade_id=trade_id,
                symbol=event_bar.symbol,
                quantity=risk_result.approval.quantity,
                average_entry_price=slipped_entry,
                stop_price=plan.stop_price,
                target_price=actual_target,
                breakout_level=plan.breakout_level,
                initial_risk=Decimal(risk_result.approval.quantity)
                * actual_risk_per_share,
                opened_at=trigger_time,
            )
            entry_setup_id = setup.setup_id
            entry_time = trigger_time
            used_breakout_levels.add(setup.breakout_level)
            if trigger_trade is not None:
                trigger_index = event_trades.index(trigger_trade)
                closed = self._maybe_close_position_from_trades(
                    position,
                    event_trades[trigger_index + 1 :],
                    setup_id=entry_setup_id,
                    entry_time=entry_time,
                )
            else:
                closed = self._maybe_close_micro_position(
                    position,
                    event_bar,
                    setup_id=entry_setup_id,
                    entry_time=entry_time,
                    entry_bar=True,
                )
            if closed is not None:
                trades.append(closed)
                if closed.realized_pnl < 0:
                    realized_loss += abs(closed.realized_pnl)
                position = None
                entry_setup_id = None
                entry_time = None

        if position is not None:
            final_bar = execution_bars[-1]
            trades.append(
                self._close(
                    position,
                    final_bar,
                    final_bar.close,
                    "End-of-day flat policy.",
                    False,
                    setup_id=entry_setup_id or "unknown",
                    entry_time=entry_time or position.opened_at,
                )
            )
        return trades, rejected, opportunities, details

    @staticmethod
    def _first_trigger_trade(
        trades: Sequence[TradeTick], trigger_price: Decimal
    ) -> Optional[TradeTick]:
        return next(
            (trade for trade in trades if trade.price >= trigger_price),
            None,
        )

    def _maybe_close_position_from_trades(
        self,
        position: PositionRecord,
        trades: Sequence[TradeTick],
        *,
        setup_id: str,
        entry_time: datetime,
    ) -> Optional[BacktestTrade]:
        for trade in trades:
            if trade.price <= position.stop_price:
                return self._close(
                    position,
                    self._bar_from_trade(trade),
                    position.stop_price,
                    "Protective stop hit.",
                    False,
                    setup_id=setup_id,
                    entry_time=entry_time,
                )
            if trade.price >= position.target_price:
                return self._close(
                    position,
                    self._bar_from_trade(trade),
                    position.target_price,
                    "2R target hit.",
                    False,
                    setup_id=setup_id,
                    entry_time=entry_time,
                )
        return None

    @staticmethod
    def _bar_from_trade(trade: TradeTick) -> Bar:
        return Bar(
            symbol=trade.symbol,
            timestamp=trade.timestamp,
            open=trade.price,
            high=trade.price,
            low=trade.price,
            close=trade.price,
            volume=trade.size,
        )

    def _maybe_close_micro_position(
        self,
        position: PositionRecord,
        bar: Bar,
        *,
        setup_id: str,
        entry_time: datetime,
        entry_bar: bool,
    ) -> Optional[BacktestTrade]:
        stop_touched = bar.low <= position.stop_price
        target_touched = bar.high >= position.target_price
        ambiguous = stop_touched and target_touched
        if ambiguous or stop_touched:
            return self._close(
                position,
                bar,
                position.stop_price,
                "Protective stop hit.",
                ambiguous or entry_bar,
                setup_id=setup_id,
                entry_time=entry_time,
            )
        if target_touched:
            return self._close(
                position,
                bar,
                position.target_price,
                "2R target hit.",
                entry_bar,
                setup_id=setup_id,
                entry_time=entry_time,
            )
        return None

    @staticmethod
    def _scanner_gate_open(
        decision_time: datetime,
        allowed_decision_times: set[datetime] | None,
    ) -> bool:
        if allowed_decision_times is None:
            return True
        return any(
            timedelta(0) <= decision_time - allowed < timedelta(minutes=1)
            for allowed in allowed_decision_times
        )

    @staticmethod
    def _detail_from_diagnostic(
        symbol: str, timestamp: datetime, diagnostic
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "timestamp": timestamp,
            "stage": diagnostic.module,
            "reason": diagnostic.reason,
            "actual_values": diagnostic.actual_values,
            "expected_values": diagnostic.expected_values,
        }

    def _run_session(
        self,
        bars: Sequence[Bar],
        *,
        allowed_decision_times: set[datetime] | None = None,
        execution_bars: Sequence[Bar] | None = None,
    ) -> tuple[list[BacktestTrade], int, int, list[dict[str, Any]]]:
        trades: list[BacktestTrade] = []
        position: Optional[PositionRecord] = None
        entry_setup_id: Optional[str] = None
        entry_time: Optional[datetime] = None
        realized_loss = Decimal("0")
        rejected = 0
        opportunities = 0
        rejection_details: list[dict[str, Any]] = []

        for index, event_bar in enumerate(bars):
            available = bars[:index]

            if position is not None:
                closed = self._maybe_close_position(
                    position,
                    event_bar,
                    available + [event_bar],
                    setup_id=entry_setup_id or "unknown",
                    entry_time=entry_time or position.opened_at,
                )
                if closed is not None:
                    trades.append(closed)
                    if closed.realized_pnl < 0:
                        realized_loss += abs(closed.realized_pnl)
                    position = None
                    entry_setup_id = None
                    entry_time = None
                continue

            if len(available) < (
                self.settings.ema_period
                + self.settings.pullback_candles_min
                + 2
            ):
                continue
            decision_time = available[-1].timestamp
            if allowed_decision_times is not None and decision_time not in allowed_decision_times:
                continue
            setup_result = self.strategy.detect_setup(
                event_bar.symbol, available, decision_time
            )
            if setup_result.value is None:
                rejected += 1
                rejection_details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": setup_result.diagnostic.module,
                        "reason": setup_result.diagnostic.reason,
                        "actual_values": setup_result.diagnostic.actual_values,
                        "expected_values": setup_result.diagnostic.expected_values,
                    }
                )
                continue
            setup = setup_result.value
            trigger_bar = self._first_trigger_bar(
                setup.trigger_price,
                context_cutoff=decision_time,
                context_event_time=event_bar.timestamp,
                context_event_bar=event_bar,
                execution_bars=execution_bars,
            )
            if trigger_bar is None:
                interval_execution_bars = [
                    bar
                    for bar in execution_bars or ()
                    if decision_time < bar.timestamp <= event_bar.timestamp
                ]
                rejection_details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": "entry_trigger",
                        "reason": (
                            "Breakout trigger was not reached on a ten-second execution bar."
                            if execution_bars
                            else "Breakout trigger was not reached on the next bar."
                        ),
                        "actual_values": {
                            "next_bar_high": (
                                max(
                                    (bar.high for bar in interval_execution_bars),
                                    default=None,
                                )
                                if execution_bars
                                else event_bar.high
                            ),
                            "execution_bar_count": (
                                len(interval_execution_bars)
                                if execution_bars
                                else 1
                            ),
                        },
                        "expected_values": {"minimum_high": setup.trigger_price},
                    }
                )
                continue

            opportunities += 1
            assumed_ask = max(setup.trigger_price, trigger_bar.open)
            assumed_spread = min(self.settings.max_spread_percent / Decimal("2"), Decimal("0.001"))
            assumed_bid = assumed_ask * (Decimal("1") - assumed_spread)
            quote = Quote(
                symbol=trigger_bar.symbol,
                timestamp=trigger_bar.timestamp,
                bid=assumed_bid,
                ask=assumed_ask,
            )
            entry_result = self.strategy.check_entry(setup, quote)
            if entry_result.value is None or not entry_result.value.triggered:
                rejection_details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": entry_result.diagnostic.module,
                        "reason": entry_result.diagnostic.reason,
                        "actual_values": entry_result.diagnostic.actual_values,
                        "expected_values": entry_result.diagnostic.expected_values,
                    }
                )
                continue
            plan_result = self.strategy.create_trade_plan(setup, entry_result.value)
            if plan_result.value is None:
                rejection_details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": plan_result.diagnostic.module,
                        "reason": plan_result.diagnostic.reason,
                        "actual_values": plan_result.diagnostic.actual_values,
                        "expected_values": plan_result.diagnostic.expected_values,
                    }
                )
                continue
            plan = plan_result.value
            risk_result = self.risk.evaluate(
                plan,
                RiskSnapshot(
                    realized_loss=realized_loss,
                    trades_taken_today=len(trades),
                    open_positions=0,
                    active_entry_orders=0,
                ),
                trigger_bar.timestamp,
            )
            if not risk_result.approval.approved:
                rejection_details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": risk_result.diagnostic.module,
                        "reason": risk_result.diagnostic.reason,
                        "actual_values": risk_result.diagnostic.actual_values,
                        "expected_values": risk_result.diagnostic.expected_values,
                    }
                )
                continue

            slipped_entry = assumed_ask * (
                Decimal("1") + self.settings.backtest_entry_slippage_bps / Decimal("10000")
            )
            if slipped_entry > plan.maximum_entry_price:
                rejection_details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": "backtest_execution",
                        "reason": "Slipped entry exceeded the protected maximum entry price.",
                        "actual_values": {"slipped_entry": slipped_entry},
                        "expected_values": {"maximum_entry_price": plan.maximum_entry_price},
                    }
                )
                continue  # The marketable limit did not fill within its protection.
            actual_risk_per_share = slipped_entry - plan.stop_price
            actual_target = slipped_entry + (
                self.settings.target_r_multiple * actual_risk_per_share
            )
            trade_id = str(uuid4())
            position = PositionRecord(
                trade_id=trade_id,
                symbol=event_bar.symbol,
                quantity=risk_result.approval.quantity,
                average_entry_price=slipped_entry,
                stop_price=plan.stop_price,
                target_price=actual_target,
                breakout_level=plan.breakout_level,
                initial_risk=Decimal(risk_result.approval.quantity)
                * actual_risk_per_share,
                opened_at=trigger_bar.timestamp,
            )
            entry_setup_id = setup.setup_id
            entry_time = trigger_bar.timestamp

            # A completed execution bar still cannot reveal its internal trade ordering.
            closed = self._maybe_close_position(
                position,
                trigger_bar,
                available + [trigger_bar],
                setup_id=entry_setup_id,
                entry_time=entry_time,
                entry_bar=True,
            )
            if closed is not None:
                trades.append(closed)
                if closed.realized_pnl < 0:
                    realized_loss += abs(closed.realized_pnl)
                position = None
                entry_setup_id = None
                entry_time = None

        if position is not None:
            final_bar = bars[-1]
            trades.append(
                self._close(
                    position,
                    final_bar,
                    final_bar.close,
                    "End-of-day flat policy.",
                    False,
                    setup_id=entry_setup_id or "unknown",
                    entry_time=entry_time or position.opened_at,
                )
            )
        return trades, rejected, opportunities, rejection_details

    @staticmethod
    def _first_trigger_bar(
        trigger_price: Decimal,
        *,
        context_cutoff: datetime,
        context_event_time: datetime,
        context_event_bar: Bar,
        execution_bars: Sequence[Bar] | None,
    ) -> Optional[Bar]:
        if not execution_bars:
            return (
                context_event_bar
                if context_event_bar.high >= trigger_price
                else None
            )
        return next(
            (
                bar
                for bar in execution_bars
                if context_cutoff < bar.timestamp <= context_event_time
                and bar.high >= trigger_price
            ),
            None,
        )

    def _maybe_close_position(
        self,
        position: PositionRecord,
        bar: Bar,
        completed_bars: Sequence[Bar],
        *,
        setup_id: str = "unknown",
        entry_time: Optional[datetime] = None,
        entry_bar: bool = False,
    ) -> Optional[BacktestTrade]:
        stop_touched = bar.low <= position.stop_price
        target_touched = bar.high >= position.target_price
        ambiguous = stop_touched and target_touched
        if ambiguous or stop_touched:
            return self._close(
                position,
                bar,
                position.stop_price,
                "Protective stop hit.",
                ambiguous or entry_bar,
                setup_id=setup_id,
                entry_time=entry_time or position.opened_at,
            )
        if target_touched:
            return self._close(
                position,
                bar,
                position.target_price,
                "2R target hit.",
                entry_bar,
                setup_id=setup_id,
                entry_time=entry_time or position.opened_at,
            )
        if len(completed_bars) >= self.settings.ema_period:
            try:
                decision = self.exits.evaluate(position, completed_bars, bar.timestamp)
            except ValueError:
                decision = None
            if decision is not None and decision.should_exit:
                return self._close(
                    position,
                    bar,
                    bar.close,
                    decision.reason,
                    False,
                    setup_id=setup_id,
                    entry_time=entry_time or position.opened_at,
                )
        return None

    def _close(
        self,
        position: PositionRecord,
        bar: Bar,
        reference_price: Decimal,
        reason: str,
        ambiguous: bool,
        *,
        setup_id: str,
        entry_time: datetime,
    ) -> BacktestTrade:
        exit_price = reference_price * (
            Decimal("1") - self.settings.backtest_exit_slippage_bps / Decimal("10000")
        )
        pnl = (
            (exit_price - position.average_entry_price) * Decimal(position.quantity)
            - self.settings.commission_per_trade
        )
        r_multiple = pnl / position.initial_risk if position.initial_risk > 0 else Decimal("0")
        return BacktestTrade(
            trade_id=position.trade_id,
            symbol=position.symbol,
            entry_time=entry_time,
            entry_price=position.average_entry_price,
            quantity=position.quantity,
            stop_price=position.stop_price,
            target_price=position.target_price,
            initial_risk=position.initial_risk,
            exit_time=bar.timestamp,
            exit_price=exit_price,
            exit_reason=reason,
            realized_pnl=pnl,
            r_multiple=r_multiple,
            ambiguous_same_bar=ambiguous,
            setup_id=setup_id,
            config_version=self.settings.version,
            parameter_profile=self.settings.parameter_profile,
            decision_register_version=self.settings.decision_register_version,
        )

    @staticmethod
    def _validate_bars(bars: Sequence[Bar]) -> None:
        if not bars:
            raise ValueError("Backtest requires bars.")
        if len({bar.symbol for bar in bars}) != 1:
            raise ValueError("Run one pre-screened symbol per BacktestEngine invocation.")
        timestamps = [bar.timestamp for bar in bars]
        if timestamps != sorted(timestamps):
            raise ValueError("Backtest bars must be chronological.")
        if len(set(timestamps)) != len(timestamps):
            raise ValueError("Backtest bars cannot have duplicate timestamps.")

    @staticmethod
    def _validate_trades(
        trades: Sequence[TradeTick], *, symbol: str
    ) -> None:
        if any(trade.symbol != symbol for trade in trades):
            raise ValueError("Execution trades must match the backtest symbol.")
        timestamps = [trade.timestamp for trade in trades]
        if timestamps != sorted(timestamps):
            raise ValueError("Execution trades must be chronological.")


def load_bars_csv(path: Path, default_timezone: str = "America/New_York") -> list[Bar]:
    """Load timestamp,symbol,open,high,low,close,volume CSV data."""

    with path.open("r", encoding="utf-8", newline="") as stream:
        return load_bars_csv_stream(stream, default_timezone)


def load_bars_csv_stream(
    stream: IO[str], default_timezone: str = "America/New_York"
) -> list[Bar]:
    """Load backtest bars from an open text stream (including GUI uploads)."""

    bars: list[Bar] = []
    timezone = ZoneInfo(default_timezone)
    reader = csv.DictReader(stream)
    required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
    if not required.issubset(reader.fieldnames or []):
        missing = sorted(required - set(reader.fieldnames or []))
        raise ValueError(f"Backtest CSV is missing columns: {', '.join(missing)}")
    for row in reader:
        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone)
        bars.append(
            Bar(
                symbol=row["symbol"].strip().upper(),
                timestamp=timestamp,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=int(row["volume"]),
            )
        )
    return bars


def trade_rows(result: BacktestResult) -> list[dict]:
    return [asdict(trade) for trade in result.trades]
