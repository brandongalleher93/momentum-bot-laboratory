import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.reentry_forward_checkpoint import build_forward_checkpoint_readiness


class ForwardCheckpointReadinessTests(unittest.TestCase):
    def _ledger(self, directory: str, count: int) -> tuple[Path, list[dict]]:
        path = Path(directory) / "shadow_trades.sqlite3"
        rows = []
        with sqlite3.connect(path) as database:
            database.execute(
                "CREATE TABLE shadow_trades ("
                "trade_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, "
                "entry_time TEXT NOT NULL, exit_time TEXT, status TEXT NOT NULL)"
            )
            start = datetime(2026, 9, 21, 13, 30, tzinfo=timezone.utc)
            for index in range(count):
                entry = start + timedelta(minutes=index)
                exit_time = entry + timedelta(seconds=30)
                trade = {
                    "trade_id": f"trade-{index:03d}",
                    "symbol": f"TEST{index % 3}",
                    "entry_time": entry.isoformat(),
                    "exit_time": exit_time.isoformat(),
                }
                rows.append(trade)
                database.execute(
                    "INSERT INTO shadow_trades VALUES (?, ?, ?, ?, 'closed')",
                    (
                        trade["trade_id"],
                        trade["symbol"],
                        trade["entry_time"],
                        trade["exit_time"],
                    ),
                )
        return path, rows

    def _audit(self, directory: str, rows: list[dict]) -> Path:
        path = Path(directory) / "execution_audit.json"
        path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-09-22T00:00:00+00:00",
                    "ledger_trade_count": len(rows),
                    "rows": [
                        {"trade_id": row["trade_id"], "status": "confirmed"}
                        for row in rows
                    ],
                }
            )
        )
        return path

    def _replay(
        self,
        directory: str,
        *,
        later: int,
        role: str = "forward_checkpoint",
    ) -> Path:
        path = Path(directory) / "paired_replay.json"
        path.write_text(
            json.dumps(
                {
                    "experiment": "one_entry_per_symbol_day",
                    "role": role,
                    "evaluation_start": "2026-09-21",
                    "evaluation_end": "2026-09-21",
                    "checkpoint_cutoff": "2026-09-21T14:19:30+00:00",
                    "coverage": {
                        "minute_bars": [],
                        "ten_second_bars": [],
                        "trade_prints": [],
                    },
                    "one_entry_per_symbol_day": {
                        "later_same_symbol_entries_filtered": later
                    },
                }
            )
        )
        return path

    def test_reports_collection_progress_without_accepting_stale_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, rows = self._ledger(directory, 15)
            audit = self._audit(directory, rows[:2])

            report = build_forward_checkpoint_readiness(ledger, audit_path=audit)

            self.assertEqual(report["status"], "collecting")
            self.assertEqual(report["checkpoint"]["cohort_closed_trades"], 15)
            self.assertEqual(report["checkpoint"]["remaining_closed_trades"], 35)
            self.assertEqual(report["sources"]["audit"]["mature_trades"], 2)
            self.assertFalse(report["gates"]["first_50_closed_trades_frozen"])

    def test_freezes_first_50_and_becomes_ready_with_exact_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, rows = self._ledger(directory, 52)
            audit = self._audit(directory, rows)
            replay = self._replay(directory, later=16)

            report = build_forward_checkpoint_readiness(
                ledger, audit_path=audit, replay_report_path=replay
            )

            self.assertEqual(report["status"], "ready_for_evaluation")
            self.assertEqual(report["checkpoint"]["cohort_closed_trades"], 50)
            self.assertEqual(report["checkpoint"]["ignored_later_closed_trades"], 2)
            self.assertTrue(all(report["gates"].values()))

            first_fingerprint = report["checkpoint"]["cohort_sha256"]
            frozen_again = build_forward_checkpoint_readiness(
                ledger, audit_path=audit, replay_report_path=replay
            )
            self.assertEqual(
                frozen_again["checkpoint"]["cohort_sha256"], first_fingerprint
            )

    def test_marks_complete_but_small_opportunity_cohort_inconclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, rows = self._ledger(directory, 50)
            audit = self._audit(directory, rows)
            replay = self._replay(directory, later=14)

            report = build_forward_checkpoint_readiness(
                ledger, audit_path=audit, replay_report_path=replay
            )

            self.assertEqual(
                report["status"], "inconclusive_insufficient_opportunities"
            )
            self.assertIsNone(report["decision"])

    def test_rejects_retrospective_report_for_forward_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, rows = self._ledger(directory, 50)
            audit = self._audit(directory, rows)
            replay = self._replay(directory, later=20, role="retrospective_exploratory")

            report = build_forward_checkpoint_readiness(
                ledger, audit_path=audit, replay_report_path=replay
            )

            self.assertEqual(report["status"], "checkpoint_evidence_pending")
            self.assertFalse(report["gates"]["exact_forward_replay_window"])

    def test_rejects_missing_coverage_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, rows = self._ledger(directory, 50)
            audit = self._audit(directory, rows)
            replay = self._replay(directory, later=20)
            payload = json.loads(replay.read_text())
            payload["coverage"] = {}
            replay.write_text(json.dumps(payload))

            report = build_forward_checkpoint_readiness(
                ledger, audit_path=audit, replay_report_path=replay
            )

            self.assertEqual(report["status"], "checkpoint_evidence_pending")
            self.assertTrue(report["gates"]["exact_forward_replay_window"])
            self.assertFalse(report["gates"]["complete_replay_coverage"])

    def test_rejects_replay_that_extends_past_trade_50(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger, rows = self._ledger(directory, 50)
            audit = self._audit(directory, rows)
            replay = self._replay(directory, later=20)
            payload = json.loads(replay.read_text())
            payload["checkpoint_cutoff"] = "2026-09-21T23:59:59+00:00"
            replay.write_text(json.dumps(payload))

            report = build_forward_checkpoint_readiness(
                ledger, audit_path=audit, replay_report_path=replay
            )

            self.assertEqual(report["status"], "checkpoint_evidence_pending")
            self.assertFalse(report["gates"]["exact_forward_replay_window"])


if __name__ == "__main__":
    unittest.main()
