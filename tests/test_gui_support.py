import json
import tempfile
import unittest
from dataclasses import replace
from datetime import time
from decimal import Decimal
from pathlib import Path

from bot.config import Settings
from bot.gui import historical_replay_settings
from bot.gui_support import (
    active_shadow_protection_rows,
    execution_audit_table_rows,
    load_profile,
    profile_filename,
    read_jsonl,
    save_profile,
)
from bot.shadow_paper import shadow_paper_settings


class GuiSupportTests(unittest.TestCase):
    def test_profile_round_trip_preserves_safe_tunable_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original = replace(Settings(), max_risk_per_trade=Decimal("4.25"), parameter_profile="test_profile")
            path = save_profile(Path(directory), "Test Profile", original)
            loaded = load_profile(path, Settings())
            self.assertEqual(loaded.max_risk_per_trade, Decimal("4.25"))
            self.assertEqual(loaded.parameter_profile, "test_profile")
            self.assertTrue(loaded.alpaca_paper)
            self.assertFalse(loaded.allow_live_trading)

    def test_profile_filename_rejects_empty_names(self) -> None:
        with self.assertRaises(ValueError):
            profile_filename("///")

    def test_jsonl_reader_keeps_valid_and_malformed_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text(json.dumps({"event": "ok"}) + "\nnot json\n", encoding="utf-8")
            rows = read_jsonl(path)
            self.assertEqual(rows[0]["event"], "ok")
            self.assertTrue(rows[1]["malformed"])

    def test_premarket_replay_profile_does_not_mutate_live_settings(self) -> None:
        original = Settings()

        replay = historical_replay_settings(original, "Premarket validation")

        self.assertEqual(original.trade_window_start, time(9, 30))
        self.assertEqual(replay.trade_window_start, time(7, 0))
        self.assertEqual(replay.preferred_pullback_depth, Decimal("0.50"))
        self.assertIn("historical_premarket_validation", replay.parameter_profile)

    def test_regular_hours_profile_retains_relaxed_validation_rules(self) -> None:
        replay = historical_replay_settings(
            Settings(), "Regular-hours paper validation"
        )

        self.assertEqual(replay.trade_window_start, time(9, 30))
        self.assertEqual(replay.preferred_pullback_depth, Decimal("0.50"))
        self.assertEqual(
            replay.max_extension_above_vwap_percent, Decimal("0.25")
        )
        self.assertIn(
            "historical_regular-hours_paper_validation",
            replay.parameter_profile,
        )

    def test_active_shadow_protections_describe_shadow_profile(self) -> None:
        settings = shadow_paper_settings(
            replace(
                Settings(),
                max_consecutive_losses_per_symbol_day=2,
            )
        )

        rows = active_shadow_protection_rows(settings)
        protections = {
            row["Protection"]: row["Active shadow setting"] for row in rows
        }

        self.assertEqual(protections["Maximum risk per trade"], "$5.00")
        self.assertEqual(protections["Maximum position value"], "$125.00")
        self.assertEqual(protections["Daily loss/risk budget"], "$15.00")
        self.assertEqual(protections["Maximum open positions"], "1")
        self.assertEqual(
            protections["Per-symbol daily loss stop"],
            "Enabled — after 2 consecutive losses",
        )
        self.assertEqual(
            protections["Re-entry cooldown after a loss"], "Disabled"
        )
        self.assertEqual(
            protections["Three-trade cap per symbol/day"],
            "Disabled — replay only",
        )
        self.assertEqual(protections["Profit target"], "2.0R")
        self.assertEqual(
            protections["Trading window"], "7:00 AM–11:30 AM ET"
        )
        self.assertEqual(protections["Execution data"], "Current IEX")
        self.assertEqual(
            protections["Broker order submission"],
            "Disabled — no broker orders",
        )

    def test_active_shadow_protections_expose_unsafe_submission_state(self) -> None:
        rows = active_shadow_protection_rows(
            replace(Settings(), paper_order_submission_enabled=True)
        )
        protections = {
            row["Protection"]: row["Active shadow setting"] for row in rows
        }

        self.assertEqual(
            protections["Broker order submission"],
            "Enabled — shadow mode blocked",
        )

    def test_execution_audit_rows_prioritize_discrepancies(self) -> None:
        report = {
            "rows": [
                {
                    "symbol": "GOOD",
                    "entry_time": "2026-07-01T14:00:00+00:00",
                    "status": "confirmed",
                    "raw_pnl": "2.00",
                },
                {
                    "symbol": "CHECK",
                    "entry_time": "2026-07-02T14:00:00+00:00",
                    "status": "discrepant",
                    "raw_pnl": "-5.00",
                    "sip_exit_bid": "5.10",
                    "reconstruction": {
                        "realized_pnl": "2.00",
                        "reason": "SIP price path later reached the target.",
                    },
                },
            ]
        }

        rows = execution_audit_table_rows(report)

        self.assertEqual(rows[0]["symbol"], "CHECK")
        self.assertEqual(rows[0]["raw P/L"], -5.0)
        self.assertEqual(rows[0]["estimated SIP-path P/L"], 2.0)


if __name__ == "__main__":
    unittest.main()
