import subprocess
import unittest
from unittest import mock

from repo_sync.commands import CommandError, CommandRunner


class CommandRunnerTest(unittest.TestCase):
    def test_token_in_env_and_no_shell(self):
        runner = CommandRunner(token="tok", base_env={"PATH": "/bin"})
        with mock.patch("repo_sync.commands.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["x"], 0, stdout=None)
            runner(["gh", "x"], cwd="/c")
            _, kwargs = run.call_args
            self.assertEqual(kwargs["env"]["GITHUB_TOKEN"], "tok")
            self.assertEqual(kwargs["env"]["PATH"], "/bin")
            self.assertEqual(kwargs["cwd"], "/c")
            self.assertNotIn("shell", kwargs)
            self.assertIsNone(kwargs["stdout"])
            self.assertEqual(run.call_args[0][0], ["gh", "x"])

    def test_capture_returns_stdout(self):
        runner = CommandRunner(token="tok", base_env={})
        with mock.patch("repo_sync.commands.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["x"], 0, stdout="1\n2\n")
            self.assertEqual(runner(["gh", "x"], capture=True), "1\n2\n")
            self.assertEqual(run.call_args[1]["stdout"], subprocess.PIPE)

    def test_failure_raises_with_command_and_code_without_token(self):
        runner = CommandRunner(token="tok", base_env={})
        with mock.patch("repo_sync.commands.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["x"], 3, stdout=None)
            with self.assertRaises(CommandError) as ctx:
                runner(["git", "push"])
        self.assertEqual(ctx.exception.command, ["git", "push"])
        self.assertEqual(ctx.exception.returncode, 3)
        self.assertNotIn("tok", str(ctx.exception))

    def test_missing_binary(self):
        runner = CommandRunner(base_env={})
        with mock.patch("repo_sync.commands.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(CommandError) as ctx:
                runner(["nope"])
        self.assertEqual(ctx.exception.returncode, 127)

    def test_no_token_leaves_env_alone(self):
        runner = CommandRunner(base_env={"A": "1"})
        self.assertNotIn("GITHUB_TOKEN", runner.env)

    def test_token_falls_back_to_api_token_in_base_env(self):
        runner = CommandRunner(base_env={"API_TOKEN_GITHUB": "from-env"})
        self.assertEqual(runner.env["GITHUB_TOKEN"], "from-env")

    def test_explicit_token_overrides_base_env(self):
        runner = CommandRunner(token="explicit", base_env={"API_TOKEN_GITHUB": "from-env"})
        self.assertEqual(runner.env["GITHUB_TOKEN"], "explicit")

    def test_default_base_env_is_os_environ(self):
        with mock.patch.dict("os.environ", {"API_TOKEN_GITHUB": "proc"}):
            runner = CommandRunner()
        self.assertEqual(runner.env["GITHUB_TOKEN"], "proc")


if __name__ == "__main__":
    unittest.main()
