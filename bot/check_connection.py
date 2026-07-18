"""Backward-compatible connection check: ``python -m bot.check_connection``."""

from bot.cli import main as cli_main


def main() -> int:
    return cli_main(["check"])


if __name__ == "__main__":
    raise SystemExit(main())
