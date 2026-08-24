import json
import tempfile
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from bot.event_log import JsonlEventLog


@dataclass
class Nested:
    amount: Decimal


class EventLogTests(unittest.TestCase):
    def test_nested_values_are_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            JsonlEventLog(path).append({"nested": Nested(Decimal("1.25"))})
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["nested"]["amount"], "1.25")

    def test_sensitive_account_fields_are_redacted_and_file_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            local_path = str(Path("/") / "Users" / "example" / "project")
            credential = "PK" + "ABCDEFGHIJKLMNOPQRST"
            JsonlEventLog(path).append(
                {
                    "account_id": "paper-account-id",
                    "nested": {
                        "cash": "500.00",
                        "safe": "kept",
                        "message": (
                            f"failed at {local_path} with {credential}"
                        ),
                    },
                }
            )
            value = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(value["account_id"], "[REDACTED]")
            self.assertEqual(value["nested"]["cash"], "[REDACTED]")
            self.assertEqual(value["nested"]["safe"], "kept")
            self.assertNotIn("example", value["nested"]["message"])
            self.assertNotIn(credential, value["nested"]["message"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
