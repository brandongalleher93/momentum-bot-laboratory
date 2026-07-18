"""Idempotent order lifecycle transitions and partial-fill safety actions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from bot.models import OrderRecord, OrderState


TERMINAL_STATES = {
    OrderState.FILLED,
    OrderState.CANCELED,
    OrderState.EXPIRED,
    OrderState.REJECTED,
    OrderState.CLOSED,
}


@dataclass(frozen=True)
class OrderTransition:
    changed: bool
    cancel_remainder: bool = False
    protect_filled_quantity: bool = False
    release_reserved_risk: bool = False
    reconcile: bool = False
    reason: str = ""


class OrderStateMachine:
    STATUS_MAP = {
        "accepted": OrderState.PENDING,
        "pending_new": OrderState.PENDING,
        "new": OrderState.PENDING,
        "partially_filled": OrderState.PARTIALLY_FILLED,
        "partial_fill": OrderState.PARTIALLY_FILLED,
        "filled": OrderState.FILLED,
        "fill": OrderState.FILLED,
        "canceled": OrderState.CANCELED,
        "expired": OrderState.EXPIRED,
        "rejected": OrderState.REJECTED,
        "replaced": OrderState.REPLACED,
    }

    def apply(
        self,
        order: OrderRecord,
        broker_status: str,
        *,
        filled_quantity: Optional[int] = None,
        average_fill_price: Optional[Decimal] = None,
    ) -> OrderTransition:
        normalized = broker_status.strip().lower()
        if normalized not in self.STATUS_MAP:
            return OrderTransition(
                changed=False,
                reconcile=True,
                reason=f"Unknown broker order status: {broker_status}",
            )
        target = self.STATUS_MAP[normalized]
        if order.state in TERMINAL_STATES and target != order.state:
            return OrderTransition(
                changed=False,
                reconcile=True,
                reason="Conflicting event arrived after terminal order state.",
            )

        new_quantity = order.filled_quantity if filled_quantity is None else filled_quantity
        if new_quantity < order.filled_quantity or new_quantity > order.requested_quantity:
            return OrderTransition(
                changed=False,
                reconcile=True,
                reason="Broker filled quantity conflicts with local order state.",
            )

        changed = (
            target != order.state
            or new_quantity != order.filled_quantity
            or (
                average_fill_price is not None
                and average_fill_price != order.average_fill_price
            )
        )
        if not changed:
            return OrderTransition(changed=False, reason="Duplicate event ignored.")

        order.state = target
        order.filled_quantity = new_quantity
        if average_fill_price is not None:
            order.average_fill_price = average_fill_price

        if target == OrderState.PARTIALLY_FILLED:
            return OrderTransition(
                changed=True,
                cancel_remainder=True,
                reason="Partial fill requires cancellation of the unfilled remainder.",
            )
        if target in {OrderState.CANCELED, OrderState.EXPIRED, OrderState.REJECTED}:
            return OrderTransition(
                changed=True,
                protect_filled_quantity=order.filled_quantity > 0,
                release_reserved_risk=True,
                reason=(
                    f"Order entered terminal state {target.value}; protect any filled quantity."
                    if order.filled_quantity > 0
                    else f"Order entered terminal state {target.value}."
                ),
            )
        return OrderTransition(changed=True, reason=f"Order moved to {target.value}.")
