"""Conservative completed-bar backtester for one pre-screened symbol at a time."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, IO, Optional, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.exits import ExitManager
from bot.models import Bar, PositionRecord, Quote, RiskSnapshot
from bot.risk_manager import RiskManager
from bot.strategy import BullFlagStrategy


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
        self.risk = RiskManager(settings, self.diagnostics)
        self.exits = ExitManager(settings, self.diagnostics)

    def run(
        self,
        bars: Sequence[Bar],
        *,
        allowed_decision_times: set[datetime] | None = None,
    ) -> BacktestResult:
        self._validate_bars(bars)
        symbol = bars[0].symbol
        eastern = ZoneInfo(self.settings.timezone)
        by_date: dict[object, list[Bar]] = {}
        for bar in bars:
            by_date.setdefault(bar.timestamp.astimezone(eastern).date(), []).append(bar)

        trades: list[BacktestTrade] = []
        rejected = 0
        opportunities = 0
        ambiguous_count = 0
        rejection_details: list[dict[str, Any]] = []
        for session_bars in by_date.values():
            session_trades, session_rejected, session_opportunities, session_details = self._run_session(
                session_bars,
                allowed_decision_times=allowed_decision_times,
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
                "One-minute bars use conservative stop-first resolution when sequence is unknown.",
                "Scanner-universe survivorship and point-in-time symbol membership require a separate dataset audit.",
            ],
        )

    def _run_session(
        self,
        bars: Sequence[Bar],
        *,
        allowed_decision_times: set[datetime] | None = None,
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
            if event_bar.high < setup.trigger_price:
                rejection_details.append(
                    {
                        "symbol": event_bar.symbol,
                        "timestamp": decision_time,
                        "stage": "entry_trigger",
                        "reason": "Breakout trigger was not reached on the next bar.",
                        "actual_values": {"next_bar_high": event_bar.high},
                        "expected_values": {"minimum_high": setup.trigger_price},
                    }
                )
                continue

            opportunities += 1
            assumed_ask = max(setup.trigger_price, event_bar.open)
            assumed_spread = min(self.settings.max_spread_percent / Decimal("2"), Decimal("0.001"))
            assumed_bid = assumed_ask * (Decimal("1") - assumed_spread)
            quote = Quote(
                symbol=event_bar.symbol,
                timestamp=event_bar.timestamp,
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
                event_bar.timestamp,
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
                opened_at=event_bar.timestamp,
            )
            entry_setup_id = setup.setup_id
            entry_time = event_bar.timestamp

            # Entire-bar OHLC cannot reveal whether the low occurred before the trigger.
            closed = self._maybe_close_position(
                position,
                event_bar,
                available + [event_bar],
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
