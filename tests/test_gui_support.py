import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from bot.config import Settings
from bot.gui_support import load_profile, profile_filename, read_jsonl, save_profile


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


if __name__ == "__main__":
    unittest.main()
