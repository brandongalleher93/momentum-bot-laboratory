from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from bot.models import Bar


def d(value: str) -> Decimal:
    return Decimal(value)


def valid_bull_flag_bars(symbol: str = "TEST") -> list[Bar]:
    """Ten baseline bars, one flagpole bar, and a two-bar pullback."""

    eastern = ZoneInfo("America/New_York")
    start = datetime(2026, 7, 15, 9, 31, tzinfo=eastern)
    bars: list[Bar] = []
    prices = [
        ("4.80", "4.83", "4.79", "4.82"),
        ("4.82", "4.84", "4.81", "4.83"),
        ("4.83", "4.85", "4.82", "4.84"),
        ("4.84", "4.86", "4.83", "4.85"),
        ("4.85", "4.87", "4.84", "4.86"),
        ("4.86", "4.88", "4.85", "4.87"),
        ("4.87", "4.89", "4.86", "4.88"),
        ("4.88", "4.90", "4.87", "4.89"),
        ("4.89", "4.91", "4.88", "4.90"),
        ("4.90", "4.92", "4.89", "4.91"),
    ]
    for index, (open_, high, low, close) in enumerate(prices):
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=start + timedelta(minutes=index),
                open=d(open_),
                high=d(high),
                low=d(low),
                close=d(close),
                volume=1_000,
            )
        )
    bars.extend(
        [
            Bar(
                symbol=symbol,
                timestamp=start + timedelta(minutes=10),
                open=d("4.90"),
                high=d("5.10"),
                low=d("4.90"),
                close=d("5.08"),
                volume=2_500,
            ),
            Bar(
                symbol=symbol,
                timestamp=start + timedelta(minutes=11),
                open=d("5.08"),
                high=d("5.09"),
                low=d("5.06"),
                close=d("5.07"),
                volume=800,
            ),
            Bar(
                symbol=symbol,
                timestamp=start + timedelta(minutes=12),
                open=d("5.07"),
                high=d("5.08"),
                low=d("5.05"),
                close=d("5.06"),
                volume=700,
            ),
        ]
    )
    return bars


def breakout_bar(symbol: str = "TEST") -> Bar:
    prior = valid_bull_flag_bars(symbol)[-1]
    return Bar(
        symbol=symbol,
        timestamp=prior.timestamp + timedelta(minutes=1),
        open=d("5.08"),
        high=d("5.25"),
        low=d("5.04"),
        close=d("5.20"),
        volume=3_000,
    )
