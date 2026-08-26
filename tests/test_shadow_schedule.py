import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.shadow_schedule import (
    LAUNCH_AGENT_LABEL,
    LEGACY_LAUNCH_AGENT_LABEL,
    WEEKDAYS,
    install_launch_agent,
    legacy_launch_agent_is_loaded,
    legacy_launch_agent_path,
    launch_agent_is_loaded,
    launch_agent_path,
    launch_agent_payload,
    uninstall_launch_agent,
)


class ShadowScheduleTests(unittest.TestCase):
    def test_launch_agent_runs_shadow_on_weekdays_and_at_login(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "Trading Bot"
            home = base / "home"
            payload = launch_agent_payload(
                project_root=root,
                home=home,
            )

        self.assertEqual(payload["Label"], LAUNCH_AGENT_LABEL)
        self.assertEqual(
            payload["ProgramArguments"],
            [
                "/usr/bin/open",
                "-g",
                "-a",
                "Terminal",
                str((root / "launcher/automatic_shadow.command").resolve()),
            ],
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
        self.assertNotIn("WorkingDirectory", payload)
        self.assertEqual(
            payload["StandardOutPath"],
            str(
                home
                / "Library"
                / "Logs"
                / "Trading Bot"
                / "automatic_shadow_launcher.log"
            ),
        )

    @patch("bot.shadow_schedule.platform.system", return_value="Darwin")
    @patch("bot.shadow_schedule.subprocess.run")
    def test_loaded_checks_inspect_current_and_legacy_labels(self, run, _system):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 113, "", "not loaded"),
        ]

        self.assertTrue(launch_agent_is_loaded(uid=501))
        self.assertFalse(legacy_launch_agent_is_loaded(uid=501))

        self.assertEqual(
            run.call_args_list[0].args[0],
            ["launchctl", "print", f"gui/501/{LAUNCH_AGENT_LABEL}"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "launchctl",
                "print",
                f"gui/501/{LEGACY_LAUNCH_AGENT_LABEL}",
            ],
        )

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
            launcher = root / "launcher" / "automatic_shadow.command"
            launcher.parent.mkdir(parents=True)
            launcher.touch(mode=0o700)
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
                payload["ProgramArguments"],
                [
                    "/usr/bin/open",
                    "-g",
                    "-a",
                    "Terminal",
                    str(launcher.resolve()),
                ],
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
    def test_install_replaces_legacy_launch_agent(self, run, _system):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            interpreter = root / ".venv" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            launcher = root / "launcher" / "automatic_shadow.command"
            launcher.parent.mkdir(parents=True)
            launcher.touch(mode=0o700)
            home = base / "home"
            legacy = legacy_launch_agent_path(home)
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(
                plistlib.dumps({"Label": LEGACY_LAUNCH_AGENT_LABEL})
            )

            installed = install_launch_agent(
                project_root=root,
                home=home,
                uid=501,
            )

            self.assertTrue(installed.exists())
            self.assertFalse(legacy.exists())

        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "launchctl",
                "bootout",
                f"gui/501/{LEGACY_LAUNCH_AGENT_LABEL}",
            ],
        )
        self.assertEqual(
            run.call_args_list[2].args[0][:3],
            ["launchctl", "bootstrap", "gui/501"],
        )

    @patch("bot.shadow_schedule.platform.system", return_value="Darwin")
    @patch("bot.shadow_schedule.subprocess.run")
    def test_failed_migration_restores_legacy_launch_agent(self, run, _system):
        run.side_effect = [
            subprocess.CompletedProcess([], 113, "", "not loaded"),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 5, "", "bootstrap failed"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "project"
            interpreter = root / ".venv" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            launcher = root / "launcher" / "automatic_shadow.command"
            launcher.parent.mkdir(parents=True)
            launcher.touch(mode=0o700)
            home = base / "home"
            legacy = legacy_launch_agent_path(home)
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(
                plistlib.dumps({"Label": LEGACY_LAUNCH_AGENT_LABEL})
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "Previous schedule restored",
            ):
                install_launch_agent(
                    project_root=root,
                    home=home,
                    uid=501,
                )

            self.assertTrue(legacy.exists())
            self.assertFalse(launch_agent_path(home).exists())

        self.assertEqual(
            run.call_args_list[3].args[0],
            ["launchctl", "bootstrap", "gui/501", str(legacy)],
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

    @patch("bot.shadow_schedule.platform.system", return_value="Darwin")
    @patch("bot.shadow_schedule.subprocess.run")
    def test_uninstall_removes_current_and_legacy_agents(self, run, _system):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            current = launch_agent_path(home)
            legacy = legacy_launch_agent_path(home)
            current.parent.mkdir(parents=True)
            current.write_bytes(plistlib.dumps({"Label": LAUNCH_AGENT_LABEL}))
            legacy.write_bytes(
                plistlib.dumps({"Label": LEGACY_LAUNCH_AGENT_LABEL})
            )

            removed = uninstall_launch_agent(home=home, uid=501)

            self.assertTrue(removed)
            self.assertFalse(current.exists())
            self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
