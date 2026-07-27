"""Recoverable local session state; broker reconciliation remains authoritative."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from threading import RLock
from typing import Optional

from bot.models import OrderRecord, OrderState, PositionRecord, RiskSnapshot


ACTIVE_ORDER_STATES = {
    OrderState.PENDING,
    OrderState.PARTIALLY_FILLED,
    OrderState.REPLACED,
}


@dataclass
class SessionState:
    realized_loss: Decimal = Decimal("0")
    unrealized_loss: Decimal = Decimal("0")
    trades_taken_today: int = 0
    consecutive_losses: int = 0
    symbol_loss_streaks: dict[tuple[date, str], int] = field(
        default_factory=dict
    )
    orders: dict[str, OrderRecord] = field(default_factory=dict)
    positions: dict[str, PositionRecord] = field(default_factory=dict)
    new_entries_enabled: bool = True
    halt_reason: Optional[str] = None
    _lock: RLock = field(default_factory=RLock, repr=False)

    def risk_snapshot(
        self,
        *,
        symbol: str | None = None,
        session_date: date | None = None,
    ) -> RiskSnapshot:
        with self._lock:
            open_stop_risk = sum(
                (
                    Decimal(position.quantity)
                    * max(position.average_entry_price - position.stop_price, Decimal("0"))
                )
                for position in self.positions.values()
            )
            active_orders = [
                order for order in self.orders.values() if order.state in ACTIVE_ORDER_STATES
            ]
            pending_risk = sum(
                (order.reserved_risk for order in active_orders), Decimal("0")
            )
            return RiskSnapshot(
                realized_loss=self.realized_loss,
                open_stop_risk=open_stop_risk,
                pending_order_risk=pending_risk,
                trades_taken_today=self.trades_taken_today,
                consecutive_losses=self.consecutive_losses,
                symbol_consecutive_losses=(
                    self.symbol_loss_streaks.get(
                        (session_date, symbol.upper()), 0
                    )
                    if symbol is not None and session_date is not None
                    else 0
                ),
                open_positions=len(self.positions),
                active_entry_orders=len(active_orders),
            )

    def add_order(self, order: OrderRecord) -> None:
        with self._lock:
            if order.trade_id in self.orders:
                raise ValueError(f"Trade {order.trade_id} already has a local order.")
            self.orders[order.trade_id] = order

    def find_order(
        self, *, broker_order_id: Optional[str] = None, client_order_id: Optional[str] = None
    ) -> Optional[OrderRecord]:
        with self._lock:
            for order in self.orders.values():
                if broker_order_id and order.broker_order_id == broker_order_id:
                    return order
                if client_order_id and order.client_order_id == client_order_id:
                    return order
        return None

    def halt_entries(self, reason: str) -> None:
        with self._lock:
            self.new_entries_enabled = False
            self.halt_reason = reason

    def record_closed_pnl(
        self,
        realized_pnl: Decimal,
        *,
        symbol: str | None = None,
        session_date: date | None = None,
    ) -> None:
        with self._lock:
            if realized_pnl < 0:
                self.realized_loss += abs(realized_pnl)
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
            if symbol is not None and session_date is not None:
                key = (session_date, symbol.upper())
                if realized_pnl < 0:
                    self.symbol_loss_streaks[key] = (
                        self.symbol_loss_streaks.get(key, 0) + 1
                    )
                else:
                    self.symbol_loss_streaks[key] = 0
