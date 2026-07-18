"""Bull Flag / First Pullback strategy using completed bars and live quotes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Generic, Optional, Sequence, TypeVar
from uuid import uuid4

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.indicators import (
    average,
    body_to_range_ratio,
    calculate_indicators,
    extension_above,
    upper_wick_ratio,
)
from bot.models import (
    Bar,
    DiagnosticResult,
    EntrySignal,
    Flagpole,
    IndicatorSnapshot,
    Pullback,
    Quote,
    Setup,
    TradePlan,
)


T = TypeVar("T")


@dataclass(frozen=True)
class StrategyResult(Generic[T]):
    value: Optional[T]
    diagnostic: DiagnosticResult


def tick_size(price: Decimal) -> Decimal:
    return Decimal("0.0001") if price < Decimal("1") else Decimal("0.01")


def round_down_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick


class BullFlagStrategy:
    def __init__(self, settings: Settings, diagnostics: DiagnosticFactory):
        self.settings = settings
        self.diagnostics = diagnostics

    def detect_setup(
        self, symbol: str, completed_bars: Sequence[Bar], decision_time: datetime
    ) -> StrategyResult[Setup]:
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware.")
        if not completed_bars:
            return self._reject(symbol, "No completed bars.", decision_time, decision_time)
        cutoff = completed_bars[-1].timestamp
        if cutoff > decision_time:
            return self._reject(
                symbol,
                "Completed-bar cutoff is after decision time.",
                decision_time,
                cutoff,
            )
        if len(completed_bars) < (
            self.settings.ema_period
            + self.settings.pullback_candles_min
            + 2  # one flagpole bar and at least one prior volume-baseline bar
        ):
            return self._reject(
                symbol,
                "Insufficient completed bars for EMA, flagpole, and pullback.",
                decision_time,
                cutoff,
                actual={"bar_count": len(completed_bars)},
            )

        try:
            indicators = calculate_indicators(completed_bars, self.settings)
        except ValueError as exc:
            return self._reject(symbol, str(exc), decision_time, cutoff)

        candidate = self._find_latest_candidate(completed_bars, indicators)
        if candidate is None:
            return self._reject(
                symbol,
                "No valid two-to-three-candle first pullback follows a qualifying flagpole.",
                decision_time,
                cutoff,
                expected={
                    "strong_up_move_min_percent": self.settings.strong_up_move_min_percent,
                    "pullback_candle_range": [
                        self.settings.pullback_candles_min,
                        self.settings.pullback_candles_max,
                    ],
                    "preferred_pullback_depth": self.settings.preferred_pullback_depth,
                },
            )

        flagpole, pullback = candidate
        latest_close = completed_bars[-1].close
        rejection = self._context_rejection(
            symbol, latest_close, indicators, flagpole, pullback, decision_time
        )
        if rejection is not None:
            return StrategyResult(None, rejection)

        breakout_level = pullback.bars[-1].high
        tick = tick_size(breakout_level)
        trigger = breakout_level + (tick * self.settings.entry_trigger_buffer_ticks)
        setup = Setup(
            setup_id=str(uuid4()),
            symbol=symbol,
            flagpole=flagpole,
            pullback=pullback,
            breakout_level=breakout_level,
            trigger_price=trigger,
            stop_price=pullback.low,
            created_at=decision_time,
            data_cutoff_time=cutoff,
            expires_at=cutoff
            + timedelta(
                minutes=self.settings.primary_timeframe_minutes
                * self.settings.setup_expiration_candles
            ),
        )
        diagnostic = self.diagnostics.passed(
            module="bull_flag_detector",
            symbol=symbol,
            reason="Valid Bull Flag / First Pullback setup.",
            decision_time=decision_time,
            data_cutoff_time=cutoff,
            actual_values={"setup": setup},
            expected_values={
                "pullback_candles_max": self.settings.pullback_candles_max,
                "pullback_depth_max": self.settings.preferred_pullback_depth,
            },
        )
        return StrategyResult(setup, diagnostic)

    def check_entry(self, setup: Setup, quote: Quote) -> StrategyResult[EntrySignal]:
        if quote.timestamp > setup.expires_at:
            return StrategyResult(
                None,
                self.diagnostics.rejected(
                    module="entry_validator",
                    symbol=setup.symbol,
                    reason="Setup expired before entry.",
                    decision_time=quote.timestamp,
                    data_cutoff_time=quote.timestamp,
                    actual_values={"quote_time": quote.timestamp},
                    expected_values={"expires_at": setup.expires_at},
                ),
            )
        if quote.ask < setup.trigger_price:
            signal = EntrySignal(
                triggered=False,
                observed_price=quote.ask,
                trigger_price=setup.trigger_price,
                maximum_entry_price=setup.trigger_price,
                timestamp=quote.timestamp,
            )
            return StrategyResult(
                signal,
                self.diagnostics.passed(
                    module="entry_validator",
                    symbol=setup.symbol,
                    reason="Entry trigger not reached.",
                    decision_time=quote.timestamp,
                    data_cutoff_time=quote.timestamp,
                    actual_values={"signal": signal},
                    expected_values={"minimum_price": setup.trigger_price},
                ),
            )

        tick = tick_size(setup.trigger_price)
        maximum_chase = setup.trigger_price + tick * self.settings.max_entry_chase_ticks
        if quote.ask > maximum_chase:
            return StrategyResult(
                None,
                self.diagnostics.rejected(
                    module="entry_validator",
                    symbol=setup.symbol,
                    reason="Price moved beyond maximum entry chase.",
                    decision_time=quote.timestamp,
                    data_cutoff_time=quote.timestamp,
                    actual_values={"ask": quote.ask},
                    expected_values={"maximum_chase_price": maximum_chase},
                ),
            )
        if quote.spread_percent > self.settings.max_spread_percent:
            return StrategyResult(
                None,
                self.diagnostics.rejected(
                    module="entry_validator",
                    symbol=setup.symbol,
                    reason="Spread widened beyond the entry limit.",
                    decision_time=quote.timestamp,
                    data_cutoff_time=quote.timestamp,
                    actual_values={"spread_percent": quote.spread_percent},
                    expected_values={
                        "max_spread_percent": self.settings.max_spread_percent
                    },
                ),
            )

        limit_price = min(
            quote.ask + tick * self.settings.entry_limit_offset_ticks, maximum_chase
        )
        signal = EntrySignal(
            triggered=True,
            observed_price=quote.ask,
            trigger_price=setup.trigger_price,
            maximum_entry_price=round_down_to_tick(limit_price, tick),
            timestamp=quote.timestamp,
        )
        return StrategyResult(
            signal,
            self.diagnostics.passed(
                module="entry_validator",
                symbol=setup.symbol,
                reason="Intrabar breakout trigger approved.",
                decision_time=quote.timestamp,
                data_cutoff_time=quote.timestamp,
                actual_values={"signal": signal, "spread_percent": quote.spread_percent},
                expected_values={"trigger_price": setup.trigger_price},
                notes="No unfinished candle close, final volume, color, or wick was used.",
            ),
        )

    def create_trade_plan(
        self, setup: Setup, signal: EntrySignal
    ) -> StrategyResult[TradePlan]:
        risk_per_share = signal.maximum_entry_price - setup.stop_price
        now = signal.timestamp
        if risk_per_share <= 0:
            return self._trade_plan_rejection(setup, now, "Risk per share is not positive.")
        if risk_per_share > self.settings.max_allowed_stop_distance:
            return self._trade_plan_rejection(
                setup,
                now,
                "Stop distance exceeds configured maximum.",
                actual={"risk_per_share": risk_per_share},
            )
        target = signal.maximum_entry_price + (
            self.settings.target_r_multiple * risk_per_share
        )
        reward_to_risk = (target - signal.maximum_entry_price) / risk_per_share
        if reward_to_risk < self.settings.min_reward_to_risk:
            return self._trade_plan_rejection(
                setup, now, "Reward-to-risk is below the configured minimum."
            )
        tick = tick_size(signal.maximum_entry_price)
        plan = TradePlan(
            symbol=setup.symbol,
            setup_id=setup.setup_id,
            maximum_entry_price=signal.maximum_entry_price,
            stop_price=round_down_to_tick(setup.stop_price, tick),
            target_price=round_down_to_tick(target, tick),
            maximum_risk_per_share=risk_per_share,
            reward_to_risk=reward_to_risk,
            breakout_level=setup.breakout_level,
        )
        return StrategyResult(
            plan,
            self.diagnostics.passed(
                module="trade_plan_calculator",
                symbol=setup.symbol,
                reason="Conservative pre-fill trade plan approved.",
                decision_time=now,
                data_cutoff_time=now,
                actual_values={"trade_plan": plan},
                expected_values={
                    "max_stop_distance": self.settings.max_allowed_stop_distance,
                    "target_r_multiple": self.settings.target_r_multiple,
                },
            ),
        )

    def _find_latest_candidate(
        self, bars: Sequence[Bar], indicators: IndicatorSnapshot
    ) -> Optional[tuple[Flagpole, Pullback]]:
        for pullback_count in range(
            self.settings.pullback_candles_max,
            self.settings.pullback_candles_min - 1,
            -1,
        ):
            split = len(bars) - pullback_count
            if split <= 1:
                continue
            pullback_bars = bars[split:]
            for pole_count in range(1, self.settings.strong_up_move_max_candles + 1):
                start = split - pole_count
                if start < 1:
                    continue
                flagpole_bars = bars[start:split]
                baseline_bars = bars[
                    max(0, start - self.settings.recent_volume_lookback_candles) : start
                ]
                flagpole = self._build_flagpole(flagpole_bars, baseline_bars)
                if flagpole is None:
                    continue
                pullback = self._build_pullback(pullback_bars, flagpole)
                if pullback is not None:
                    return flagpole, pullback
        return None

    def _build_flagpole(
        self, bars: Sequence[Bar], baseline_bars: Sequence[Bar]
    ) -> Optional[Flagpole]:
        if not baseline_bars:
            return None
        low = bars[0].low
        high = max(bar.high for bar in bars)
        if bars[-1].high != high:
            return None
        move = (high - low) / low
        green_count = sum(1 for bar in bars if bar.green)
        avg_volume = average([Decimal(bar.volume) for bar in bars])
        baseline_volume = average([Decimal(bar.volume) for bar in baseline_bars])
        volume_ratio = avg_volume / baseline_volume if baseline_volume > 0 else Decimal("0")
        if (
            move < self.settings.strong_up_move_min_percent
            or green_count < self.settings.min_flagpole_green_candles
            or volume_ratio < self.settings.flagpole_volume_ratio_min
        ):
            return None
        return Flagpole(
            bars=tuple(bars),
            low=low,
            high=high,
            percent_move=move,
            green_candle_count=green_count,
            average_volume=avg_volume,
            average_range=average([bar.range for bar in bars]),
            volume_ratio=volume_ratio,
        )

    def _build_pullback(
        self, bars: Sequence[Bar], flagpole: Flagpole
    ) -> Optional[Pullback]:
        for bar in bars:
            small_consolidation = (
                body_to_range_ratio(bar)
                <= self.settings.small_consolidation_body_to_range_max
                and bar.range / flagpole.average_range
                <= self.settings.small_consolidation_range_to_flagpole_avg_max
            )
            if not (bar.red or small_consolidation):
                return None
        if self.settings.require_nonincreasing_pullback_highs:
            for previous, current in zip(bars, bars[1:]):
                if current.high > previous.high:
                    return None
                if not self.settings.allow_equal_pullback_highs and current.high == previous.high:
                    return None

        low = min(bar.low for bar in bars)
        span = flagpole.high - flagpole.low
        if span <= 0:
            return None
        depth = (flagpole.high - low) / span
        volume_ratio = average([Decimal(bar.volume) for bar in bars]) / flagpole.average_volume
        largest_wick = max(upper_wick_ratio(bar) for bar in bars)
        if depth > self.settings.preferred_pullback_depth:
            return None
        if volume_ratio > self.settings.pullback_volume_ratio_max:
            return None
        if largest_wick > self.settings.large_upper_wick_ratio:
            return None
        return Pullback(
            bars=tuple(bars),
            low=low,
            high=max(bar.high for bar in bars),
            depth=depth,
            volume_ratio=volume_ratio,
            largest_upper_wick_ratio=largest_wick,
        )

    def _context_rejection(
        self,
        symbol: str,
        price: Decimal,
        indicators: IndicatorSnapshot,
        flagpole: Flagpole,
        pullback: Pullback,
        decision_time: datetime,
    ) -> Optional[DiagnosticResult]:
        checks = (
            (
                (indicators.high_of_day - price) / indicators.high_of_day
                > self.settings.near_high_of_day_percent,
                "Price is not near the completed-bar high of day.",
                {
                    "price": price,
                    "high_of_day": indicators.high_of_day,
                    "distance_from_high": (indicators.high_of_day - price)
                    / indicators.high_of_day,
                },
            ),
            (price <= indicators.vwap, "Price is not above VWAP.", {"price": price, "vwap": indicators.vwap}),
            (price <= indicators.ema, "Price is not above 9 EMA.", {"price": price, "ema": indicators.ema}),
            (extension_above(price, indicators.vwap) > self.settings.max_extension_above_vwap_percent, "Price is too extended above VWAP.", {"extension": extension_above(price, indicators.vwap)}),
            (extension_above(price, indicators.ema) > self.settings.max_extension_above_ema_percent, "Price is too extended above 9 EMA.", {"extension": extension_above(price, indicators.ema)}),
        )
        for failed, reason, actual in checks:
            if failed:
                return self.diagnostics.rejected(
                    module="bull_flag_detector",
                    symbol=symbol,
                    reason=reason,
                    decision_time=decision_time,
                    data_cutoff_time=indicators.data_cutoff_time,
                    actual_values=actual,
                    expected_values={},
                )
        flag_top = flagpole.bars[-1]
        if body_to_range_ratio(flag_top) <= self.settings.doji_body_to_range_max:
            return self.diagnostics.rejected(
                module="bull_flag_detector",
                symbol=symbol,
                reason="Doji detected at the flagpole top.",
                decision_time=decision_time,
                data_cutoff_time=indicators.data_cutoff_time,
                actual_values={"body_to_range": body_to_range_ratio(flag_top)},
                expected_values={"minimum": self.settings.doji_body_to_range_max},
            )
        if upper_wick_ratio(flag_top) >= self.settings.shooting_star_upper_wick_to_range_min:
            return self.diagnostics.rejected(
                module="bull_flag_detector",
                symbol=symbol,
                reason="Shooting-star wick detected at the flagpole top.",
                decision_time=decision_time,
                data_cutoff_time=indicators.data_cutoff_time,
                actual_values={"upper_wick_to_range": upper_wick_ratio(flag_top)},
                expected_values={
                    "maximum": self.settings.shooting_star_upper_wick_to_range_min
                },
            )
        return None

    def _reject(
        self,
        symbol: str,
        reason: str,
        decision_time: datetime,
        cutoff: datetime,
        actual: Optional[dict] = None,
        expected: Optional[dict] = None,
    ) -> StrategyResult[Setup]:
        return StrategyResult(
            None,
            self.diagnostics.rejected(
                module="bull_flag_detector",
                symbol=symbol,
                reason=reason,
                decision_time=decision_time,
                data_cutoff_time=cutoff,
                actual_values=actual or {},
                expected_values=expected or {},
            ),
        )

    def _trade_plan_rejection(
        self,
        setup: Setup,
        decision_time: datetime,
        reason: str,
        actual: Optional[dict] = None,
    ) -> StrategyResult[TradePlan]:
        return StrategyResult(
            None,
            self.diagnostics.rejected(
                module="trade_plan_calculator",
                symbol=setup.symbol,
                reason=reason,
                decision_time=decision_time,
                data_cutoff_time=decision_time,
                actual_values=actual or {},
                expected_values={
                    "max_stop_distance": self.settings.max_allowed_stop_distance
                },
            ),
        )
