import os
import unittest
from unittest import mock

from repo_sync.commands import CommandError
from repo_sync.git import GitClient, GitHubClient, MissingTokenError
from tests.helpers import FakeRunner, patch_github_token


class GitClientTest(unittest.TestCase):
    def setUp(self):
        patch_github_token(self)
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

    def test_requires_github_token(self):
        for value in (None, ""):
            with self.subTest(value=value):
                env = {} if value is None else {"GITHUB_TOKEN": value}
                with mock.patch.dict(os.environ, env):
                    if value is None:
                        os.environ.pop("GITHUB_TOKEN")
                    with self.assertRaises(MissingTokenError) as ctx:
                        GitClient(FakeRunner())
                self.assertIn("API_TOKEN_GITHUB", str(ctx.exception))


class GitHubClientTest(unittest.TestCase):
    def setUp(self):
        patch_github_token(self)

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

    def test_requires_github_token(self):
        for value in (None, ""):
            with self.subTest(value=value):
                env = {} if value is None else {"GITHUB_TOKEN": value}
                with mock.patch.dict(os.environ, env):
                    if value is None:
                        os.environ.pop("GITHUB_TOKEN")
                    with self.assertRaises(MissingTokenError) as ctx:
                        GitHubClient(FakeRunner())
                self.assertIn("API_TOKEN_GITHUB", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
