import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.models import Bar, Flagpole, MarketSnapshot, Pullback, Quote, Setup
from bot.shadow_paper import (
    ShadowPaperEngine,
    ShadowTrade,
    ShadowTradeStore,
    shadow_paper_settings,
)


class FakeShadowMarketData:
    def __init__(self, now, *, quote=None):
        self.now = now
        self.quote = quote or Quote(
            "TEST", now, Decimal("5.01"), Decimal("5.02")
        )
        self.scanner_calls = 0
        self.quote_calls = 0

    def get_scanner_snapshots(self, now):
        self.scanner_calls += 1
        return [
            MarketSnapshot(
                "TEST",
                now,
                Decimal("5.02"),
                Decimal("0.20"),
                Decimal("6"),
                600_000,
                Decimal("5.01"),
                Decimal("5.02"),
            )
        ]

    def get_completed_bars(self, symbol, end, lookback_days=2):
        return []

    def get_completed_ten_second_bars(
        self, symbol, end, lookback_minutes=30
    ):
        return []

    def get_latest_quote(self, symbol):
        self.quote_calls += 1
        if isinstance(self.quote, Exception):
            raise self.quote
        return self.quote


def make_setup(now):
    bars = (
        Bar(
            "TEST",
            now - timedelta(seconds=40),
            Decimal("4.80"),
            Decimal("5.00"),
            Decimal("4.80"),
            Decimal("5.00"),
            1000,
        ),
    )
    pullback_bars = (
        Bar(
            "TEST",
            now - timedelta(seconds=20),
            Decimal("5.00"),
            Decimal("5.00"),
            Decimal("4.90"),
            Decimal("4.95"),
            400,
        ),
    )
    return Setup(
        setup_id="setup",
        symbol="TEST",
        flagpole=Flagpole(
            bars=bars,
            low=Decimal("4.80"),
            high=Decimal("5.00"),
            percent_move=Decimal("0.0417"),
            green_candle_count=1,
            average_volume=Decimal("1000"),
            average_range=Decimal("0.20"),
            volume_ratio=Decimal("2"),
        ),
        pullback=Pullback(
            bars=pullback_bars,
            low=Decimal("4.90"),
            high=Decimal("5.00"),
            depth=Decimal("0.50"),
            volume_ratio=Decimal("0.40"),
            largest_upper_wick_ratio=Decimal("0"),
        ),
        breakout_level=Decimal("5.00"),
        trigger_price=Decimal("5.01"),
        stop_price=Decimal("4.90"),
        created_at=now,
        data_cutoff_time=now - timedelta(seconds=10),
        expires_at=now + timedelta(seconds=20),
    )


def make_trade(now, trade_id="trade"):
    return ShadowTrade(
        trade_id=trade_id,
        symbol="TEST",
        setup_id="setup",
        entry_time=now,
        entry_price=Decimal("5.02"),
        quantity=10,
        stop_price=Decimal("4.90"),
        target_price=Decimal("5.26"),
        breakout_level=Decimal("5.00"),
        initial_risk=Decimal("1.20"),
        status="open",
    )


