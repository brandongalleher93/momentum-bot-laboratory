"""Parquet caches and Alpaca historical market-data downloaders."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence

from bot.config import Settings, validate_settings
from bot.history_store import HistoryStore
from bot.models import Bar


@dataclass(frozen=True)
class CachedBarFile:
    path: Path
    feed: str
    symbol: str
    modified_at: datetime


@dataclass(frozen=True)
class TradeTick:
    symbol: str
    timestamp: datetime
    price: Decimal
    size: int

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Trade timestamps must be timezone-aware.")
        if self.price <= 0:
            raise ValueError("Trade prices must be positive.")
        if self.size <= 0:
            raise ValueError("Trade sizes must be positive.")


@dataclass(frozen=True)
class CachedTradeFile:
    path: Path
    feed: str
    symbol: str
    modified_at: datetime


class TradeTape:
    """File-backed trade columns with ten-second window lookup."""

    def __init__(self, symbol: str, path: Path) -> None:
        import pyarrow.parquet as parquet

        self.symbol = symbol.upper()
        self._parquet = parquet.ParquetFile(path, memory_map=True)
        timestamp_column = self._parquet.schema_arrow.names.index("timestamp")
        self._row_group_bounds: list[tuple[datetime, datetime]] = []
        for index in range(self._parquet.num_row_groups):
            statistics = (
                self._parquet.metadata.row_group(index)
                .column(timestamp_column)
                .statistics
            )
            if statistics is None or not statistics.has_min_max:
                raise ValueError("Trade parquet timestamp statistics are required.")
            self._row_group_bounds.append((statistics.min, statistics.max))
        self._loaded_group: int | None = None
        self._frame = None

    def _load_group(self, index: int):
        if self._loaded_group != index:
            self._frame = None
            self._release_arrow_memory()
            self._frame = self._parquet.read_row_group(
                index, columns=["timestamp", "price", "size"]
            ).to_pandas()
            if not self._frame["timestamp"].is_monotonic_increasing:
                self._frame = self._frame.sort_values(
                    "timestamp", kind="stable"
                ).reset_index(drop=True)
            self._loaded_group = index
        return self._frame

    def trades_for_bar(self, bar: Bar) -> list[TradeTick]:
        import pandas as pd

        if bar.symbol != self.symbol:
            raise ValueError("Trade tape and execution bar symbols must match.")
        start = pd.Timestamp(bar.timestamp - timedelta(seconds=10))
        end = pd.Timestamp(bar.timestamp)
        values: list[TradeTick] = []
        for index, (minimum, maximum) in enumerate(self._row_group_bounds):
            if pd.Timestamp(maximum) < start or pd.Timestamp(minimum) >= end:
                continue
            frame = self._load_group(index)
            timestamps = frame["timestamp"]
            left = int(timestamps.searchsorted(start, side="left"))
            right = int(timestamps.searchsorted(end, side="left"))
            values.extend(
                TradeTick(
                    symbol=self.symbol,
                    timestamp=row.timestamp.to_pydatetime(),
                    price=Decimal(str(row.price)),
                    size=int(row.size),
                )
                for row in frame.iloc[left:right].itertuples(index=False)
            )
        return values

    def close(self) -> None:
        self._frame = None
        self._parquet = None
        self._release_arrow_memory()

    @staticmethod
    def _release_arrow_memory() -> None:
        import gc
        import pyarrow

        gc.collect()
        pyarrow.default_memory_pool().release_unused()


class BarCache:
    def __init__(self, root: Path, store: HistoryStore):
        self.root = root
        self.store = store
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str, start: datetime, end: datetime, feed: str) -> Path:
        safe = symbol.upper().replace("/", "-")
        return self.root / feed.lower() / safe / f"{start:%Y%m%d}_{end:%Y%m%d}.parquet"

    def write(self, bars: Sequence[Bar], *, feed: str, adjustment: str = "raw") -> Path:
        if not bars:
            raise ValueError("Cannot cache an empty bar collection.")
        import pandas as pd
        start, end, symbol = bars[0].timestamp, bars[-1].timestamp, bars[0].symbol
        path = self.path_for(symbol, start, end, feed)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame([{"timestamp": b.timestamp, "symbol": b.symbol, "open": float(b.open), "high": float(b.high), "low": float(b.low), "close": float(b.close), "volume": b.volume} for b in bars])
        frame.to_parquet(path, index=False)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        self.store.record_data_file(path=path, symbol=symbol, start_time=start.isoformat(), end_time=end.isoformat(), feed=feed, adjustment=adjustment, row_count=len(frame), created_at=datetime.now(timezone.utc).isoformat(), checksum=checksum)
        return path

    @staticmethod
    def read(path: Path) -> list[Bar]:
        import pandas as pd
        frame = pd.read_parquet(path)
        return [Bar(symbol=str(row.symbol), timestamp=row.timestamp.to_pydatetime(), open=Decimal(str(row.open)), high=Decimal(str(row.high)), low=Decimal(str(row.low)), close=Decimal(str(row.close)), volume=int(row.volume)) for row in frame.itertuples(index=False)]

    def discover(self, *, feed: str | None = None) -> list[CachedBarFile]:
        files: list[CachedBarFile] = []
        feeds = [self.root / feed.lower()] if feed else [path for path in self.root.iterdir() if path.is_dir()]
        for feed_path in feeds:
            if not feed_path.exists():
                continue
            for path in feed_path.glob("*/*.parquet"):
                files.append(
                    CachedBarFile(
                        path=path,
                        feed=feed_path.name,
                        symbol=path.parent.name.upper(),
                        modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
                    )
                )
        return sorted(files, key=lambda value: (value.feed, value.symbol, value.modified_at), reverse=True)

    def latest_paths(self, *, feed: str, symbols: Iterable[str]) -> dict[str, Path]:
        requested = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        latest: dict[str, CachedBarFile] = {}
        for cached in self.discover(feed=feed):
            if cached.symbol not in requested:
                continue
            if cached.symbol not in latest or cached.modified_at > latest[cached.symbol].modified_at:
                latest[cached.symbol] = cached
        return {symbol: cached.path for symbol, cached in latest.items()}


class TradeCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str, start: datetime, end: datetime, feed: str) -> Path:
        safe = symbol.upper().replace("/", "-")
        return self.root / feed.lower() / safe / f"{start:%Y%m%d}_{end:%Y%m%d}.parquet"

    def write(self, trades: Sequence[TradeTick], *, feed: str) -> Path:
        if not trades:
            raise ValueError("Cannot cache an empty trade collection.")
        import pandas as pd

        start, end, symbol = trades[0].timestamp, trades[-1].timestamp, trades[0].symbol
        path = self.path_for(symbol, start, end, feed)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(
            [
                {
                    "timestamp": trade.timestamp,
                    "symbol": trade.symbol,
                    "price": float(trade.price),
                    "size": trade.size,
                }
                for trade in trades
            ]
        )
        frame.to_parquet(path, index=False)
        return path

    @staticmethod
    def read(path: Path) -> list[TradeTick]:
        import pandas as pd

        frame = pd.read_parquet(path)
        return [
            TradeTick(
                symbol=str(row.symbol),
                timestamp=row.timestamp.to_pydatetime(),
                price=Decimal(str(row.price)),
                size=int(row.size),
            )
            for row in frame.itertuples(index=False)
        ]

    @staticmethod
    def read_tape(path: Path) -> TradeTape:
        return TradeTape(path.parent.name.upper(), path)

    def discover(self, *, feed: str | None = None) -> list[CachedTradeFile]:
        files: list[CachedTradeFile] = []
        feeds = (
            [self.root / feed.lower()]
            if feed
            else [path for path in self.root.iterdir() if path.is_dir()]
        )
        for feed_path in feeds:
            if not feed_path.exists():
                continue
            for path in feed_path.glob("*/*.parquet"):
                files.append(
                    CachedTradeFile(
                        path=path,
                        feed=feed_path.name,
                        symbol=path.parent.name.upper(),
                        modified_at=datetime.fromtimestamp(
                            path.stat().st_mtime, tz=timezone.utc
                        ),
                    )
                )
        return sorted(
            files,
            key=lambda value: (value.feed, value.symbol, value.modified_at),
            reverse=True,
        )

    def latest_paths(self, *, feed: str, symbols: Iterable[str]) -> dict[str, Path]:
        requested = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        latest: dict[str, CachedTradeFile] = {}
        for cached in self.discover(feed=feed):
            if cached.symbol not in requested:
                continue
            if cached.symbol not in latest or cached.modified_at > latest[cached.symbol].modified_at:
                latest[cached.symbol] = cached
        return {symbol: cached.path for symbol, cached in latest.items()}


def aggregate_trades_to_bars(
    trades: Sequence[TradeTick], *, seconds: int = 10
) -> list[Bar]:
    """Aggregate chronological trades into completed, end-stamped OHLCV bars."""

    if seconds <= 0 or 60 % seconds != 0:
        raise ValueError("Execution-bar length must be a positive divisor of 60 seconds.")
    if not trades:
        return []
    symbols = {trade.symbol for trade in trades}
    if len(symbols) != 1:
        raise ValueError("Aggregate one symbol at a time.")
    ordered = sorted(trades, key=lambda trade: trade.timestamp)
    buckets: dict[datetime, list[TradeTick]] = {}
    for trade in ordered:
        bucket_start = trade.timestamp.replace(
            second=(trade.timestamp.second // seconds) * seconds,
            microsecond=0,
        )
        buckets.setdefault(bucket_start, []).append(trade)
    bars: list[Bar] = []
    for bucket_start, values in sorted(buckets.items()):
        prices = [trade.price for trade in values]
        bars.append(
            Bar(
                symbol=values[0].symbol,
                timestamp=bucket_start + timedelta(seconds=seconds),
                open=prices[0],
                high=max(prices),
                low=min(prices),
                close=prices[-1],
                volume=sum(trade.size for trade in values),
            )
        )
    return bars


def save_workspace_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def load_workspace_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class AlpacaHistoricalDownloader:
    def __init__(
        self,
        settings: Settings,
        cache: BarCache,
        *,
        trade_cache: TradeCache | None = None,
        execution_cache: BarCache | None = None,
    ):
        validate_settings(settings, require_alpaca_keys=True)
        from alpaca.data.historical import StockHistoricalDataClient
        self.settings, self.cache = settings, cache
        self.trade_cache = trade_cache
        self.execution_cache = execution_cache
        self.client = StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)

    def download(self, symbols: Sequence[str], start: datetime, end: datetime, *, feed: str = "sip", adjustment: str = "raw") -> list[Path]:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        if end <= start: raise ValueError("Historical end must be after start.")
        paths: list[Path] = []
        for symbol in sorted({s.strip().upper() for s in symbols if s.strip()}):
            request = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, start=start, end=end, feed=getattr(DataFeed, feed.upper()), adjustment=getattr(Adjustment, adjustment.upper()))
            response = self.client.get_stock_bars(request)
            values = response.data.get(symbol, [])
            bars = [Bar(symbol=symbol, timestamp=v.timestamp + timedelta(minutes=1), open=Decimal(str(v.open)), high=Decimal(str(v.high)), low=Decimal(str(v.low)), close=Decimal(str(v.close)), volume=int(v.volume)) for v in values]
            if bars: paths.append(self.cache.write(bars, feed=feed, adjustment=adjustment))
        return paths

    def download_ten_second_bars(
        self,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        *,
        feed: str = "sip",
    ) -> tuple[list[Path], list[Path]]:
        """Download trades, cache them, and derive completed ten-second bars."""

        if self.trade_cache is None or self.execution_cache is None:
            raise ValueError("Trade and execution-bar caches are required.")
        if end <= start:
            raise ValueError("Historical end must be after start.")
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockTradesRequest

        trade_paths: list[Path] = []
        bar_paths: list[Path] = []
        for symbol in sorted({s.strip().upper() for s in symbols if s.strip()}):
            request = StockTradesRequest(
                symbol_or_symbols=symbol,
                start=start,
                end=end,
                feed=getattr(DataFeed, feed.upper()),
            )
            response = self.client.get_stock_trades(request)
            values = response.data.get(symbol, [])
            trades = [
                TradeTick(
                    symbol=symbol,
                    timestamp=value.timestamp,
                    price=Decimal(str(value.price)),
                    size=int(value.size),
                )
                for value in values
                if int(value.size) > 0
            ]
            if not trades:
                continue
            trade_paths.append(self.trade_cache.write(trades, feed=feed))
            bars = aggregate_trades_to_bars(trades, seconds=10)
            if bars:
                bar_paths.append(
                    self.execution_cache.write(
                        bars, feed=feed, adjustment="trade_aggregation_10s"
                    )
                )
        return trade_paths, bar_paths
