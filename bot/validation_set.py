"""Independent, date-scoped momentum validation-set helpers."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ValidationTarget:
    trade_date: date
    symbol: str
    premarket_gain_percent: Decimal
    observed_price: Decimal
    observed_volume: int
    source_url: str


def load_validation_targets(path: Path) -> list[ValidationTarget]:
    """Load and validate a preselected symbol/date manifest."""

    targets: list[ValidationTarget] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            target = ValidationTarget(
                trade_date=date.fromisoformat(row["trade_date"]),
                symbol=row["symbol"].strip().upper(),
                premarket_gain_percent=Decimal(row["premarket_gain_percent"]),
                observed_price=Decimal(row["observed_price"]),
                observed_volume=int(row["observed_volume"]),
                source_url=row["source_url"].strip(),
            )
            if not target.symbol:
                raise ValueError("Validation symbols cannot be blank.")
            targets.append(target)

    keys = [(target.trade_date, target.symbol) for target in targets]
    if len(keys) != len(set(keys)):
        raise ValueError("Validation symbol/date pairs must be unique.")
    return sorted(targets, key=lambda target: (target.trade_date, target.symbol))


def target_dates_by_symbol(
    targets: Iterable[ValidationTarget],
) -> dict[str, set[date]]:
    result: dict[str, set[date]] = {}
    for target in targets:
        result.setdefault(target.symbol, set()).add(target.trade_date)
    return result


def validation_download_windows(
    target: ValidationTarget,
    timezone_name: str,
    *,
    minute_lookback_calendar_days: int = 35,
) -> tuple[datetime, datetime, datetime, datetime]:
    """Return UTC minute-context and target-day execution windows."""

    if minute_lookback_calendar_days < 1:
        raise ValueError("Minute lookback must be at least one calendar day.")
    local_zone = ZoneInfo(timezone_name)
    target_start = datetime.combine(
        target.trade_date, time.min, tzinfo=local_zone
    )
    target_end = datetime.combine(
        target.trade_date, time.max, tzinfo=local_zone
    )
    minute_start = target_start - timedelta(
        days=minute_lookback_calendar_days
    )
    return (
        minute_start.astimezone(timezone.utc),
        target_end.astimezone(timezone.utc),
        target_start.astimezone(timezone.utc),
        target_end.astimezone(timezone.utc),
    )
