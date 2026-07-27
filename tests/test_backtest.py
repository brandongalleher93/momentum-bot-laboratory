import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from bot.backtest import BacktestEngine
from bot.config import Settings
from bot.historical_data import TradeTick
from bot.models import Bar
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

    def test_ten_second_bars_choose_first_breakout_trigger(self) -> None:
        bars = valid_bull_flag_bars()
        minute_event = breakout_bar()
        bars.append(minute_event)
        cutoff = bars[-2].timestamp
        execution_bars = [
            Bar(
                symbol="TEST",
                timestamp=cutoff + timedelta(seconds=10),
                open=Decimal("5.07"),
                high=Decimal("5.08"),
                low=Decimal("5.06"),
                close=Decimal("5.07"),
                volume=100,
            ),
            Bar(
                symbol="TEST",
                timestamp=cutoff + timedelta(seconds=20),
                open=Decimal("5.08"),
                high=Decimal("5.12"),
                low=Decimal("5.08"),
                close=Decimal("5.11"),
                volume=250,
            ),
        ]

        result = BacktestEngine(Settings()).run(
            bars,
            allowed_decision_times={cutoff},
            execution_bars=execution_bars,
        )

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_time, execution_bars[1].timestamp)
        self.assertTrue(any("Ten-second bars" in note for note in result.notes))

    def test_micro_replay_uses_scanner_gate_and_ten_second_setup(self) -> None:
        context = valid_bull_flag_bars()
        start = context[-1].timestamp
        execution = [
            Bar("TEST", start, Decimal("5.00"), Decimal("5.02"), Decimal("4.99"), Decimal("5.01"), 100),
            Bar("TEST", start + timedelta(seconds=10), Decimal("5.01"), Decimal("5.30"), Decimal("5.00"), Decimal("5.28"), 300),
            Bar("TEST", start + timedelta(seconds=20), Decimal("5.28"), Decimal("5.50"), Decimal("5.27"), Decimal("5.48"), 400),
            Bar("TEST", start + timedelta(seconds=30), Decimal("5.48"), Decimal("5.49"), Decimal("5.38"), Decimal("5.40"), 150),
            Bar("TEST", start + timedelta(seconds=40), Decimal("5.40"), Decimal("5.45"), Decimal("5.35"), Decimal("5.42"), 100),
            Bar("TEST", start + timedelta(seconds=50), Decimal("5.42"), Decimal("5.75"), Decimal("5.42"), Decimal("5.70"), 500),
        ]
        engine = BacktestEngine(Settings())

        blocked = engine.run_micro(
            context, execution, allowed_decision_times=set()
        )
        allowed = engine.run_micro(
            context,
            execution,
            allowed_decision_times={execution[-2].timestamp},
        )

        self.assertEqual(blocked.trades, [])
        self.assertEqual(len(allowed.trades), 1)
        self.assertEqual(allowed.trades[0].entry_time, execution[-1].timestamp)

    def test_micro_replay_uses_trade_order_after_entry(self) -> None:
        context = valid_bull_flag_bars()
        start = context[-1].timestamp
        execution = [
            Bar("TEST", start, Decimal("5.00"), Decimal("5.02"), Decimal("4.99"), Decimal("5.01"), 100),
            Bar("TEST", start + timedelta(seconds=10), Decimal("5.01"), Decimal("5.30"), Decimal("5.00"), Decimal("5.28"), 300),
            Bar("TEST", start + timedelta(seconds=20), Decimal("5.28"), Decimal("5.50"), Decimal("5.27"), Decimal("5.48"), 400),
            Bar("TEST", start + timedelta(seconds=30), Decimal("5.48"), Decimal("5.49"), Decimal("5.38"), Decimal("5.40"), 150),
            Bar("TEST", start + timedelta(seconds=40), Decimal("5.40"), Decimal("5.45"), Decimal("5.35"), Decimal("5.42"), 100),
            Bar("TEST", start + timedelta(seconds=50), Decimal("5.42"), Decimal("5.75"), Decimal("5.20"), Decimal("5.70"), 500),
            Bar("TEST", start + timedelta(seconds=60), Decimal("5.70"), Decimal("6.50"), Decimal("5.69"), Decimal("6.40"), 500),
        ]
        trigger_bar = execution[-2]
        ticks = [
            TradeTick("TEST", trigger_bar.timestamp - timedelta(seconds=9), Decimal("5.20"), 10),
            TradeTick("TEST", trigger_bar.timestamp - timedelta(seconds=8), Decimal("5.52"), 10),
            TradeTick("TEST", trigger_bar.timestamp - timedelta(seconds=7), Decimal("5.20"), 10),
            TradeTick("TEST", execution[-1].timestamp - timedelta(seconds=5), Decimal("6.50"), 10),
        ]

        result = BacktestEngine(Settings()).run_micro(
            context,
            execution,
            allowed_decision_times={execution[-3].timestamp},
            execution_trades=ticks,
        )

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_time, ticks[1].timestamp)
        self.assertIn("stop", trade.exit_reason.lower())
        self.assertEqual(trade.exit_time, ticks[2].timestamp)
        self.assertFalse(trade.ambiguous_same_bar)


if __name__ == "__main__":
    unittest.main()
