import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from bot.config import Settings
from bot.models import Quote
from bot.shadow_execution_audit import (
    ShadowExecutionAuditor,
    load_execution_audit,
    save_execution_audit,
)
from bot.shadow_paper import ShadowTrade


class FakeHistoricalQuoteProvider:
    def __init__(self, quotes):
        self.quotes = quotes
        self.calls = []

    def get_quotes(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        return [
            quote
            for quote in self.quotes.get(symbol, [])
            if start <= quote.timestamp <= end
        ]


def make_trade(
    now,
    *,
    trade_id="trade",
    exit_reason="Shadow protective stop reached.",
    exit_price=Decimal("4.90"),
):
    entry_price = Decimal("5.00")
    quantity = 10
    initial_risk = Decimal("1.00")
    pnl = (exit_price - entry_price) * Decimal(quantity)
    return ShadowTrade(
        trade_id=trade_id,
        symbol="TEST",
        setup_id="setup",
        entry_time=now,
        entry_price=entry_price,
        quantity=quantity,
        stop_price=Decimal("4.90"),
        target_price=Decimal("5.20"),
        breakout_level=Decimal("4.98"),
        initial_risk=initial_risk,
        status="closed",
        exit_time=now + timedelta(minutes=1),
        exit_price=exit_price,
        exit_reason=exit_reason,
        realized_pnl=pnl,
        r_multiple=pnl / initial_risk,
    )


class ShadowExecutionAuditTests(unittest.TestCase):
    def setUp(self):
        self.entry_time = datetime(2026, 7, 1, 14, 0, tzinfo=timezone.utc)
        self.audit_time = self.entry_time + timedelta(days=1)
        self.settings = Settings(
            trade_window_start=time(7, 0),
            trade_window_end=time(11, 30),
        )

    def test_confirmed_stop_uses_nearby_sip_quotes(self):
        trade = make_trade(self.entry_time)
        provider = FakeHistoricalQuoteProvider(
            {
                "TEST": [
                    Quote(
                        "TEST",
                        trade.entry_time,
                        Decimal("4.98"),
                        Decimal("5.00"),
                    ),
                    Quote(
                        "TEST",
                        trade.exit_time,
                        Decimal("4.90"),
                        Decimal("4.92"),
                    ),
                ]
            }
        )

        report = ShadowExecutionAuditor(self.settings, provider).audit(
            [trade], now=self.audit_time
        )

        row = report.rows[0]
        self.assertEqual(row.status, "confirmed")
        self.assertIsNone(row.reconstruction)
        self.assertEqual(report.summary()["confirmed_count"], 1)
        self.assertEqual(report.summary()["raw_net_profit"], Decimal("-1.00"))

    def test_false_iex_stop_is_discrepant_and_reconstructed_to_target(self):
        trade = make_trade(self.entry_time, exit_price=Decimal("4.50"))
        provider = FakeHistoricalQuoteProvider(
            {
                "TEST": [
                    Quote(
                        "TEST",
                        trade.entry_time,
                        Decimal("4.98"),
                        Decimal("5.00"),
                    ),
                    Quote(
                        "TEST",
                        trade.exit_time,
                        Decimal("4.97"),
                        Decimal("4.99"),
                    ),
                    Quote(
                        "TEST",
                        trade.exit_time + timedelta(seconds=20),
                        Decimal("5.20"),
                        Decimal("5.22"),
                    ),
                ]
            }
        )

        report = ShadowExecutionAuditor(self.settings, provider).audit(
            [trade], now=self.audit_time
        )

        row = report.rows[0]
        self.assertEqual(row.status, "discrepant")
        self.assertIn("remained above", row.explanation)
        self.assertIsNotNone(row.reconstruction)
        self.assertEqual(row.reconstruction.exit_price, Decimal("5.20"))
        self.assertEqual(row.reconstruction.realized_pnl, Decimal("2.00"))
        summary = report.summary()
        self.assertEqual(summary["raw_net_profit"], Decimal("-5.00"))
        self.assertEqual(
            summary["estimated_sip_path_net_profit"], Decimal("2.00")
        )

    def test_recent_or_missing_sip_evidence_remains_unresolved(self):
        trade = make_trade(self.entry_time)
        provider = FakeHistoricalQuoteProvider({})
        auditor = ShadowExecutionAuditor(self.settings, provider)

        recent = auditor.audit(
            [trade],
            now=trade.exit_time + timedelta(minutes=5),
        )
        mature = auditor.audit([trade], now=self.audit_time)

        self.assertEqual(recent.rows[0].status, "unresolved")
        self.assertIn("not old enough", recent.rows[0].explanation)
        self.assertEqual(mature.rows[0].status, "unresolved")
        self.assertIn("No nearby", mature.rows[0].explanation)

    def test_report_round_trip_is_separate_from_trade_ledger(self):
        trade = make_trade(self.entry_time)
        provider = FakeHistoricalQuoteProvider(
            {
                "TEST": [
                    Quote(
                        "TEST",
                        trade.entry_time,
                        Decimal("4.98"),
                        Decimal("5.00"),
                    ),
                    Quote(
                        "TEST",
                        trade.exit_time,
                        Decimal("4.90"),
                        Decimal("4.92"),
                    ),
                ]
            }
        )
        report = ShadowExecutionAuditor(self.settings, provider).audit(
            [trade], now=self.audit_time
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "execution_audit.json"
            save_execution_audit(report, path)
            restored = load_execution_audit(path)

        self.assertEqual(restored["summary"]["confirmed_count"], 1)
        self.assertEqual(restored["rows"][0]["trade_id"], trade.trade_id)
        self.assertEqual(trade.realized_pnl, Decimal("-1.00"))


if __name__ == "__main__":
    unittest.main()
