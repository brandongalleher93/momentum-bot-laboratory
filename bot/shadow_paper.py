"""Real-time shadow-paper simulation with no broker order boundary."""

from __future__ import annotations

import sqlite3
import time as clock_time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Protocol, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from bot.config import Settings, validate_settings
from bot.diagnostics import DiagnosticFactory
from bot.event_log import DiagnosticLogger, JsonlEventLog
from bot.exits import ExitManager
from bot.history_store import HistoryStore
from bot.models import (
    Bar,
    MarketSnapshot,
    PositionRecord,
    Quote,
    RiskSnapshot,
    Setup,
)
from bot.risk_manager import RiskManager
from bot.scanner import MarketScanner
from bot.strategy import BullFlagStrategy, MicroPullbackStrategy


def shadow_paper_settings(settings: Settings) -> Settings:
    """Apply the frozen premarket validation profile to shadow mode only."""

    return replace(
        settings,
        trade_window_start=time(7, 0),
        preferred_pullback_depth=Decimal("0.50"),
        large_upper_wick_ratio=Decimal("0.50"),
        near_high_of_day_percent=Decimal("0.10"),
        max_extension_above_vwap_percent=Decimal("0.25"),
        max_extension_above_ema_percent=Decimal("0.10"),
        parameter_profile=(
            f"{settings.parameter_profile}:shadow_premarket_validation_v1"
        ),
    )


class ShadowMarketData(Protocol):
    def get_scanner_snapshots(self, now: datetime) -> list[MarketSnapshot]: ...

    def get_completed_bars(
        self, symbol: str, end: datetime, lookback_days: int = 2
    ) -> list[Bar]: ...

    def get_completed_ten_second_bars(
        self, symbol: str, end: datetime, lookback_minutes: int = 30
    ) -> list[Bar]: ...

    def get_latest_quote(self, symbol: str) -> Quote: ...


@dataclass(frozen=True)
class ShadowTrade:
    trade_id: str
    symbol: str
    setup_id: str
    entry_time: datetime
    entry_price: Decimal
    quantity: int
    stop_price: Decimal
    target_price: Decimal
    breakout_level: Decimal
    initial_risk: Decimal
    status: str
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_reason: str | None = None
    realized_pnl: Decimal | None = None
    r_multiple: Decimal | None = None

    def position(self) -> PositionRecord:
        return PositionRecord(
            trade_id=self.trade_id,
            symbol=self.symbol,
            quantity=self.quantity,
            average_entry_price=self.entry_price,
            stop_price=self.stop_price,
            target_price=self.target_price,
            breakout_level=self.breakout_level,
            initial_risk=self.initial_risk,
            opened_at=self.entry_time,
        )


