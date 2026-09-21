"""Exploratory first-entry screen from an immutable shadow execution audit."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.event_log import to_json_safe
from bot.security import ensure_private_file


def build_observed_screen(audit_path: Path, *, timezone_name: str) -> dict:
    before = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    rows = audit.get("rows")
    if not isinstance(rows, list) or len(rows) != audit.get("ledger_trade_count"):
        raise ValueError("The execution audit does not cover its recorded ledger count.")
    if any(row.get("status") == "unresolved" for row in rows):
        raise ValueError("The execution audit contains unresolved trades.")
    if len({row.get("trade_id") for row in rows}) != len(rows):
        raise ValueError("The execution audit contains duplicate trade IDs.")

    zone = ZoneInfo(timezone_name)
    seen: Counter[tuple[object, str]] = Counter()
    groups: dict[str, list[dict]] = {"first": [], "repeat": []}
    for row in sorted(rows, key=lambda value: (value["entry_time"], value["trade_id"])):
        entry_time = datetime.fromisoformat(row["entry_time"])
        if entry_time.tzinfo is None:
            raise ValueError("Audit entry timestamps must include a timezone.")
        key = (entry_time.astimezone(zone).date(), row["symbol"])
        seen[key] += 1
        groups["first" if seen[key] == 1 else "repeat"].append(row)

    after = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    if before != after:
        raise RuntimeError("The execution audit changed during the screen.")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "audit_generated_at": audit.get("generated_at"),
        "audit_sha256": before,
        "timezone": timezone_name,
        "groups": {name: _summarize(values) for name, values in groups.items()},
        "all": _summarize(rows),
        "interpretation": (
            "Exploratory observed-entry screen only. Removing repeat entries "
            "from the recorded ledger does not replay replacement opportunities, "
            "portfolio capacity, or changed future decisions. SIP price-path "
            "reconstructions do not reproduce indicator exits."
        ),
    }


def _summarize(rows: list[dict]) -> dict:
    raw = [Decimal(row["raw_pnl"]) for row in rows]
    adjusted = [
        Decimal(row["reconstruction"]["realized_pnl"])
        if row.get("reconstruction") is not None
        else Decimal(row["raw_pnl"])
        for row in rows
    ]
    return {
        "trades": len(rows),
        "confirmed": sum(row["status"] == "confirmed" for row in rows),
        "discrepant": sum(row["status"] == "discrepant" for row in rows),
        "raw_net_profit": str(sum(raw, Decimal(0))),
        "estimated_sip_path_net_profit": str(sum(adjusted, Decimal(0))),
        "estimated_sip_path_average_per_trade": (
            str(sum(adjusted, Decimal(0)) / Decimal(len(rows))) if rows else None
        ),
    }


def save_observed_screen(report: dict, path: Path) -> None:
    ensure_private_file(path)
    path.write_text(json.dumps(to_json_safe(report), indent=2) + "\n", encoding="utf-8")
