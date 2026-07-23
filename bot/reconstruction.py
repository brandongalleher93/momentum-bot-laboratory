"""Reconstruct approximate historical scanner candidates from point-in-time bars."""

from __future__ import annotations

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
        decision_minutes: Sequence[datetime] | None = None,
        estimated_spread_percent: Decimal = Decimal("0.005"),
    ) -> int:
        eastern = ZoneInfo(self.settings.timezone)
        indexed = {symbol: self._index(bars, eastern) for symbol, bars in bars_by_symbol.items()}
        if decision_minutes is None:
            decision_minutes = sorted({bar.timestamp for bars in bars_by_symbol.values() for bar in bars if self.settings.trade_window_start <= bar.timestamp.astimezone(eastern).time() <= self.settings.trade_window_end})
        recorded = 0
        for minute in decision_minutes:
            snapshots = []
            for symbol, days in indexed.items():
                local_date = minute.astimezone(eastern).date()
                today = [bar for bar in days.get(local_date, []) if bar.timestamp <= minute]
                prior_dates = sorted(date for date in days if date < local_date)
                if not today or not prior_dates: continue
                prior = days[prior_dates[-1]]
                prior_close = prior[-1].close
                price = today[-1].close
                gain = (price - prior_close) / prior_close if prior_close > 0 else Decimal("0")
                current_volume = sum(bar.volume for bar in today)
                aligned_history = []
                cutoff_time = minute.astimezone(eastern).time()
                for date in prior_dates[-20:]:
                    aligned_history.append(sum(bar.volume for bar in days[date] if bar.timestamp.astimezone(eastern).time() <= cutoff_time))
                baseline = Decimal(sum(aligned_history)) / Decimal(len(aligned_history)) if aligned_history else Decimal("0")
                rvol = Decimal(current_volume) / baseline if baseline > 0 else Decimal("0")
                half = estimated_spread_percent / Decimal("2")
                snapshots.append(MarketSnapshot(symbol=symbol, timestamp=minute, price=price, percent_gain=gain, rvol=rvol, day_volume=current_volume, bid=price*(Decimal("1")-half), ask=price*(Decimal("1")+half), float_shares=None))
            snapshots.sort(key=lambda value: (value.percent_gain, value.rvol, value.day_volume), reverse=True)
            snapshots = snapshots[: self.settings.scanner_top]
            if not snapshots: continue
            outcome = self.scanner.scan(snapshots)
            recorded += self.store.record_scan(snapshots, outcome.diagnostics, source="reconstructed")
        return recorded

    @staticmethod
    def _index(bars: Sequence[Bar], eastern: ZoneInfo):
        result = defaultdict(list)
        for bar in sorted(bars, key=lambda value: value.timestamp):
            result[bar.timestamp.astimezone(eastern).date()].append(bar)
        return result