class ShadowTradeStore:
    """SQLite ledger that survives shadow-process restarts."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS shadow_trades (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    setup_id TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    stop_price TEXT NOT NULL,
                    target_price TEXT NOT NULL,
                    breakout_level TEXT NOT NULL,
                    initial_risk TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open','closed')),
                    exit_time TEXT,
                    exit_price TEXT,
                    exit_reason TEXT,
                    realized_pnl TEXT,
                    r_multiple TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_shadow_status
                ON shadow_trades(status, entry_time);
                """
            )

    def record_entry(self, trade: ShadowTrade) -> None:
        if trade.status != "open":
            raise ValueError("A new shadow trade must be open.")
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO shadow_trades (
                    trade_id,symbol,setup_id,entry_time,entry_price,quantity,
                    stop_price,target_price,breakout_level,initial_risk,status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trade.trade_id,
                    trade.symbol,
                    trade.setup_id,
                    trade.entry_time.isoformat(),
                    str(trade.entry_price),
                    trade.quantity,
                    str(trade.stop_price),
                    str(trade.target_price),
                    str(trade.breakout_level),
                    str(trade.initial_risk),
                    trade.status,
                ),
            )

    def record_exit(
        self,
        trade_id: str,
        *,
        exit_time: datetime,
        exit_price: Decimal,
        reason: str,
    ) -> ShadowTrade:
        trade = self.get(trade_id)
        if trade is None or trade.status != "open":
            raise ValueError("Only an open shadow trade can be closed.")
        pnl = (exit_price - trade.entry_price) * Decimal(trade.quantity)
        r_multiple = (
            pnl / trade.initial_risk
            if trade.initial_risk > 0
            else Decimal("0")
        )
        with self.connect() as db:
            db.execute(
                """
                UPDATE shadow_trades
                SET status='closed',exit_time=?,exit_price=?,exit_reason=?,
                    realized_pnl=?,r_multiple=?
                WHERE trade_id=? AND status='open'
                """,
                (
                    exit_time.isoformat(),
                    str(exit_price),
                    reason,
                    str(pnl),
                    str(r_multiple),
                    trade_id,
                ),
            )
        closed = self.get(trade_id)
        if closed is None:
            raise RuntimeError("Closed shadow trade could not be restored.")
        return closed

    def get(self, trade_id: str) -> ShadowTrade | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM shadow_trades WHERE trade_id=?", (trade_id,)
            ).fetchone()
        return self._trade(row) if row is not None else None

    def open_trades(self) -> list[ShadowTrade]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM shadow_trades WHERE status='open' "
                "ORDER BY entry_time"
            ).fetchall()
        return [self._trade(row) for row in rows]

    def all_trades(self) -> list[ShadowTrade]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM shadow_trades ORDER BY entry_time"
            ).fetchall()
        return [self._trade(row) for row in rows]

    def risk_snapshot(
        self,
        symbol: str,
        session_date: date,
        timezone_name: str,
    ) -> RiskSnapshot:
        zone = ZoneInfo(timezone_name)
        trades = [
            trade
            for trade in self.all_trades()
            if trade.entry_time.astimezone(zone).date() == session_date
        ]
        closed = sorted(
            (
                trade
                for trade in trades
                if trade.status == "closed" and trade.exit_time is not None
            ),
            key=lambda trade: trade.exit_time or trade.entry_time,
        )
        realized_loss = sum(
            (
                abs(trade.realized_pnl or Decimal("0"))
                for trade in closed
                if (trade.realized_pnl or Decimal("0")) < 0
            ),
            Decimal("0"),
        )
        consecutive_losses = 0
        symbol_consecutive_losses = 0
        for trade in closed:
            if (trade.realized_pnl or Decimal("0")) < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0
            if trade.symbol == symbol.upper():
                if (trade.realized_pnl or Decimal("0")) < 0:
                    symbol_consecutive_losses += 1
                else:
                    symbol_consecutive_losses = 0
        open_trades = self.open_trades()
        open_stop_risk = sum(
            (
                Decimal(trade.quantity)
                * max(
                    trade.entry_price - trade.stop_price,
                    Decimal("0"),
                )
                for trade in open_trades
            ),
            Decimal("0"),
        )
        return RiskSnapshot(
            realized_loss=realized_loss,
            open_stop_risk=open_stop_risk,
            pending_order_risk=Decimal("0"),
            trades_taken_today=len(trades),
            consecutive_losses=consecutive_losses,
            symbol_consecutive_losses=symbol_consecutive_losses,
            open_positions=len(open_trades),
            active_entry_orders=0,
        )

    def summary(self) -> dict:
        trades = self.all_trades()
        closed = [trade for trade in trades if trade.status == "closed"]
        net = sum(
            (trade.realized_pnl or Decimal("0") for trade in closed),
            Decimal("0"),
        )
        return {
            "total_trades": len(trades),
            "open_trades": sum(1 for trade in trades if trade.status == "open"),
            "closed_trades": len(closed),
            "winning_trades": sum(
                1 for trade in closed if (trade.realized_pnl or 0) > 0
            ),
            "losing_trades": sum(
                1 for trade in closed if (trade.realized_pnl or 0) < 0
            ),
            "net_profit": net,
        }

    @staticmethod
    def _trade(row: sqlite3.Row) -> ShadowTrade:
        return ShadowTrade(
            trade_id=row["trade_id"],
            symbol=row["symbol"],
            setup_id=row["setup_id"],
            entry_time=datetime.fromisoformat(row["entry_time"]),
            entry_price=Decimal(row["entry_price"]),
            quantity=int(row["quantity"]),
            stop_price=Decimal(row["stop_price"]),
            target_price=Decimal(row["target_price"]),
            breakout_level=Decimal(row["breakout_level"]),
            initial_risk=Decimal(row["initial_risk"]),
            status=row["status"],
            exit_time=(
                datetime.fromisoformat(row["exit_time"])
                if row["exit_time"]
                else None
            ),
            exit_price=(
                Decimal(row["exit_price"]) if row["exit_price"] else None
            ),
            exit_reason=row["exit_reason"],
            realized_pnl=(
                Decimal(row["realized_pnl"])
                if row["realized_pnl"] is not None
                else None
            ),
            r_multiple=(
                Decimal(row["r_multiple"])
                if row["r_multiple"] is not None
                else None
            ),
        )


