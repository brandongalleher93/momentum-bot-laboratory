import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from bot.config import PROJECT_ROOT
from bot.validation_set import (
    load_validation_targets,
    target_dates_by_symbol,
    validation_download_windows,
)


class ValidationSetTests(unittest.TestCase):
    def test_built_in_manifests_are_unique_and_meet_basic_thresholds(self):
        paths = [
            PROJECT_ROOT / "data" / "independent_momentum_validation.csv",
            PROJECT_ROOT / "data" / "independent_momentum_validation_2.csv",
        ]
        targets = [
            target
            for path in paths
            for target in load_validation_targets(path)
        ]

        self.assertEqual(len(targets), 33)
        self.assertEqual(
            len({(target.trade_date, target.symbol) for target in targets}),
            33,
        )
        for target in targets:
            self.assertGreaterEqual(target.observed_price, 2)
            self.assertLessEqual(target.observed_price, 20)
            self.assertGreaterEqual(target.premarket_gain_percent, 10)
            self.assertGreaterEqual(target.observed_volume, 500_000)

    def test_manifest_loads_and_groups_target_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.csv"
            path.write_text(
                "trade_date,symbol,premarket_gain_percent,observed_price,"
                "observed_volume,source_url\n"
                "2026-07-24,test,25.5,4.20,1000000,https://example.com\n",
                encoding="utf-8",
            )

            targets = load_validation_targets(path)
            grouped = target_dates_by_symbol(targets)

        self.assertEqual(targets[0].symbol, "TEST")
        self.assertEqual(grouped, {"TEST": {date(2026, 7, 24)}})

    def test_download_windows_limit_execution_to_target_local_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.csv"
            path.write_text(
                "trade_date,symbol,premarket_gain_percent,observed_price,"
                "observed_volume,source_url\n"
                "2026-07-24,TEST,25.5,4.20,1000000,https://example.com\n",
                encoding="utf-8",
            )
            target = load_validation_targets(path)[0]

        minute_start, minute_end, execution_start, execution_end = (
            validation_download_windows(target, "America/New_York")
        )

        self.assertEqual(
            minute_start,
            datetime(2026, 6, 19, 4, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            execution_start,
            datetime(2026, 7, 24, 4, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(minute_end, execution_end)
        self.assertEqual(execution_end.date(), date(2026, 7, 25))


if __name__ == "__main__":
    unittest.main()
