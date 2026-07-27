"""Durable point-in-time history for scanner captures and replay metadata."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from bot.event_log import to_json_safe
from bot.models import DiagnosticResult, MarketSnapshot


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CandidateRecord:
    timestamp: datetime
    symbol: str
    source: str
    rank: int
    passed: bool
    price: Decimal
    percent_gain: Decimal
    rvol: Decimal
    day_volume: int
    bid: Decimal
    ask: Decimal
    reason: str
    config_version: str
    parameter_profile: str


class HistoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scanner_snapshots (
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('captured','reconstructed','imported')),
                    rank INTEGER NOT NULL,
                    price TEXT NOT NULL,
                    percent_gain TEXT NOT NULL,
                    rvol TEXT NOT NULL,
                    day_volume INTEGER NOT NULL,
                    bid TEXT NOT NULL,
                    ask TEXT NOT NULL,
                    spread_percent TEXT NOT NULL,
                    float_shares INTEGER,
                    passed INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    config_version TEXT NOT NULL,
                    parameter_profile TEXT NOT NULL,
                    actual_values_json TEXT NOT NULL,
                    expected_values_json TEXT NOT NULL,
                    PRIMARY KEY(timestamp, symbol, source)
                );
                CREATE INDEX IF NOT EXISTS idx_scanner_time ON scanner_snapshots(timestamp);
                CREATE INDEX IF NOT EXISTS idx_scanner_symbol ON scanner_snapshots(symbol, timestamp);
                CREATE TABLE IF NOT EXISTS data_files (
                    path TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    feed TEXT NOT NULL,
                    adjustment TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    checksum TEXT
                );
                CREATE TABLE IF NOT EXISTS replay_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    feed TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    symbol_count INTEGER NOT NULL,
                    candidate_count INTEGER NOT NULL,
                    trade_count INTEGER NOT NULL,
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    notes_json TEXT NOT NULL
                );
                """
            )
            db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))

    def record_scan(
        self,
        snapshots: Sequence[MarketSnapshot],
        diagnostics: Sequence[DiagnosticResult],
        *,
        source: str,
    ) -> int:
        return self.record_scan_batch(
            [(snapshots, diagnostics)], source=source
        )

    def record_scan_batch(
        self,
        scans: Sequence[
            tuple[Sequence[MarketSnapshot], Sequence[DiagnosticResult]]
        ],
        *,
        source: str,
    ) -> int:
        if source not in {"captured", "reconstructed", "imported"}:
            raise ValueError("Unknown history source.")
        rows = []
        for snapshots, diagnostics in scans:
            by_symbol = {
                value.symbol: value for value in diagnostics if value.symbol
            }
            for rank, snapshot in enumerate(snapshots, start=1):
                diagnostic = by_symbol.get(snapshot.symbol)
                rows.append(
                    (
                        snapshot.timestamp.isoformat(), snapshot.symbol, source, rank,
                        str(snapshot.price), str(snapshot.percent_gain), str(snapshot.rvol),
                        snapshot.day_volume, str(snapshot.bid), str(snapshot.ask),
                        str(snapshot.spread_percent), snapshot.float_shares,
                        int(bool(diagnostic.passed if diagnostic else False)),
                        diagnostic.reason if diagnostic else "No diagnostic was recorded.",
                        diagnostic.config_version if diagnostic else "unknown",
                        diagnostic.parameter_profile if diagnostic else "unknown",
                        json.dumps(to_json_safe(diagnostic.actual_values if diagnostic else {}), sort_keys=True),
                        json.dumps(to_json_safe(diagnostic.expected_values if diagnostic else {}), sort_keys=True),
                    )
                )
        if not rows:
            return 0
        with self.connect() as db:
            db.executemany(
                """INSERT OR REPLACE INTO scanner_snapshots
                (timestamp,symbol,source,rank,price,percent_gain,rvol,day_volume,bid,ask,
                 spread_percent,float_shares,passed,reason,config_version,parameter_profile,
                 actual_values_json,expected_values_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows
            )
        return len(rows)

    def candidates(
        self,
        *,
        source: str | None = None,
        passed_only: bool = True,
        symbols: Iterable[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        clauses, parameters = [], []
        if source:
            clauses.append("source=?"); parameters.append(source)
        if passed_only:
            clauses.append("passed=1")
        normalized_symbols = sorted({value.strip().upper() for value in (symbols or []) if value.strip()})
        if symbols is not None:
            if not normalized_symbols:
                return []
            clauses.append(f"symbol IN ({','.join('?' for _ in normalized_symbols)})")
            parameters.extend(normalized_symbols)
        if start_time is not None:
            clauses.append("datetime(timestamp)>=datetime(?)"); parameters.append(start_time.isoformat())
        if end_time is not None:
            clauses.append("datetime(timestamp)<=datetime(?)"); parameters.append(end_time.isoformat())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM scanner_snapshots" + where + " ORDER BY timestamp, rank", parameters)]

    def record_data_file(self, **values: Any) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO data_files
                (path,symbol,start_time,end_time,feed,adjustment,row_count,created_at,checksum)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (str(values["path"]), values["symbol"], str(values["start_time"]), str(values["end_time"]), values["feed"], values["adjustment"], int(values["row_count"]), str(values["created_at"]), values.get("checksum")),
            )

    def record_replay(self, payload: Mapping[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO replay_runs
                (run_id,created_at,source,feed,start_time,end_time,symbol_count,candidate_count,
                 trade_count,config_json,metrics_json,notes_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["run_id"], payload["created_at"], payload["source"], payload["feed"],
                    payload["start_time"], payload["end_time"], payload["symbol_count"],
                    payload["candidate_count"], payload["trade_count"],
                    json.dumps(to_json_safe(payload["config"]), sort_keys=True),
                    json.dumps(to_json_safe(payload["metrics"]), sort_keys=True),
                    json.dumps(to_json_safe(payload.get("notes", [])), sort_keys=True),
                ),
            )

    def summary(self) -> dict[str, int]:
        with self.connect() as db:
            return {
                "snapshots": db.execute("SELECT COUNT(*) FROM scanner_snapshots").fetchone()[0],
                "captured": db.execute("SELECT COUNT(*) FROM scanner_snapshots WHERE source='captured'").fetchone()[0],
                "reconstructed": db.execute("SELECT COUNT(*) FROM scanner_snapshots WHERE source='reconstructed'").fetchone()[0],
                "data_files": db.execute("SELECT COUNT(*) FROM data_files").fetchone()[0],
                "replay_runs": db.execute("SELECT COUNT(*) FROM replay_runs").fetchone()[0],
            }
