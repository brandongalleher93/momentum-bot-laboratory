"""Command-line entry points for validation, paper connection, and backtesting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.alpaca_adapters import AlpacaMarketData, AlpacaPaperBroker
from bot.app import TradingBot
from bot.backtest import BacktestEngine, load_bars_csv, trade_rows
from bot.config import load_settings, validate_settings
from bot.event_log import to_json_safe, write_csv
from bot.review import build_backtest_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bot",
        description="Paper-only Bull Flag / First Pullback trading MVP.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("validate", help="Validate configuration without connecting.")
    subcommands.add_parser("check", help="Verify the Alpaca paper connection; place no orders.")

    run = subcommands.add_parser("run", help="Run the Alpaca paper bot.")
    run.add_argument(
        "--once", action="store_true", help="Run one polling cycle and exit."
    )

    backtest = subcommands.add_parser(
        "backtest", help="Backtest one pre-screened symbol from OHLCV CSV data."
    )
    backtest.add_argument("csv", type=Path)
    backtest.add_argument("--report", type=Path, default=None)
    backtest.add_argument("--trades", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings()

    if args.command == "validate":
        validate_settings(settings)
        print(json.dumps(settings.snapshot(), indent=2, sort_keys=True))
        print("Configuration is valid. Paper order submission is " + (
            "ARMED." if settings.paper_order_submission_enabled else "DISARMED."
        ))
        return 0

    if args.command == "check":
        validate_settings(settings, require_alpaca_keys=True)
        broker = AlpacaPaperBroker(settings)
        account = broker.get_account()
        print("Connected to Alpaca paper trading.")
        print(f"Account status: {account.status}")
        print(f"Equity: ${account.equity}")
        print(f"Cash: ${account.cash}")
        print(f"Buying power: ${account.buying_power}")
        print(f"Open orders: {len(broker.get_open_orders())}")
        print(f"Open positions: {len(broker.get_positions())}")
        print("No orders were placed.")
        return 0

    if args.command == "run":
        validate_settings(settings, require_alpaca_keys=True)
        broker = AlpacaPaperBroker(settings)
        data = AlpacaMarketData(settings)
        bot = TradingBot(settings, broker, data)
        if args.once:
            bot.run_once()
        else:
            bot.run_forever()
        return 0

    if args.command == "backtest":
        bars = load_bars_csv(args.csv, settings.timezone)
        result = BacktestEngine(settings).run(bars)
        report = build_backtest_report(result)
        report_path = args.report or settings.output_dir / "backtest_summary.json"
        trades_path = args.trades or settings.output_dir / "trades.csv"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(to_json_safe(report), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        rows = trade_rows(result)
        fields = list(rows[0].keys()) if rows else [
            "trade_id",
            "symbol",
            "entry_time",
            "entry_price",
            "quantity",
            "stop_price",
            "target_price",
            "initial_risk",
            "exit_time",
            "exit_price",
            "exit_reason",
            "realized_pnl",
            "r_multiple",
            "ambiguous_same_bar",
            "setup_id",
            "config_version",
            "parameter_profile",
            "decision_register_version",
        ]
        write_csv(trades_path, rows, fields)
        print(f"Backtest complete: {len(result.trades)} trades")
        print(f"Report: {report_path}")
        print(f"Trades: {trades_path}")
        return 0

    return 2