class ShadowPaperEngine:
    """Forward-test the strategy locally while broker submission stays disarmed."""

    def __init__(
        self,
        settings: Settings,
        market_data: ShadowMarketData,
        *,
        store: ShadowTradeStore | None = None,
    ):
        validate_settings(settings, require_alpaca_keys=True)
        if settings.paper_order_submission_enabled:
            raise ValueError(
                "Shadow mode requires PAPER_ORDER_SUBMISSION_ENABLED=false."
            )
        if not settings.alpaca_paper or settings.allow_live_trading:
            raise ValueError("Shadow mode requires verified paper-only settings.")
        self.settings = settings
        self.market_data = market_data
        self.store = store or ShadowTradeStore(
            settings.output_dir / "shadow_paper" / "shadow_trades.sqlite3"
        )
        self.diagnostics = DiagnosticFactory(settings)
        self.logger = DiagnosticLogger(settings.log_dir)
        self.events = JsonlEventLog(
            settings.output_dir / "shadow_paper" / "events.jsonl"
        )
        self.history = HistoryStore(
            settings.output_dir / "history" / "history.sqlite3"
        )
        self.scanner = MarketScanner(settings, self.diagnostics)
        self.strategy = BullFlagStrategy(settings, self.diagnostics)
        self.micro_strategy = MicroPullbackStrategy(
            settings, self.diagnostics
        )
        self.risk = RiskManager(settings, self.diagnostics)
        self.exits = ExitManager(settings, self.diagnostics)
        self._armed_setups: dict[str, Setup] = {}
        self._last_scan_bucket: datetime | None = None

    def run_once(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Shadow cycle time must be timezone-aware.")
        closed = self._manage_open_trades(now)
        local = now.astimezone(ZoneInfo(self.settings.timezone))
        inside_window = (
            local.weekday() < 5
            and self.settings.trade_window_start
            <= local.time()
            <= self.settings.trade_window_end
        )
        if not inside_window:
            self._armed_setups.clear()
            result = {
                "status": "outside_entry_window",
                "timestamp": now,
                "entries": 0,
                "exits": closed,
                "summary": self.store.summary(),
            }
            self.events.append({"event": "shadow_cycle", **result})
            return result

        entries = self._process_armed_setups(now)
        bucket = now.replace(
            second=(now.second // 10) * 10, microsecond=0
        )
        if bucket != self._last_scan_bucket:
            self._last_scan_bucket = bucket
            snapshots = self.market_data.get_scanner_snapshots(now)
            outcome = self.scanner.scan(snapshots)
            self.history.record_scan(
                snapshots, outcome.diagnostics, source="captured"
            )
            for diagnostic in outcome.diagnostics:
                self.logger.log(diagnostic)
            for candidate in outcome.candidates:
                if any(
                    trade.symbol == candidate.symbol
                    for trade in self.store.open_trades()
                ):
                    continue
                setup = self._detect_setup(candidate.symbol, now)
                if setup is not None:
                    self._armed_setups[candidate.symbol] = setup
            entries += self._process_armed_setups(now)

        result = {
            "status": "completed",
            "timestamp": now,
            "entries": entries,
            "exits": closed,
            "armed_setups": len(self._armed_setups),
            "summary": self.store.summary(),
        }
        self.events.append({"event": "shadow_cycle", **result})
        return result

    def run_forever(self) -> None:
        zone = ZoneInfo(self.settings.timezone)
        while True:
            now = datetime.now(timezone.utc)
            local = now.astimezone(zone)
            if local.weekday() >= 5 or local.time() > self.settings.trade_window_end:
                self.run_once(now)
                return
            if local.time() >= self.settings.trade_window_start:
                self.run_once(now)
            clock_time.sleep(self.settings.poll_seconds)

    def _detect_setup(self, symbol: str, now: datetime) -> Setup | None:
        execution_bars = self.market_data.get_completed_ten_second_bars(
            symbol, now
        )
        if len(execution_bars) >= 4:
            micro = self.micro_strategy.detect_setup(
                symbol, execution_bars, now
            )
            self.logger.log(micro.diagnostic)
            if micro.value is not None:
                return micro.value
        bars = self._latest_session_bars(
            self.market_data.get_completed_bars(
                symbol, now, lookback_days=2
            ),
            now,
        )
        standard = self.strategy.detect_setup(symbol, bars, now)
        self.logger.log(standard.diagnostic)
        return standard.value

    def _process_armed_setups(self, now: datetime) -> int:
        entries = 0
        local_date = now.astimezone(
            ZoneInfo(self.settings.timezone)
        ).date()
        for symbol, setup in list(self._armed_setups.items()):
            if now > setup.expires_at:
                del self._armed_setups[symbol]
                continue
            quote = self.market_data.get_latest_quote(symbol)
            if not self._quote_is_current(quote, now):
                continue
            entry = self.strategy.check_entry(setup, quote)
            self.logger.log(entry.diagnostic)
            if entry.value is None or not entry.value.triggered:
                continue
            plan_result = self.strategy.create_trade_plan(setup, entry.value)
            self.logger.log(plan_result.diagnostic)
            if plan_result.value is None:
                del self._armed_setups[symbol]
                continue
            plan = plan_result.value
            snapshot = self.store.risk_snapshot(
                symbol, local_date, self.settings.timezone
            )
            risk = self.risk.evaluate(plan, snapshot, quote.timestamp)
            self.logger.log(risk.diagnostic)
            if not risk.approval.approved:
                del self._armed_setups[symbol]
                continue
            if quote.ask > plan.maximum_entry_price:
                del self._armed_setups[symbol]
                continue
            actual_risk_per_share = quote.ask - plan.stop_price
            if actual_risk_per_share <= 0:
                del self._armed_setups[symbol]
                continue
            target = quote.ask + (
                self.settings.target_r_multiple * actual_risk_per_share
            )
            trade = ShadowTrade(
                trade_id=str(uuid4()),
                symbol=symbol,
                setup_id=setup.setup_id,
                entry_time=quote.timestamp,
                entry_price=quote.ask,
                quantity=risk.approval.quantity,
                stop_price=plan.stop_price,
                target_price=target,
                breakout_level=plan.breakout_level,
                initial_risk=(
                    Decimal(risk.approval.quantity)
                    * actual_risk_per_share
                ),
                status="open",
            )
            self.store.record_entry(trade)
            self.events.append({"event": "shadow_entry", "trade": trade})
            del self._armed_setups[symbol]
            entries += 1
            if len(self.store.open_trades()) >= self.settings.max_open_positions:
                break
        return entries

    def _manage_open_trades(self, now: datetime) -> int:
        closed_count = 0
        zone = ZoneInfo(self.settings.timezone)
        for trade in self.store.open_trades():
            quote = self.market_data.get_latest_quote(trade.symbol)
            if not self._quote_is_current(quote, now):
                self.events.append(
                    {
                        "event": "shadow_stale_quote",
                        "symbol": trade.symbol,
                        "quote_timestamp": quote.timestamp,
                        "cycle_timestamp": now,
                    }
                )
                continue
            reason = None
            price = quote.bid
            if quote.bid <= trade.stop_price:
                reason = "Shadow protective stop reached."
            elif quote.bid >= trade.target_price:
                reason = "Shadow 2R target reached."
                price = trade.target_price
            else:
                bars = self._latest_session_bars(
                    self.market_data.get_completed_bars(
                        trade.symbol, now, lookback_days=2
                    ),
                    now,
                )
                if len(bars) >= self.settings.ema_period:
                    position = trade.position()
                    decision = self.exits.evaluate(position, bars, now)
                    self.logger.log(decision.diagnostic)
                    if decision.should_exit:
                        reason = decision.reason
                if (
                    reason is None
                    and self.settings.flat_by_end_of_day
                    and now.astimezone(zone).time()
                    > self.settings.trade_window_end
                ):
                    reason = "Shadow end-of-window flat policy."
            if reason is None:
                continue
            closed = self.store.record_exit(
                trade.trade_id,
                exit_time=quote.timestamp,
                exit_price=price,
                reason=reason,
            )
            self.events.append({"event": "shadow_exit", "trade": closed})
            closed_count += 1
        return closed_count

    def _latest_session_bars(
        self, bars: Sequence[Bar], now: datetime
    ) -> list[Bar]:
        zone = ZoneInfo(self.settings.timezone)
        session_date = now.astimezone(zone).date()
        return [
            bar
            for bar in bars
            if bar.timestamp.astimezone(zone).date() == session_date
            and bar.timestamp <= now
        ]

    @staticmethod
    def _quote_is_current(quote: Quote, now: datetime) -> bool:
        if quote.timestamp.tzinfo is None:
            return False
        age = now - quote.timestamp.astimezone(now.tzinfo)
        return timedelta(seconds=-2) <= age <= timedelta(seconds=30)
