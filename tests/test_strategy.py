import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.models import Bar, Quote
from bot.strategy import BullFlagStrategy, MicroPullbackStrategy
from tests.fixtures import valid_bull_flag_bars


class StrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.strategy = BullFlagStrategy(
            self.settings, DiagnosticFactory(self.settings)
        )
        self.bars = valid_bull_flag_bars()

    def test_detects_first_pullback_from_completed_bars(self) -> None:
        result = self.strategy.detect_setup(
            "TEST", self.bars, self.bars[-1].timestamp
        )
        self.assertIsNotNone(result.value, result.diagnostic.reason)
        setup = result.value
        assert setup is not None
        self.assertEqual(len(setup.pullback.bars), 2)
        self.assertEqual(setup.trigger_price, Decimal("5.09"))
        self.assertEqual(setup.stop_price, Decimal("5.05"))

    def test_intrabar_entry_does_not_need_candle_close(self) -> None:
        setup_result = self.strategy.detect_setup(
            "TEST", self.bars, self.bars[-1].timestamp
        )
        setup = setup_result.value
        assert setup is not None
        quote = Quote(
            symbol="TEST",
            timestamp=self.bars[-1].timestamp + timedelta(seconds=15),
            bid=Decimal("5.085"),
            ask=Decimal("5.09"),
        )
        result = self.strategy.check_entry(setup, quote)
        self.assertIsNotNone(result.value)
        assert result.value is not None
        self.assertTrue(result.value.triggered)
        self.assertEqual(result.value.maximum_entry_price, Decimal("5.11"))
        self.assertIn("unfinished candle", result.diagnostic.notes or "")

    def test_trade_plan_uses_pullback_low_and_two_r(self) -> None:
        setup = self.strategy.detect_setup(
            "TEST", self.bars, self.bars[-1].timestamp
        ).value
        assert setup is not None
        quote = Quote(
            symbol="TEST",
            timestamp=self.bars[-1].timestamp + timedelta(seconds=15),
            bid=Decimal("5.085"),
            ask=Decimal("5.09"),
        )
        signal = self.strategy.check_entry(setup, quote).value
        assert signal is not None
        plan = self.strategy.create_trade_plan(setup, signal).value
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.stop_price, Decimal("5.05"))
        self.assertEqual(plan.reward_to_risk, Decimal("2.0"))

    def test_reports_closest_flagpole_failure_with_measurements(self) -> None:
        settings = replace(
            self.settings, strong_up_move_min_percent=Decimal("0.50")
        )
        strategy = BullFlagStrategy(settings, DiagnosticFactory(settings))

        result = strategy.detect_setup(
            "TEST", self.bars, self.bars[-1].timestamp
        )

        self.assertIsNone(result.value)
        self.assertEqual(
            result.diagnostic.reason,
            "Flagpole move is below the configured minimum.",
        )
        self.assertIn("flagpole_percent_move", result.diagnostic.actual_values)
        self.assertEqual(
            result.diagnostic.expected_values["minimum_flagpole_percent_move"],
            Decimal("0.50"),
        )
        self.assertEqual(
            result.diagnostic.actual_values["candidate_rules_passed"], 2
        )

    def test_reports_closest_pullback_depth_failure_with_measurements(self) -> None:
        settings = replace(
            self.settings, preferred_pullback_depth=Decimal("0.01")
        )
        strategy = BullFlagStrategy(settings, DiagnosticFactory(settings))

        result = strategy.detect_setup(
            "TEST", self.bars, self.bars[-1].timestamp
        )

        self.assertIsNone(result.value)
        self.assertEqual(
            result.diagnostic.reason,
            "Pullback depth exceeds the configured maximum.",
        )
        self.assertEqual(
            result.diagnostic.actual_values["pullback_depth"], Decimal("0.25")
        )
        self.assertEqual(
            result.diagnostic.actual_values["closest_pullback_candles"], 2
        )

    def test_reports_closest_pullback_volume_failure_with_measurements(self) -> None:
        settings = replace(
            self.settings, pullback_volume_ratio_max=Decimal("0.01")
        )
        strategy = BullFlagStrategy(settings, DiagnosticFactory(settings))

        result = strategy.detect_setup(
            "TEST", self.bars, self.bars[-1].timestamp
        )

        self.assertIsNone(result.value)
        self.assertEqual(
            result.diagnostic.reason,
            "Pullback volume did not contract enough.",
        )
        self.assertEqual(
            result.diagnostic.actual_values["pullback_volume_ratio"],
            Decimal("0.3"),
        )
        self.assertEqual(
            result.diagnostic.actual_values["candidate_rules_passed"], 9
        )

    def test_zero_range_flagpole_is_rejected_without_division_error(self) -> None:
        setup = self.strategy.detect_setup(
            "TEST", self.bars, self.bars[-1].timestamp
        ).value
        assert setup is not None
        zero_range_flagpole = replace(
            setup.flagpole, average_range=Decimal("0")
        )

        pullback, failure = self.strategy._inspect_pullback(
            setup.pullback.bars, zero_range_flagpole
        )

        self.assertIsNone(pullback)
        assert failure is not None
        self.assertEqual(
            failure.reason,
            "Flagpole average candle range is not positive.",
        )

    def test_detects_completed_ten_second_micro_pullback(self) -> None:
        start = self.bars[-1].timestamp
        micro_bars = [
            Bar("TEST", start, Decimal("5.00"), Decimal("5.02"), Decimal("4.99"), Decimal("5.01"), 100),
            Bar("TEST", start + timedelta(seconds=10), Decimal("5.01"), Decimal("5.30"), Decimal("5.00"), Decimal("5.28"), 300),
            Bar("TEST", start + timedelta(seconds=20), Decimal("5.28"), Decimal("5.50"), Decimal("5.27"), Decimal("5.48"), 400),
            Bar("TEST", start + timedelta(seconds=30), Decimal("5.48"), Decimal("5.49"), Decimal("5.38"), Decimal("5.40"), 150),
            Bar("TEST", start + timedelta(seconds=40), Decimal("5.40"), Decimal("5.45"), Decimal("5.35"), Decimal("5.42"), 100),
        ]
        strategy = MicroPullbackStrategy(
            self.settings, DiagnosticFactory(self.settings)
        )

        result = strategy.detect_setup(
            "TEST", micro_bars, micro_bars[-1].timestamp
        )

        self.assertIsNotNone(result.value, result.diagnostic.reason)
        assert result.value is not None
        self.assertEqual(result.value.breakout_level, Decimal("5.50"))
        self.assertEqual(result.value.trigger_price, Decimal("5.51"))
        self.assertIn(
            "micro_pullback_breakout",
            result.diagnostic.actual_values["pattern"],
        )


if __name__ == "__main__":
    unittest.main()