def read_events(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class ShadowPaperTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("America/New_York")
        self.now = datetime(2026, 7, 27, 7, 30, tzinfo=self.zone)

    def settings(self, directory, **overrides):
        return replace(
            Settings(),
            alpaca_api_key="paper-key",
            alpaca_secret_key="paper-secret",
            trade_window_start=self.now.time(),
            log_dir=Path(directory) / "logs",
            output_dir=Path(directory) / "output",
            **overrides,
        )

    def test_shadow_profile_is_isolated_from_base_paper_settings(self):
        original = Settings()
        shadow = shadow_paper_settings(original)

        self.assertEqual(original.trade_window_start, time(9, 30))
        self.assertEqual(shadow.trade_window_start, time(7, 0))
        self.assertEqual(
            shadow.preferred_pullback_depth, Decimal("0.50")
        )
        self.assertIn(
            "shadow_premarket_validation", shadow.parameter_profile
        )

    def test_shadow_store_persists_and_reconstructs_symbol_loss_streak(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shadow.sqlite3"
            store = ShadowTradeStore(path)
            first = make_trade(self.now, "first")
            second = replace(
                make_trade(self.now + timedelta(minutes=1), "second"),
                entry_price=Decimal("5.10"),
                stop_price=Decimal("5.00"),
                target_price=Decimal("5.30"),
                initial_risk=Decimal("1.00"),
            )
            store.record_entry(first)
            store.record_exit(
                first.trade_id,
                exit_time=self.now + timedelta(seconds=30),
                exit_price=Decimal("4.90"),
                reason="stop",
            )
            store.record_entry(second)
            store.record_exit(
                second.trade_id,
                exit_time=self.now + timedelta(minutes=1, seconds=30),
                exit_price=Decimal("5.00"),
                reason="stop",
            )

            restored = ShadowTradeStore(path)
            snapshot = restored.risk_snapshot(
                "TEST", self.now.date(), "America/New_York"
            )

            self.assertEqual(snapshot.trades_taken_today, 2)
            self.assertEqual(snapshot.symbol_consecutive_losses, 2)
            self.assertEqual(snapshot.realized_loss, Decimal("2.20"))
            self.assertEqual(restored.summary()["losing_trades"], 2)

    def test_shadow_engine_refuses_to_run_when_broker_orders_are_armed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "requires"):
                ShadowPaperEngine(
                    self.settings(
                        directory, paper_order_submission_enabled=True
                    ),
                    FakeShadowMarketData(self.now),
                )

    def test_shadow_cycle_records_local_entry_without_broker(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            engine = ShadowPaperEngine(
                settings,
                FakeShadowMarketData(self.now),
                store=store,
            )
            engine._detect_setup = lambda symbol, now: make_setup(now)

            result = engine.run_once(self.now)

            self.assertEqual(result["entries"], 1)
            self.assertEqual(
                result["scanner_source"],
                "delayed_sip_full_premarket_universe",
            )
            self.assertIn("-04:00", result["session_timestamp"])
            self.assertEqual(len(store.open_trades()), 1)
            self.assertEqual(
                store.open_trades()[0].entry_price, Decimal("5.02")
            )

    def test_live_cycle_refreshes_decision_time_after_slow_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            clock_value = [self.now]
            quote = Quote(
                "TEST",
                self.now + timedelta(seconds=17),
                Decimal("5.01"),
                Decimal("5.02"),
            )
            data = FakeShadowMarketData(self.now, quote=quote)
            original_scan = data.get_scanner_snapshots

            def slow_scan(now):
                clock_value[0] = self.now + timedelta(seconds=17)
                return original_scan(now)

            data.get_scanner_snapshots = slow_scan
            engine = ShadowPaperEngine(
                settings,
                data,
                store=store,
                clock=lambda: clock_value[0],
            )
            engine._detect_setup = lambda symbol, now: make_setup(now)

            result = engine.run_once()

            self.assertEqual(result["entries"], 1)
            self.assertEqual(len(store.open_trades()), 1)

    def test_shadow_cycle_does_not_enter_on_stale_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            stale_quote = Quote(
                "TEST",
                self.now - timedelta(seconds=17),
                Decimal("5.01"),
                Decimal("5.02"),
            )
            engine = ShadowPaperEngine(
                settings,
                FakeShadowMarketData(self.now, quote=stale_quote),
                store=store,
            )
            engine._detect_setup = lambda symbol, now: make_setup(now)

            result = engine.run_once(self.now)

            self.assertEqual(result["entries"], 0)
            self.assertEqual(store.summary()["total_trades"], 0)
            rejected = [
                event
                for event in read_events(engine.events.path)
                if event["event"] == "shadow_quote_rejected"
            ]
            self.assertEqual(rejected[-1]["stage"], "entry")
            self.assertEqual(
                rejected[-1]["reason"],
                "Entry quote is stale or future-dated.",
            )

    def test_shadow_cycle_rejects_entry_when_bid_is_at_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            quote = Quote(
                "TEST",
                self.now,
                Decimal("5.00"),
                Decimal("5.02"),
            )
            engine = ShadowPaperEngine(
                settings,
                FakeShadowMarketData(self.now, quote=quote),
                store=store,
            )
            engine._detect_setup = lambda symbol, now: replace(
                make_setup(now),
                stop_price=Decimal("5.00"),
            )

            result = engine.run_once(self.now)

            self.assertEqual(result["entries"], 0)
            self.assertEqual(store.summary()["total_trades"], 0)
            rejected = [
                event
                for event in read_events(engine.events.path)
                if event["event"] == "shadow_quote_rejected"
            ]
            self.assertEqual(
                rejected[-1]["reason"],
                "Current bid is at or below the protective stop.",
            )

    def test_bad_symbol_quote_is_logged_without_failing_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            data = FakeShadowMarketData(
                self.now,
                quote=ValueError(
                    "Quote must have positive bid <= ask."
                ),
            )
            engine = ShadowPaperEngine(settings, data, store=store)
            engine._detect_setup = lambda symbol, now: make_setup(now)

            result = engine.run_once(self.now)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["entries"], 0)
            errors = [
                event
                for event in read_events(engine.events.path)
                if event["event"] == "shadow_quote_error"
            ]
            self.assertEqual(errors[-1]["symbol"], "TEST")
            self.assertEqual(errors[-1]["stage"], "entry")

    def test_open_position_monitoring_skips_scanner(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            store.record_entry(
                make_trade(self.now - timedelta(minutes=1))
            )
            data = FakeShadowMarketData(self.now)
            engine = ShadowPaperEngine(settings, data, store=store)

            result = engine.run_once(self.now)

            self.assertEqual(result["exits"], 0)
            self.assertEqual(data.scanner_calls, 0)
            self.assertEqual(
                result["scanner_skipped_reason"],
                "Open-position monitoring has priority.",
            )

    def test_exit_requires_quote_newer_than_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            store.record_entry(make_trade(self.now))
            same_quote = Quote(
                "TEST",
                self.now,
                Decimal("4.85"),
                Decimal("4.86"),
            )
            engine = ShadowPaperEngine(
                settings,
                FakeShadowMarketData(
                    self.now + timedelta(seconds=5),
                    quote=same_quote,
                ),
                store=store,
            )

            result = engine.run_once(
                self.now + timedelta(seconds=5)
            )

            self.assertEqual(result["exits"], 0)
            self.assertEqual(store.get("trade").status, "open")
            rejected = [
                event
                for event in read_events(engine.events.path)
                if event["event"] == "shadow_quote_rejected"
            ]
            self.assertEqual(rejected[-1]["stage"], "exit")
            self.assertIn(
                "did not advance",
                rejected[-1]["reason"],
            )

    def test_shadow_cycle_uses_conservative_quote_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            store.record_entry(
                make_trade(self.now - timedelta(minutes=1))
            )
            stopped_quote = Quote(
                "TEST",
                self.now,
                Decimal("4.85"),
                Decimal("4.86"),
            )
            engine = ShadowPaperEngine(
                settings,
                FakeShadowMarketData(self.now, quote=stopped_quote),
                store=store,
            )

            result = engine.run_once(self.now)
            closed = store.get("trade")

            self.assertEqual(result["exits"], 1)
            self.assertEqual(closed.status, "closed")
            self.assertEqual(closed.exit_price, Decimal("4.85"))
            self.assertLess(closed.realized_pnl, 0)
            exits = [
                event
                for event in read_events(engine.events.path)
                if event["event"] == "shadow_exit"
            ]
            execution = exits[-1]["execution"]
            self.assertEqual(
                execution["stop_slippage_per_share"], "0.05"
            )
            self.assertEqual(execution["stop_slippage_total"], "0.50")
            self.assertEqual(execution["planned_risk"], "1.20")
            self.assertEqual(execution["realized_loss"], "1.70")
            self.assertEqual(execution["risk_overrun"], "0.50")

    def test_two_symbol_losses_block_next_shadow_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(
                directory,
                max_consecutive_losses_per_symbol_day=2,
            )
            store = ShadowTradeStore(
                Path(directory) / "shadow.sqlite3"
            )
            for index in range(2):
                trade = make_trade(
                    self.now - timedelta(minutes=3 - index),
                    f"loss-{index}",
                )
                store.record_entry(trade)
                store.record_exit(
                    trade.trade_id,
                    exit_time=trade.entry_time + timedelta(seconds=30),
                    exit_price=Decimal("4.90"),
                    reason="stop",
                )
            engine = ShadowPaperEngine(
                settings,
                FakeShadowMarketData(self.now),
                store=store,
            )
            engine._detect_setup = lambda symbol, now: make_setup(now)

            result = engine.run_once(self.now)

            self.assertEqual(result["entries"], 0)
            self.assertEqual(store.summary()["total_trades"], 2)


if __name__ == "__main__":
    unittest.main()
