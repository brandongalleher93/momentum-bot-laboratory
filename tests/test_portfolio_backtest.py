import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.backtest import BacktestTrade
from bot.diagnostics import DiagnosticFactory
from bot.history_store import HistoryStore
from bot.models import MarketSnapshot
from bot.portfolio_backtest import PortfolioReplayEngine, ReplayGuardrails
from bot.scanner import MarketScanner
from tests.fixtures import breakout_bar, valid_bull_flag_bars


class PortfolioReplayTests(unittest.TestCase):
    @staticmethod
    def _trade(
        entry_time: datetime,
        exit_time: datetime,
        pnl: str,
        *,
        symbol: str = "TEST",
    ) -> BacktestTrade:
        realized = Decimal(pnl)
        return BacktestTrade(
            trade_id=f"{symbol}-{entry_time.isoformat()}",
            symbol=symbol,
            entry_time=entry_time,
            entry_price=Decimal("5"),
            quantity=10,
            stop_price=Decimal("4.90"),
            target_price=Decimal("5.20"),
            initial_risk=Decimal("1"),
            exit_time=exit_time,
            exit_price=Decimal("5.20") if realized >= 0 else Decimal("4.90"),
            exit_reason="test",
            realized_pnl=realized,
            r_multiple=realized,
            ambiguous_same_bar=False,
            setup_id="setup",
            config_version="test",
            parameter_profile="test",
            decision_register_version="test",
        )

    def test_replay_guardrails_filter_cooldown_and_consecutive_losses(self):
        start = datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc)
        trades = [
            self._trade(start, start + timedelta(seconds=30), "2"),
            self._trade(
                start + timedelta(minutes=1),
                start + timedelta(minutes=1, seconds=30),
                "2",
            ),
            self._trade(
                start + timedelta(minutes=3),
                start + timedelta(minutes=3, seconds=30),
                "-1",
            ),
            self._trade(
                start + timedelta(minutes=6),
                start + timedelta(minutes=6, seconds=30),
                "-1",
            ),
            self._trade(
                start + timedelta(minutes=9),
                start + timedelta(minutes=9, seconds=30),
                "2",
            ),
        ]

        kept, rejected = PortfolioReplayEngine._apply_replay_guardrails(
            trades,
            ReplayGuardrails(
                reentry_cooldown_minutes=2,
                max_consecutive_losses_per_symbol_day=2,
            ),
            ZoneInfo("America/New_York"),
        )

        self.assertEqual(kept, [trades[0], trades[2], trades[3]])
        self.assertIn("cooldown", rejected[0]["reason"].lower())
        self.assertIn("consecutive-loss", rejected[1]["reason"].lower())

    def test_replay_guardrail_caps_trades_per_symbol_day(self):
        start = datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc)
        trades = [
            self._trade(
                start + timedelta(minutes=index * 2),
                start + timedelta(minutes=index * 2, seconds=30),
                "1",
            )
            for index in range(3)
        ]

        kept, rejected = PortfolioReplayEngine._apply_replay_guardrails(
            trades,
            ReplayGuardrails(max_trades_per_symbol_day=2),
            ZoneInfo("America/New_York"),
        )

        self.assertEqual(kept, trades[:2])
        self.assertEqual(len(rejected), 1)
        self.assertIn("maximum trades", rejected[0]["reason"].lower())

    def test_replay_ignores_stale_candidates_outside_loaded_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings()
            store = HistoryStore(Path(directory) / "history.sqlite3")
            scanner = MarketScanner(settings, DiagnosticFactory(settings))
            bars = valid_bull_flag_bars("TEST") + [breakout_bar("TEST")]
            evaluation_start = bars[0].timestamp.astimezone(timezone.utc) - timedelta(minutes=1)
            evaluation_end = bars[-1].timestamp.astimezone(timezone.utc) + timedelta(minutes=1)

            current = MarketSnapshot(
                "TEST", bars[-2].timestamp, Decimal("5"), Decimal("0.20"), Decimal("6"),
                600000, Decimal("4.99"), Decimal("5.01"),
            )
            stale = MarketSnapshot(
                "ACHR", datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc), Decimal("5"),
                Decimal("0.20"), Decimal("6"), 600000, Decimal("4.99"), Decimal("5.01"),
            )
            outside_trade_window = MarketSnapshot(
                "TEST",
                datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
                Decimal("5"),
                Decimal("0.20"),
                Decimal("6"),
                600000,
                Decimal("4.99"),
                Decimal("5.01"),
            )
            wrong_date = MarketSnapshot(
                "TEST",
                bars[-2].timestamp + timedelta(days=1),
                Decimal("5"),
                Decimal("0.20"),
                Decimal("6"),
                600000,
                Decimal("4.99"),
                Decimal("5.01"),
            )
            for snapshot in (
                current,
                stale,
                outside_trade_window,
                wrong_date,
            ):
                outcome = scanner.scan([snapshot])
                store.record_scan([snapshot], outcome.diagnostics, source="reconstructed")

            result = PortfolioReplayEngine(settings, store).run(
                {"TEST": bars},
                source="reconstructed",
                feed="iex",
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end + timedelta(days=2),
                evaluation_dates_by_symbol={
                    "TEST": {
                        current.timestamp.astimezone(
                            ZoneInfo(settings.timezone)
                        ).date()
                    }
                },
            )

            self.assertEqual(result.symbols, ["TEST"])
            self.assertEqual(result.candidate_count, 1)
            self.assertEqual(len(result.accepted_trades), 1)
            self.assertTrue(all(trade.symbol == "TEST" for trade in result.accepted_trades))


if __name__ == "__main__":
    unittest.main()
