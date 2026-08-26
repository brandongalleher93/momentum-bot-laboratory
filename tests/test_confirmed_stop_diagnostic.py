import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bot.cli import main
from bot.confirmed_stop_diagnostic import (
    PROTECTIVE_STOP_REASON,
    build_confirmed_stop_diagnostic,
    save_confirmed_stop_diagnostic,
)
from bot.event_log import JsonlEventLog
from bot.shadow_paper import ShadowTrade, ShadowTradeStore


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_closed_trade(
    store: ShadowTradeStore,
    *,
    trade_id: str,
    entry_time: datetime,
    exit_reason: str,
    exit_price: Decimal,
) -> ShadowTrade:
    trade = ShadowTrade(
        trade_id=trade_id,
        symbol="TEST",
        setup_id=f"setup-{trade_id}",
        entry_time=entry_time,
        entry_price=Decimal("5.00"),
        quantity=10,
        stop_price=Decimal("4.90"),
        target_price=Decimal("5.20"),
        breakout_level=Decimal("4.98"),
        initial_risk=Decimal("1.00"),
        status="open",
    )
    store.record_entry(trade)
    return store.record_exit(
        trade_id,
        exit_time=entry_time + timedelta(seconds=20),
        exit_price=exit_price,
        reason=exit_reason,
    )


def audit_row(
    trade: ShadowTrade,
    status: str,
    *,
    reconstruction: dict | None = None,
) -> dict:
    return {
        "trade_id": trade.trade_id,
        "symbol": trade.symbol,
        "entry_time": trade.entry_time.isoformat(),
        "exit_time": trade.exit_time.isoformat(),
        "exit_reason": trade.exit_reason,
        "raw_pnl": str(trade.realized_pnl),
        "raw_r_multiple": str(trade.r_multiple),
        "status": status,
        "entry_price_difference": "0.00",
        "exit_price_difference": "0.00",
        "reconstruction": reconstruction,
    }


class ConfirmedStopDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.ledger = self.base / "shadow_trades.sqlite3"
        self.audit = self.base / "execution_audit.json"
        self.events = self.base / "events.jsonl"
        self.report_path = self.base / "confirmed_stop_diagnostic.json"
        self.entry_time = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary.cleanup()

    def test_builds_separate_cohorts_without_modifying_sources(self):
        store = ShadowTradeStore(self.ledger)
        target = add_closed_trade(
            store,
            trade_id="target",
            entry_time=self.entry_time,
            exit_reason="Shadow 2R target reached.",
            exit_price=Decimal("5.20"),
        )
        confirmed = add_closed_trade(
            store,
            trade_id="confirmed",
            entry_time=self.entry_time + timedelta(minutes=1),
            exit_reason=PROTECTIVE_STOP_REASON,
            exit_price=Decimal("4.90"),
        )
        discrepant = add_closed_trade(
            store,
            trade_id="discrepant",
            entry_time=self.entry_time + timedelta(minutes=2),
            exit_reason=PROTECTIVE_STOP_REASON,
            exit_price=Decimal("4.50"),
        )
        self.audit.write_text(
            json.dumps(
                {
                    "rows": [
                        audit_row(target, "confirmed"),
                        audit_row(confirmed, "confirmed"),
                        audit_row(
                            discrepant,
                            "discrepant",
                            reconstruction={
                                "realized_pnl": "2.00",
                                "r_multiple": "2.00",
                            },
                        ),
                    ]
                }
            ),
            encoding="utf-8",
        )
        event_log = JsonlEventLog(self.events)
        event_log.append(
            {
                "event": "shadow_position_quote",
                "trade_id": "confirmed",
                "spread_percent": Decimal("0.005"),
                "spread_anomaly": False,
                "risk_overrun_at_bid": Decimal("0"),
                "quote_age_seconds": Decimal("1"),
            }
        )
        event_log.append(
            {
                "event": "shadow_exit",
                "trade": {"trade_id": "confirmed"},
                "execution": {
                    "spread_percent": Decimal("0.005"),
                    "spread_anomaly": False,
                    "risk_overrun": Decimal("0"),
                    "quote_age_seconds": Decimal("1"),
                },
            }
        )
        event_log.append(
            {
                "event": "shadow_position_quote",
                "trade_id": "discrepant",
                "spread_percent": Decimal("0.04"),
                "spread_anomaly": True,
                "risk_overrun_at_bid": Decimal("4.00"),
                "quote_age_seconds": Decimal("2"),
            }
        )

        source_hashes = {
            path: file_hash(path)
            for path in (self.ledger, self.audit, self.events)
        }
        report = build_confirmed_stop_diagnostic(
            self.ledger,
            self.audit,
            self.events,
            timezone_name="America/New_York",
            generated_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
        )

        self.assertEqual(
            report["classification_counts"],
            {"confirmed": 1, "discrepant": 1, "unresolved": 0},
        )
        self.assertEqual(report["evidence_boundary"]["ledger_rows_selected"], 2)
        rows = {row["trade_id"]: row for row in report["rows"]}
        self.assertNotIn("target", rows)
        self.assertEqual(
            rows["confirmed"]["adjusted_outcome_source"],
            "confirmed_raw_ledger",
        )
        self.assertEqual(
            rows["discrepant"]["adjusted_outcome_source"],
            "estimated_sip_price_path",
        )
        self.assertEqual(rows["discrepant"]["adjusted_pnl"], Decimal("2.00"))
        self.assertEqual(rows["confirmed"]["repeated_symbol_entry_number"], 2)
        self.assertEqual(rows["discrepant"]["repeated_symbol_entry_number"], 3)
        self.assertFalse(rows["confirmed"]["spread_warning_observed"])
        self.assertTrue(rows["discrepant"]["spread_warning_observed"])
        self.assertTrue(rows["discrepant"]["risk_overrun_observed"])
        self.assertFalse(rows["confirmed"]["primary_marker_observed"])
        self.assertTrue(rows["discrepant"]["primary_marker_observed"])
        self.assertEqual(
            report["exploratory_primary_comparison_percentage_points"],
            Decimal("100"),
        )
        self.assertEqual(
            report["cohorts"]["discrepant"]["adjusted_net_profit"],
            Decimal("2.00"),
        )
        self.assertEqual(
            report["research_plan"]["status"],
            "predeclared_for_future_validation",
        )
        self.assertEqual(report["research_cohorts"]["exploratory"]["count"], 2)
        self.assertEqual(
            report["research_cohorts"]["future_validation"]["count"], 0
        )
        self.assertEqual(
            report["future_validation_assessment"]["status"], "inconclusive"
        )
        self.assertFalse(
            report["future_validation_assessment"]["parameter_change_authorized"]
        )
        self.assertEqual(
            source_hashes,
            {
                path: file_hash(path)
                for path in (self.ledger, self.audit, self.events)
            },
        )
        self.assertFalse(Path(str(self.ledger) + "-wal").exists())
        self.assertFalse(Path(str(self.ledger) + "-shm").exists())

        save_confirmed_stop_diagnostic(
            report,
            self.report_path,
            source_paths=(self.ledger, self.audit, self.events),
        )
        restored = json.loads(self.report_path.read_text(encoding="utf-8"))
        self.assertEqual(restored["classification_counts"]["confirmed"], 1)
        self.assertEqual(self.report_path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(ValueError, "separate from every source"):
            save_confirmed_stop_diagnostic(
                report,
                self.audit,
                source_paths=(self.ledger, self.audit, self.events),
            )

        cli_report = self.base / "cli_confirmed_stop_diagnostic.json"
        output = io.StringIO()
        with patch("bot.cli.enforce_private_umask"), redirect_stdout(output):
            exit_code = main(
                [
                    "confirmed-stop-diagnostic",
                    "--ledger",
                    str(self.ledger),
                    "--audit",
                    str(self.audit),
                    "--events",
                    str(self.events),
                    "--report",
                    str(cli_report),
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertIn("1 confirmed, 1 discrepant", output.getvalue())
        self.assertTrue(cli_report.is_file())

    def test_missing_telemetry_is_not_treated_as_no_anomaly(self):
        store = ShadowTradeStore(self.ledger)
        trade = add_closed_trade(
            store,
            trade_id="unresolved",
            entry_time=self.entry_time,
            exit_reason=PROTECTIVE_STOP_REASON,
            exit_price=Decimal("4.90"),
        )
        self.audit.write_text(
            json.dumps({"rows": [audit_row(trade, "unresolved")]}),
            encoding="utf-8",
        )
        self.events.write_text("", encoding="utf-8")

        report = build_confirmed_stop_diagnostic(
            self.ledger,
            self.audit,
            self.events,
            timezone_name="America/New_York",
        )

        row = report["rows"][0]
        self.assertFalse(row["telemetry_available"])
        self.assertIsNone(row["spread_warning_observed"])
        self.assertIsNone(row["risk_overrun_observed"])
        self.assertIsNone(row["primary_marker_observed"])
        self.assertEqual(
            report["cohorts"]["unresolved"]["telemetry_missing_count"], 1
        )

    def test_rejects_audit_missing_a_protective_stop(self):
        store = ShadowTradeStore(self.ledger)
        add_closed_trade(
            store,
            trade_id="missing",
            entry_time=self.entry_time,
            exit_reason=PROTECTIVE_STOP_REASON,
            exit_price=Decimal("4.90"),
        )
        self.audit.write_text(json.dumps({"rows": []}), encoding="utf-8")
        self.events.write_text("", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "missing protective-stop trade"):
            build_confirmed_stop_diagnostic(
                self.ledger,
                self.audit,
                self.events,
                timezone_name="America/New_York",
            )

    def test_rejects_stale_audit_trade_count(self):
        store = ShadowTradeStore(self.ledger)
        trade = add_closed_trade(
            store,
            trade_id="new-ledger-trade",
            entry_time=self.entry_time,
            exit_reason=PROTECTIVE_STOP_REASON,
            exit_price=Decimal("4.90"),
        )
        self.audit.write_text(
            json.dumps(
                {
                    "ledger_trade_count": 0,
                    "rows": [audit_row(trade, "confirmed")],
                }
            ),
            encoding="utf-8",
        )
        self.events.write_text("", encoding="utf-8")

        with self.assertRaisesRegex(
            ValueError, "0 audited versus 1 in the ledger"
        ):
            build_confirmed_stop_diagnostic(
                self.ledger,
                self.audit,
                self.events,
                timezone_name="America/New_York",
            )


if __name__ == "__main__":
    unittest.main()
