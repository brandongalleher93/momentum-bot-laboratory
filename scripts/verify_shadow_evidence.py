"""Verify exact baseline reproduction from a completed private shadow journal."""
import argparse
import json
from pathlib import Path

from bot.shadow_evidence import verify_shadow_journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_shadow_journal(args.journal), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
