"""Lifecycle helpers for the dashboard-managed shadow-paper process."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path


class ShadowRunner:
    def __init__(self, output_dir: Path, python: str, project_root: Path):
        self.directory = output_dir / "shadow_paper"
        self.pid_path = self.directory / "runner.pid"
        self.log_path = self.directory / "runner.log"
        self.python = python
        self.project_root = project_root

    def active_pid(self) -> int | None:
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            return None
        if self._is_shadow_process(pid):
            return pid
        self.pid_path.unlink(missing_ok=True)
        return None

    def start(self) -> int:
        active = self.active_pid()
        if active is not None:
            return active
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [self.python, "-m", "bot", "shadow"],
                cwd=self.project_root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
        return process.pid

    def stop(self) -> bool:
        pid = self.active_pid()
        if pid is None:
            return False
        os.kill(pid, signal.SIGTERM)
        self.pid_path.unlink(missing_ok=True)
        return True

    @staticmethod
    def _is_shadow_process(pid: int) -> bool:
        if pid <= 1:
            return False
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return False
        check = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
        return (
            check.returncode == 0
            and "-m bot shadow" in check.stdout
        )
