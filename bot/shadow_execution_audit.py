"""Read-only post-session validation of shadow fills against historical SIP quotes."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Protocol, Sequence
from zoneinfo import ZoneInfo

from bot.config import Settings, validate_settings
from bot.event_log import to_json_safe
from bot.models import Quote
from bot.security import ensure_private_file
from bot.shadow_paper import ShadowTrade


AUDIT_MATURITY_DELAY = timedelta(minutes=16)
QUOTE_WINDOW = timedelta(seconds=3)
RECONSTRUCTION_CHUNK = timedelta(minutes=15)
MINIMUM_PRICE_TOLERANCE = Decimal("0.02")
PRICE_TOLERANCE_PERCENT = Decimal("0.01")


class HistoricalQuoteProvider(Protocol):
    def get_quotes(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[Quote]: ...


class AlpacaSipQuoteProvider:
    """Fetch consolidated historical quotes without placing broker orders."""

    def __init__(self, settings: Settings):
        validate_settings(settings, require_alpaca_keys=True)
        from alpaca.data.historical import StockHistoricalDataClient

        self.client = StockHistoricalDataClient(
            settings.alpaca_api_key,
            settings.alpaca_secret_key,
        )

    def get_quotes(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[Quote]:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockQuotesRequest

        response = self.client.get_stock_quotes(
            StockQuotesRequest(
                symbol_or_symbols=symbol,
                start=start,
                end=end,
                feed=DataFeed.SIP,
            )
        )
        quotes: list[Quote] = []
        for value in response.data.get(symbol, []):
            try:
                quotes.append(
                    Quote(
                        symbol=symbol,
                        timestamp=value.timestamp,
                        bid=Decimal(str(value.bid_price)),
                        ask=Decimal(str(value.ask_price)),
                    )
                )
            except ValueError:
                continue
        return sorted(quotes, key=lambda quote: quote.timestamp)


@dataclass(frozen=True)
class PricePathReconstruction:
    exit_time: datetime
    exit_price: Decimal
    reason: str
    realized_pnl: Decimal
    r_multiple: Decimal


@dataclass(frozen=True)
class ExecutionAuditRow:
    trade_id: str
    symbol: str
    entry_time: datetime
    exit_time: datetime | None
    exit_reason: str | None
    raw_pnl: Decimal | None
    raw_r_multiple: Decimal | None
    status: str
    explanation: str
    sip_entry_timestamp: datetime | None = None
    sip_entry_ask: Decimal | None = None
    entry_price_difference: Decimal | None = None
    sip_exit_timestamp: datetime | None = None
    sip_exit_bid: Decimal | None = None
    exit_price_difference: Decimal | None = None
    reconstruction: PricePathReconstruction | None = None


@dataclass(frozen=True)
class ExecutionAuditReport:
    generated_at: datetime
    source: str
    ledger_trade_count: int
    rows: tuple[ExecutionAuditRow, ...]
    notes: tuple[str, ...]

    def summary(self) -> dict[str, Decimal | int]:
        raw_net = sum(
            (row.raw_pnl or Decimal("0") for row in self.rows),
            Decimal("0"),
        )
        confirmed = [row for row in self.rows if row.status == "confirmed"]
        discrepant = [row for row in self.rows if row.status == "discrepant"]
        unresolved = [row for row in self.rows if row.status == "unresolved"]
        estimated_rows = [
            row
            for row in self.rows
            if row.status == "confirmed" or row.reconstruction is not None
        ]
        estimated_net = sum(
            (
                row.reconstruction.realized_pnl
                if row.reconstruction is not None
                else row.raw_pnl or Decimal("0")
                for row in estimated_rows
            ),
            Decimal("0"),
        )
        return {
            "ledger_trade_count": self.ledger_trade_count,
            "confirmed_count": len(confirmed),
            "discrepant_count": len(discrepant),
            "unresolved_count": len(unresolved),
            "raw_net_profit": raw_net,
            "confirmed_raw_net_profit": sum(
                (row.raw_pnl or Decimal("0") for row in confirmed),
                Decimal("0"),
            ),
            "estimated_trade_count": len(estimated_rows),
            "estimated_sip_path_net_profit": estimated_net,
        }


class ShadowExecutionAuditor:
    """Compare immutable shadow records with later-available SIP evidence."""

    def __init__(
        self,
        settings: Settings,
        quote_provider: HistoricalQuoteProvider,
    ):
        self.settings = settings
        self.quote_provider = quote_provider

    def audit(
        self,
        trades: Sequence[ShadowTrade],
        *,
        now: datetime | None = None,
    ) -> ExecutionAuditReport:
        generated_at = now or datetime.now(timezone.utc)
        if generated_at.tzinfo is None:
            raise ValueError("Audit time must be timezone-aware.")
        closed = [trade for trade in trades if trade.status == "closed"]
        rows = tuple(self._audit_trade(trade, generated_at) for trade in closed)
        return ExecutionAuditReport(
            generated_at=generated_at,
            source="historical_sip_quotes",
            ledger_trade_count=len(closed),
            rows=rows,
            notes=(
                "The shadow SQLite ledger is never modified by this audit.",
                (
                    "Confirmed rows have SIP evidence consistent with the "
                    "recorded entry and exit."
                ),
                (
                    "Price-only reconstructions ignore VWAP, EMA, and "
                    "breakout-close exits and are estimates, not replacement "
                    "ledger results."
                ),
                (
                    "Trades inside the recent SIP subscription window remain "
                    "unresolved until rerun later."
                ),
            ),
        )

    def _audit_trade(
        self,
        trade: ShadowTrade,
        now: datetime,
    ) -> ExecutionAuditRow:
        if trade.exit_time is None or trade.exit_price is None:
            return self._unresolved(trade, "The trade has no completed exit.")
        if trade.exit_time > now - AUDIT_MATURITY_DELAY:
            return self._unresolved(
                trade,
                "Historical SIP evidence is not old enough to query yet.",
            )
        try:
            entry_quote = self._nearest_quote(trade.symbol, trade.entry_time)
            exit_quote = self._nearest_quote(trade.symbol, trade.exit_time)
        except Exception as exc:
            return self._unresolved(
                trade,
                f"Historical SIP request failed: {type(exc).__name__}.",
            )
        if entry_quote is None or exit_quote is None:
            missing = []
            if entry_quote is None:
                missing.append("entry")
            if exit_quote is None:
                missing.append("exit")
            return self._unresolved(
                trade,
                (
                    "No nearby historical SIP "
                    f"{' and '.join(missing)} quote was available."
                ),
                entry_quote=entry_quote,
                exit_quote=exit_quote,
            )

        entry_difference = entry_quote.ask - trade.entry_price
        exit_difference = exit_quote.bid - trade.exit_price
        entry_consistent = abs(entry_difference) <= self._price_tolerance(
            trade.entry_price
        )
        exit_consistent, exit_explanation = self._exit_is_consistent(
            trade, exit_quote
        )
        status = "confirmed" if entry_consistent and exit_consistent else "discrepant"
        explanations = []
        if entry_consistent:
            explanations.append("SIP entry ask is consistent with the shadow entry.")
        else:
            explanations.append(
                "SIP entry ask materially differs from the shadow entry."
            )
        explanations.append(exit_explanation)

        reconstruction = None
        if status == "discrepant" and entry_consistent and self._is_price_exit(trade):
            try:
                reconstruction = self._reconstruct_price_path(trade)
            except Exception as exc:
                explanations.append(
                    f"Price-path reconstruction failed: {type(exc).__name__}."
                )
        return ExecutionAuditRow(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            exit_reason=trade.exit_reason,
            raw_pnl=trade.realized_pnl,
            raw_r_multiple=trade.r_multiple,
            status=status,
            explanation=" ".join(explanations),
            sip_entry_timestamp=entry_quote.timestamp,
            sip_entry_ask=entry_quote.ask,
            entry_price_difference=entry_difference,
            sip_exit_timestamp=exit_quote.timestamp,
            sip_exit_bid=exit_quote.bid,
            exit_price_difference=exit_difference,
            reconstruction=reconstruction,
        )

    def _nearest_quote(self, symbol: str, timestamp: datetime) -> Quote | None:
        quotes = self.quote_provider.get_quotes(
            symbol,
            timestamp - QUOTE_WINDOW,
            timestamp + QUOTE_WINDOW,
        )
        if not quotes:
            return None
        nearest = min(
            quotes,
            key=lambda quote: abs((quote.timestamp - timestamp).total_seconds()),
        )
        distance = abs((nearest.timestamp - timestamp).total_seconds())
        if distance > QUOTE_WINDOW.total_seconds():
            return None
        return nearest

    def _exit_is_consistent(
        self,
        trade: ShadowTrade,
        quote: Quote,
    ) -> tuple[bool, str]:
        if trade.exit_reason == "Shadow protective stop reached.":
            if quote.bid <= trade.stop_price:
                return True, "SIP bid confirms the protective-stop trigger."
            return False, "SIP bid remained above the protective stop."
        if trade.exit_reason == "Shadow 2R target reached.":
            if quote.bid >= trade.target_price:
                return True, "SIP bid confirms the target trigger."
            return False, "SIP bid remained below the target."
        difference = abs(quote.bid - (trade.exit_price or quote.bid))
        if difference <= self._price_tolerance(trade.entry_price):
            return True, "SIP bid is consistent with the recorded indicator exit."
        return False, "SIP bid materially differs from the recorded indicator exit."

    def _reconstruct_price_path(
        self,
        trade: ShadowTrade,
    ) -> PricePathReconstruction | None:
        if trade.exit_time is None:
            return None
        zone = ZoneInfo(self.settings.timezone)
        local_exit = trade.exit_time.astimezone(zone)
        end_local = datetime.combine(
            local_exit.date(),
            self.settings.trade_window_end,
            tzinfo=zone,
        )
        end = end_local.astimezone(timezone.utc)
        if end <= trade.exit_time:
            return None

        cursor = trade.exit_time + timedelta(microseconds=1)
        last_quote: Quote | None = None
        while cursor < end:
            chunk_end = min(cursor + RECONSTRUCTION_CHUNK, end)
            quotes = self.quote_provider.get_quotes(
                trade.symbol,
                cursor,
                chunk_end,
            )
            for quote in quotes:
                if quote.timestamp <= trade.exit_time:
                    continue
                last_quote = quote
                if quote.bid <= trade.stop_price:
                    return self._reconstruction(
                        trade,
                        quote.timestamp,
                        quote.bid,
                        "SIP price path later reached the protective stop.",
                    )
                if quote.bid >= trade.target_price:
                    return self._reconstruction(
                        trade,
                        quote.timestamp,
                        trade.target_price,
                        "SIP price path later reached the target.",
                    )
            cursor = chunk_end + timedelta(microseconds=1)
        if last_quote is None:
            return None
        return self._reconstruction(
            trade,
            last_quote.timestamp,
            last_quote.bid,
            "SIP price path marked at the last quote before the window ended.",
        )

    @staticmethod
    def _reconstruction(
        trade: ShadowTrade,
        exit_time: datetime,
        exit_price: Decimal,
        reason: str,
    ) -> PricePathReconstruction:
        pnl = (exit_price - trade.entry_price) * Decimal(trade.quantity)
        r_multiple = (
            pnl / trade.initial_risk
            if trade.initial_risk > 0
            else Decimal("0")
        )
        return PricePathReconstruction(
            exit_time=exit_time,
            exit_price=exit_price,
            reason=reason,
            realized_pnl=pnl,
            r_multiple=r_multiple,
        )

    @staticmethod
    def _is_price_exit(trade: ShadowTrade) -> bool:
        return trade.exit_reason in {
            "Shadow protective stop reached.",
            "Shadow 2R target reached.",
        }

    @staticmethod
    def _price_tolerance(price: Decimal) -> Decimal:
        return max(MINIMUM_PRICE_TOLERANCE, price * PRICE_TOLERANCE_PERCENT)

    @staticmethod
    def _unresolved(
        trade: ShadowTrade,
        explanation: str,
        *,
        entry_quote: Quote | None = None,
        exit_quote: Quote | None = None,
    ) -> ExecutionAuditRow:
        return ExecutionAuditRow(
            trade_id=trade.trade_id,
            symbol=trade.symbol,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            exit_reason=trade.exit_reason,
            raw_pnl=trade.realized_pnl,
            raw_r_multiple=trade.r_multiple,
            status="unresolved",
            explanation=explanation,
            sip_entry_timestamp=(entry_quote.timestamp if entry_quote else None),
            sip_entry_ask=(entry_quote.ask if entry_quote else None),
            sip_exit_timestamp=(exit_quote.timestamp if exit_quote else None),
            sip_exit_bid=(exit_quote.bid if exit_quote else None),
        )


def save_execution_audit(report: ExecutionAuditReport, path: Path) -> Path:
    ensure_private_file(path)
    payload = {
        **asdict(report),
        "summary": report.summary(),
    }
    path.write_text(
        json.dumps(to_json_safe(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def load_execution_audit(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
