import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from bot.backtest import BacktestEngine
from bot.config import Settings
from bot.portfolio_backtest import PortfolioReplayEngine
from bot.scanner_availability import load_scanner_approvals, candidate_approval_times
from tests.fixtures import valid_bull_flag_bars


class ScannerAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.market = datetime(2026, 7, 15, 13, 30, tzinfo=timezone.utc)
        self.approval = self.market + timedelta(minutes=15, seconds=7)
        self.event = {
            "module": "scanner", "passed": True, "symbol": "TEST",
            "timestamp": self.approval.isoformat(),
            "decision_time": self.market.isoformat(),
            "data_cutoff_time": self.market.isoformat(),
            "actual_values": {"price": "5", "day_volume": 600000},
        }
        self.row = {
            "symbol": "TEST", "timestamp": self.market.isoformat(),
            "actual_values_json": json.dumps(self.event["actual_values"]),
        }

    def load(self, events):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events))
            return load_scanner_approvals([path])

    def test_delayed_and_repeated_approvals_never_open_gate_early(self):
        later = self.approval + timedelta(minutes=5)
        index = self.load([self.event, dict(self.event, timestamp=later.isoformat()), self.event])
        times = candidate_approval_times(self.row, index)
        self.assertEqual(times, {self.approval, later})
        for moment in (self.market, self.approval - timedelta(microseconds=1),
                       self.approval + timedelta(minutes=1)):
            self.assertFalse(BacktestEngine._scanner_gate_open(moment, times))
        self.assertTrue(BacktestEngine._scanner_gate_open(self.approval, times))
        self.assertTrue(BacktestEngine._scanner_gate_open(later, times))

    def test_missing_rejected_or_different_values_fail_closed(self):
        for events in ([], [dict(self.event, passed=False)],
                       [dict(self.event, actual_values={"price": "6"})]):
            with self.subTest(events=events):
                with self.assertRaisesRegex(ValueError, "no matching"):
                    candidate_approval_times(self.row, self.load(events))

    def test_invalid_chronology_or_schema_is_rejected(self):
        for change in (
            {"timestamp": self.market.replace(tzinfo=None).isoformat()},
            {"timestamp": (self.market - timedelta(seconds=1)).isoformat()},
            {"decision_time": (self.market - timedelta(seconds=1)).isoformat()},
            {"passed": "true"}, {"actual_values": []}, {"symbol": None},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load([dict(self.event, **change)])

    def test_offsets_and_archive_order_do_not_change_identity(self):
        eastern = timezone(timedelta(hours=-4))
        event = dict(self.event, data_cutoff_time=self.market.astimezone(eastern).isoformat())
        self.assertEqual(candidate_approval_times(self.row, self.load([event])), {self.approval})

    def test_captured_engine_requires_evidence(self):
        with self.assertRaisesRegex(ValueError, "requires scanner"):
            PortfolioReplayEngine(Settings(), None).run({}, source="captured")

    def test_engine_passes_actual_approval_to_both_strategy_paths(self):
        bars = valid_bull_flag_bars()
        index = self.load([self.event])
        from unittest.mock import Mock
        store = Mock()
        store.candidates.return_value = [self.row]
        result = SimpleNamespace(trades=[], rejection_details=[])
        with patch("bot.portfolio_backtest.BacktestEngine") as factory:
            factory.return_value.run.return_value = result
            factory.return_value.run_micro.return_value = result
            replay = PortfolioReplayEngine(Settings(), store).run(
                {"TEST": bars}, execution_bars_by_symbol={"TEST": bars},
                source="captured", scanner_approvals=index,
                evaluation_start=self.market + timedelta(minutes=1),
                evaluation_end=self.approval,
            )
            self.assertEqual(replay.candidate_count, 1)
            for method in (factory.return_value.run, factory.return_value.run_micro):
                self.assertEqual(method.call_args.kwargs["allowed_decision_times"], {self.approval})
            self.assertIsNone(store.candidates.call_args.kwargs["start_time"])

    def test_cutoff_uses_availability_instead_of_market_time(self):
        from unittest.mock import Mock
        store = Mock()
        store.candidates.return_value = [self.row]
        replay = PortfolioReplayEngine(Settings(), store).run(
            {"TEST": valid_bull_flag_bars()}, source="captured",
            scanner_approvals=self.load([self.event]),
            evaluation_end=self.approval - timedelta(microseconds=1),
        )
        self.assertEqual(replay.candidate_count, 0)
        self.assertEqual(replay.accepted_trades, [])
