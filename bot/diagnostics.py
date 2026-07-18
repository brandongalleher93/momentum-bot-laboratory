"""Structured diagnostics and the causality guard from decision D-013."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from uuid import uuid4

from bot.config import Settings
from bot.models import DiagnosticResult, Severity


class DiagnosticFactory:
    def __init__(self, settings: Settings):
        self.settings = settings

    def make(
        self,
        *,
        passed: bool,
        severity: Severity,
        module: str,
        reason: str,
        decision_time: datetime,
        data_cutoff_time: datetime,
        symbol: Optional[str] = None,
        actual_values: Optional[Mapping[str, Any]] = None,
        expected_values: Optional[Mapping[str, Any]] = None,
        trade_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> DiagnosticResult:
        if decision_time.tzinfo is None or data_cutoff_time.tzinfo is None:
            raise ValueError("Diagnostic times must be timezone-aware.")
        if data_cutoff_time > decision_time:
            passed = False
            severity = Severity.CRITICAL
            reason = "Data cutoff occurs after decision time; lookahead blocked."
            notes = "The requested diagnostic violated decision D-013."

        return DiagnosticResult(
            event_id=str(uuid4()),
            parent_event_id=parent_event_id,
            trade_id=trade_id,
            passed=passed,
            module=module,
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            decision_time=decision_time,
            data_cutoff_time=data_cutoff_time,
            reason=reason,
            expected_values=expected_values or {},
            actual_values=actual_values or {},
            config_version=self.settings.version,
            parameter_profile=self.settings.parameter_profile,
            decision_register_version=self.settings.decision_register_version,
            severity=severity,
            notes=notes,
        )

    def passed(self, **kwargs: Any) -> DiagnosticResult:
        return self.make(passed=True, severity=Severity.INFO, **kwargs)

    def warning(self, **kwargs: Any) -> DiagnosticResult:
        return self.make(passed=True, severity=Severity.WARNING, **kwargs)

    def rejected(self, **kwargs: Any) -> DiagnosticResult:
        return self.make(passed=False, severity=Severity.REJECTION, **kwargs)

    def critical(self, **kwargs: Any) -> DiagnosticResult:
        return self.make(passed=False, severity=Severity.CRITICAL, **kwargs)
