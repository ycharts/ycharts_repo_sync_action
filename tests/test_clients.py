import subprocess
import unittest
from unittest import mock

from sync import CommandError, CommandRunner, GitClient, GitHubClient
from tests.helpers import FakeRunner


class GitClientTest(unittest.TestCase):
    def setUp(self):
        self.runner = FakeRunner()
        self.git = GitClient(self.runner)

    def test_commands(self):
        self.git.configure_user("e@x.com", "name")
        self.git.clone("https://github.com/o/r.git", "/tmp/c")
        self.git.checkout_new_branch("/tmp/c", "br")
        self.git.stage_all("/tmp/c")
        self.git.commit("/tmp/c", "msg")
        self.git.push("/tmp/c", "br")
        self.assertEqual(
            self.runner.calls,
            [
                {"args": ["git", "config", "--global", "user.email", "e@x.com"], "cwd": None, "capture": False},
                {"args": ["git", "config", "--global", "user.name", "name"], "cwd": None, "capture": False},
                {"args": ["git", "clone", "https://github.com/o/r.git", "/tmp/c"], "cwd": None, "capture": False},
                {"args": ["git", "checkout", "-b", "br"], "cwd": "/tmp/c", "capture": False},
                {"args": ["git", "add", "."], "cwd": "/tmp/c", "capture": False},
                {"args": ["git", "commit", "--message", "msg"], "cwd": "/tmp/c", "capture": False},
                {"args": ["git", "push", "-u", "origin", "HEAD:br"], "cwd": "/tmp/c", "capture": False},
            ],
        )

    def test_staged_changes_detected_on_exit_1(self):
        git = GitClient(FakeRunner(failures={("git", "diff"): 1}))
        self.assertTrue(git.has_staged_changes("/c"))

    def test_no_staged_changes_on_exit_0(self):
        self.assertFalse(self.git.has_staged_changes("/c"))
        self.assertEqual(self.runner.calls[0]["args"], ["git", "diff", "--cached", "--quiet"])
        self.assertEqual(self.runner.calls[0]["cwd"], "/c")

    def test_diff_other_failure_propagates(self):
        git = GitClient(FakeRunner(failures={("git", "diff"): 128}))
        with self.assertRaises(CommandError):
            git.has_staged_changes("/c")


class GitHubClientTest(unittest.TestCase):
    def test_setup(self):
        runner = FakeRunner()
        GitHubClient(runner).setup_git_auth()
        self.assertEqual(runner.commands, [["gh", "auth", "setup-git"]])

    def test_list_prs_command_and_parsing(self):
        cases = {"": [], "7\n": [7], "3\n12\n5\n": [3, 12, 5]}
        for output, expected in cases.items():
            runner = FakeRunner(outputs={("gh", "pr", "list"): output})
            with self.subTest(output=output):
                self.assertEqual(GitHubClient(runner).list_open_prs("/c", "pre"), expected)
                self.assertEqual(
                    runner.calls[0],
                    {
                        "args": [
                            "gh", "pr", "list",
                            "--search", "is:pr is:open pre in:title",
                            "--json", "number",
                            "--jq", ".[].number",
                        ],
                        "cwd": "/c",
                        "capture": True,
                    },
                )

    def test_close_create_label(self):
        runner = FakeRunner()
        gh = GitHubClient(runner)
        gh.close_pr("/c", 4)
        gh.create_pr("/c", "t", "t", "main", "t")
        gh.create_pr("/c", "t", "t", "main", "t", "a,b")
        gh.add_label("/c", "t", "bug")
        self.assertEqual(
            runner.commands,
            [
                ["gh", "pr", "close", "4"],
                ["gh", "pr", "create", "--title", "t", "--body", "t", "--base", "main", "--head", "t"],
                ["gh", "pr", "create", "--title", "t", "--body", "t", "--base", "main", "--head", "t", "-r", "a,b"],
                ["gh", "pr", "edit", "t", "--add-label", "bug"],
            ],
        )
        self.assertTrue(all(call["cwd"] == "/c" for call in runner.calls))


class CommandRunnerTest(unittest.TestCase):
    def test_token_in_env_and_no_shell(self):
        runner = CommandRunner(token="tok", base_env={"PATH": "/bin"})
        with mock.patch("sync.subprocess.run") as run:
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
        with mock.patch("sync.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["x"], 0, stdout="1\n2\n")
            self.assertEqual(runner(["gh", "x"], capture=True), "1\n2\n")
            self.assertEqual(run.call_args[1]["stdout"], subprocess.PIPE)

    def test_failure_raises_with_command_and_code_without_token(self):
        runner = CommandRunner(token="tok", base_env={})
        with mock.patch("sync.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["x"], 3, stdout=None)
            with self.assertRaises(CommandError) as ctx:
                runner(["git", "push"])
        self.assertEqual(ctx.exception.command, ["git", "push"])
        self.assertEqual(ctx.exception.returncode, 3)
        self.assertNotIn("tok", str(ctx.exception))

    def test_missing_binary(self):
        runner = CommandRunner(base_env={})
        with mock.patch("sync.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(CommandError) as ctx:
                runner(["nope"])
        self.assertEqual(ctx.exception.returncode, 127)

    def test_no_token_leaves_env_alone(self):
        runner = CommandRunner(base_env={"A": "1"})
        self.assertNotIn("GITHUB_TOKEN", runner.env)


if __name__ == "__main__":
    unittest.main()
