"""Domain models shared by live trading, paper execution, and backtesting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


ZERO = Decimal("0")


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    REJECTION = "rejection"
    CRITICAL = "critical"


class OrderState(str, Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    REPLACED = "replaced"
    EXIT_PENDING = "exit_pending"
    CLOSED = "closed"


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Bar timestamps must be timezone-aware.")
        if min(self.open, self.high, self.low, self.close) <= ZERO:
            raise ValueError("Bar prices must be positive.")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("Bar high is inconsistent with OHLC values.")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("Bar low is inconsistent with OHLC values.")
        if self.volume < 0:
            raise ValueError("Bar volume cannot be negative.")

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    @property
    def body(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def green(self) -> bool:
        return self.close > self.open

    @property
    def red(self) -> bool:
        return self.close < self.open

    @property
    def upper_wick(self) -> Decimal:
        return self.high - max(self.open, self.close)


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Quote timestamps must be timezone-aware.")
        if self.bid <= ZERO or self.ask <= ZERO or self.ask < self.bid:
            raise ValueError("Quote must have positive bid <= ask.")

    @property
    def midpoint(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")

    @property
    def spread_percent(self) -> Decimal:
        return (self.ask - self.bid) / self.midpoint


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    timestamp: datetime
    price: Decimal
    percent_gain: Decimal
    rvol: Decimal
    day_volume: int
    bid: Decimal
    ask: Decimal
    float_shares: Optional[int] = None

    @property
    def spread_percent(self) -> Decimal:
        midpoint = (self.bid + self.ask) / Decimal("2")
        return (self.ask - self.bid) / midpoint if midpoint > ZERO else Decimal("Infinity")


@dataclass(frozen=True)
class IndicatorSnapshot:
    timestamp: datetime
    data_cutoff_time: datetime
    vwap: Decimal
    ema: Decimal
    high_of_day: Decimal
    low_of_day: Decimal
    average_recent_volume: Decimal
    average_recent_range: Decimal


@dataclass(frozen=True)
class Flagpole:
    bars: Sequence[Bar]
    low: Decimal
    high: Decimal
    percent_move: Decimal
    green_candle_count: int
    average_volume: Decimal
    average_range: Decimal
    volume_ratio: Decimal


@dataclass(frozen=True)
class Pullback:
    bars: Sequence[Bar]
    low: Decimal
    high: Decimal
    depth: Decimal
    volume_ratio: Decimal
    largest_upper_wick_ratio: Decimal


@dataclass(frozen=True)
class Setup:
    setup_id: str
    symbol: str
    flagpole: Flagpole
    pullback: Pullback
    breakout_level: Decimal
    trigger_price: Decimal
    stop_price: Decimal
    created_at: datetime
    data_cutoff_time: datetime
    expires_at: datetime


@dataclass(frozen=True)
class EntrySignal:
    triggered: bool
    observed_price: Decimal
    trigger_price: Decimal
    maximum_entry_price: Decimal
    timestamp: datetime


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    setup_id: str
    maximum_entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    maximum_risk_per_share: Decimal
    reward_to_risk: Decimal
    breakout_level: Decimal


@dataclass(frozen=True)
class RiskSnapshot:
    realized_loss: Decimal = ZERO
    open_stop_risk: Decimal = ZERO
    pending_order_risk: Decimal = ZERO
    trades_taken_today: int = 0
    consecutive_losses: int = 0
    symbol_consecutive_losses: int = 0
    open_positions: int = 0
    active_entry_orders: int = 0


@dataclass(frozen=True)
class RiskApproval:
    approved: bool
    reason: str
    quantity: int
    proposed_risk: Decimal
    position_value: Decimal
    projected_daily_loss: Decimal


@dataclass
class OrderRecord:
    trade_id: str
    client_order_id: str
    symbol: str
    requested_quantity: int
    reserved_risk: Decimal
    state: OrderState = OrderState.PENDING
    broker_order_id: Optional[str] = None
    filled_quantity: int = 0
    average_fill_price: Optional[Decimal] = None
    child_order_ids: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    protection_order_id: Optional[str] = None
    protection_confirmed: bool = False
    target_replaced_from_fill: bool = False


@dataclass
class PositionRecord:
    trade_id: str
    symbol: str
    quantity: int
    average_entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    breakout_level: Decimal
    initial_risk: Decimal
    opened_at: datetime
    last_exit_evaluated_bar: Optional[datetime] = None


@dataclass(frozen=True)
class Fill:
    trade_id: str
    broker_order_id: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    timestamp: datetime


@dataclass(frozen=True)
class DiagnosticResult:
    event_id: str
    parent_event_id: Optional[str]
    trade_id: Optional[str]
    passed: bool
    module: str
    symbol: Optional[str]
    timestamp: datetime
    decision_time: datetime
    data_cutoff_time: datetime
    reason: str
    expected_values: Mapping[str, Any]
    actual_values: Mapping[str, Any]
    config_version: str
    parameter_profile: str
    decision_register_version: str
    severity: Severity
    notes: Optional[str] = None
