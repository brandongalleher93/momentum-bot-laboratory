import unittest
from datetime import datetime, timezone
from decimal import Decimal

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.models import MarketSnapshot
from bot.scanner import MarketScanner


def snapshot(**overrides):
    values = {
        "symbol": "TEST",
        "timestamp": datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc),
        "price": Decimal("5"),
        "percent_gain": Decimal("0.12"),
        "rvol": Decimal("6"),
        "day_volume": 600_000,
        "bid": Decimal("4.99"),
        "ask": Decimal("5.01"),
        "float_shares": None,
    }
    values.update(overrides)
    return MarketSnapshot(**values)


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings()
        self.scanner = MarketScanner(settings, DiagnosticFactory(settings))

    def test_valid_candidate_passes_even_when_float_is_missing(self) -> None:
        result = self.scanner.scan([snapshot()])
        self.assertEqual(len(result.candidates), 1)
        self.assertIn("Float unavailable", result.diagnostics[0].notes or "")

    def test_wide_spread_is_rejected(self) -> None:
        result = self.scanner.scan(
            [snapshot(bid=Decimal("4.90"), ask=Decimal("5.10"))]
        )
        self.assertEqual(result.candidates, [])
        self.assertIn("Spread", result.diagnostics[0].reason)


if __name__ == "__main__":
    unittest.main()
