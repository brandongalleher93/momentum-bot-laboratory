import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from decimal import Decimal

from bot.config import Settings
from bot.models import Quote
from bot.shadow_evidence import EvidenceSession, EvidenceCaptureError, Journal, read_journal, verify_shadow_journal
from bot.shadow_paper import ShadowPaperEngine
from tests.test_shadow_paper import FakeShadowMarketData
from tests.fixtures import valid_bull_flag_bars


class ShadowEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 7, 15, 9, 44, tzinfo=ZoneInfo("America/New_York"))
        self.settings = replace(Settings(), alpaca_api_key="test-key", alpaca_secret_key="test-secret", output_dir=self.root / "output", log_dir=self.root / "logs")

    def engine(self, provider=None):
        session = EvidenceSession(self.root / "evidence")
        engine = ShadowPaperEngine(self.settings, provider or FakeShadowMarketData(self.now), evidence=session)
        return engine, session

    def test_roundtrip_empty_and_repeated_scanner_cycles(self):
        engine, session = self.engine()
        for i in (0, 5, 10):
            engine.run_once(self.now + timedelta(seconds=i))
        session.close()
        report = verify_shadow_journal(session.journal.path)
        self.assertEqual(report["cycles"], 3)
        self.assertFalse(report["strategy_validation"])
        self.assertFalse(report["complete_window"])
        header = next(read_journal(session.journal.path))
        self.assertNotIn("test-secret", json.dumps(header))
        self.assertEqual(session.journal.path.stat().st_mode & 0o777, 0o600)

    def test_actual_strategy_entry_and_exit_reproduce(self):
        class Market(FakeShadowMarketData):
            def get_completed_bars(self, symbol, end, lookback_days=2):
                return valid_bull_flag_bars(symbol)
        market = Market(self.now, quote=Quote("TEST", self.now, Decimal("5.09"), Decimal("5.10")))
        engine, session = self.engine(market)
        engine.run_once(self.now)
        self.assertEqual(len(engine.store.open_trades()), 1)
        market.quote = Quote("TEST", self.now + timedelta(seconds=5), Decimal("5.50"), Decimal("5.51"))
        engine.run_once(self.now + timedelta(seconds=5))
        self.assertEqual(engine.store.summary()["closed_trades"], 1)
        session.close()
        self.assertEqual(verify_shadow_journal(session.journal.path)["cycles"], 2)

    def test_restart_with_existing_open_position_reproduces_exit(self):
        from bot.shadow_paper import ShadowTradeStore
        from tests.test_shadow_paper import make_trade
        store = ShadowTradeStore(self.settings.output_dir / "shadow_paper" / "shadow_trades.sqlite3")
        trade = make_trade(self.now - timedelta(seconds=30))
        store.record_entry(trade)
        market = FakeShadowMarketData(self.now, quote=Quote("TEST", self.now, Decimal("6"), Decimal("6.01")))
        session = EvidenceSession(self.root / "evidence")
        engine = ShadowPaperEngine(self.settings, market, store=store, evidence=session)
        engine.run_once(self.now)
        self.assertEqual(store.summary()["closed_trades"], 1)
        session.close()
        self.assertEqual(verify_shadow_journal(session.journal.path)["new_closed_trades"], 1)

    def test_changed_market_call_fails_even_with_valid_journal_hashes(self):
        engine, session = self.engine()
        engine.run_once(self.now)
        session.close()
        records = list(read_journal(session.journal.path))
        cycle = next(r for r in records if r["kind"] == "cycle")
        cycle["calls"][0]["method"] = "get_latest_quote"
        altered = Journal(self.root / "altered", self.now)
        for record in records:
            altered.append(record)
        with self.assertRaisesRegex(EvidenceCaptureError, "ordering"):
            verify_shadow_journal(altered.path)

    def test_oversized_record_and_low_disk_fail_closed(self):
        journal = Journal(self.root / "evidence", self.now)
        with patch("bot.shadow_evidence.MAX_RECORD_BYTES", 10):
            with self.assertRaises(EvidenceCaptureError):
                journal.append({"payload": "a" * 11})
        from types import SimpleNamespace
        with patch("bot.shadow_evidence.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaises(EvidenceCaptureError):
                journal.append({"kind": "test"})

    def test_full_window_is_distinct_from_partial_or_gapped_capture(self):
        for gap, expected in ((60, True), (300, False)):
            with self.subTest(gap=gap):
                directory = self.root / str(gap)
                now = self.now.replace(hour=9, minute=30)
                settings = replace(self.settings, trade_window_start=time(9, 30),
                                   trade_window_end=time(9, 31),
                                   output_dir=directory / "output", log_dir=directory / "logs")
                session = EvidenceSession(directory / "evidence")
                engine = ShadowPaperEngine(settings, FakeShadowMarketData(now), evidence=session)
                engine.run_once(now)
                engine.run_once(now + timedelta(seconds=gap))
                session.close()
                report = verify_shadow_journal(session.journal.path)
                self.assertEqual(report["complete_window"], expected)

    def test_live_cycle_clocks_are_replayed(self):
        session = EvidenceSession(self.root / "evidence")
        engine = ShadowPaperEngine(self.settings, FakeShadowMarketData(self.now), clock=lambda: self.now, evidence=session)
        engine.run_once()
        session.close()
        self.assertEqual(verify_shadow_journal(session.journal.path)["cycles"], 1)

    def test_unfinished_session_and_truncated_cycle_rejected(self):
        engine, session = self.engine()
        engine.run_once(self.now)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            verify_shadow_journal(session.journal.path)
        session.begin(self.now)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            verify_shadow_journal(session.journal.path)

    def test_provider_error_is_not_valid_evidence(self):
        class Broken(FakeShadowMarketData):
            def get_scanner_snapshots(self, now):
                raise TimeoutError("private provider message")
        engine, session = self.engine(Broken(self.now))
        with self.assertRaises(TimeoutError):
            engine.run_once(self.now)
        session.close()
        with self.assertRaisesRegex(ValueError, "Provider-error"):
            verify_shadow_journal(session.journal.path)
        self.assertNotIn("private provider message", json.dumps(list(read_journal(session.journal.path))))

    def test_tampering_and_source_drift_fail_closed(self):
        engine, session = self.engine()
        engine.run_once(self.now)
        session.close()
        with patch("bot.shadow_evidence.source_fingerprint", return_value={}):
            with self.assertRaisesRegex(ValueError, "Source differs"):
                verify_shadow_journal(session.journal.path)
        with sqlite3.connect(session.journal.path) as db:
            db.execute("UPDATE records SET digest='bad' WHERE sequence=1")
        with self.assertRaisesRegex(ValueError, "integrity"):
            verify_shadow_journal(session.journal.path)

    def test_storage_failure_stops_before_market_call(self):
        market = FakeShadowMarketData(self.now)
        engine, session = self.engine(market)
        with patch("bot.shadow_evidence.MAX_DAILY_BYTES", 1):
            with self.assertRaises(EvidenceCaptureError):
                engine.run_once(self.now)
        self.assertEqual(market.scanner_calls, 0)
