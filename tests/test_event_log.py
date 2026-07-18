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


if __name__ == "__main__":
    unittest.main()
