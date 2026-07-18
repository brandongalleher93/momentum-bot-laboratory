"""Backtest and paper-trading summary metrics with safe zero handling."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any, Iterable, Sequence

from bot.backtest import BacktestResult, BacktestTrade


def calculate_metrics(trades: Sequence[BacktestTrade]) -> dict[str, Any]:
    winners = [trade for trade in trades if trade.realized_pnl > 0]
    losers = [trade for trade in trades if trade.realized_pnl < 0]
    gross_profit = sum((trade.realized_pnl for trade in winners), Decimal("0"))
    gross_loss = abs(sum((trade.realized_pnl for trade in losers), Decimal("0")))
    total = len(trades)
    cumulative = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for trade in trades:
        cumulative += trade.realized_pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    return {
        "total_trades": total,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": Decimal(len(winners)) / Decimal(total) if total else None,
        "average_win": gross_profit / Decimal(len(winners)) if winners else None,
        "average_loss": gross_loss / Decimal(len(losers)) if losers else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_profit": gross_profit - gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "average_r_multiple": (
            sum((trade.r_multiple for trade in trades), Decimal("0")) / Decimal(total)
            if total
            else None
        ),
        "max_drawdown": max_drawdown,
        "ambiguous_same_bar_count": sum(
            1 for trade in trades if trade.ambiguous_same_bar
        ),
        "exit_reasons": _counts(trade.exit_reason for trade in trades),
    }


def build_backtest_report(result: BacktestResult) -> dict[str, Any]:
    return {
        "symbol": result.symbol,
        "metrics": calculate_metrics(result.trades),
        "setup_rejections": result.setup_rejections,
        "entry_opportunities": result.entry_opportunities,
        "ambiguous_same_bar_count": result.ambiguous_same_bar_count,
        "notes": result.notes,
        "trades": [asdict(trade) for trade in result.trades],
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result
