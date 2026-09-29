"""Write a private readiness report for the fixed re-entry forward checkpoint."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from bot.reentry_forward_checkpoint import (
    DEFAULT_START_DATE,
    DEFAULT_TIMEZONE,
    build_forward_checkpoint_readiness,
    save_forward_checkpoint_readiness,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--replay-report", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START_DATE)
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    args = parser.parse_args()
    report = build_forward_checkpoint_readiness(
        args.ledger,
        audit_path=args.audit,
        replay_report_path=args.replay_report,
        start_date=args.start,
        timezone_name=args.timezone,
    )
    save_forward_checkpoint_readiness(report, args.report)
    checkpoint = report["checkpoint"]
    print(f"Forward checkpoint readiness: {report['status']}")
    print(
        f"Closed trades: {checkpoint['cohort_closed_trades']}/"
        f"{checkpoint['target_closed_trades']} "
        f"({checkpoint['remaining_closed_trades']} remaining)"
    )
    print(f"Private report: {args.report}")


if __name__ == "__main__":
    main()
