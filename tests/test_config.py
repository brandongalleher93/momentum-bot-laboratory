import unittest
from dataclasses import replace
from datetime import time
from decimal import Decimal

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

    def test_symbol_daily_loss_limit_loads_and_must_be_positive(self) -> None:
        value = load_settings(
            {
                "ALPACA_PAPER": "true",
                "MAX_CONSECUTIVE_LOSSES_PER_SYMBOL_DAY": "2",
            }
        )
        self.assertEqual(value.max_consecutive_losses_per_symbol_day, 2)

        with self.assertRaisesRegex(
            ValueError, "MAX_CONSECUTIVE_LOSSES_PER_SYMBOL_DAY"
        ):
            validate_settings(
                replace(
                    Settings(),
                    max_consecutive_losses_per_symbol_day=0,
                )
            )

    def test_premarket_paper_profile_fields_load_from_environment(self) -> None:
        value = load_settings(
            {
                "ALPACA_PAPER": "true",
                "PARAMETER_PROFILE": "premarket_validation_paper_v1",
                "TRADE_WINDOW_START": "07:00",
                "TRADE_WINDOW_END": "11:30",
                "PREFERRED_PULLBACK_DEPTH": "0.50",
                "LARGE_UPPER_WICK_RATIO": "0.50",
                "NEAR_HIGH_OF_DAY_PERCENT": "0.10",
                "MAX_EXTENSION_ABOVE_VWAP_PERCENT": "0.25",
                "MAX_EXTENSION_ABOVE_EMA_PERCENT": "0.10",
            }
        )

        self.assertEqual(value.trade_window_start, time(7, 0))
        self.assertEqual(value.trade_window_end, time(11, 30))
        self.assertEqual(value.preferred_pullback_depth, Decimal("0.50"))
        self.assertEqual(value.large_upper_wick_ratio, Decimal("0.50"))
        self.assertEqual(value.near_high_of_day_percent, Decimal("0.10"))
        self.assertEqual(
            value.max_extension_above_vwap_percent, Decimal("0.25")
        )
        self.assertEqual(
            value.max_extension_above_ema_percent, Decimal("0.10")
        )

    def test_entry_window_must_be_chronological(self) -> None:
        with self.assertRaisesRegex(ValueError, "TRADE_WINDOW_START"):
            validate_settings(
                replace(
                    Settings(),
                    trade_window_start=time(11, 30),
                    trade_window_end=time(7, 0),
                )
            )


if __name__ == "__main__":
    unittest.main()
