import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bot.app import TradingBot
from bot.broker import BrokerAccount, BrokerOrder
from bot.config import Settings
from bot.models import PositionRecord


class EmptyMarketData:
    def get_scanner_snapshots(self, now):
        return []

    def get_completed_bars(self, symbol, end, lookback_days=2):
        return []

    def get_latest_quote(self, symbol):
        raise AssertionError("Quote should not be requested in this test.")


class ReconciliationBroker:
    def __init__(self, orders):
        self.orders = orders
        self.positions = []

    def get_account(self):
        return BrokerAccount(
            account_id="paper",
            status="ACTIVE",
            equity=Decimal("500"),
            cash=Decimal("500"),
            buying_power=Decimal("500"),
            trading_blocked=False,
        )

    def get_open_orders(self):
        return []

    def get_recent_orders(self):
        return self.orders

    def get_positions(self):
        return self.positions

    def submit_bracket_entry(self, **kwargs):
        raise AssertionError

    def cancel_order(self, broker_order_id):
        raise AssertionError

    def replace_order(self, broker_order_id, **kwargs):
        raise AssertionError

    def submit_oco_exit(self, **kwargs):
        raise AssertionError

    def close_position(self, symbol):
        raise AssertionError

    def cancel_orders_for_symbol(self, symbol):
        raise AssertionError

    def market_is_open(self):
        return True


class AppReconciliationTests(unittest.TestCase):
    def test_closed_broker_position_updates_positive_realized_loss(self) -> None:
        now = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
        exit_order = BrokerOrder(
            broker_order_id="exit",
            client_order_id="exit-client",
            symbol="TEST",
            status="filled",
            requested_quantity=10,
            filled_quantity=10,
            average_fill_price=Decimal("4.90"),
            submitted_at=now,
            side="sell",
            filled_at=now,
            exit_fill_price=Decimal("4.90"),
            exit_fill_quantity=10,
            exit_order_id="exit",
            exit_filled_at=now,
        )
        with tempfile.TemporaryDirectory() as directory:
            settings = replace(Settings(), log_dir=Path(directory) / "logs")
            bot = TradingBot(
                settings, ReconciliationBroker([exit_order]), EmptyMarketData()
            )
            bot.state.positions["trade"] = PositionRecord(
                trade_id="trade",
                symbol="TEST",
                quantity=10,
                average_entry_price=Decimal("5.00"),
                stop_price=Decimal("4.90"),
                target_price=Decimal("5.20"),
                breakout_level=Decimal("5.05"),
                initial_risk=Decimal("1.00"),
                opened_at=now,
            )
            bot._reconcile_positions([exit_order], now)
            self.assertEqual(bot.state.realized_loss, Decimal("1.00"))
            self.assertEqual(bot.state.positions, {})


if __name__ == "__main__":
    unittest.main()
