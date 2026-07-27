"""Bull Flag / First Pullback strategy using completed bars and live quotes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Any, Generic, Mapping, Optional, Sequence, TypeVar
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


@dataclass(frozen=True)
class CandidateFailure:
    reason: str
    actual_values: Mapping[str, Any]
    expected_values: Mapping[str, Any]
    score: int


@dataclass(frozen=True)
class CandidateSearchResult:
    candidate: Optional[tuple[Flagpole, Pullback]]
    closest_failure: Optional[CandidateFailure]


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

        search = self._find_latest_candidate(completed_bars, indicators)
        if search.candidate is None:
            failure = search.closest_failure
            return self._reject(
                symbol,
                (
                    failure.reason
                    if failure is not None
                    else "No eligible flagpole and pullback window was available."
                ),
                decision_time,
                cutoff,
                actual=dict(failure.actual_values) if failure is not None else {},
                expected=dict(failure.expected_values) if failure is not None else {},
            )

        flagpole, pullback = search.candidate
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
    ) -> CandidateSearchResult:
        del indicators  # Reserved for future candidate ranking; context checks use it later.
        closest_failure: Optional[CandidateFailure] = None
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
                flagpole, failure = self._inspect_flagpole(
                    flagpole_bars, baseline_bars
                )
                if flagpole is None:
                    closest_failure = self._prefer_failure(
                        closest_failure,
                        self._with_candidate_window(
                            failure, pole_count, pullback_count
                        ),
                    )
                    continue
                pullback, failure = self._inspect_pullback(pullback_bars, flagpole)
                if pullback is not None:
                    return CandidateSearchResult((flagpole, pullback), closest_failure)
                closest_failure = self._prefer_failure(
                    closest_failure,
                    self._with_candidate_window(failure, pole_count, pullback_count),
                )
        return CandidateSearchResult(None, closest_failure)

    def _build_flagpole(
        self, bars: Sequence[Bar], baseline_bars: Sequence[Bar]
    ) -> Optional[Flagpole]:
        return self._inspect_flagpole(bars, baseline_bars)[0]

    def _inspect_flagpole(
        self, bars: Sequence[Bar], baseline_bars: Sequence[Bar]
    ) -> tuple[Optional[Flagpole], Optional[CandidateFailure]]:
        if not baseline_bars:
            return None, CandidateFailure(
                "Flagpole has no prior volume baseline.",
                {"baseline_bar_count": 0},
                {"minimum_baseline_bar_count": 1},
                0,
            )
        low = bars[0].low
        high = max(bar.high for bar in bars)
        if bars[-1].high != high:
            return None, CandidateFailure(
                "Flagpole does not end at its highest high.",
                {"last_high": bars[-1].high, "flagpole_high": high},
                {"last_high_must_equal_flagpole_high": True},
                1,
            )
        move = (high - low) / low
        green_count = sum(1 for bar in bars if bar.green)
        avg_volume = average([Decimal(bar.volume) for bar in bars])
        baseline_volume = average([Decimal(bar.volume) for bar in baseline_bars])
        volume_ratio = avg_volume / baseline_volume if baseline_volume > 0 else Decimal("0")
        if move < self.settings.strong_up_move_min_percent:
            return None, CandidateFailure(
                "Flagpole move is below the configured minimum.",
                {"flagpole_percent_move": move},
                {"minimum_flagpole_percent_move": self.settings.strong_up_move_min_percent},
                2,
            )
        if green_count < self.settings.min_flagpole_green_candles:
            return None, CandidateFailure(
                "Flagpole has too few green candles.",
                {"green_candle_count": green_count},
                {"minimum_green_candle_count": self.settings.min_flagpole_green_candles},
                3,
            )
        if volume_ratio < self.settings.flagpole_volume_ratio_min:
            return None, CandidateFailure(
                "Flagpole volume expansion is below the configured minimum.",
                {
                    "flagpole_volume_ratio": volume_ratio,
                    "flagpole_average_volume": avg_volume,
                    "baseline_average_volume": baseline_volume,
                },
                {"minimum_flagpole_volume_ratio": self.settings.flagpole_volume_ratio_min},
                4,
            )
        return (
            Flagpole(
                bars=tuple(bars),
                low=low,
                high=high,
                percent_move=move,
                green_candle_count=green_count,
                average_volume=avg_volume,
                average_range=average([bar.range for bar in bars]),
                volume_ratio=volume_ratio,
            ),
            None,
        )

    def _build_pullback(
        self, bars: Sequence[Bar], flagpole: Flagpole
    ) -> Optional[Pullback]:
        return self._inspect_pullback(bars, flagpole)[0]

    def _inspect_pullback(
        self, bars: Sequence[Bar], flagpole: Flagpole
    ) -> tuple[Optional[Pullback], Optional[CandidateFailure]]:
        if flagpole.average_range <= 0:
            return None, CandidateFailure(
                "Flagpole average candle range is not positive.",
                {"flagpole_average_range": flagpole.average_range},
                {"minimum_flagpole_average_range_exclusive": Decimal("0")},
                5,
            )
        for index, bar in enumerate(bars):
            body_ratio = body_to_range_ratio(bar)
            range_ratio = bar.range / flagpole.average_range
            small_consolidation = (
                body_ratio <= self.settings.small_consolidation_body_to_range_max
                and range_ratio
                <= self.settings.small_consolidation_range_to_flagpole_avg_max
            )
            if not (bar.red or small_consolidation):
                return None, CandidateFailure(
                    "Pullback contains a bullish candle that is not a small consolidation.",
                    {
                        "pullback_bar_index": index + 1,
                        "bar_color": "green",
                        "body_to_range_ratio": body_ratio,
                        "range_to_flagpole_average": range_ratio,
                    },
                    {
                        "bar_must_be_red_or_small_consolidation": True,
                        "maximum_small_body_ratio": self.settings.small_consolidation_body_to_range_max,
                        "maximum_small_range_ratio": self.settings.small_consolidation_range_to_flagpole_avg_max,
                    },
                    5,
                )
        if self.settings.require_nonincreasing_pullback_highs:
            for index, (previous, current) in enumerate(zip(bars, bars[1:])):
                if current.high > previous.high:
                    return None, CandidateFailure(
                        "Pullback highs are increasing.",
                        {
                            "previous_high": previous.high,
                            "current_high": current.high,
                            "pullback_bar_index": index + 2,
                        },
                        {"current_high_must_be_at_or_below_previous_high": True},
                        6,
                    )
                if not self.settings.allow_equal_pullback_highs and current.high == previous.high:
                    return None, CandidateFailure(
                        "Equal pullback highs are not allowed.",
                        {
                            "previous_high": previous.high,
                            "current_high": current.high,
                            "pullback_bar_index": index + 2,
                        },
                        {"current_high_must_be_below_previous_high": True},
                        6,
                    )

        low = min(bar.low for bar in bars)
        span = flagpole.high - flagpole.low
        if span <= 0:
            return None, CandidateFailure(
                "Flagpole price span is not positive.",
                {"flagpole_span": span},
                {"minimum_flagpole_span_exclusive": Decimal("0")},
                7,
            )
        depth = (flagpole.high - low) / span
        volume_ratio = average([Decimal(bar.volume) for bar in bars]) / flagpole.average_volume
        largest_wick = max(upper_wick_ratio(bar) for bar in bars)
        if depth > self.settings.preferred_pullback_depth:
            return None, CandidateFailure(
                "Pullback depth exceeds the configured maximum.",
                {"pullback_depth": depth},
                {"maximum_pullback_depth": self.settings.preferred_pullback_depth},
                8,
            )
        if volume_ratio > self.settings.pullback_volume_ratio_max:
            return None, CandidateFailure(
                "Pullback volume did not contract enough.",
                {"pullback_volume_ratio": volume_ratio},
                {"maximum_pullback_volume_ratio": self.settings.pullback_volume_ratio_max},
                9,
            )
        if largest_wick > self.settings.large_upper_wick_ratio:
            return None, CandidateFailure(
                "Pullback upper wick is too large.",
                {"largest_pullback_upper_wick_ratio": largest_wick},
                {"maximum_upper_wick_ratio": self.settings.large_upper_wick_ratio},
                10,
            )
        return (
            Pullback(
                bars=tuple(bars),
                low=low,
                high=max(bar.high for bar in bars),
                depth=depth,
                volume_ratio=volume_ratio,
                largest_upper_wick_ratio=largest_wick,
            ),
            None,
        )

    @staticmethod
    def _prefer_failure(
        current: Optional[CandidateFailure],
        candidate: Optional[CandidateFailure],
    ) -> Optional[CandidateFailure]:
        if candidate is None:
            return current
        if current is None or candidate.score > current.score:
            return candidate
        return current

    @staticmethod
    def _with_candidate_window(
        failure: Optional[CandidateFailure],
        flagpole_candles: int,
        pullback_candles: int,
    ) -> Optional[CandidateFailure]:
        if failure is None:
            return None
        return CandidateFailure(
            reason=failure.reason,
            actual_values={
                **failure.actual_values,
                "closest_flagpole_candles": flagpole_candles,
                "closest_pullback_candles": pullback_candles,
                "candidate_rules_passed": failure.score,
            },
            expected_values=failure.expected_values,
            score=failure.score,
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


class MicroPullbackStrategy:
    """Completed ten-second pullback/reversal patterns behind a scanner gate."""

    def __init__(self, settings: Settings, diagnostics: DiagnosticFactory):
        self.settings = settings
        self.diagnostics = diagnostics

    def detect_setup(
        self, symbol: str, completed_bars: Sequence[Bar], decision_time: datetime
    ) -> StrategyResult[Setup]:
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware.")
        if not completed_bars:
            return self._reject(symbol, decision_time, "No completed ten-second bars.")
        cutoff = completed_bars[-1].timestamp
        if cutoff > decision_time:
            return self._reject(
                symbol,
                decision_time,
                "Ten-second data cutoff is after decision time.",
            )
        if len(completed_bars) < 4:
            return self._reject(
                symbol,
                decision_time,
                "Insufficient completed ten-second bars.",
                actual={"bar_count": len(completed_bars)},
            )

        candidate = self._find_pullback(completed_bars)
        pattern = "micro_pullback_breakout"
        if candidate is None:
            candidate = self._find_reversal(completed_bars)
            pattern = "micro_reversal_breakout"
        if candidate is None:
            return self._reject(
                symbol,
                decision_time,
                "No causal ten-second micro-pullback or reversal setup.",
            )

        flagpole_bars, pullback_bars, breakout_level = candidate
        flagpole_low = min(bar.low for bar in flagpole_bars)
        flagpole_high = max(bar.high for bar in flagpole_bars)
        flagpole_range = flagpole_high - flagpole_low
        flagpole_average_volume = average(
            [Decimal(bar.volume) for bar in flagpole_bars]
        )
        flagpole_average_range = average([bar.range for bar in flagpole_bars])
        pullback_low = min(bar.low for bar in pullback_bars)
        pullback_high = max(bar.high for bar in pullback_bars)
        pullback_depth = (
            (flagpole_high - pullback_low) / flagpole_range
            if flagpole_range > 0
            else Decimal("0")
        )
        pullback_average_volume = average(
            [Decimal(bar.volume) for bar in pullback_bars]
        )
        volume_ratio = (
            pullback_average_volume / flagpole_average_volume
            if flagpole_average_volume > 0
            else Decimal("0")
        )
        tick = tick_size(breakout_level)
        trigger = breakout_level + tick * self.settings.entry_trigger_buffer_ticks
        structural_stop = pullback_low
        protected_entry = (
            trigger + tick * self.settings.entry_limit_offset_ticks
        )
        risk_floor = protected_entry - self.settings.max_allowed_stop_distance
        stop = max(structural_stop, risk_floor)
        if stop >= trigger:
            return self._reject(
                symbol,
                decision_time,
                "Ten-second setup has no positive risk distance.",
            )

        flagpole = Flagpole(
            bars=tuple(flagpole_bars),
            low=flagpole_low,
            high=flagpole_high,
            percent_move=(
                flagpole_range / flagpole_low
                if flagpole_low > 0
                else Decimal("0")
            ),
            green_candle_count=sum(1 for bar in flagpole_bars if bar.green),
            average_volume=flagpole_average_volume,
            average_range=flagpole_average_range,
            volume_ratio=Decimal("0"),
        )
        pullback = Pullback(
            bars=tuple(pullback_bars),
            low=pullback_low,
            high=pullback_high,
            depth=pullback_depth,
            volume_ratio=volume_ratio,
            largest_upper_wick_ratio=max(
                upper_wick_ratio(bar) for bar in pullback_bars
            ),
        )
        setup = Setup(
            setup_id=str(uuid4()),
            symbol=symbol,
            flagpole=flagpole,
            pullback=pullback,
            breakout_level=breakout_level,
            trigger_price=trigger,
            stop_price=stop,
            created_at=decision_time,
            data_cutoff_time=cutoff,
            expires_at=cutoff + timedelta(seconds=20),
        )
        return StrategyResult(
            setup,
            self.diagnostics.passed(
                module="ten_second_setup_detector",
                symbol=symbol,
                reason=f"Valid {pattern.replace('_', ' ')}.",
                decision_time=decision_time,
                data_cutoff_time=cutoff,
                actual_values={
                    "pattern": pattern,
                    "setup": setup,
                    "structural_stop": structural_stop,
                    "risk_capped_stop": stop,
                },
                expected_values={
                    "minimum_impulse_percent": Decimal("0.02"),
                    "maximum_pullback_depth": Decimal("0.95"),
                    "setup_expiration_seconds": 20,
                },
                notes=(
                    "The detector uses completed ten-second bars only. The stop is "
                    "tightened to the configured maximum risk distance when the "
                    "full structural pullback is wider."
                ),
            ),
        )

    def _find_pullback(
        self, bars: Sequence[Bar]
    ) -> Optional[tuple[Sequence[Bar], Sequence[Bar], Decimal]]:
        candidates: list[
            tuple[tuple[Decimal, Decimal, int], Sequence[Bar], Sequence[Bar], Decimal]
        ] = []
        for pullback_count in range(2, 7):
            split = len(bars) - pullback_count
            if split < 3:
                continue
            pullback = bars[split:]
            for pole_count in range(1, 9):
                start = split - pole_count
                if start < 1:
                    continue
                flagpole = bars[start:split]
                high = max(bar.high for bar in flagpole)
                low = min(bar.low for bar in flagpole)
                if flagpole[-1].high != high or low <= 0:
                    continue
                move = (high - low) / low
                if move < Decimal("0.02"):
                    continue
                span = high - low
                depth = (high - min(bar.low for bar in pullback)) / span
                if depth > Decimal("0.95"):
                    continue
                if max(bar.high for bar in pullback) > high:
                    continue
                if pullback[-1].close >= high:
                    continue
                flagpole_volume = average(
                    [Decimal(bar.volume) for bar in flagpole]
                )
                pullback_volume = average(
                    [Decimal(bar.volume) for bar in pullback]
                )
                baseline_bars = bars[max(0, start - 20) : start]
                baseline_volume = average(
                    [Decimal(bar.volume) for bar in baseline_bars]
                )
                if pullback_volume > flagpole_volume:
                    continue
                if (
                    baseline_volume > 0
                    and flagpole_volume / baseline_volume < Decimal("1.10")
                ):
                    continue
                candidates.append(
                    ((high, move, -pullback_count), flagpole, pullback, high)
                )
        if not candidates:
            return None
        _, flagpole, pullback, breakout = max(
            candidates, key=lambda value: value[0]
        )
        return flagpole, pullback, breakout

    @staticmethod
    def _find_reversal(
        bars: Sequence[Bar],
    ) -> Optional[tuple[Sequence[Bar], Sequence[Bar], Decimal]]:
        if len(bars) < 2:
            return None
        reversal = bars[-1]
        previous = bars[-2]
        if reversal.range <= 0:
            return None
        move = reversal.range / reversal.low
        closes_in_upper_half = reversal.close >= (
            reversal.low + reversal.range / Decimal("2")
        )
        recent = bars[-7:-1]
        recent_high = max(bar.high for bar in recent)
        pullback_depth = (
            (recent_high - previous.low) / recent_high
            if recent_high > 0
            else Decimal("0")
        )
        bounced = (
            previous.red
            and reversal.green
            and reversal.close > previous.close
            and reversal.high < recent_high
            and reversal.volume < previous.volume
        )
        if (
            move < Decimal("0.015")
            or pullback_depth < Decimal("0.02")
            or not closes_in_upper_half
            or not bounced
        ):
            return None
        return (previous, reversal), (reversal,), reversal.high

    def _reject(
        self,
        symbol: str,
        decision_time: datetime,
        reason: str,
        *,
        actual: Optional[dict] = None,
    ) -> StrategyResult[Setup]:
        return StrategyResult(
            None,
            self.diagnostics.rejected(
                module="ten_second_setup_detector",
                symbol=symbol,
                reason=reason,
                decision_time=decision_time,
                data_cutoff_time=decision_time,
                actual_values=actual or {},
                expected_values={},
            ),
        )
