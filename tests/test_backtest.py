import unittest

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


if __name__ == "__main__":
    unittest.main()
