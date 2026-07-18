import unittest
from datetime import timedelta
from decimal import Decimal

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.models import Quote
from bot.strategy import BullFlagStrategy
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


if __name__ == "__main__":
    unittest.main()
