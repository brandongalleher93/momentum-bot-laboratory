"""Parquet bar cache and Alpaca SIP historical downloader."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Sequence

from bot.config import Settings, validate_settings
from bot.history_store import HistoryStore
from bot.models import Bar


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


class AlpacaHistoricalDownloader:
    def __init__(self, settings: Settings, cache: BarCache):
        validate_settings(settings, require_alpaca_keys=True)
        from alpaca.data.historical import StockHistoricalDataClient
        self.settings, self.cache = settings, cache
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
