import unittest
from datetime import date

from scripts.run_reentry_paired_replay import validate_daily_coverage


class ReplayCoverageTests(unittest.TestCase):
    def test_empty_expectations_cannot_claim_complete_coverage(self):
        for required in ({}, {"TEST": set()}):
            with self.subTest(required=required):
                with self.assertRaisesRegex(ValueError, "nonempty"):
                    validate_daily_coverage(required, {})

    def test_symbol_presence_does_not_hide_a_missing_date(self):
        first, second = date(2026, 7, 1), date(2026, 7, 2)
        for missing_kind in ("minute_bars", "ten_second_bars", "trade_prints"):
            with self.subTest(missing_kind=missing_kind):
                available = {
                    kind: {"TEST": {first, second}}
                    for kind in ("minute_bars", "ten_second_bars", "trade_prints")
                }
                available[missing_kind]["TEST"] = {first}
                with self.assertRaisesRegex(ValueError, missing_kind):
                    validate_daily_coverage({"TEST": {first, second}}, available)

    def test_missing_source_category_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "minute_bars"):
            validate_daily_coverage({"TEST": {date(2026, 7, 1)}}, {})

    def test_complete_dates_allow_extra_context(self):
        first, second = date(2026, 7, 1), date(2026, 7, 2)
        available = {
            kind: {"TEST": {first, second}}
            for kind in ("minute_bars", "ten_second_bars", "trade_prints")
        }
        validate_daily_coverage({"TEST": {second}}, available)
