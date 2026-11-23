import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bot.cli import main
from bot.config import Settings
from tests.test_shadow_paper import FakeShadowMarketData


class ShadowCollectionCliTests(unittest.TestCase):
    def test_shadow_command_captures_verifies_and_writes_private_health(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(Settings(), alpaca_api_key="synthetic-key", alpaca_secret_key="synthetic-secret", output_dir=root / "output", log_dir=root / "logs")
            data = FakeShadowMarketData(datetime.now(timezone.utc))
            output = io.StringIO()
            with patch("bot.cli.load_settings", return_value=settings), patch("bot.cli.AlpacaMarketData", return_value=data), contextlib.redirect_stdout(output):
                self.assertEqual(main(["shadow", "--once"]), 0)
            health_path = root / "output" / "shadow_evidence" / "health.json"
            health = json.loads(health_path.read_text())
            self.assertEqual(health["status"], "verified")
            self.assertEqual(health["verification"]["cycles"], 1)
            self.assertNotIn("synthetic-secret", health_path.read_text())
            self.assertFalse((root / "output" / "shadow_paper" / "runner.pid").exists())
            self.assertEqual(health_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list(health_path.parent.glob("*.verified.json"))), 1)
