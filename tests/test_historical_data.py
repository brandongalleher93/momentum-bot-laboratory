import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from bot.historical_data import (
    BarCache,
    TradeCache,
    TradeTick,
    aggregate_trades_to_bars,
    load_workspace_manifest,
    save_workspace_manifest,
)
from bot.history_store import HistoryStore


class HistoricalDataTests(unittest.TestCase):
    def test_latest_paths_selects_newest_cached_file_per_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = BarCache(root / "bars", HistoryStore(root / "history.sqlite3"))
            older = cache.root / "sip" / "TEST" / "older.parquet"
            newer = cache.root / "sip" / "TEST" / "newer.parquet"
            other = cache.root / "iex" / "TEST" / "other.parquet"
            for path in (older, newer, other):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"placeholder")
            older.touch()
            newer.touch()
            other.touch()
            older_stat = older.stat()
            newer_stat = newer.stat()
            older_time = older_stat.st_mtime - 10
            newer_time = newer_stat.st_mtime
            os.utime(older, (older_time, older_time))
            os.utime(newer, (newer_time, newer_time))

            selected = cache.latest_paths(feed="sip", symbols=["test"])

            self.assertEqual(selected, {"TEST": newer})

    def test_workspace_manifest_round_trip_and_invalid_json_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active_workspace.json"
            payload = {
                "feed": "sip",
                "symbols": ["ERNA", "GMM"],
                "evaluation_start": "2026-07-09",
            }

            save_workspace_manifest(path, payload)
            self.assertEqual(load_workspace_manifest(path), payload)

            path.write_text("{not-json", encoding="utf-8")
            self.assertEqual(load_workspace_manifest(path), {})

    def test_trade_cache_round_trip_and_ten_second_aggregation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = TradeCache(root / "trades")
            start = datetime(2026, 7, 10, 12, 19, 1, tzinfo=timezone.utc)
            trades = [
                TradeTick("GMM", start, Decimal("4.40"), 100),
                TradeTick(
                    "GMM", start + timedelta(seconds=4), Decimal("4.55"), 200
                ),
                TradeTick(
                    "GMM", start + timedelta(seconds=10), Decimal("4.50"), 50
                ),
            ]

            path = cache.write(trades, feed="sip")
            restored = cache.read(path)
            tape = cache.read_tape(path)
            bars = aggregate_trades_to_bars(restored, seconds=10)

            self.assertEqual(restored, trades)
            self.assertEqual(len(bars), 2)
            self.assertEqual(bars[0].timestamp, start.replace(second=10))
            self.assertEqual(bars[0].open, Decimal("4.4"))
            self.assertEqual(bars[0].high, Decimal("4.55"))
            self.assertEqual(bars[0].close, Decimal("4.55"))
            self.assertEqual(bars[0].volume, 300)
            self.assertEqual(bars[1].timestamp, start.replace(second=20))
            self.assertEqual(bars[1].volume, 50)
            self.assertEqual(tape.symbol, "GMM")
            self.assertEqual(tape.trades_for_bar(bars[0]), trades[:2])

    def test_trade_aggregation_rejects_mixed_symbols(self):
        timestamp = datetime(2026, 7, 10, 12, 19, 1, tzinfo=timezone.utc)
        trades = [
            TradeTick("GMM", timestamp, Decimal("4.40"), 100),
            TradeTick("NXTC", timestamp, Decimal("8.40"), 100),
        ]

        with self.assertRaisesRegex(ValueError, "one symbol"):
            aggregate_trades_to_bars(trades)


if __name__ == "__main__":
    unittest.main()
