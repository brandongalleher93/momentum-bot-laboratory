"""Broker boundary types. Core modules depend on this protocol, not Alpaca classes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Protocol, Sequence

from bot.models import TradePlan


@dataclass(frozen=True)
class BrokerAccount:
    account_id: str
    status: str
    equity: Decimal
    cash: Decimal
    buying_power: Decimal
    trading_blocked: bool


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    status: str
    requested_quantity: int
    filled_quantity: int
    average_fill_price: Optional[Decimal]
    submitted_at: datetime
    child_order_ids: tuple[str, ...] = ()
    take_profit_order_id: Optional[str] = None
    stop_loss_order_id: Optional[str] = None
    side: str = ""
    filled_at: Optional[datetime] = None
    exit_fill_price: Optional[Decimal] = None
    exit_fill_quantity: int = 0
    exit_order_id: Optional[str] = None
    exit_filled_at: Optional[datetime] = None


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    quantity: int
    average_entry_price: Decimal
    current_price: Decimal


class PaperBroker(Protocol):
    def get_account(self) -> BrokerAccount: ...

    def get_open_orders(self) -> Sequence[BrokerOrder]: ...

    def get_recent_orders(self) -> Sequence[BrokerOrder]: ...

    def get_positions(self) -> Sequence[BrokerPosition]: ...

    def submit_bracket_entry(
        self,
        *,
        trade_id: str,
        client_order_id: str,
        plan: TradePlan,
        quantity: int,
    ) -> BrokerOrder: ...

    def cancel_order(self, broker_order_id: str) -> None: ...

    def replace_order(
        self,
        broker_order_id: str,
        *,
        limit_price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None,
    ) -> BrokerOrder: ...

    def submit_oco_exit(
        self,
        *,
        trade_id: str,
        symbol: str,
        quantity: int,
        target_price: Decimal,
        stop_price: Decimal,
    ) -> BrokerOrder: ...

    def close_position(self, symbol: str) -> None: ...

    def cancel_orders_for_symbol(self, symbol: str) -> None: ...

    def market_is_open(self) -> bool: ...
