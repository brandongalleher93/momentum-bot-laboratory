import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.shadow_schedule import (
    LAUNCH_AGENT_LABEL,
    WEEKDAYS,
    install_launch_agent,
    launch_agent_path,
    launch_agent_payload,
    uninstall_launch_agent,
)


class ShadowScheduleTests(unittest.TestCase):
    def test_launch_agent_runs_shadow_on_weekdays_and_at_login(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Trading Bot"
            python = root / ".venv" / "bin" / "python"
            payload = launch_agent_payload(
                project_root=root,
                python_path=python,
            )

        self.assertEqual(payload["Label"], LAUNCH_AGENT_LABEL)
        self.assertEqual(
            payload["ProgramArguments"],
            [str(python.resolve()), "-m", "bot", "shadow"],
        )
        self.assertTrue(payload["RunAtLoad"])
        self.assertEqual(
            payload["StartCalendarInterval"],
            [
                {"Weekday": day, "Hour": 6, "Minute": 0}
                for day in WEEKDAYS
            ],
        )
        self.assertNotIn("KeepAlive", payload)

    @patch("bot.shadow_schedule.platform.system", return_value="Darwin")
    @patch("bot.shadow_schedule.subprocess.run")
    def test_install_writes_and_bootstraps_launch_agent(
        self,
        run,
        _system,
    ):
        run.side_effect = [
            subprocess.CompletedProcess([], 113, "", "not loaded"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            interpreter = root / ".venv" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            home = base / "home"

            installed = install_launch_agent(
                project_root=root,
                home=home,
                uid=501,
            )

            self.assertEqual(installed, launch_agent_path(home))
            with installed.open("rb") as source:
                payload = plistlib.load(source)
            self.assertEqual(payload["Label"], LAUNCH_AGENT_LABEL)
            self.assertEqual(
                payload["ProgramArguments"][0],
                str(interpreter.resolve()),
            )
            self.assertEqual(installed.stat().st_mode & 0o777, 0o600)

        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "launchctl",
                "bootout",
                f"gui/501/{LAUNCH_AGENT_LABEL}",
            ],
        )
        self.assertEqual(
            run.call_args_list[1].args[0][:3],
            ["launchctl", "bootstrap", "gui/501"],
        )

    @patch("bot.shadow_schedule.platform.system", return_value="Darwin")
    @patch("bot.shadow_schedule.subprocess.run")
    def test_uninstall_only_removes_its_launch_agent(self, run, _system):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = launch_agent_path(home)
            path.parent.mkdir(parents=True)
            path.write_bytes(
                plistlib.dumps({"Label": LAUNCH_AGENT_LABEL})
            )

            removed = uninstall_launch_agent(home=home, uid=501)

            self.assertTrue(removed)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
