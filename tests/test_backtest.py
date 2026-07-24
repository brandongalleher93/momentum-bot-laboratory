import unittest
from dataclasses import replace
from datetime import timedelta

from bot.backtest import BacktestEngine
from bot.config import Settings
from tests.fixtures import breakout_bar, valid_bull_flag_bars


class BacktestTests(unittest.TestCase):
    def test_same_bar_stop_and_target_resolves_stop_first(self) -> None:
        bars = valid_bull_flag_bars()
        bars.append(breakout_bar())
        result = BacktestEngine(Settings()).run(bars)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertIn("stop", trade.exit_reason.lower())
        self.assertTrue(trade.ambiguous_same_bar)
        self.assertLess(trade.realized_pnl, 0)

    def test_allowed_decision_times_gate_setup_evaluation(self) -> None:
        bars = valid_bull_flag_bars()
        bars.append(breakout_bar())
        engine = BacktestEngine(Settings())

        blocked = engine.run(bars, allowed_decision_times=set())
        allowed = engine.run(bars, allowed_decision_times={bars[-2].timestamp})

        self.assertEqual(blocked.entry_opportunities, 0)
        self.assertEqual(len(blocked.trades), 0)
        self.assertEqual(allowed.entry_opportunities, 1)
        self.assertEqual(len(allowed.trades), 1)

    def test_rejection_details_capture_reason_and_timestamp(self) -> None:
        bars = valid_bull_flag_bars()
        first_breakout = breakout_bar()
        bars.extend(
            [
                first_breakout,
                replace(first_breakout, timestamp=first_breakout.timestamp + timedelta(minutes=1)),
            ]
        )
        result = BacktestEngine(Settings()).run(
            bars,
            allowed_decision_times={bars[-2].timestamp},
        )

        self.assertEqual(result.setup_rejections, 1)
        self.assertEqual(result.rejection_details[0]["timestamp"], bars[-2].timestamp)
        self.assertTrue(result.rejection_details[0]["reason"])


if __name__ == "__main__":
    unittest.main()
