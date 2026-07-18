"""Completed-candle indicator exits; stop and target remain broker-hosted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from bot.config import Settings
from bot.diagnostics import DiagnosticFactory
from bot.indicators import calculate_indicators
from bot.models import Bar, DiagnosticResult, PositionRecord


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str
    diagnostic: DiagnosticResult


class ExitManager:
    def __init__(self, settings: Settings, diagnostics: DiagnosticFactory):
        self.settings = settings
        self.diagnostics = diagnostics

    def evaluate(
        self,
        position: PositionRecord,
        completed_bars: Sequence[Bar],
        decision_time: datetime,
    ) -> ExitDecision:
        latest = completed_bars[-1]
        if latest.timestamp > decision_time:
            diagnostic = self.diagnostics.critical(
                module="exit_manager",
                symbol=position.symbol,
                reason="Exit evaluation attempted with a future completed bar.",
                decision_time=decision_time,
                data_cutoff_time=latest.timestamp,
                trade_id=position.trade_id,
            )
            return ExitDecision(False, diagnostic.reason, diagnostic)
        if latest.timestamp <= position.opened_at:
            diagnostic = self.diagnostics.passed(
                module="exit_manager",
                symbol=position.symbol,
                reason="No post-entry completed candle is available for indicator exits.",
                decision_time=decision_time,
                data_cutoff_time=latest.timestamp,
                trade_id=position.trade_id,
                actual_values={"should_exit": False},
            )
            return ExitDecision(False, diagnostic.reason, diagnostic)
        if position.last_exit_evaluated_bar == latest.timestamp:
            diagnostic = self.diagnostics.passed(
                module="exit_manager",
                symbol=position.symbol,
                reason="Completed bar already evaluated for exits.",
                decision_time=decision_time,
                data_cutoff_time=latest.timestamp,
                trade_id=position.trade_id,
            )
            return ExitDecision(False, diagnostic.reason, diagnostic)

        indicators = calculate_indicators(completed_bars, self.settings)
        checks = (
            (
                self.settings.exit_on_vwap_loss and latest.close < indicators.vwap,
                "VWAP lost on one completed candle close.",
                {"close": latest.close, "vwap": indicators.vwap},
            ),
            (
                self.settings.exit_on_ema_loss and latest.close < indicators.ema,
                "9 EMA lost on one completed candle close.",
                {"close": latest.close, "ema": indicators.ema},
            ),
            (
                self.settings.exit_on_breakout_level_fail
                and latest.close < position.breakout_level,
                "Breakout level failed on one completed candle close.",
                {"close": latest.close, "breakout_level": position.breakout_level},
            ),
        )
        for failed, reason, actual in checks:
            if failed:
                diagnostic = self.diagnostics.passed(
                    module="exit_manager",
                    symbol=position.symbol,
                    reason=reason,
                    decision_time=decision_time,
                    data_cutoff_time=latest.timestamp,
                    trade_id=position.trade_id,
                    actual_values={"should_exit": True, **actual},
                    expected_values={"confirmation": "one_completed_candle_close"},
                )
                return ExitDecision(True, reason, diagnostic)

        diagnostic = self.diagnostics.passed(
            module="exit_manager",
            symbol=position.symbol,
            reason="No completed-candle exit signal.",
            decision_time=decision_time,
            data_cutoff_time=latest.timestamp,
            trade_id=position.trade_id,
            actual_values={"should_exit": False, "close": latest.close},
            expected_values={"hold": True},
        )
        return ExitDecision(False, diagnostic.reason, diagnostic)
