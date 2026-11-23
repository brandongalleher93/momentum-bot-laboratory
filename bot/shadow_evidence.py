"""Private input journals and offline reproduction of shadow decisions.

The journal records normalized adapter responses, never provider credentials or
broker state. Reproduction uses the shadow engine itself rather than a second
implementation of its strategy and portfolio ordering.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version
import tempfile
import zlib
from contextlib import closing
from dataclasses import fields, replace
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator, TYPE_CHECKING, TypeVar
from uuid import uuid4

from bot.config import Settings
from bot.event_log import to_json_safe
from bot.models import Bar, MarketSnapshot, Quote
from bot.security import ensure_private_directory

if TYPE_CHECKING:
    from bot.shadow_paper import ShadowMarketData, ShadowPaperEngine

ModelT = TypeVar("ModelT")

MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_DAILY_BYTES = 512 * 1024 * 1024
MIN_FREE_BYTES = 1024 * 1024 * 1024


class EvidenceCaptureError(RuntimeError):
    """Stop collection when its evidence cannot be durably captured."""


def canonical(value: Any) -> Any:
    value = to_json_safe(value)
    if isinstance(value, dict):
        ignored = {"event_id", "parent_event_id", "trade_id", "setup_id"}
        if "module" in value and "decision_time" in value:
            ignored.add("timestamp")  # Diagnostic creation wall clock is incidental.
        return {k: canonical(v) for k, v in value.items() if k not in ignored}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_fingerprint() -> dict[str, str]:
    return {
        path.name: file_digest(path)
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }


def runtime_fingerprint() -> dict[str, Any]:
    packages = {}
    for name in ("alpaca-py", "python-dotenv", "pandas", "pyarrow"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"python": sys.version.split()[0], "packages": packages}


def _encoded(value: Any) -> bytes:
    return json.dumps(to_json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class Journal:
    def __init__(self, directory: Path, now: datetime):
        ensure_private_directory(directory)
        self.directory = directory
        self.prefix = now.astimezone(timezone.utc).strftime("%Y%m%d")
        self.path = directory / f"{self.prefix}-{uuid4().hex}.sqlite3"
        self.previous = ""
        self.sequence = 0
        with self.path.open("xb"):
            pass
        self.path.chmod(0o600)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE records (sequence INTEGER PRIMARY KEY, previous TEXT NOT NULL, digest TEXT NOT NULL, payload BLOB NOT NULL)")

    def append(self, payload: dict[str, Any]) -> None:
        try:
            raw = _encoded(payload)
            if len(raw) > MAX_RECORD_BYTES:
                raise EvidenceCaptureError("Evidence record exceeds its size budget.")
            compressed = zlib.compress(raw)
            used = sum(p.stat().st_size for p in self.directory.glob(f"{self.prefix}-*.sqlite3"))
            if used + len(compressed) + 65536 > MAX_DAILY_BYTES:
                raise EvidenceCaptureError("Daily evidence storage budget exhausted.")
            if shutil.disk_usage(self.directory).free < MIN_FREE_BYTES + len(compressed):
                raise EvidenceCaptureError("Evidence disk reserve exhausted.")
            digest = hashlib.sha256(self.previous.encode() + raw).hexdigest()
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("INSERT INTO records VALUES (?,?,?,?)", (self.sequence, self.previous, digest, compressed))
            self.previous = digest
            self.sequence += 1
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise EvidenceCaptureError("Cannot persist shadow evidence.") from exc


def read_journal(path: Path) -> Iterator[dict[str, Any]]:
    """Stream bounded, hash-checked records from a finalized journal."""
    if path.stat().st_size > MAX_DAILY_BYTES:
        raise ValueError("Evidence file exceeds budget.")
    previous = ""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        for index, row in enumerate(db.execute("SELECT sequence,previous,digest,payload FROM records ORDER BY sequence")):
            sequence, parent, digest, compressed = row
            decoder = zlib.decompressobj()
            raw = decoder.decompress(compressed, MAX_RECORD_BYTES + 1)
            if len(raw) > MAX_RECORD_BYTES or not decoder.eof or decoder.unused_data:
                raise ValueError("Invalid or oversized evidence record.")
            if sequence != index or parent != previous or hashlib.sha256(previous.encode() + raw).hexdigest() != digest:
                raise ValueError("Evidence integrity check failed.")
            previous = digest
            yield json.loads(raw)


class RecordingMarketData:
    def __init__(self, provider: ShadowMarketData, session: EvidenceSession):
        self.provider, self.session = provider, session

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        record = {"method": method, "args": to_json_safe(args), "kwargs": to_json_safe(kwargs)}
        try:
            result = getattr(self.provider, method)(*args, **kwargs)
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            self.session.add_call(record)
            raise
        record["result"] = to_json_safe(result)
        self.session.add_call(record)
        return result

    def get_scanner_snapshots(self, now: datetime) -> list[MarketSnapshot]:
        return self._call("get_scanner_snapshots", now)

    def get_completed_bars(self, symbol: str, end: datetime, lookback_days: int = 2) -> list[Bar]:
        return self._call("get_completed_bars", symbol, end, lookback_days=lookback_days)

    def get_completed_ten_second_bars(self, symbol: str, end: datetime, lookback_minutes: int = 30) -> list[Bar]:
        return self._call("get_completed_ten_second_bars", symbol, end, lookback_minutes=lookback_minutes)

    def get_latest_quote(self, symbol: str) -> Quote:
        return self._call("get_latest_quote", symbol)


class _DiagnosticTee:
    def __init__(self, original, session):
        self.original, self.session = original, session

    def log(self, result):
        self.session.diagnostics.append(canonical(result))
        self.original.log(result)


class EvidenceSession:
    def __init__(self, directory: Path, *, clock: Callable[[], datetime] | None = None):
        self.wall_clock = clock or (lambda: datetime.now(timezone.utc))
        self.journal = Journal(directory, self.wall_clock())
        self.active = False
        self.calls: list[dict[str, Any]] = []
        self.clocks: list[str] = []
        self.diagnostics: list[dict[str, Any]] = []
        self.failed = False
        self.call_bytes = 0
        self.cycle_count = 0
        self.error_cycles = 0
        self.started_at = self.wall_clock()

    def attach(self, engine: ShadowPaperEngine) -> None:
        self.engine = engine
        original_clock = engine._clock
        def recorded_clock():
            value = original_clock()
            if self.active:
                self.clocks.append(value.isoformat())
            return value
        engine._clock = recorded_clock
        engine.market_data = RecordingMarketData(engine.market_data, self)
        engine.logger = _DiagnosticTee(engine.logger, self)
        self.journal.append({
            "kind": "session", "version": 1, "created_at": self.wall_clock(),
            "source": source_fingerprint(), "runtime": runtime_fingerprint(),
            "settings": engine.settings.snapshot(),
            "initial_trades": engine.store.all_trades(),
        })

    def add_call(self, record: dict[str, Any]) -> None:
        if not self.active:
            raise EvidenceCaptureError("Market input occurred outside an evidence cycle.")
        self.calls.append(record)
        self.call_bytes += len(_encoded(record))
        if len(self.calls) > 1000 or self.call_bytes > MAX_RECORD_BYTES // 2:
            raise EvidenceCaptureError("Cycle input budget exhausted.")

    def begin(self, explicit_now: datetime | None) -> None:
        self.calls, self.clocks, self.diagnostics = [], [], []
        self.call_bytes = 0
        self.active = True
        self.explicit_now = explicit_now
        self.journal.append({"kind": "cycle_started", "timestamp": self.wall_clock()})
        self.write_health("degraded" if self.failed else "collecting")

    def finish(self, result=None, error: Exception | None = None) -> None:
        self.active = False
        had_error = error is not None or any("error_type" in call for call in self.calls)
        self.failed |= had_error
        self.error_cycles += int(had_error)
        self.journal.append({
            "kind": "cycle", "timestamp": self.wall_clock(),
            "explicit_now": self.explicit_now, "calls": self.calls, "clocks": self.clocks,
            "diagnostics": self.diagnostics, "result": canonical(result),
            "trades": canonical(self.engine.store.all_trades()),
            "armed_setups": canonical(self.engine._armed_setups),
            "error_type": type(error).__name__ if error else None,
        })

        self.cycle_count += 1
        self.write_health("degraded" if self.failed else "collecting")

    def write_health(self, status: str, *, verification=None) -> None:
        payload = {
            "status": status, "updated_at": self.wall_clock(),
            "session_started_at": self.started_at,
            "journal": self.journal.path.name, "cycles": self.cycle_count,
            "error_cycles": self.error_cycles,
            "verification": verification,
        }
        if status == "verified" and verification is not None:
            report = self.journal.path.with_suffix(".verified.json")
            with report.open("x", encoding="utf-8") as stream:
                report.chmod(0o600)
                json.dump(verification, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        target = self.journal.directory / "health.json"
        descriptor, temporary = tempfile.mkstemp(prefix=".health-", dir=self.journal.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_encoded(payload))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise EvidenceCaptureError("Cannot persist collector health.") from exc
        finally:
            Path(temporary).unlink(missing_ok=True)

    def close(self) -> None:
        self.write_health("verifying")
        self.journal.append({
            "kind": "session_closed", "timestamp": self.wall_clock(),
            "had_errors": self.failed,
            "open_trades": len(self.engine.store.open_trades()),
        })


def _restore_model(cls: type[ModelT], value: dict[str, Any]) -> ModelT:
    result = dict(value)
    for field in fields(cls):
        if field.name not in result or result[field.name] is None:
            continue
        if field.name in {"timestamp", "entry_time", "exit_time"}:
            result[field.name] = datetime.fromisoformat(result[field.name])
        elif "Decimal" in str(field.type):
            result[field.name] = Decimal(result[field.name])
        elif field.name == "conditions":
            result[field.name] = tuple(result[field.name])
    return cls(**result)


def _restore_settings(values: dict[str, Any], directory: Path) -> Settings:
    defaults = Settings()
    restored = {}
    for field in fields(defaults):
        if field.name in {"alpaca_api_key", "alpaca_secret_key", "log_dir", "output_dir"}:
            continue
        value = values[field.name]
        default = getattr(defaults, field.name)
        if isinstance(default, Decimal) or "Decimal" in str(field.type):
            value = Decimal(value) if value is not None else None
        elif isinstance(default, time):
            value = time.fromisoformat(value)
        restored[field.name] = value
    return replace(defaults, **restored, alpaca_api_key="offline-placeholder", alpaca_secret_key="offline-placeholder", log_dir=directory / "logs", output_dir=directory / "output")


class ReplayMarketData:
    def __init__(self):
        self.calls = []
        self.position = 0

    def _call(self, method, *args, **kwargs):
        if self.position >= len(self.calls):
            raise EvidenceCaptureError("Replay requested an uncaptured market input.")
        record = self.calls[self.position]
        self.position += 1
        if (record["method"], record["args"], record["kwargs"]) != (method, to_json_safe(args), to_json_safe(kwargs)):
            raise EvidenceCaptureError("Replay market call ordering or arguments diverged.")
        if "error_type" in record:
            raise EvidenceCaptureError("Provider-error segments require diagnosis, not qualification.")
        cls = {"get_scanner_snapshots": MarketSnapshot, "get_completed_bars": Bar,
               "get_completed_ten_second_bars": Bar, "get_latest_quote": Quote}[method]
        data = record["result"]
        return _restore_model(cls, data) if cls is Quote else [_restore_model(cls, v) for v in data]

    get_scanner_snapshots = RecordingMarketData.get_scanner_snapshots
    get_completed_bars = RecordingMarketData.get_completed_bars
    get_completed_ten_second_bars = RecordingMarketData.get_completed_ten_second_bars
    get_latest_quote = RecordingMarketData.get_latest_quote


def verify_shadow_journal(path: Path) -> dict[str, Any]:
    """Reproduce a completed segment in a temporary ledger, with no network client."""
    from bot.shadow_paper import ShadowPaperEngine, ShadowTrade
    before = file_digest(path)
    records = iter(read_journal(path))
    header = next(records, None)
    if not header or header.get("kind") != "session" or header.get("version") != 1:
        raise ValueError("Missing or unsupported evidence header.")
    if header.get("runtime") != runtime_fingerprint():
        raise ValueError("Runtime versions differ from the captured instrument.")
    if header["source"] != source_fingerprint():
        raise ValueError("Source differs from the captured instrument.")
    with tempfile.TemporaryDirectory(prefix="shadow-reproduction-") as temporary:
        root = Path(temporary)
        settings = _restore_settings(header["settings"], root)
        provider = ReplayMarketData()
        clocks = iter(())
        def clock():
            try:
                return datetime.fromisoformat(next(clocks))
            except StopIteration as exc:
                raise EvidenceCaptureError("Replay exhausted recorded cycle clocks.") from exc
        engine = ShadowPaperEngine(settings, provider, clock=clock)
        for value in header["initial_trades"]:
            trade = _restore_model(ShadowTrade, value)
            engine.store.record_entry(replace(trade, status="open", exit_time=None, exit_price=None, exit_reason=None, realized_pnl=None, r_multiple=None))
            if trade.status == "closed":
                engine.store.record_exit(trade.trade_id, exit_time=trade.exit_time, exit_price=trade.exit_price, reason=trade.exit_reason)
        cycle_count = 0
        cycle_times = []
        pending = False
        closed = False
        for record in records:
            kind = record.get("kind")
            if closed:
                raise ValueError("Unexpected records after session close.")
            if kind == "cycle_started" and not pending:
                pending = True
                continue
            if kind == "session_closed" and not pending:
                if record["had_errors"] or record["open_trades"]:
                    raise ValueError("Session ended with errors or unresolved open trades.")
                closed = True
                continue
            if kind != "cycle" or not pending:
                raise ValueError("Incomplete or invalid cycle sequence.")
            pending = False
            if record["error_type"] or any("error_type" in c for c in record["calls"]):
                raise ValueError("Provider-error segments cannot qualify for baseline parity.")
            provider.calls, provider.position = record["calls"], 0
            clocks = iter(record["clocks"])
            diagnostics = []
            engine.logger = type("Diagnostics", (), {"log": lambda self, result: diagnostics.append(canonical(result))})()
            explicit = datetime.fromisoformat(record["explicit_now"]) if record["explicit_now"] else None
            result = engine.run_once(explicit)
            if provider.position != len(provider.calls) or next(clocks, None) is not None:
                raise ValueError("Replay did not consume the captured inputs and clocks exactly.")
            actual = {"result": canonical(result), "trades": canonical(engine.store.all_trades()),
                      "armed_setups": canonical(engine._armed_setups), "diagnostics": diagnostics}
            for key, value in actual.items():
                if value != record[key]:
                    raise ValueError(f"Baseline parity failed at cycle {cycle_count + 1}: {key}.")
            if result is not None and "timestamp" in result:
                cycle_times.append(result["timestamp"])
            cycle_count += 1
        if pending or not closed or not cycle_count:
            raise ValueError("Session is incomplete or has no completed cycles.")
        final_closed = engine.store.summary()["closed_trades"]
    if file_digest(path) != before:
        raise ValueError("Evidence changed during verification.")
    from zoneinfo import ZoneInfo
    local_times = [stamp.astimezone(ZoneInfo(settings.timezone)) for stamp in cycle_times]
    first = local_times[0]
    session_start = datetime.combine(first.date(), settings.trade_window_start, tzinfo=first.tzinfo)
    session_end = datetime.combine(first.date(), settings.trade_window_end, tzinfo=first.tzinfo)
    gaps = [(b - a).total_seconds() for a, b in zip(local_times, local_times[1:])]
    complete_window = (
        first.weekday() < 5
        and 0 <= (first - session_start).total_seconds() <= 120
        and local_times[-1] >= session_end
        and local_times[-1].date() == first.date()
        and all(0 <= gap <= 120 for gap in gaps)
    )
    initial_closed = sum(t["status"] == "closed" for t in header["initial_trades"])
    return {"status": "baseline_reproduced", "cycles": cycle_count, "journal_sha256": before,
            "source": header["source"], "settings": header["settings"],
            "session_date": first.date().isoformat(), "complete_window": complete_window,
            "maximum_cycle_gap_seconds": max(gaps, default=0),
            "new_closed_trades": final_closed - initial_closed,
            "strategy_validation": False}
