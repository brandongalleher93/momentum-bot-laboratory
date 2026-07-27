"""Polling orchestration for Alpaca paper trading.

Core decisions are delegated to pure modules. This layer performs I/O and is
deliberately disarmed unless PAPER_ORDER_SUBMISSION_ENABLED=true.
"""

from __future__ import annotations

import time as clock_time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol, Sequence
from zoneinfo import ZoneInfo

from bot.broker import PaperBroker
from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.event_log import DiagnosticLogger, JsonlEventLog
from bot.history_store import HistoryStore
from bot.execution import ExecutionService
from bot.exits import ExitManager
from bot.models import Bar, MarketSnapshot, Quote, Setup
from bot.risk_manager import RiskLedger, RiskManager
from bot.scanner import MarketScanner
from bot.session import SessionState
from bot.strategy import BullFlagStrategy


class MarketData(Protocol):
    def get_scanner_snapshots(self, now: datetime) -> list[MarketSnapshot]: ...

    def get_completed_bars(
        self, symbol: str, end: datetime, lookback_days: int = 2
    ) -> list[Bar]: ...

    def get_latest_quote(self, symbol: str) -> Quote: ...

    def get_completed_ten_second_bars(
        self, symbol: str, end: datetime, lookback_minutes: int = 30
    ) -> list[Bar]: ...


