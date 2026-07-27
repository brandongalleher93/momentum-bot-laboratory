import tempfile
import unittest
from pathlib import Path

from bot.shadow_runner import ShadowRunner


class ShadowRunnerTests(unittest.TestCase):
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
