import unittest
from datetime import datetime, timezone
from decimal import Decimal

from bot.config import Settings
from bot.models import RiskSnapshot, TradePlan
from bot.risk_manager import RiskLedger, RiskManager


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


class RiskManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RiskManager(Settings())
        self.now = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)

    def test_sizes_by_risk_and_position_value(self) -> None:
        result = self.manager.evaluate(plan(), RiskSnapshot(), self.now)
        self.assertTrue(result.approval.approved)
        self.assertEqual(result.approval.quantity, 24)
        self.assertEqual(result.approval.proposed_risk, Decimal("1.44"))

    def test_projected_loss_includes_open_pending_and_proposed(self) -> None:
        result = self.manager.evaluate(
            plan(),
            RiskSnapshot(
                realized_loss=Decimal("10"),
                open_stop_risk=Decimal("2"),
                pending_order_risk=Decimal("2"),
            ),
            self.now,
        )
        self.assertFalse(result.approval.approved)
        self.assertIn("Projected", result.approval.reason)

    def test_risk_ledger_reservation_is_idempotent(self) -> None:
        ledger = RiskLedger()
        self.assertTrue(
            ledger.reserve(
                "trade", Decimal("5"), Decimal("0"), Decimal("0"), Decimal("15")
            )
        )
        self.assertTrue(
            ledger.reserve(
                "trade", Decimal("5"), Decimal("0"), Decimal("0"), Decimal("15")
            )
        )
        self.assertEqual(ledger.total_reserved, Decimal("5"))


if __name__ == "__main__":
    unittest.main()