class TradingBot:
    def __init__(self, settings: Settings, broker: PaperBroker, market_data: MarketData):
        self.settings = settings
        self.broker = broker
        self.market_data = market_data
        self.diagnostics = DiagnosticFactory(settings)
        self.logger = DiagnosticLogger(settings.log_dir)
        self.state = SessionState()
        self.ledger = RiskLedger()
        self.scanner = MarketScanner(settings, self.diagnostics)
        self.strategy = BullFlagStrategy(settings, self.diagnostics)
        self.risk = RiskManager(settings, self.diagnostics)
        self.exits = ExitManager(settings, self.diagnostics)
        self.execution = ExecutionService(
            settings=settings,
            broker=broker,
            state=self.state,
            ledger=self.ledger,
            diagnostics=self.diagnostics,
            diagnostic_logger=self.logger,
        )
        self.run_metadata = JsonlEventLog(settings.log_dir / "config_change_history.jsonl")
        self.position_events = JsonlEventLog(settings.log_dir / "position_events.jsonl")
        self.history = HistoryStore(settings.output_dir / "history" / "history.sqlite3")
        self._started = False
        self._last_scan_minute: datetime | None = None
        self._armed_setups: dict[str, Setup] = {}

    def startup(self) -> None:
        account = self.broker.get_account()
        orders = list(self.broker.get_open_orders())
        positions = list(self.broker.get_positions())
        self.run_metadata.append(
            {
                "event": "run_started",
                "timestamp": datetime.now(timezone.utc),
                "config": self.settings.snapshot(),
                "account_id": account.account_id,
                "recovered_orders": [order.broker_order_id for order in orders],
                "recovered_positions": [position.symbol for position in positions],
            }
        )
        if account.status.lower() != "active" or account.trading_blocked:
            raise RuntimeError("Alpaca paper account is not active for trading.")
        if orders or positions:
            self.state.halt_entries(
                "Broker has pre-existing state; automated reconstruction is intentionally conservative."
            )
            event_time = datetime.now(timezone.utc)
            diagnostic = self.diagnostics.critical(
                module="startup",
                symbol=None,
                reason="Pre-existing broker orders/positions require reconciliation review.",
                decision_time=event_time,
                data_cutoff_time=event_time,
                actual_values={
                    "orders": [order.broker_order_id for order in orders],
                    "positions": [position.symbol for position in positions],
                },
            )
        else:
            now = datetime.now(timezone.utc)
            diagnostic = self.diagnostics.passed(
                module="startup",
                symbol=None,
                reason="Paper bot startup and broker reconciliation completed.",
                decision_time=now,
                data_cutoff_time=now,
                actual_values={"paper": self.settings.alpaca_paper},
                expected_values={"paper": True},
            )
        self.logger.log(diagnostic)
        self._started = True

    def run_once(self) -> None:
        if not self._started:
            self.startup()
        now = datetime.now(timezone.utc)

        recent_orders = list(self.broker.get_recent_orders())
        for broker_order in recent_orders:
            if self.state.find_order(
                broker_order_id=broker_order.broker_order_id,
                client_order_id=broker_order.client_order_id,
            ) is not None:
                self.execution.process_broker_order(broker_order)
        self._reconcile_positions(recent_orders, now)
        self._monitor_positions(now)

        if not self.state.new_entries_enabled or not self._inside_entry_window(now):
            self._armed_setups.clear()
            return
        if self.state.realized_loss >= self.settings.max_daily_loss:
            self.state.halt_entries("Daily realized-loss limit reached.")
            return

        if self._process_armed_setups(now):
            return

        scan_minute = now.replace(second=0, microsecond=0)
        if self._last_scan_minute == scan_minute:
            return
        self._last_scan_minute = scan_minute

        scanner_snapshots = self.market_data.get_scanner_snapshots(now)
        outcome = self.scanner.scan(scanner_snapshots)
        self.history.record_scan(
            scanner_snapshots, outcome.diagnostics, source="captured"
        )
        for diagnostic in outcome.diagnostics:
            self.logger.log(diagnostic)

        for candidate in outcome.candidates:
            if self.state.risk_snapshot().active_entry_orders >= self.settings.max_active_entry_orders:
                break
            bars = self._latest_session_bars(
                self.market_data.get_completed_bars(candidate.symbol, now), now
            )
            setup_result = self.strategy.detect_setup(candidate.symbol, bars, now)
            self.logger.log(setup_result.diagnostic)
            if setup_result.value is None:
                continue
            self._armed_setups[candidate.symbol] = setup_result.value

        self._process_armed_setups(now)

    def _process_armed_setups(self, now: datetime) -> bool:
        if not self._inside_entry_window(now):
            self._armed_setups.clear()
            return False
        for symbol, setup in list(self._armed_setups.items()):
            if now > setup.expires_at:
                del self._armed_setups[symbol]
                continue
            entry_result = self.strategy.check_entry(
                setup, self.market_data.get_latest_quote(symbol)
            )
            self.logger.log(entry_result.diagnostic)
            if entry_result.value is None or not entry_result.value.triggered:
                continue

            plan_result = self.strategy.create_trade_plan(
                setup, entry_result.value
            )
            self.logger.log(plan_result.diagnostic)
            if plan_result.value is None:
                continue

            risk_result = self.risk.evaluate(
                plan_result.value,
                self.state.risk_snapshot(
                    symbol=plan_result.value.symbol,
                    session_date=now.astimezone(
                        ZoneInfo(self.settings.timezone)
                    ).date(),
                ),
                now,
            )
            self.logger.log(risk_result.diagnostic)
            if not risk_result.approval.approved:
                del self._armed_setups[symbol]
                continue
            self.execution.submit(plan_result.value, risk_result.approval, now)
            del self._armed_setups[symbol]
            return True
        return False

    def run_forever(self) -> None:
        self.startup()
        while self.broker.market_is_open():
            self.run_once()
            clock_time.sleep(self.settings.poll_seconds)

    def _monitor_positions(self, now: datetime) -> None:
        for trade_id, position in list(self.state.positions.items()):
            bars = self._latest_session_bars(
                self.market_data.get_completed_bars(position.symbol, now), now
            )
            if len(bars) < self.settings.ema_period:
                continue
            decision = self.exits.evaluate(position, bars, now)
            self.logger.log(decision.diagnostic)
            position.last_exit_evaluated_bar = bars[-1].timestamp
            if decision.should_exit:
                self.execution.request_indicator_exit(trade_id, decision.reason, now)

    def _reconcile_positions(self, recent_orders, now: datetime) -> None:
        broker_positions = {value.symbol: value for value in self.broker.get_positions()}
        unrealized_loss = Decimal("0")
        for trade_id, local in list(self.state.positions.items()):
            broker_position = broker_positions.get(local.symbol)
            if broker_position is not None:
                local.quantity = broker_position.quantity
                unrealized_loss += max(
                    (local.average_entry_price - broker_position.current_price)
                    * Decimal(local.quantity),
                    Decimal("0"),
                )
                continue

            exit_candidates = [
                order
                for order in recent_orders
                if order.symbol == local.symbol
                and order.exit_fill_price is not None
                and order.exit_fill_quantity > 0
            ]
            if not exit_candidates:
                self.state.halt_entries(
                    f"Broker position {local.symbol} disappeared without exit-fill evidence."
                )
                continue
            exit_order = max(
                exit_candidates,
                key=lambda order: order.exit_filled_at or order.submitted_at,
            )
            quantity = min(local.quantity, exit_order.exit_fill_quantity)
            realized_pnl = (
                exit_order.exit_fill_price - local.average_entry_price
            ) * Decimal(quantity)
            closed_at = exit_order.exit_filled_at or now
            self.state.record_closed_pnl(
                realized_pnl,
                symbol=local.symbol,
                session_date=closed_at.astimezone(
                    ZoneInfo(self.settings.timezone)
                ).date(),
            )
            self.position_events.append(
                {
                    "event": "position_closed",
                    "trade_id": trade_id,
                    "symbol": local.symbol,
                    "quantity": quantity,
                    "entry_price": local.average_entry_price,
                    "exit_price": exit_order.exit_fill_price,
                    "realized_pnl": realized_pnl,
                    "exit_order_id": exit_order.exit_order_id,
                    "timestamp": exit_order.exit_filled_at or now,
                }
            )
            del self.state.positions[trade_id]

        self.state.unrealized_loss = unrealized_loss
        if (
            self.settings.daily_equity_drawdown_limit is not None
            and unrealized_loss >= self.settings.daily_equity_drawdown_limit
        ):
            self.state.halt_entries("Configured unrealized drawdown limit reached.")

    def _inside_entry_window(self, value: datetime) -> bool:
        local = value.astimezone(ZoneInfo(self.settings.timezone)).time()
        return self.settings.trade_window_start <= local <= self.settings.trade_window_end

    def _latest_session_bars(
        self, bars: Sequence[Bar], now: datetime
    ) -> list[Bar]:
        eastern = ZoneInfo(self.settings.timezone)
        session_date = now.astimezone(eastern).date()
        return [
            bar for bar in bars if bar.timestamp.astimezone(eastern).date() == session_date
        ]
