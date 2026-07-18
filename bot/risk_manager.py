"""Projected daily-risk gate and position sizing from decision D-003."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from threading import Lock
from typing import Optional

from bot.config import Settings, settings as default_settings
from bot.diagnostics import DiagnosticFactory
from bot.models import DiagnosticResult, RiskApproval, RiskSnapshot, TradePlan


@dataclass(frozen=True)
class RiskEvaluation:
    approval: RiskApproval
    diagnostic: DiagnosticResult


class RiskLedger:
    """Atomically reserves pending risk so simultaneous entries cannot oversubscribe it."""

    def __init__(self) -> None:
        self._reservations: dict[str, Decimal] = {}
        self._lock = Lock()

    @property
    def total_reserved(self) -> Decimal:
        with self._lock:
            return sum(self._reservations.values(), Decimal("0"))

    def reserve(
        self,
        trade_id: str,
        amount: Decimal,
        realized_loss: Decimal,
        open_stop_risk: Decimal,
        max_daily_loss: Decimal,
    ) -> bool:
        with self._lock:
            if trade_id in self._reservations:
                return self._reservations[trade_id] == amount
            projected = (
                realized_loss
                + open_stop_risk
                + sum(self._reservations.values(), Decimal("0"))
                + amount
            )
            if projected > max_daily_loss:
                return False
            self._reservations[trade_id] = amount
            return True

    def release(self, trade_id: str) -> Decimal:
        with self._lock:
            return self._reservations.pop(trade_id, Decimal("0"))


class RiskManager:
    def __init__(
        self,
        settings: Settings = default_settings,
        diagnostics: Optional[DiagnosticFactory] = None,
    ) -> None:
        self.settings = settings
        self.diagnostics = diagnostics or DiagnosticFactory(settings)

    def evaluate(
        self,
        plan: TradePlan,
        snapshot: RiskSnapshot,
        decision_time: datetime,
    ) -> RiskEvaluation:
        if snapshot.realized_loss < 0:
            return self._reject(
                plan,
                snapshot,
                decision_time,
                "Realized loss must be stored as a positive magnitude.",
            )
        if snapshot.realized_loss >= self.settings.max_daily_loss:
            return self._reject(
                plan, snapshot, decision_time, "Daily realized-loss limit reached."
            )
        if snapshot.open_positions >= self.settings.max_open_positions:
            return self._reject(
                plan, snapshot, decision_time, "Maximum open positions reached."
            )
        if snapshot.active_entry_orders >= self.settings.max_active_entry_orders:
            return self._reject(
                plan, snapshot, decision_time, "Maximum active entry orders reached."
            )
        if (
            self.settings.max_trades_per_day is not None
            and snapshot.trades_taken_today >= self.settings.max_trades_per_day
        ):
            return self._reject(
                plan, snapshot, decision_time, "Maximum trades per day reached."
            )
        if (
            self.settings.max_consecutive_losses is not None
            and snapshot.consecutive_losses >= self.settings.max_consecutive_losses
        ):
            return self._reject(
                plan, snapshot, decision_time, "Consecutive-loss limit reached."
            )
        if plan.maximum_risk_per_share <= 0:
            return self._reject(
                plan, snapshot, decision_time, "Risk per share must be positive."
            )

        quantity_by_risk = int(
            (self.settings.max_risk_per_trade / plan.maximum_risk_per_share).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        quantity_by_value = int(
            (self.settings.max_position_value / plan.maximum_entry_price).to_integral_value(
                rounding=ROUND_FLOOR
            )
        )
        quantity = min(quantity_by_risk, quantity_by_value)
        if quantity < 1:
            return self._reject(
                plan, snapshot, decision_time, "Position size would be below one share."
            )

        proposed_risk = Decimal(quantity) * plan.maximum_risk_per_share
        position_value = Decimal(quantity) * plan.maximum_entry_price
        projected = (
            snapshot.realized_loss
            + snapshot.open_stop_risk
            + snapshot.pending_order_risk
            + proposed_risk
        )
        if projected > self.settings.max_daily_loss:
            return self._reject(
                plan,
                snapshot,
                decision_time,
                "Projected daily risk exceeds the daily budget.",
                projected=projected,
            )

        approval = RiskApproval(
            approved=True,
            reason="Projected-risk gate approved the trade.",
            quantity=quantity,
            proposed_risk=proposed_risk,
            position_value=position_value,
            projected_daily_loss=projected,
        )
        diagnostic = self.diagnostics.passed(
            module="risk_manager",
            symbol=plan.symbol,
            reason=approval.reason,
            decision_time=decision_time,
            data_cutoff_time=decision_time,
            actual_values={"approval": approval, "risk_snapshot": snapshot},
            expected_values={
                "max_risk_per_trade": self.settings.max_risk_per_trade,
                "max_daily_loss": self.settings.max_daily_loss,
                "max_position_value": self.settings.max_position_value,
            },
        )
        return RiskEvaluation(approval, diagnostic)

    def _reject(
        self,
        plan: TradePlan,
        snapshot: RiskSnapshot,
        decision_time: datetime,
        reason: str,
        projected: Optional[Decimal] = None,
    ) -> RiskEvaluation:
        projected_loss = projected or (
            snapshot.realized_loss
            + snapshot.open_stop_risk
            + snapshot.pending_order_risk
        )
        approval = RiskApproval(
            approved=False,
            reason=reason,
            quantity=0,
            proposed_risk=Decimal("0"),
            position_value=Decimal("0"),
            projected_daily_loss=projected_loss,
        )
        return RiskEvaluation(
            approval,
            self.diagnostics.rejected(
                module="risk_manager",
                symbol=plan.symbol,
                reason=reason,
                decision_time=decision_time,
                data_cutoff_time=decision_time,
                actual_values={"risk_snapshot": snapshot, "projected_loss": projected_loss},
                expected_values={"max_daily_loss": self.settings.max_daily_loss},
            ),
        )
