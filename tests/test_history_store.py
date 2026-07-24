import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.history_store import HistoryStore
from bot.models import MarketSnapshot
from bot.scanner import MarketScanner


class HistoryStoreTests(unittest.TestCase):
    def test_captured_scan_round_trip_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite3")
            now = datetime(2026, 7, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))
            snapshot = MarketSnapshot("TEST", now, Decimal("5"), Decimal("0.20"), Decimal("6"), 600000, Decimal("4.99"), Decimal("5.01"))
            outcome = MarketScanner(Settings(), DiagnosticFactory(Settings())).scan([snapshot])
            self.assertEqual(store.record_scan([snapshot], outcome.diagnostics, source="captured"), 1)
            rows = store.candidates(source="captured")
            self.assertEqual(rows[0]["symbol"], "TEST")
            self.assertEqual(rows[0]["source"], "captured")
            self.assertEqual(store.summary()["captured"], 1)

    def test_candidate_query_scopes_symbols_and_time_window(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite3")
            settings = Settings()
            scanner = MarketScanner(settings, DiagnosticFactory(settings))
            base = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
            snapshots = [
                MarketSnapshot("KEEP", base, Decimal("5"), Decimal("0.20"), Decimal("6"), 600000, Decimal("4.99"), Decimal("5.01")),
                MarketSnapshot("STALE", base - timedelta(days=5), Decimal("5"), Decimal("0.20"), Decimal("6"), 600000, Decimal("4.99"), Decimal("5.01")),
            ]
            for snapshot in snapshots:
                outcome = scanner.scan([snapshot])
                store.record_scan([snapshot], outcome.diagnostics, source="reconstructed")

            rows = store.candidates(
                source="reconstructed",
                symbols={"KEEP"},
                start_time=base - timedelta(minutes=1),
                end_time=base + timedelta(minutes=1),
            )

            self.assertEqual([row["symbol"] for row in rows], ["KEEP"])


if __name__ == "__main__": unittest.main()
