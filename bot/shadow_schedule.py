"""Install and inspect the macOS weekday shadow-observation schedule."""

from __future__ import annotations

import os
import platform
import plistlib
import subprocess
import tempfile
from pathlib import Path

from bot.config import PROJECT_ROOT


LAUNCH_AGENT_LABEL = "app.momentumbot.shadow"
LAUNCH_AGENT_FILENAME = f"{LAUNCH_AGENT_LABEL}.plist"
LEGACY_LAUNCH_AGENT_LABEL = "local.brandon.tradingbot.shadow"
LEGACY_LAUNCH_AGENT_FILENAME = f"{LEGACY_LAUNCH_AGENT_LABEL}.plist"
AUTOMATIC_START_LOCAL_HOUR = 6
AUTOMATIC_START_LOCAL_MINUTE = 0
WEEKDAYS = tuple(range(1, 6))
AUTOMATIC_LAUNCHER_RELATIVE_PATH = Path("launcher/automatic_shadow.command")


def launch_agent_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / "Library" / "LaunchAgents" / LAUNCH_AGENT_FILENAME


def legacy_launch_agent_path(home: Path | None = None) -> Path:
    base = home or Path.home()
    return base / "Library" / "LaunchAgents" / LEGACY_LAUNCH_AGENT_FILENAME


def launch_agent_payload(
    *,
    project_root: Path = PROJECT_ROOT,
    home: Path | None = None,
) -> dict:
    root = project_root.resolve()
    launcher = (root / AUTOMATIC_LAUNCHER_RELATIVE_PATH).resolve()
    log_path = (home or Path.home()) / "Library" / "Logs" / "Trading Bot"
    launch_log = log_path / "automatic_shadow_launcher.log"
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            "/usr/bin/open",
            "-g",
            "-a",
            "Terminal",
            str(launcher),
        ],
        "RunAtLoad": True,
        "StartCalendarInterval": [
            {
                "Weekday": weekday,
                "Hour": AUTOMATIC_START_LOCAL_HOUR,
                "Minute": AUTOMATIC_START_LOCAL_MINUTE,
            }
            for weekday in WEEKDAYS
        ],
        "ProcessType": "Standard",
        "StandardOutPath": str(launch_log),
        "StandardErrorPath": str(launch_log),
        "Umask": 0o077,
        "AssociatedBundleIdentifiers": ["app.momentumbot"],
    }


def launch_agent_is_configured(home: Path | None = None) -> bool:
    return launch_agent_path(home).is_file()


def legacy_launch_agent_is_configured(home: Path | None = None) -> bool:
    return legacy_launch_agent_path(home).is_file()


def launch_agent_is_loaded(*, uid: int | None = None) -> bool:
    return _launch_agent_is_loaded(LAUNCH_AGENT_LABEL, uid=uid)


def legacy_launch_agent_is_loaded(*, uid: int | None = None) -> bool:
    return _launch_agent_is_loaded(LEGACY_LAUNCH_AGENT_LABEL, uid=uid)


def _launch_agent_is_loaded(label: str, *, uid: int | None = None) -> bool:
    if platform.system() != "Darwin":
        return False
    domain = f"gui/{uid if uid is not None else os.getuid()}"
    completed = subprocess.run(
        ["launchctl", "print", f"{domain}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def install_launch_agent(
    *,
    project_root: Path = PROJECT_ROOT,
    home: Path | None = None,
    uid: int | None = None,
) -> Path:
    """Write and load the current user's automatic shadow LaunchAgent."""

    _require_macos()
    root = project_root.resolve()
    interpreter = (root / ".venv" / "bin" / "python").resolve()
    if not interpreter.is_file():
        raise FileNotFoundError(
            f"Project interpreter not found at {interpreter}."
        )
    launcher = root / AUTOMATIC_LAUNCHER_RELATIVE_PATH
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise FileNotFoundError(
            f"Executable automatic shadow launcher not found at {launcher}."
        )

    destination = launch_agent_path(home)
    legacy_destination = legacy_launch_agent_path(home)
    current_payload = _load_managed_agent(
        destination,
        expected_label=LAUNCH_AGENT_LABEL,
    )
    legacy_payload = _load_managed_agent(
        legacy_destination,
        expected_label=LEGACY_LAUNCH_AGENT_LABEL,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    log_directory = (home or Path.home()) / "Library" / "Logs" / "Trading Bot"
    log_directory.mkdir(parents=True, exist_ok=True)

    domain = f"gui/{uid if uid is not None else os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if legacy_payload is not None:
        subprocess.run(
            [
                "launchctl",
                "bootout",
                f"{domain}/{LEGACY_LAUNCH_AGENT_LABEL}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    _write_plist_atomically(
        destination,
        launch_agent_payload(
            project_root=root,
            home=home,
        ),
    )
    completed = subprocess.run(
        ["launchctl", "bootstrap", domain, str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        rollback_detail = _restore_previous_agent(
            domain=domain,
            destination=destination,
            current_payload=current_payload,
            legacy_destination=legacy_destination,
            legacy_payload=legacy_payload,
        )
        raise RuntimeError(
            "launchctl could not load the automatic shadow schedule"
            + (f": {detail}" if detail else ".")
            + rollback_detail
        )
    if legacy_payload is not None:
        legacy_destination.unlink(missing_ok=True)
    return destination


def uninstall_launch_agent(
    *,
    home: Path | None = None,
    uid: int | None = None,
) -> bool:
    """Unload and remove only this project's LaunchAgent configuration."""

    _require_macos()
    destinations = (
        (LAUNCH_AGENT_LABEL, launch_agent_path(home)),
        (LEGACY_LAUNCH_AGENT_LABEL, legacy_launch_agent_path(home)),
    )
    managed = [
        (label, destination)
        for label, destination in destinations
        if _load_managed_agent(
            destination,
            expected_label=label,
        )
        is not None
    ]
    domain = f"gui/{uid if uid is not None else os.getuid()}"
    removed = bool(managed)
    for label, _destination in destinations:
        completed = subprocess.run(
            ["launchctl", "bootout", f"{domain}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )
        removed = removed or completed.returncode == 0
    for _label, destination in managed:
        destination.unlink()
    return removed


def _require_macos() -> None:
    if platform.system() != "Darwin":
        raise RuntimeError("Automatic shadow scheduling is supported on macOS.")


def _load_managed_agent(
    path: Path,
    *,
    expected_label: str,
) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as source:
            existing = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise RuntimeError(
            f"Refusing to replace unreadable LaunchAgent file {path}."
        ) from exc
    if existing.get("Label") != expected_label:
        raise RuntimeError(
            f"Refusing to replace unrelated LaunchAgent file {path}."
        )
    return existing


def _restore_previous_agent(
    *,
    domain: str,
    destination: Path,
    current_payload: dict | None,
    legacy_destination: Path,
    legacy_payload: dict | None,
) -> str:
    rollback_path: Path | None = None
    if current_payload is not None:
        _write_plist_atomically(destination, current_payload)
        rollback_path = destination
    else:
        destination.unlink(missing_ok=True)
        if legacy_payload is not None:
            rollback_path = legacy_destination
    if rollback_path is None:
        return ""
    rollback = subprocess.run(
        ["launchctl", "bootstrap", domain, str(rollback_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if rollback.returncode == 0:
        return " Previous schedule restored."
    detail = (rollback.stderr or rollback.stdout).strip()
    return " Previous schedule could not be restored" + (
        f": {detail}." if detail else "."
    )


def _write_plist_atomically(path: Path, payload: dict) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            plistlib.dump(payload, temporary, fmt=plistlib.FMT_XML)
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
