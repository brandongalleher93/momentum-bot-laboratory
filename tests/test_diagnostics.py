import unittest
from datetime import datetime, timedelta, timezone

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.models import Severity


class DiagnosticTests(unittest.TestCase):
    def test_future_data_becomes_critical_rejection(self) -> None:
        factory = DiagnosticFactory(Settings())
        decision = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
        result = factory.passed(
            module="test",
            reason="Would otherwise pass.",
            decision_time=decision,
            data_cutoff_time=decision + timedelta(seconds=1),
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.severity, Severity.CRITICAL)
        self.assertIn("lookahead", result.reason.lower())


if __name__ == "__main__":
    unittest.main()
