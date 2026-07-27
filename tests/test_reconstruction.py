import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.history_store import HistoryStore
from bot.models import Bar
from bot.reconstruction import CandidateReconstructor


class ReconstructionTests(unittest.TestCase):
    def test_execution_bars_create_causal_ten_second_scanner_rows(self):
        eastern = ZoneInfo("America/New_York")
        prior = datetime(2026, 7, 8, 16, 0, tzinfo=eastern)
        decision = datetime(2026, 7, 9, 7, 30, 10, tzinfo=eastern)
        minute_bars = [
            Bar("TEST", prior, Decimal("5"), Decimal("5"), Decimal("5"), Decimal("5"), 100),
            Bar("TEST", decision.replace(second=0), Decimal("6"), Decimal("6"), Decimal("6"), Decimal("6"), 1000),
        ]
        execution_bars = [
            Bar("TEST", decision, Decimal("6"), Decimal("6.10"), Decimal("6"), Decimal("6.10"), 1000),
        ]
        settings = replace(
            Settings(),
            trade_window_start=decision.time(),
            min_percent_gain=Decimal("0"),
            min_rvol=Decimal("0"),
            min_day_volume=0,
        )

        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite3")
            recorded = CandidateReconstructor(settings, store).reconstruct(
                {"TEST": minute_bars},
                execution_bars_by_symbol={"TEST": execution_bars},
                decision_minutes=[decision],
            )
            rows = store.candidates(
                source="reconstructed", passed_only=False, symbols=["TEST"]
            )

        self.assertEqual(recorded, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], decision.isoformat())
        self.assertEqual(rows[0]["day_volume"], 1000)

    def test_date_scoped_reconstruction_excludes_other_symbol_days(self):
        eastern = ZoneInfo("America/New_York")
        prior = datetime(2026, 7, 8, 16, 0, tzinfo=eastern)
        first = datetime(2026, 7, 9, 7, 30, 10, tzinfo=eastern)
        selected = datetime(2026, 7, 10, 7, 30, 10, tzinfo=eastern)
        minute_bars = [
            Bar("TEST", prior, Decimal("5"), Decimal("5"), Decimal("5"), Decimal("5"), 100),
            Bar("TEST", first.replace(second=0), Decimal("6"), Decimal("6"), Decimal("6"), Decimal("6"), 1000),
            Bar("TEST", selected.replace(second=0), Decimal("7"), Decimal("7"), Decimal("7"), Decimal("7"), 1000),
        ]
        execution_bars = [
            Bar("TEST", first, Decimal("6"), Decimal("6"), Decimal("6"), Decimal("6"), 1000),
            Bar("TEST", selected, Decimal("7"), Decimal("7"), Decimal("7"), Decimal("7"), 1000),
        ]
        settings = replace(
            Settings(),
            trade_window_start=first.time(),
            min_percent_gain=Decimal("0"),
            min_rvol=Decimal("0"),
            min_day_volume=0,
        )

        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite3")
            recorded = CandidateReconstructor(settings, store).reconstruct(
                {"TEST": minute_bars},
                execution_bars_by_symbol={"TEST": execution_bars},
                decision_minutes=[first, selected],
                decision_dates_by_symbol={"TEST": {selected.date()}},
            )
            rows = store.candidates(
                source="reconstructed", passed_only=False, symbols=["TEST"]
            )

        self.assertEqual(recorded, 1)
        self.assertEqual([row["timestamp"] for row in rows], [selected.isoformat()])


if __name__ == "__main__":
    unittest.main()
