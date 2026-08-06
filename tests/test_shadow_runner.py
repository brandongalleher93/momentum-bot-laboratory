import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.shadow_runner import ShadowRunner


class ShadowRunnerTests(unittest.TestCase):
    def test_registered_current_process_is_visible_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ShadowRunner(
                Path(directory),
                "python",
                Path(directory),
            )
            with patch.object(runner, "active_pid", return_value=None), patch(
                "bot.shadow_runner.os.getpid", return_value=4321
            ):
                with runner.register_current_process() as pid:
                    self.assertEqual(pid, 4321)
                    self.assertEqual(
                        runner.pid_path.read_text(encoding="utf-8"),
                        "4321\n",
                    )
                self.assertFalse(runner.pid_path.exists())

    def test_registered_current_process_refuses_duplicate_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ShadowRunner(
                Path(directory),
                "python",
                Path(directory),
            )
            with patch.object(runner, "active_pid", return_value=1234), patch(
                "bot.shadow_runner.os.getpid", return_value=4321
            ):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with runner.register_current_process():
                        self.fail("Duplicate shadow process was allowed.")

    def test_stale_pid_file_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ShadowRunner(
                Path(directory),
                "python",
                Path(directory),
            )
            runner.directory.mkdir(parents=True)
            runner.pid_path.write_text("99999999\n", encoding="utf-8")

            self.assertIsNone(runner.active_pid())
            self.assertFalse(runner.pid_path.exists())

    def test_invalid_pid_is_never_signaled(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = ShadowRunner(
                Path(directory),
                "python",
                Path(directory),
            )
            runner.directory.mkdir(parents=True)
            runner.pid_path.write_text("1\n", encoding="utf-8")

            self.assertFalse(runner.stop())
            self.assertFalse(runner.pid_path.exists())


if __name__ == "__main__":
    unittest.main()
