import json
import tempfile
import unittest
from pathlib import Path

from bot.reentry_observed_screen import build_observed_screen


class ObservedReentryScreenTests(unittest.TestCase):
    def test_groups_by_eastern_trading_day_and_keeps_reconstruction_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.json"
            rows = [
                {
                    "trade_id": "first",
                    "symbol": "TEST",
                    "entry_time": "2026-09-01T13:00:00+00:00",
                    "status": "confirmed",
                    "raw_pnl": "-1",
                    "reconstruction": None,
                },
                {
                    "trade_id": "repeat",
                    "symbol": "TEST",
                    "entry_time": "2026-09-01T14:00:00+00:00",
                    "status": "discrepant",
                    "raw_pnl": "-5",
                    "reconstruction": {"realized_pnl": "2"},
                },
                {
                    "trade_id": "next-day",
                    "symbol": "TEST",
                    "entry_time": "2026-09-02T13:00:00+00:00",
                    "status": "confirmed",
                    "raw_pnl": "3",
                    "reconstruction": None,
                },
            ]
            audit_path.write_text(json.dumps({"ledger_trade_count": 3, "rows": rows}))

            report = build_observed_screen(
                audit_path, timezone_name="America/New_York"
            )

            self.assertEqual(report["groups"]["first"]["trades"], 2)
            self.assertEqual(report["groups"]["repeat"]["trades"], 1)
            self.assertEqual(report["groups"]["repeat"]["raw_net_profit"], "-5")
            self.assertEqual(
                report["groups"]["repeat"]["estimated_sip_path_net_profit"],
                "2",
            )

    def test_rejects_incomplete_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.json"
            audit_path.write_text(
                json.dumps(
                    {
                        "ledger_trade_count": 1,
                        "rows": [{"trade_id": "one", "status": "unresolved"}],
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "unresolved"):
                build_observed_screen(audit_path, timezone_name="America/New_York")
