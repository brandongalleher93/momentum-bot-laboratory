import unittest
from dataclasses import replace

from bot.config import Settings, load_settings, validate_settings


class ConfigTests(unittest.TestCase):
    def test_defaults_are_paper_only_and_disarmed(self) -> None:
        value = Settings()
        self.assertTrue(value.alpaca_paper)
        self.assertFalse(value.allow_live_trading)
        self.assertFalse(value.paper_order_submission_enabled)
        validate_settings(value)

    def test_live_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "ALPACA_PAPER"):
            validate_settings(replace(Settings(), alpaca_paper=False))

    def test_percentages_use_decimal_fractions(self) -> None:
        with self.assertRaisesRegex(ValueError, "MIN_PERCENT_GAIN"):
            validate_settings(replace(Settings(), min_percent_gain=10))

    def test_environment_overrides_money_not_percent_of_equity(self) -> None:
        value = load_settings(
            {
                "ALPACA_PAPER": "true",
                "MAX_RISK_PER_TRADE": "4.50",
                "MAX_DAILY_LOSS": "14",
            }
        )
        self.assertEqual(str(value.max_risk_per_trade), "4.50")
        self.assertEqual(str(value.max_daily_loss), "14")


if __name__ == "__main__":
    unittest.main()
