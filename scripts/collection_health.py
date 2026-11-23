"""Inspect private collection status without starting the bot or contacting Alpaca."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bot.collection_health import collection_health
from bot.config import load_settings
from bot.shadow_paper import shadow_paper_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    settings = shadow_paper_settings(load_settings())
    directory = args.directory or settings.output_dir / "shadow_evidence"
    result = collection_health(directory, settings, datetime.now(timezone.utc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["status"] in {"needs_attention", "not_verified"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
