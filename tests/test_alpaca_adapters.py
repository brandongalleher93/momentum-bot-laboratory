import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from bot.alpaca_adapters import AlpacaMarketData
from bot.config import Settings
from bot.models import Bar, MarketSnapshot


class AlpacaMarketDataTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("America/New_York")
        self.data = AlpacaMarketData.__new__(AlpacaMarketData)
        self.data.settings = Settings()
        self.data._scanner_cache_at = None
        self.data._scanner_cache = []

    def test_premarket_seed_uses_current_session_trade_and_previous_close(self):
        now = datetime(2026, 7, 28, 7, 0, tzinfo=self.zone)
        snapshot = SimpleNamespace(
            latest_trade=SimpleNamespace(
                timestamp=now - timedelta(minutes=15),
                price=Decimal("5.50"),
            ),
            latest_quote=SimpleNamespace(
                timestamp=now - timedelta(minutes=15),
                bid_price=Decimal("5.48"),
                ask_price=Decimal("5.52"),
            ),
            previous_daily_bar=SimpleNamespace(
                close=Decimal("5.00")
            ),
        )

        seed = self.data._premarket_seed("TEST", snapshot, now)

        self.assertIsNotNone(seed)
        self.assertEqual(seed[2], Decimal("5.50"))
        self.assertEqual(seed[3], Decimal("0.10"))

    def test_premarket_seed_rejects_yesterdays_trade(self):
        now = datetime(2026, 7, 28, 7, 0, tzinfo=self.zone)
        yesterday = now - timedelta(days=1)
        snapshot = SimpleNamespace(
            latest_trade=SimpleNamespace(
                timestamp=yesterday,
                price=Decimal("5.50"),
            ),
            latest_quote=SimpleNamespace(
                timestamp=yesterday,
                bid_price=Decimal("5.48"),
                ask_price=Decimal("5.52"),
            ),
            previous_daily_bar=SimpleNamespace(
                close=Decimal("5.00")
            ),
        )

        self.assertIsNone(
            self.data._premarket_seed("TEST", snapshot, now)
        )

    def test_scanner_routes_premarket_to_custom_universe_and_caches(self):
        now = datetime(2026, 7, 28, 7, 0, tzinfo=self.zone)
        expected = [
            MarketSnapshot(
                "TEST",
                now,
                Decimal("5"),
                Decimal("0.10"),
                Decimal("5"),
                500_000,
                Decimal("4.99"),
                Decimal("5.01"),
            )
        ]
        calls = []
        self.data._get_premarket_scanner_snapshots = (
            lambda value: calls.append(value) or expected
        )
        self.data._get_regular_scanner_snapshots = lambda value: []

        first = self.data.get_scanner_snapshots(now)
        second = self.data.get_scanner_snapshots(
            now + timedelta(seconds=30)
        )

        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(len(calls), 1)

    def test_scanner_routes_market_open_to_regular_movers(self):
        now = datetime(2026, 7, 28, 9, 30, tzinfo=self.zone)
        calls = []
        self.data._get_premarket_scanner_snapshots = lambda value: []
        self.data._get_regular_scanner_snapshots = (
            lambda value: calls.append(value) or []
        )

        self.data.get_scanner_snapshots(now)

        self.assertEqual(calls, [now])

    def test_scanner_bars_use_sip_outside_recent_data_window(self):
        now = datetime(2026, 7, 28, 7, 0, tzinfo=self.zone)
        observed = {}
        self.data._sip_feed = lambda: "sip"

        def bars(symbol, end, *, lookback_days, feed):
            observed.update(
                {
                    "symbol": symbol,
                    "end": end,
                    "lookback_days": lookback_days,
                    "feed": feed,
                }
            )
            return []

        self.data._get_completed_bars_with_feed = bars

        result, cutoff = self.data._get_scanner_bars("TEST", now)

        self.assertEqual(result, [])
        self.assertEqual(cutoff, now - timedelta(minutes=16))
        self.assertEqual(observed["feed"], "sip")
        self.assertEqual(observed["lookback_days"], 35)

    def test_session_volume_only_counts_current_day(self):
        now = datetime(2026, 7, 28, 8, 0, tzinfo=self.zone)
        bars = [
            Bar(
                "TEST",
                now - timedelta(days=1),
                Decimal("5"),
                Decimal("5"),
                Decimal("5"),
                Decimal("5"),
                900,
            ),
            Bar(
                "TEST",
                now - timedelta(minutes=1),
                Decimal("5"),
                Decimal("5"),
                Decimal("5"),
                Decimal("5"),
                100,
            ),
        ]

        self.assertEqual(self.data._session_volume(bars, now), 100)

    def test_latest_quote_retains_available_market_context(self):
        now = datetime(2026, 7, 28, 8, 0, tzinfo=self.zone)
        value = SimpleNamespace(
            timestamp=now,
            bid_price=Decimal("4.99"),
            ask_price=Decimal("5.01"),
            bid_size=120,
            ask_size=80,
            bid_exchange=SimpleNamespace(value="V"),
            ask_exchange=SimpleNamespace(value="V"),
            conditions=[SimpleNamespace(value="R")],
            tape=SimpleNamespace(value="C"),
        )
        self.data.stock = SimpleNamespace(
            get_stock_latest_quote=lambda request: {"TEST": value}
        )
        self.data._feed = lambda: "iex"

        quote = self.data.get_latest_quote("TEST")

        self.assertEqual(quote.bid_size, 120)
        self.assertEqual(quote.ask_size, 80)
        self.assertEqual(quote.bid_exchange, "V")
        self.assertEqual(quote.ask_exchange, "V")
        self.assertEqual(quote.conditions, ("R",))
        self.assertEqual(quote.tape, "C")


if __name__ == "__main__":
    unittest.main()
