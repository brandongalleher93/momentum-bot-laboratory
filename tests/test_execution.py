import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bot.broker import BrokerAccount, BrokerOrder
from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.event_log import DiagnosticLogger
from bot.execution import ExecutionService
from bot.models import RiskApproval, TradePlan
from bot.risk_manager import RiskLedger
from bot.session import SessionState


class FakeBroker:
    def __init__(self) -> None:
        self.submissions = 0
        self.canceled: list[str] = []
        self.protections = 0
        self.closed: list[str] = []
        self.replacements: list[tuple[str, dict]] = []

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
        return []

    def get_positions(self):
        return []

    def submit_bracket_entry(self, **kwargs):
        self.submissions += 1
        return BrokerOrder(
            broker_order_id="broker-entry",
            client_order_id=kwargs["client_order_id"],
            symbol=kwargs["plan"].symbol,
            status="new",
            requested_quantity=kwargs["quantity"],
            filled_quantity=0,
            average_fill_price=None,
            submitted_at=datetime.now(timezone.utc),
        )

    def cancel_order(self, broker_order_id):
        self.canceled.append(broker_order_id)

    def replace_order(self, broker_order_id, **kwargs):
        self.replacements.append((broker_order_id, kwargs))
        return BrokerOrder(
            broker_order_id=broker_order_id,
            client_order_id="replacement",
            symbol="TEST",
            status="replaced",
            requested_quantity=10,
            filled_quantity=10,
            average_fill_price=Decimal("5.10"),
            submitted_at=datetime.now(timezone.utc),
        )

    def submit_oco_exit(self, **kwargs):
        self.protections += 1
        return BrokerOrder(
            broker_order_id="broker-protection",
            client_order_id="protect",
            symbol=kwargs["symbol"],
            status="new",
            requested_quantity=kwargs["quantity"],
            filled_quantity=0,
            average_fill_price=None,
            submitted_at=datetime.now(timezone.utc),
        )

    def close_position(self, symbol):
        self.closed.append(symbol)

    def cancel_orders_for_symbol(self, symbol):
        return None

    def market_is_open(self):
        return True


def plan() -> TradePlan:
    return TradePlan(
        symbol="TEST",
        setup_id="setup",
        maximum_entry_price=Decimal("5.11"),
        stop_price=Decimal("5.05"),
        target_price=Decimal("5.23"),
        maximum_risk_per_share=Decimal("0.06"),
        reward_to_risk=Decimal("2"),
        breakout_level=Decimal("5.08"),
    )


def approval() -> RiskApproval:
    return RiskApproval(
        approved=True,
        reason="approved",
        quantity=10,
        proposed_risk=Decimal("0.60"),
        position_value=Decimal("51.10"),
        projected_daily_loss=Decimal("0.60"),
    )


class ExecutionTests(unittest.TestCase):
    def make_service(self, directory: str, armed: bool):
        settings = replace(
            Settings(),
            paper_order_submission_enabled=armed,
            log_dir=Path(directory) / "logs",
        )
        broker = FakeBroker()
        state = SessionState()
        service = ExecutionService(
            settings=settings,
            broker=broker,
            state=state,
            ledger=RiskLedger(),
            diagnostics=DiagnosticFactory(settings),
            diagnostic_logger=DiagnosticLogger(settings.log_dir),
        )
        return service, broker, state

    def test_submission_is_disarmed_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, broker, _ = self.make_service(directory, False)
            result = service.submit(
                plan(), approval(), datetime.now(timezone.utc)
            )
            self.assertFalse(result.passed)
            self.assertEqual(broker.submissions, 0)

    def test_partial_fill_waits_for_cancel_then_adds_oco_protection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, broker, state = self.make_service(directory, True)
            service.submit(plan(), approval(), datetime.now(timezone.utc))
            local = next(iter(state.orders.values()))
            partial = BrokerOrder(
                broker_order_id="broker-entry",
                client_order_id=local.client_order_id,
                symbol="TEST",
                status="partially_filled",
                requested_quantity=10,
                filled_quantity=4,
                average_fill_price=Decimal("5.10"),
                submitted_at=datetime.now(timezone.utc),
            )
            service.process_broker_order(partial)
            self.assertEqual(broker.canceled, ["broker-entry"])
            self.assertEqual(broker.protections, 0)

            canceled = replace(partial, status="canceled")
            service.process_broker_order(canceled)
            self.assertEqual(broker.protections, 1)
            self.assertTrue(local.protection_confirmed)

    def test_full_fill_replaces_provisional_target_from_actual_fill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, broker, state = self.make_service(directory, True)
            service.submit(plan(), approval(), datetime.now(timezone.utc))
            local = next(iter(state.orders.values()))
            filled = BrokerOrder(
                broker_order_id="broker-entry",
                client_order_id=local.client_order_id,
                symbol="TEST",
                status="filled",
                requested_quantity=10,
                filled_quantity=10,
                average_fill_price=Decimal("5.10"),
                submitted_at=datetime.now(timezone.utc),
                filled_at=datetime.now(timezone.utc),
                child_order_ids=("take-profit", "stop"),
                take_profit_order_id="take-profit",
                stop_loss_order_id="stop",
            )
            service.process_broker_order(filled)
            self.assertEqual(len(broker.replacements), 1)
            self.assertEqual(
                broker.replacements[0][1]["limit_price"], Decimal("5.20")
            )
            self.assertIn(local.trade_id, state.positions)


if __name__ == "__main__":
    unittest.main()
