"""Reconstruct approximate historical scanner candidates from point-in-time bars."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.history_store import HistoryStore
from bot.models import Bar, MarketSnapshot
from bot.scanner import MarketScanner


class CandidateReconstructor:
    """Approximation only: spread is supplied or conservatively estimated."""

    def __init__(self, settings: Settings, store: HistoryStore):
        self.settings, self.store = settings, store
        self.scanner = MarketScanner(settings, DiagnosticFactory(settings))

    def reconstruct(
        self,
        bars_by_symbol: Mapping[str, Sequence[Bar]],
        *,
        execution_bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None,
        decision_minutes: Sequence[datetime] | None = None,
        estimated_spread_percent: Decimal = Decimal("0.005"),
    ) -> int:
        eastern = ZoneInfo(self.settings.timezone)
        indexed = {symbol: self._index(bars, eastern) for symbol, bars in bars_by_symbol.items()}
        execution_indexed = {
            symbol: self._index(bars, eastern)
            for symbol, bars in (execution_bars_by_symbol or {}).items()
        }
        minute_lookup = {
            symbol: self._lookup(days, eastern) for symbol, days in indexed.items()
        }
        current_lookup = {
            symbol: self._lookup(execution_indexed.get(symbol, days), eastern)
            for symbol, days in indexed.items()
        }
        if decision_minutes is None:
            decision_source = (
                execution_bars_by_symbol
                if execution_bars_by_symbol
                else bars_by_symbol
            )
            decision_minutes = sorted({bar.timestamp for bars in decision_source.values() for bar in bars if self.settings.trade_window_start <= bar.timestamp.astimezone(eastern).time() <= self.settings.trade_window_end})
        recorded = 0
        pending_scans = []
        for minute in decision_minutes:
            snapshots = []
            for symbol, days in indexed.items():
                local_date = minute.astimezone(eastern).date()
                current_day = current_lookup[symbol].get(local_date)
                prior_dates = sorted(date for date in days if date < local_date)
                if current_day is None or not prior_dates:
                    continue
                current_position = bisect_right(
                    current_day["timestamps"], minute
                )
                if current_position == 0:
                    continue
                prior = days[prior_dates[-1]]
                prior_close = prior[-1].close
                price = current_day["bars"][current_position - 1].close
                gain = (price - prior_close) / prior_close if prior_close > 0 else Decimal("0")
                current_volume = current_day["cumulative_volume"][
                    current_position - 1
                ]
                aligned_history = []
                cutoff_time = minute.astimezone(eastern).time()
                for date in prior_dates[-20:]:
                    prior_day = minute_lookup[symbol][date]
                    position = bisect_right(prior_day["times"], cutoff_time)
                    aligned_history.append(
                        prior_day["cumulative_volume"][position - 1]
                        if position
                        else 0
                    )
                baseline = Decimal(sum(aligned_history)) / Decimal(len(aligned_history)) if aligned_history else Decimal("0")
                rvol = Decimal(current_volume) / baseline if baseline > 0 else Decimal("0")
                half = estimated_spread_percent / Decimal("2")
                snapshots.append(MarketSnapshot(symbol=symbol, timestamp=minute, price=price, percent_gain=gain, rvol=rvol, day_volume=current_volume, bid=price*(Decimal("1")-half), ask=price*(Decimal("1")+half), float_shares=None))
            snapshots.sort(key=lambda value: (value.percent_gain, value.rvol, value.day_volume), reverse=True)
            snapshots = snapshots[: self.settings.scanner_top]
            if not snapshots: continue
            outcome = self.scanner.scan(snapshots)
            pending_scans.append((snapshots, outcome.diagnostics))
            if len(pending_scans) >= 250:
                recorded += self.store.record_scan_batch(
                    pending_scans, source="reconstructed"
                )
                pending_scans = []
        if pending_scans:
            recorded += self.store.record_scan_batch(
                pending_scans, source="reconstructed"
            )
        return recorded

    @staticmethod
    def _index(bars: Sequence[Bar], eastern: ZoneInfo):
        result = defaultdict(list)
        for bar in sorted(bars, key=lambda value: value.timestamp):
            result[bar.timestamp.astimezone(eastern).date()].append(bar)
        return result

    @staticmethod
    def _lookup(days, eastern: ZoneInfo):
        lookup = {}
        for date, bars in days.items():
            cumulative = []
            total = 0
            for bar in bars:
                total += bar.volume
                cumulative.append(total)
            lookup[date] = {
                "bars": bars,
                "timestamps": [bar.timestamp for bar in bars],
                "times": [
                    bar.timestamp.astimezone(eastern).time() for bar in bars
                ],
                "cumulative_volume": cumulative,
            }
        return lookup
