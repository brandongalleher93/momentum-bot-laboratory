"""Build a resumable SIP replay workspace for captured shadow candidates."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.config import load_settings
from bot.historical_data import (
    AlpacaHistoricalDownloader,
    BarCache,
    TradeCache,
    save_workspace_manifest,
)
from bot.history_store import HistoryStore
from bot.shadow_paper import shadow_paper_settings


def candidate_dates(
    history_path: Path, start: date, end: date, timezone_name: str
) -> dict[str, set[date]]:
    zone = ZoneInfo(timezone_name)
    result: dict[str, set[date]] = defaultdict(set)
    with sqlite3.connect(
        history_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    ) as db:
        for timestamp, symbol in db.execute(
            "SELECT timestamp, symbol FROM scanner_snapshots "
            "WHERE source='captured' AND passed=1 ORDER BY timestamp, symbol"
        ):
            local_date = datetime.fromisoformat(timestamp).astimezone(zone).date()
            if start <= local_date <= end:
                result[symbol].add(local_date)
    return dict(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--feed", default="sip")
    parser.add_argument("--skip-trades", action="store_true")
    parser.add_argument(
        "--symbols",
        default="",
        help="Optional comma-separated subset for a resumable retry.",
    )
    args = parser.parse_args()

    settings = shadow_paper_settings(load_settings())
    dates_by_symbol = candidate_dates(
        args.history, args.start, args.end, settings.timezone
    )
    requested = {
        value.strip().upper() for value in args.symbols.split(",") if value.strip()
    }
    if requested:
        dates_by_symbol = {
            symbol: dates
            for symbol, dates in dates_by_symbol.items()
            if symbol in requested
        }
    if not dates_by_symbol:
        raise ValueError("No captured passing candidates exist in the date range.")
    store = HistoryStore(args.history)
    cache = BarCache(args.output / "bars", store)
    execution_cache = BarCache(args.output / "bars_10s", store)
    trade_cache = TradeCache(args.output / "trades")
    downloader = AlpacaHistoricalDownloader(
        settings,
        cache,
        trade_cache=trade_cache,
        execution_cache=execution_cache,
    )
    zone = ZoneInfo(settings.timezone)
    minute_paths: list[Path] = []
    trade_paths: list[Path] = []
    execution_paths: list[Path] = []
    failures: list[str] = []
    symbols = sorted(dates_by_symbol)
    for index, symbol in enumerate(symbols, start=1):
        symbol_dates = dates_by_symbol[symbol]
        context_start = datetime.combine(
            min(symbol_dates) - timedelta(days=4), time.min, tzinfo=zone
        ).astimezone(timezone.utc)
        context_end = datetime.combine(
            max(symbol_dates) + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(timezone.utc)
        try:
            minute_paths.extend(
                downloader.download(
                    [symbol], context_start, context_end, feed=args.feed
                )
            )
            if not args.skip_trades:
                symbol_trades = []
                for session_date in sorted(symbol_dates):
                    session_start = datetime.combine(
                        session_date, settings.trade_window_start, tzinfo=zone
                    ).astimezone(timezone.utc)
                    session_end = datetime.combine(
                        session_date, settings.trade_window_end, tzinfo=zone
                    ).astimezone(timezone.utc)
                    paths, _ = downloader.download_ten_second_bars(
                        [symbol], session_start, session_end, feed=args.feed
                    )
                    for path in paths:
                        import pandas as pd

                        frame = pd.read_parquet(path)
                        from bot.historical_data import TradeTick

                        symbol_trades.extend(
                            TradeTick(
                                symbol=symbol,
                                timestamp=row.timestamp.to_pydatetime(),
                                price=Decimal(str(row.price)),
                                size=int(row.size),
                            )
                            for row in frame.itertuples(index=False)
                        )
                if symbol_trades:
                    from bot.historical_data import aggregate_trades_to_bars

                    combined_trade = trade_cache.write(
                        symbol_trades, feed=args.feed
                    )
                    trade_paths.append(combined_trade)
                    combined_bars = aggregate_trades_to_bars(
                        symbol_trades, seconds=10
                    )
                    execution_paths.append(
                        execution_cache.write(
                            combined_bars,
                            feed=args.feed,
                            adjustment="trade_aggregation_10s",
                        )
                    )
        except Exception as exc:
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
        print(f"{index}/{len(symbols)} {symbol}", flush=True)

    manifest_path = args.output / "reentry_workspace.json"
    previous = {}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))

    def merged_paths(name: str, current: list[Path]) -> list[str]:
        values = {
            Path(path).parent.name.upper(): str(path)
            for path in previous.get(name, [])
            if Path(path).exists()
        }
        values.update({path.parent.name.upper(): str(path) for path in current})
        return [values[symbol] for symbol in sorted(values)]

    previous_dates = previous.get("validation_dates_by_symbol", {})
    all_dates = {
        symbol: set(values)
        for symbol, values in previous_dates.items()
    }
    for symbol, dates in dates_by_symbol.items():
        all_dates[symbol] = {value.isoformat() for value in dates}
    all_symbols = sorted(set(previous.get("symbols", [])) | set(dates_by_symbol))
    remaining_failures = [
        failure for failure in previous.get("failures", [])
        if failure.split(":", 1)[0] not in dates_by_symbol
    ] + failures
    merged_minutes = merged_paths("paths", minute_paths)
    merged_trades = merged_paths("trade_paths", trade_paths)
    merged_execution = merged_paths("execution_paths", execution_paths)
    manifest = {
        "experiment": "one_entry_per_symbol_day",
        "feed": args.feed,
        "evaluation_start": args.start.isoformat(),
        "evaluation_end": args.end.isoformat(),
        "paths": merged_minutes,
        "execution_paths": merged_execution,
        "trade_paths": merged_trades,
        "symbols": all_symbols,
        "validation_dates_by_symbol": {
            symbol: sorted(dates) for symbol, dates in all_dates.items()
        },
        "failures": remaining_failures,
        "complete": not remaining_failures and (
            len(merged_minutes) == len(all_symbols)
        ) and (
            args.skip_trades or len(merged_trades) == len(all_symbols)
        ),
    }
    save_workspace_manifest(manifest_path, manifest)
    print(json.dumps({"symbols": len(all_symbols), "failures": len(remaining_failures), "complete": manifest["complete"]}))


if __name__ == "__main__":
    main()
