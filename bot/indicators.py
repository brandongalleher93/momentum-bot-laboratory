"""Point-in-time indicators calculated from completed bars only."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.models import Bar, IndicatorSnapshot


def _require_chronological(bars: Sequence[Bar]) -> None:
    if not bars:
        raise ValueError("At least one completed bar is required.")
    timestamps = [bar.timestamp for bar in bars]
    if timestamps != sorted(timestamps):
        raise ValueError("Bars must be chronological.")
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("Bars cannot contain duplicate timestamps.")


def average(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("Cannot average an empty sequence.")
    return sum(values, Decimal("0")) / Decimal(len(values))


def ema(values: Sequence[Decimal], period: int) -> Decimal:
    if period < 1:
        raise ValueError("EMA period must be positive.")
    if len(values) < period:
        raise ValueError(f"EMA({period}) requires at least {period} values.")
    seed = average(values[:period])
    multiplier = Decimal("2") / Decimal(period + 1)
    result = seed
    for value in values[period:]:
        result = ((value - result) * multiplier) + result
    return result


def vwap(bars: Sequence[Bar]) -> Decimal:
    total_volume = sum(bar.volume for bar in bars)
    if total_volume <= 0:
        raise ValueError("VWAP requires positive cumulative volume.")
    weighted = sum(
        (((bar.high + bar.low + bar.close) / Decimal("3")) * Decimal(bar.volume))
        for bar in bars
    )
    return weighted / Decimal(total_volume)


def calculate_indicators(
    completed_bars: Sequence[Bar], settings: Settings
) -> IndicatorSnapshot:
    _require_chronological(completed_bars)
    eastern = ZoneInfo(settings.timezone)
    session_date = completed_bars[-1].timestamp.astimezone(eastern).date()
    session_bars = [
        bar
        for bar in completed_bars
        if bar.timestamp.astimezone(eastern).date() == session_date
    ]
    if not session_bars:
        raise ValueError("No bars belong to the latest regular-session date.")
    if len(session_bars) < settings.ema_period:
        raise ValueError(
            f"Need {settings.ema_period} completed regular-session bars for the EMA."
        )

    volume_window = session_bars[-settings.recent_volume_lookback_candles :]
    range_window = session_bars[-settings.recent_range_lookback_candles :]
    cutoff = completed_bars[-1].timestamp
    return IndicatorSnapshot(
        timestamp=cutoff,
        data_cutoff_time=cutoff,
        vwap=vwap(session_bars),
        ema=ema([bar.close for bar in session_bars], settings.ema_period),
        high_of_day=max(bar.high for bar in session_bars),
        low_of_day=min(bar.low for bar in session_bars),
        average_recent_volume=average(
            [Decimal(bar.volume) for bar in volume_window]
        ),
        average_recent_range=average([bar.range for bar in range_window]),
    )


def upper_wick_ratio(bar: Bar) -> Decimal:
    return bar.upper_wick / bar.range if bar.range > 0 else Decimal("0")


def body_to_range_ratio(bar: Bar) -> Decimal:
    return bar.body / bar.range if bar.range > 0 else Decimal("0")


def extension_above(price: Decimal, reference: Decimal) -> Decimal:
    if reference <= 0:
        raise ValueError("Extension reference must be positive.")
    return (price - reference) / reference
