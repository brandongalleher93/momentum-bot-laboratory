"""Paper-only execution service with risk reservations and partial-fill protection."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from bot.broker import BrokerOrder, PaperBroker
from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.event_log import DiagnosticLogger, JsonlEventLog
from bot.models import (
    DiagnosticResult,
    OrderRecord,
    OrderState,
    PositionRecord,
    RiskApproval,
    TradePlan,
)
from bot.order_state import OrderStateMachine
from bot.risk_manager import RiskLedger
from bot.session import SessionState


class ExecutionService:
    def __init__(
        self,
        *,
        settings: Settings,
        broker: PaperBroker,
        state: SessionState,
        ledger: RiskLedger,
        diagnostics: DiagnosticFactory,
        diagnostic_logger: DiagnosticLogger,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.state = state
        self.ledger = ledger
        self.diagnostics = diagnostics
        self.diagnostic_logger = diagnostic_logger
        self.order_events = JsonlEventLog(settings.log_dir / "order_events.jsonl")
        self.machine = OrderStateMachine()
        self.plans: dict[str, TradePlan] = {}

    def submit(
        self, plan: TradePlan, approval: RiskApproval, decision_time: datetime
    ) -> DiagnosticResult:
        if not self.settings.alpaca_paper or self.settings.allow_live_trading:
            result = self.diagnostics.critical(
                module="order_executor",
                symbol=plan.symbol,
                reason="Order blocked because verified paper-only settings are not active.",
                decision_time=decision_time,
                data_cutoff_time=decision_time,
            )
            self.diagnostic_logger.log(result)
            return result
        if not self.settings.paper_order_submission_enabled:
            result = self.diagnostics.rejected(
                module="order_executor",
                symbol=plan.symbol,
                reason="Paper submission is disarmed; set PAPER_ORDER_SUBMISSION_ENABLED=true after review.",
                decision_time=decision_time,
                data_cutoff_time=decision_time,
            )
            self.diagnostic_logger.log(result)
            return result
        if not approval.approved or approval.quantity < 1:
            raise ValueError("Execution requires a positive risk approval.")

        account = self.broker.get_account()
        if account.trading_blocked or account.status.lower() != "active":
            result = self.diagnostics.critical(
                module="order_executor",
                symbol=plan.symbol,
                reason="Paper account is not active for trading.",
                decision_time=decision_time,
                data_cutoff_time=decision_time,
                actual_values={"account_status": account.status, "blocked": account.trading_blocked},
            )
            self.diagnostic_logger.log(result)
            return result

        trade_id = str(uuid4())
        reserved = self.ledger.reserve(
            trade_id,
            approval.proposed_risk,
            self.state.realized_loss,
            self.state.risk_snapshot().open_stop_risk,
            self.settings.max_daily_loss,
        )
        if not reserved:
            result = self.diagnostics.rejected(
                module="order_executor",
                symbol=plan.symbol,
                reason="Atomic risk reservation failed; projected budget changed.",
                decision_time=decision_time,
                data_cutoff_time=decision_time,
                trade_id=trade_id,
            )
            self.diagnostic_logger.log(result)
            return result

        client_order_id = f"mvp-{trade_id[:12]}-entry"
        try:
            broker_order = self.broker.submit_bracket_entry(
                trade_id=trade_id,
                client_order_id=client_order_id,
                plan=plan,
                quantity=approval.quantity,
            )
        except Exception as exc:
            self.ledger.release(trade_id)
            result = self.diagnostics.rejected(
                module="order_executor",
                symbol=plan.symbol,
                reason="Paper order submission failed.",
                decision_time=decision_time,
                data_cutoff_time=decision_time,
                trade_id=trade_id,
                actual_values={"error_type": type(exc).__name__, "error": str(exc)},
            )
            self.diagnostic_logger.log(result)
            return result

        local = OrderRecord(
            trade_id=trade_id,
            client_order_id=client_order_id,
            broker_order_id=broker_order.broker_order_id,
            symbol=plan.symbol,
            requested_quantity=approval.quantity,
            reserved_risk=approval.proposed_risk,
            state=OrderState.PENDING,
            child_order_ids=list(broker_order.child_order_ids),
        )
        self.state.add_order(local)
        self.plans[trade_id] = plan
        self.order_events.append({"event": "submitted", "trade_id": trade_id, "order": broker_order})
        result = self.diagnostics.passed(
            module="order_executor",
            symbol=plan.symbol,
            reason="Paper bracket order accepted as pending.",
            decision_time=decision_time,
            data_cutoff_time=decision_time,
            trade_id=trade_id,
            actual_values={
                "broker_order_id": broker_order.broker_order_id,
                "client_order_id": client_order_id,
                "requested_quantity": approval.quantity,
            },
            expected_values={"paper": True},
        )
        self.diagnostic_logger.log(result)
        return result

    def process_broker_order(self, value: BrokerOrder, plan: Optional[TradePlan] = None) -> None:
        local = self.state.find_order(
            broker_order_id=value.broker_order_id,
            client_order_id=value.client_order_id,
        )
        if local is None:
            self.state.halt_entries("Broker order was not found in local state.")
            self.order_events.append({"event": "unmatched_broker_order", "order": value})
            return

        plan = plan or self.plans.get(local.trade_id)

        transition = self.machine.apply(
            local,
            value.status,
            filled_quantity=value.filled_quantity,
            average_fill_price=value.average_fill_price,
        )
        self.order_events.append(
            {"event": "broker_transition", "order": value, "transition": transition}
        )
        if value.child_order_ids:
            local.child_order_ids = list(value.child_order_ids)
        if transition.reconcile:
            self.state.halt_entries(transition.reason)
            return
        needs_fill_target_replacement = (
            local.state == OrderState.FILLED
            and value.take_profit_order_id is not None
            and not local.target_replaced_from_fill
        )
        if not transition.changed and not needs_fill_target_replacement:
            return
        if transition.cancel_remainder and not local.cancel_requested:
            self.broker.cancel_order(value.broker_order_id)
            local.cancel_requested = True
        if transition.release_reserved_risk:
            self.ledger.release(local.trade_id)
            local.reserved_risk = Decimal("0")
        if transition.protect_filled_quantity and not local.protection_confirmed:
            if plan is None:
                self.state.halt_entries("Partial fill needs a trade plan for OCO protection.")
                self.broker.close_position(local.symbol)
                return
            try:
                partial_risk_per_share = local.average_fill_price - plan.stop_price
                partial_target = local.average_fill_price + (
                    self.settings.target_r_multiple * partial_risk_per_share
                )
                protection = self.broker.submit_oco_exit(
                    trade_id=local.trade_id,
                    symbol=local.symbol,
                    quantity=local.filled_quantity,
                    target_price=partial_target,
                    stop_price=plan.stop_price,
                )
                local.protection_order_id = protection.broker_order_id
                local.protection_confirmed = True
            except Exception:
                self.broker.close_position(local.symbol)
                self.state.halt_entries(
                    "Partial fill could not be protected and was flattened."
                )
        if local.filled_quantity > 0 and local.average_fill_price is not None:
            if plan is None:
                self.state.halt_entries("Filled order has no local trade plan.")
                return
            actual_risk_per_share = local.average_fill_price - plan.stop_price
            actual_target = local.average_fill_price + (
                self.settings.target_r_multiple * actual_risk_per_share
            )
            existing_position = self.state.positions.get(local.trade_id)
            if existing_position is None:
                self.state.positions[local.trade_id] = PositionRecord(
                    trade_id=local.trade_id,
                    symbol=local.symbol,
                    quantity=local.filled_quantity,
                    average_entry_price=local.average_fill_price,
                    stop_price=plan.stop_price,
                    target_price=actual_target,
                    breakout_level=plan.breakout_level,
                    initial_risk=Decimal(local.filled_quantity) * actual_risk_per_share,
                    opened_at=value.filled_at or datetime.now(timezone.utc),
                )
                self.state.trades_taken_today += 1
            else:
                existing_position.quantity = local.filled_quantity
                existing_position.average_entry_price = local.average_fill_price
                existing_position.target_price = actual_target
                existing_position.initial_risk = (
                    Decimal(local.filled_quantity) * actual_risk_per_share
                )
        if local.state == OrderState.FILLED:
            if (
                value.take_profit_order_id is not None
                and local.average_fill_price is not None
                and plan is not None
            ):
                actual_risk_per_share = local.average_fill_price - plan.stop_price
                actual_target = local.average_fill_price + (
                    self.settings.target_r_multiple * actual_risk_per_share
                )
                if actual_target != plan.target_price:
                    try:
                        self.broker.replace_order(
                            value.take_profit_order_id, limit_price=actual_target
                        )
                        local.target_replaced_from_fill = True
                    except Exception as exc:
                        self.state.halt_entries(
                            "Could not replace provisional target with fill-based 2R target."
                        )
                        self.order_events.append(
                            {
                                "event": "target_replace_failed",
                                "trade_id": local.trade_id,
                                "error": str(exc),
                            }
                        )
                else:
                    local.target_replaced_from_fill = True
            self.ledger.release(local.trade_id)
            local.reserved_risk = Decimal("0")

    def request_indicator_exit(
        self, trade_id: str, reason: str, decision_time: datetime
    ) -> DiagnosticResult:
        position = self.state.positions.get(trade_id)
        if position is None:
            return self.diagnostics.warning(
                module="exit_executor",
                symbol=None,
                reason="Indicator exit ignored because no local position exists.",
                decision_time=decision_time,
                data_cutoff_time=decision_time,
                trade_id=trade_id,
            )

        self.broker.cancel_orders_for_symbol(position.symbol)
        remaining_orders = [
            value
            for value in self.broker.get_open_orders()
            if value.symbol == position.symbol
        ]
        if remaining_orders:
            self.state.halt_entries(
                "Could not confirm bracket cancellation before indicator exit."
            )
            result = self.diagnostics.critical(
                module="exit_executor",
                symbol=position.symbol,
                reason="Indicator exit deferred because protective order cancellation is unconfirmed.",
                decision_time=decision_time,
                data_cutoff_time=decision_time,
                trade_id=trade_id,
                actual_values={"remaining_order_ids": [o.broker_order_id for o in remaining_orders]},
            )
            self.diagnostic_logger.log(result)
            return result

        self.broker.close_position(position.symbol)
        result = self.diagnostics.passed(
            module="exit_executor",
            symbol=position.symbol,
            reason=f"Paper position close submitted: {reason}",
            decision_time=decision_time,
            data_cutoff_time=decision_time,
            trade_id=trade_id,
        )
        self.diagnostic_logger.log(result)
        return result
