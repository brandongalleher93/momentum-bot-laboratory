"""Copy scanner-passing shadow candidates into an isolated research history DB."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path

from bot.history_store import HistoryStore


COLUMNS = (
    "timestamp", "symbol", "source", "rank", "price", "percent_gain",
    "rvol", "day_volume", "bid", "ask", "spread_percent", "float_shares",
    "passed", "reason", "config_version", "parameter_profile",
    "actual_values_json", "expected_values_json",
)


def import_candidates(source_path: Path, destination_path: Path, start: str) -> int:
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("Source and destination histories must differ.")
    wal = Path(str(source_path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Source history has a non-empty WAL; retry when idle.")
    before = hashlib.sha256(source_path.read_bytes()).hexdigest()
    with sqlite3.connect(
        source_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    ) as source:
        source.row_factory = sqlite3.Row
        available = {row[1] for row in source.execute("PRAGMA table_info(scanner_snapshots)")}
        if not set(COLUMNS).issubset(available):
            raise ValueError("Source scanner history has an incompatible schema.")
        names = ", ".join(COLUMNS)
        rows = source.execute(
            f"SELECT {names} FROM scanner_snapshots "
            "WHERE source='captured' AND passed=1 AND timestamp>=? "
            "ORDER BY timestamp, symbol",
            (start,),
        ).fetchall()
    if before != hashlib.sha256(source_path.read_bytes()).hexdigest():
        raise RuntimeError("Source history changed during import.")

    destination = HistoryStore(destination_path)
    placeholders = ", ".join("?" for _ in COLUMNS)
    with destination.connect() as db:
        count_before = db.total_changes
        db.executemany(
            f"INSERT OR IGNORE INTO scanner_snapshots ({names}) "
            f"VALUES ({placeholders})",
            (tuple(row) for row in rows),
        )
        imported = db.total_changes - count_before
    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--start", default="2026-07-27T00:00:00+00:00")
    args = parser.parse_args()
    print(
        f"Imported {import_candidates(args.source, args.destination, args.start)} "
        "new scanner-passing captured rows."
    )


if __name__ == "__main__":
    main()
