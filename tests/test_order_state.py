import unittest
from decimal import Decimal

from bot.models import OrderRecord, OrderState
from bot.order_state import OrderStateMachine


class OrderStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.order = OrderRecord(
            trade_id="trade",
            client_order_id="client",
            broker_order_id="broker",
            symbol="TEST",
            requested_quantity=10,
            reserved_risk=Decimal("2"),
        )
        self.machine = OrderStateMachine()

    def test_partial_fill_cancels_remainder_then_protects_after_cancel(self) -> None:
        partial = self.machine.apply(
            self.order,
            "partially_filled",
            filled_quantity=4,
            average_fill_price=Decimal("5.10"),
        )
        self.assertTrue(partial.cancel_remainder)
        self.assertFalse(partial.protect_filled_quantity)
        canceled = self.machine.apply(
            self.order,
            "canceled",
            filled_quantity=4,
            average_fill_price=Decimal("5.10"),
        )
        self.assertTrue(canceled.protect_filled_quantity)
        self.assertTrue(canceled.release_reserved_risk)

    def test_duplicate_event_is_ignored(self) -> None:
        self.machine.apply(self.order, "new")
        duplicate = self.machine.apply(self.order, "new")
        self.assertFalse(duplicate.changed)


if __name__ == "__main__":
    unittest.main()
