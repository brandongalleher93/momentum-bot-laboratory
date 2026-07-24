import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.history_store import HistoryStore
from bot.models import MarketSnapshot
from bot.portfolio_backtest import PortfolioReplayEngine
from bot.scanner import MarketScanner
from tests.fixtures import breakout_bar, valid_bull_flag_bars


class PortfolioReplayTests(unittest.TestCase):
    def test_replay_ignores_stale_candidates_outside_loaded_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings()
            store = HistoryStore(Path(directory) / "history.sqlite3")
            scanner = MarketScanner(settings, DiagnosticFactory(settings))
            bars = valid_bull_flag_bars("TEST") + [breakout_bar("TEST")]
            evaluation_start = bars[0].timestamp.astimezone(timezone.utc) - timedelta(minutes=1)
            evaluation_end = bars[-1].timestamp.astimezone(timezone.utc) + timedelta(minutes=1)

            current = MarketSnapshot(
                "TEST", bars[-2].timestamp, Decimal("5"), Decimal("0.20"), Decimal("6"),
                600000, Decimal("4.99"), Decimal("5.01"),
            )
            stale = MarketSnapshot(
                "ACHR", datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc), Decimal("5"),
                Decimal("0.20"), Decimal("6"), 600000, Decimal("4.99"), Decimal("5.01"),
            )
            for snapshot in (current, stale):
                outcome = scanner.scan([snapshot])
                store.record_scan([snapshot], outcome.diagnostics, source="reconstructed")

            result = PortfolioReplayEngine(settings, store).run(
                {"TEST": bars},
                source="reconstructed",
                feed="iex",
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end,
            )

            self.assertEqual(result.symbols, ["TEST"])
            self.assertEqual(result.candidate_count, 1)
            self.assertEqual(len(result.accepted_trades), 1)
            self.assertTrue(all(trade.symbol == "TEST" for trade in result.accepted_trades))


if __name__ == "__main__":
    unittest.main()
