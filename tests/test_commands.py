import inspect
import os
import subprocess
import unittest
from unittest import mock

from repo_sync.commands import CommandError, CommandRunner


class CommandRunnerTest(unittest.TestCase):
    def test_takes_no_constructor_arguments(self) -> None:
        self.assertEqual(list(inspect.signature(CommandRunner).parameters), [])

    def test_inherits_process_env_and_no_shell(self) -> None:
        runner = CommandRunner()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}):
            with mock.patch("repo_sync.commands.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(["x"], 0, stdout=None)
                runner(["gh", "x"], cwd="/c")
        _, kwargs = run.call_args
        self.assertNotIn("env", kwargs)
        self.assertEqual(kwargs["cwd"], "/c")
        self.assertNotIn("shell", kwargs)
        self.assertIsNone(kwargs["stdout"])
        self.assertEqual(run.call_args[0][0], ["gh", "x"])

    def test_adds_no_token_handling(self) -> None:
        with mock.patch.dict(os.environ, {"API_TOKEN_GITHUB": "tok"}):
            os.environ.pop("GITHUB_TOKEN", None)
            CommandRunner()
            self.assertNotIn("GITHUB_TOKEN", os.environ)

    def test_capture_returns_stdout(self) -> None:
        runner = CommandRunner()
        with mock.patch("repo_sync.commands.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["x"], 0, stdout="1\n2\n")
            self.assertEqual(runner(["gh", "x"], capture=True), "1\n2\n")
            self.assertEqual(run.call_args[1]["stdout"], subprocess.PIPE)

    def test_failure_raises_with_command_and_code(self) -> None:
        runner = CommandRunner()
        with mock.patch("repo_sync.commands.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["x"], 3, stdout=None)
            with self.assertRaises(CommandError) as ctx:
                runner(["git", "push"])
        self.assertEqual(ctx.exception.command, ["git", "push"])
        self.assertEqual(ctx.exception.returncode, 3)

    def test_missing_binary(self) -> None:
        runner = CommandRunner()
        with mock.patch("repo_sync.commands.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(CommandError) as ctx:
                runner(["nope"])
        self.assertEqual(ctx.exception.returncode, 127)


if __name__ == "__main__":
    unittest.main()
