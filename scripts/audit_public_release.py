#!/usr/bin/env python3
"""Fail when the public tree or referenced Git history violates release policy."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_ROOTS = {
    "build",
    "data",
    "dist",
    "Downloads",
    "Logo and Branding",
    "logs",
    "output",
    "private_data",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".docx",
    ".jsonl",
    ".p12",
    ".parquet",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".zip",
}
TEXT_SUFFIXES = {
    "",
    ".command",
    ".csv",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
PUBLIC_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
PUBLIC_IMAGE_ROOTS = {
    ("Docs", "Images"),
    ("assets", "branding"),
}


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _public_paths() -> list[Path]:
    output = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return sorted(
        path
        for value in output.split("\0")
        if value
        for path in [ROOT / value]
        if path.is_file()
    )


def _is_forbidden_path(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    name = relative.name
    if relative.parts[0] in FORBIDDEN_ROOTS:
        return True
    if any(part.endswith(".egg-info") for part in relative.parts):
        return True
    if relative.parts[0] == "Trading Bot Doc Garage":
        return True
    if (
        path.suffix.lower() in PUBLIC_IMAGE_SUFFIXES
        and relative.parts[:2] not in PUBLIC_IMAGE_ROOTS
    ):
        return True
    if (
        relative.parts[0] == "launcher"
        and len(relative.parts) > 1
        and relative.parts[1].endswith(".app")
    ):
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return path.suffix.lower() in FORBIDDEN_SUFFIXES


def _secret_patterns() -> dict[str, re.Pattern[str]]:
    credential_names = "|".join(
        [
            "ALPACA_API_KEY",
            "ALPACA_SECRET_KEY",
            "API_KEY",
            "API_SECRET",
            "PASSWORD",
            "SECRET_KEY",
            "TOKEN",
        ]
    )
    return {
        "Alpaca-style credential": re.compile(r"\b(?:AK|PK)[A-Z0-9]{18,}\b"),
        "non-placeholder credential assignment": re.compile(
            rf"(?m)^[ \t]*(?:{credential_names})[ \t]*=[ \t]*"
            r"(?!your_|example|placeholder|changeme|\s*$)[^\s#]{10,}"
        ),
        "private-key material": re.compile(
            "-----BEGIN " + "(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
        "absolute user path": re.compile(
            "/" + r"(?:Users|home)/[A-Za-z0-9._-]+/"
        ),
    }


def _scan_text(label: str, text: str, errors: list[str]) -> None:
    for description, pattern in _secret_patterns().items():
        if pattern.search(text):
            errors.append(f"{label}: contains {description}")


def _audit_tree(errors: list[str]) -> None:
    paths = _public_paths()
    for path in paths:
        relative = path.relative_to(ROOT)
        if _is_forbidden_path(path):
            errors.append(f"{relative}: private/generated file must not be public")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > 2_000_000:
            errors.append(f"{relative}: text file exceeds the 2 MB audit limit")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        _scan_text(str(relative), text, errors)

    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    for phrase in ("educational", "under active development", "not financial advice"):
        if phrase not in readme:
            errors.append(f"README.md: missing required phrase {phrase!r}")
    for required in (ROOT / "LICENSE", ROOT / "SECURITY.md", ROOT / ".env.example"):
        if not required.is_file():
            errors.append(f"{required.name}: required public-release file is missing")

    config_tree = ast.parse((ROOT / "bot" / "config.py").read_text(encoding="utf-8"))
    defaults: dict[str, object] = {}
    for node in ast.walk(config_tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target, ast.Name
                ):
                    try:
                        defaults[statement.target.id] = ast.literal_eval(statement.value)
                    except (TypeError, ValueError):
                        pass
    expected_defaults = {
        "alpaca_paper": True,
        "allow_live_trading": False,
        "paper_order_submission_enabled": False,
        "gui_show_account_details": False,
    }
    for name, expected in expected_defaults.items():
        if defaults.get(name) is not expected:
            errors.append(f"bot/config.py: unsafe or missing default for {name}")

    adapters_tree = ast.parse(
        (ROOT / "bot" / "alpaca_adapters.py").read_text(encoding="utf-8")
    )
    trading_client_calls = 0
    for node in ast.walk(adapters_tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = getattr(node.func, "id", None) or getattr(
            node.func, "attr", None
        )
        if function_name != "TradingClient":
            continue
        trading_client_calls += 1
        paper_values = [
            keyword.value for keyword in node.keywords if keyword.arg == "paper"
        ]
        if len(paper_values) != 1 or not (
            isinstance(paper_values[0], ast.Constant) and paper_values[0].value is True
        ):
            errors.append(
                "bot/alpaca_adapters.py: TradingClient is not hard-wired "
                "to paper=True"
            )
    if not trading_client_calls:
        errors.append("bot/alpaca_adapters.py: no TradingClient safety check was possible")


def _audit_history(errors: list[str]) -> None:
    names = _git("log", "--all", "--name-only", "--format=")
    for value in sorted(set(names.splitlines())):
        if not value:
            continue
        path = ROOT / value
        if _is_forbidden_path(path):
            errors.append(
                f"Git history: private/generated path was committed: {value}"
            )

    patch_text = _git(
        "log", "--all", "--full-history", "--no-ext-diff", "--no-color", "-p"
    )
    _scan_text("Git history", patch_text, errors)

    emails = set(_git("log", "--all", "--format=%ae%n%ce").splitlines())
    for email in sorted(emails):
        if email and not (
            email == "noreply@github.com"
            or email.endswith("@users.noreply.github.com")
        ):
            errors.append(
                "Git history: non-noreply author or committer email is present"
            )
            break


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--history",
        action="store_true",
        help="also scan all commits reachable from local refs",
    )
    arguments = parser.parse_args()
    errors: list[str] = []
    try:
        _audit_tree(errors)
        if arguments.history:
            _audit_history(errors)
    except (OSError, subprocess.CalledProcessError, SyntaxError) as exc:
        errors.append(f"audit could not complete: {type(exc).__name__}: {exc}")

    if errors:
        print("Public-release audit failed:")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1
    scope = "tree and history" if arguments.history else "working tree"
    print(f"Public-release audit passed for the {scope}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
