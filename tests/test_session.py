import unittest
from datetime import date
from decimal import Decimal

from bot.session import SessionState


class SessionStateTests(unittest.TestCase):
    def test_symbol_loss_streaks_are_isolated_by_symbol_and_day(self):
        state = SessionState()
        first_day = date(2026, 7, 15)
        second_day = date(2026, 7, 16)

        state.record_closed_pnl(
            Decimal("-1"), symbol="TEST", session_date=first_day
        )
        state.record_closed_pnl(
            Decimal("-2"), symbol="test", session_date=first_day
        )

        self.assertEqual(
            state.risk_snapshot(
                symbol="TEST", session_date=first_day
            ).symbol_consecutive_losses,
            2,
        )
        self.assertEqual(
            state.risk_snapshot(
                symbol="OTHER", session_date=first_day
            ).symbol_consecutive_losses,
            0,
        )
        self.assertEqual(
            state.risk_snapshot(
                symbol="TEST", session_date=second_day
            ).symbol_consecutive_losses,
            0,
        )

        state.record_closed_pnl(
            Decimal("3"), symbol="TEST", session_date=first_day
        )
        self.assertEqual(
            state.risk_snapshot(
                symbol="TEST", session_date=first_day
            ).symbol_consecutive_losses,
            0,
        )


if __name__ == "__main__":
    unittest.main()
