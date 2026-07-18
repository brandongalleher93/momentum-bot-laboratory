import unittest

from bot.config import Settings
from bot.indicators import calculate_indicators
from tests.fixtures import valid_bull_flag_bars


class IndicatorTests(unittest.TestCase):
    def test_indicators_use_latest_completed_session(self) -> None:
        bars = valid_bull_flag_bars()
        result = calculate_indicators(bars, Settings())
        self.assertEqual(result.data_cutoff_time, bars[-1].timestamp)
        self.assertGreater(result.vwap, 0)
        self.assertGreater(result.ema, 0)
        self.assertEqual(result.high_of_day, max(bar.high for bar in bars))


if __name__ == "__main__":
    unittest.main()
