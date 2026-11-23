"""Read-only assessment of collector heartbeats, not strategy performance."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.shadow_evidence import file_digest


def collection_health(directory: Path, settings: Settings, now: datetime) -> dict[str, Any]:
    if now.utcoffset() is None:
        raise ValueError("Health time must be timezone-aware.")
    local = now.astimezone(ZoneInfo(settings.timezone))
    start = datetime.combine(local.date(), settings.trade_window_start, tzinfo=local.tzinfo)
    end = datetime.combine(local.date(), settings.trade_window_end, tzinfo=local.tzinfo)
    expected = local.weekday() < 5 and start + timedelta(minutes=2) <= local <= end
    try:
        path = directory / "health.json"
        if path.stat().st_size > 65536:
            raise ValueError("Health record exceeds size limit.")
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            raise ValueError("Health record must be an object.")
        updated = datetime.fromisoformat(record["updated_at"])
        if updated.utcoffset() is None or updated > now + timedelta(seconds=30):
            raise ValueError("Invalid heartbeat timestamp.")
        status = record["status"]
        if status not in {"collecting", "degraded", "verifying", "verified", "verification_failed"}:
            raise ValueError("Unknown collector status.")
    except (OSError, KeyError, TypeError, ValueError):
        return {"status": "needs_attention" if expected else "not_verified", "reason": "Missing or invalid collector health record.", "expected_running": expected}
    today = updated.astimezone(local.tzinfo).date() == local.date()
    incomplete_segments = []
    prefix = local.strftime("%Y%m%d")
    for journal in directory.glob(f"{prefix}-*.sqlite3"):
        if journal.name == record.get("journal") and status in {"collecting", "degraded", "verifying"}:
            continue
        try:
            report_path = journal.with_suffix(".verified.json")
            if report_path.stat().st_size > 65536:
                raise ValueError("Verification record exceeds size limit.")
            report = json.loads(report_path.read_text())
            if not isinstance(report, dict):
                raise ValueError("Verification record must be an object.")
            if report.get("status") != "baseline_reproduced" or report.get("journal_sha256") != file_digest(journal):
                raise ValueError("Invalid verification record.")
        except (OSError, TypeError, ValueError):
            incomplete_segments.append(journal.name)
    if incomplete_segments:
        return {"status": "needs_attention", "reason": "One or more of today's evidence segments are incomplete or unverified.", "expected_running": expected, "incomplete_segments": incomplete_segments}

    if status in {"degraded", "verification_failed"}:
        outcome, reason = "needs_attention", "Collection or baseline verification reported errors."
    elif expected and (not today or now - updated > timedelta(minutes=2) or status != "collecting"):
        outcome, reason = "needs_attention", "Expected collection has no recent active heartbeat."
    elif local.weekday() < 5 and local > end + timedelta(minutes=15) and (not today or status != "verified"):
        outcome, reason = "needs_attention", "Today's completed collection has not passed baseline reproduction."
    elif status == "verified":
        outcome, reason = "verified_segment", "The recorded segment reproduced; this does not establish full-session coverage or strategy validity."
    else:
        outcome, reason = "collecting" if expected else "idle_expected", "Heartbeat observed; no strategy conclusion."
    return {"status": outcome, "reason": reason, "expected_running": expected, "collector": record}
