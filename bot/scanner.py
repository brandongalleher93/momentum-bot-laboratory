"""Pure scanner filters for timestamped market snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.models import DiagnosticResult, MarketSnapshot


@dataclass(frozen=True)
class ScanOutcome:
    candidates: list[MarketSnapshot]
    diagnostics: list[DiagnosticResult]


class MarketScanner:
    def __init__(self, settings: Settings, diagnostics: DiagnosticFactory):
        self.settings = settings
        self.diagnostics = diagnostics

    def scan(self, snapshots: Sequence[MarketSnapshot]) -> ScanOutcome:
        candidates: list[MarketSnapshot] = []
        results: list[DiagnosticResult] = []
        for snapshot in snapshots:
            rejection = self._first_rejection(snapshot)
            if rejection is not None:
                results.append(rejection)
                continue

            float_note = None
            if snapshot.float_shares is None:
                float_note = "Float unavailable; preferred-float check was not enforced."
            elif snapshot.float_shares > self.settings.preferred_float_max:
                float_note = "Float exceeds preference; recorded as a warning, not a hard filter."

            candidates.append(snapshot)
            results.append(
                self.diagnostics.passed(
                    module="scanner",
                    symbol=snapshot.symbol,
                    reason="Scanner filters passed.",
                    decision_time=snapshot.timestamp,
                    data_cutoff_time=snapshot.timestamp,
                    actual_values={
                        "price": snapshot.price,
                        "percent_gain": snapshot.percent_gain,
                        "rvol": snapshot.rvol,
                        "day_volume": snapshot.day_volume,
                        "spread_percent": snapshot.spread_percent,
                        "float_shares": snapshot.float_shares,
                    },
                    expected_values={
                        "price_range": [self.settings.min_price, self.settings.max_price],
                        "min_percent_gain": self.settings.min_percent_gain,
                        "min_rvol": self.settings.min_rvol,
                        "min_day_volume": self.settings.min_day_volume,
                        "max_spread_percent": self.settings.max_spread_percent,
                    },
                    notes=float_note,
                )
            )
        return ScanOutcome(candidates, results)

    def _first_rejection(self, value: MarketSnapshot) -> Optional[DiagnosticResult]:
        checks = (
            (value.price < self.settings.min_price, "Price below minimum.", "price", value.price, self.settings.min_price),
            (value.price > self.settings.max_price, "Price above maximum.", "price", value.price, self.settings.max_price),
            (value.percent_gain < self.settings.min_percent_gain, "Percent gain too low.", "percent_gain", value.percent_gain, self.settings.min_percent_gain),
            (value.rvol < self.settings.min_rvol, "RVOL too low.", "rvol", value.rvol, self.settings.min_rvol),
            (value.day_volume < self.settings.min_day_volume, "Day volume too low.", "day_volume", value.day_volume, self.settings.min_day_volume),
            (value.spread_percent > self.settings.max_spread_percent, "Spread too wide.", "spread_percent", value.spread_percent, self.settings.max_spread_percent),
        )
        for failed, reason, name, actual, expected in checks:
            if failed:
                return self.diagnostics.rejected(
                    module="scanner",
                    symbol=value.symbol,
                    reason=reason,
                    decision_time=value.timestamp,
                    data_cutoff_time=value.timestamp,
                    actual_values={name: actual},
                    expected_values={name: expected},
                )
        return None
