"""Write a private exploratory screen from a completed SIP execution audit."""

from __future__ import annotations

import argparse
from pathlib import Path

from bot.reentry_observed_screen import build_observed_screen, save_observed_screen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timezone", default="America/New_York")
    args = parser.parse_args()
    report = build_observed_screen(args.audit, timezone_name=args.timezone)
    save_observed_screen(report, args.report)
    print(f"Exploratory screen: {args.report}")
    for name, summary in report["groups"].items():
        print(
            f"{name}: {summary['trades']} trades, "
            f"estimated SIP-path P/L {summary['estimated_sip_path_net_profit']}"
        )


if __name__ == "__main__":
    main()
