"""Command-line entry points for validation, paper connection, and backtesting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bot.alpaca_adapters import AlpacaMarketData, AlpacaPaperBroker
from bot.app import TradingBot
from bot.backtest import BacktestEngine, load_bars_csv, trade_rows
from bot.config import PROJECT_ROOT, load_settings, validate_settings
from bot.event_log import to_json_safe, write_csv
from bot.review import build_backtest_report
from bot.security import enforce_private_umask, ensure_private_file


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
    shadow = subcommands.add_parser(
        "shadow",
        help=(
            "Forward-test with live market data and local simulated fills; "
            "never submit broker orders."
        ),
    )
    shadow.add_argument(
        "--once", action="store_true", help="Run one shadow cycle and exit."
    )
    shadow_schedule = subcommands.add_parser(
        "shadow-schedule",
        help="Manage automatic weekday shadow observation on macOS.",
    )
    shadow_schedule.add_argument(
        "action",
        choices=("install", "start", "status", "uninstall"),
        help="Install, start, inspect, or remove automatic shadow observation.",
    )
    stop_diagnostic = subcommands.add_parser(
        "confirmed-stop-diagnostic",
        help=(
            "Build a read-only comparison of confirmed and discrepant "
            "protective-stop exits."
        ),
    )
    stop_diagnostic.add_argument("--ledger", type=Path, default=None)
    stop_diagnostic.add_argument("--audit", type=Path, default=None)
    stop_diagnostic.add_argument("--events", type=Path, default=None)
    stop_diagnostic.add_argument("--report", type=Path, default=None)

    backtest = subcommands.add_parser(
        "backtest", help="Backtest one pre-screened symbol from OHLCV CSV data."
    )
    backtest.add_argument("csv", type=Path)
    backtest.add_argument("--report", type=Path, default=None)
    backtest.add_argument("--trades", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    enforce_private_umask()
    args = build_parser().parse_args(argv)

    if args.command == "shadow-schedule":
        from bot.shadow_schedule import (
            install_launch_agent,
            legacy_launch_agent_is_configured,
            legacy_launch_agent_is_loaded,
            legacy_launch_agent_path,
            launch_agent_is_configured,
            launch_agent_is_loaded,
            launch_agent_path,
            uninstall_launch_agent,
        )

        if args.action == "install":
            settings = load_settings()
            validate_settings(settings, require_alpaca_keys=True)
            if settings.paper_order_submission_enabled:
                raise ValueError(
                    "Disarm paper order submission before installing the "
                    "automatic shadow schedule."
                )
            path = install_launch_agent()
            print(f"Automatic weekday shadow schedule installed: {path}")
            print(
                "It starts at 6:00 a.m. Mac local time and also at login; "
                "the shadow engine enforces its 7:00–11:30 a.m. Eastern window."
            )
            return 0
        if args.action == "start":
            settings = load_settings()
            validate_settings(settings, require_alpaca_keys=True)
            if settings.paper_order_submission_enabled:
                raise ValueError(
                    "Disarm paper order submission before starting shadow "
                    "observation."
                )
            from bot.shadow_runner import ShadowRunner

            runner = ShadowRunner(
                settings.output_dir,
                sys.executable,
                PROJECT_ROOT,
            )
            active = runner.active_pid()
            pid = runner.start()
            print(
                f"Shadow observation is already running with PID {pid}."
                if active is not None
                else f"Shadow observation started with PID {pid}."
            )
            return 0
        if args.action == "uninstall":
            removed = uninstall_launch_agent()
            print(
                "Automatic weekday shadow schedule removed."
                if removed
                else "Automatic weekday shadow schedule was not installed."
            )
            return 0

        print(f"LaunchAgent file: {launch_agent_path()}")
        print(
            "Configured: "
            + ("yes" if launch_agent_is_configured() else "no")
        )
        print("Loaded: " + ("yes" if launch_agent_is_loaded() else "no"))
        legacy_configured = legacy_launch_agent_is_configured()
        legacy_loaded = legacy_launch_agent_is_loaded()
        print(f"Legacy LaunchAgent file: {legacy_launch_agent_path()}")
        print("Legacy configured: " + ("yes" if legacy_configured else "no"))
        print("Legacy loaded: " + ("yes" if legacy_loaded else "no"))
        print(
            "Migration required: "
            + ("yes" if legacy_configured or legacy_loaded else "no")
        )
        return 0

    settings = load_settings()

    if args.command == "confirmed-stop-diagnostic":
        from bot.confirmed_stop_diagnostic import (
            build_confirmed_stop_diagnostic,
            save_confirmed_stop_diagnostic,
        )

        shadow_output = settings.output_dir / "shadow_paper"
        ledger_path = args.ledger or shadow_output / "shadow_trades.sqlite3"
        audit_path = args.audit or shadow_output / "execution_audit.json"
        events_path = args.events or shadow_output / "events.jsonl"
        report_path = (
            args.report or shadow_output / "confirmed_stop_diagnostic.json"
        )
        report = build_confirmed_stop_diagnostic(
            ledger_path,
            audit_path,
            events_path,
            timezone_name=settings.timezone,
        )
        save_confirmed_stop_diagnostic(
            report,
            report_path,
            source_paths=(ledger_path, audit_path, events_path),
        )
        counts = report["classification_counts"]
        print(
            "Confirmed-stop diagnostic complete: "
            f"{counts['confirmed']} confirmed, "
            f"{counts['discrepant']} discrepant, "
            f"{counts['unresolved']} unresolved"
        )
        print(f"Report: {report_path}")
        print(
            "Exploratory only: this report does not authorize parameter changes."
        )
        return 0

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
        print(f"Open orders: {len(broker.get_open_orders())}")
        print(f"Open positions: {len(broker.get_positions())}")
        print("Account balances and position details are hidden by default.")
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

    if args.command == "shadow":
        validate_settings(settings, require_alpaca_keys=True)
        from bot.shadow_paper import (
            ShadowPaperEngine,
            shadow_paper_settings,
        )

        settings = shadow_paper_settings(settings)
        data = AlpacaMarketData(settings)
        engine = ShadowPaperEngine(settings, data)
        if args.once:
            print(
                json.dumps(
                    to_json_safe(engine.run_once()),
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            from bot.shadow_runner import ShadowRunner

            runner = ShadowRunner(
                settings.output_dir,
                sys.executable,
                PROJECT_ROOT,
            )
            with runner.register_current_process():
                engine.run_forever()
        return 0

    if args.command == "backtest":
        bars = load_bars_csv(args.csv, settings.timezone)
        result = BacktestEngine(settings).run(bars)
        report = build_backtest_report(result)
        report_path = args.report or settings.output_dir / "backtest_summary.json"
        trades_path = args.trades or settings.output_dir / "trades.csv"
        ensure_private_file(report_path)
        report_path.write_text(
            json.dumps(to_json_safe(report), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report_path.chmod(0o600)
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
