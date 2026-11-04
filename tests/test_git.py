import os
import unittest
from pathlib import Path
from unittest import mock

from repo_sync.commands import CommandError
from repo_sync.git import GitClient, GitHubClient, MissingTokenError, PullRequest
from tests.helpers import FakeRunner, patch_github_token


class GitClientTest(unittest.TestCase):
    def setUp(self):
        patch_github_token(self)
        self.runner = FakeRunner()
        self.git = GitClient("/tmp/c", self.runner)

    def test_commands(self):
        self.git.configure_user("e@x.com", "name")
        self.git.clone("https://github.com/o/r.git")
        self.git.checkout_new_branch("br")
        self.git.stage_all()
        self.git.commit("msg")
        self.git.push("br")
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
        git = GitClient("/c", FakeRunner(failures={("git", "diff"): 1}))
        self.assertTrue(git.has_staged_changes())

    def test_no_staged_changes_on_exit_0(self):
        self.assertFalse(self.git.has_staged_changes())
        self.assertEqual(self.runner.calls[0]["args"], ["git", "diff", "--cached", "--quiet"])
        self.assertEqual(self.runner.calls[0]["cwd"], "/tmp/c")

    def test_diff_other_failure_propagates(self):
        git = GitClient("/c", FakeRunner(failures={("git", "diff"): 128}))
        with self.assertRaises(CommandError):
            git.has_staged_changes()

    def test_requires_github_token(self):
        for value in (None, ""):
            with self.subTest(value=value):
                env = {} if value is None else {"GITHUB_TOKEN": value}
                with mock.patch.dict(os.environ, env):
                    if value is None:
                        os.environ.pop("GITHUB_TOKEN")
                    with self.assertRaises(MissingTokenError) as ctx:
                        GitClient("/c", FakeRunner())
                self.assertIn("API_TOKEN_GITHUB", str(ctx.exception))


class GitHubClientTest(unittest.TestCase):
    def setUp(self):
        patch_github_token(self)

    def test_setup(self):
        runner = FakeRunner()
        GitHubClient("/c", runner).setup_git_auth()
        self.assertEqual(runner.commands, [["gh", "auth", "setup-git"]])

    def test_list_prs_command_and_parsing(self):
        cases = {"": [], "7\n": [7], "3\n12\n5\n": [3, 12, 5]}
        for output, expected in cases.items():
            runner = FakeRunner(outputs={("gh", "pr", "list"): output})
            with self.subTest(output=output):
                self.assertEqual(GitHubClient("/c", runner).list_open_prs("pre"), expected)
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

    def test_close_pr(self):
        runner = FakeRunner()
        GitHubClient("/c", runner).close_pr(4)
        self.assertEqual(runner.calls, [{"args": ["gh", "pr", "close", "4"], "cwd": "/c", "capture": False}])

    def test_cwd_accepts_path(self):
        runner = FakeRunner()
        GitClient(Path("/c"), runner).stage_all()
        GitHubClient(Path("/c"), runner).close_pr(1)
        self.assertEqual([call["cwd"] for call in runner.calls], ["/c", "/c"])

    def test_requires_github_token(self):
        for value in (None, ""):
            with self.subTest(value=value):
                env = {} if value is None else {"GITHUB_TOKEN": value}
                with mock.patch.dict(os.environ, env):
                    if value is None:
                        os.environ.pop("GITHUB_TOKEN")
                    with self.assertRaises(MissingTokenError) as ctx:
                        GitHubClient("/c", FakeRunner())
                self.assertIn("API_TOKEN_GITHUB", str(ctx.exception))


class PullRequestTest(unittest.TestCase):
    def setUp(self):
        patch_github_token(self)
        self.runner = FakeRunner()
        self.github = GitHubClient("/c", self.runner)

    def pull_request(self) -> PullRequest:
        pr = self.github.new_pull_request()
        pr.title = "t"
        pr.body = "b"
        pr.base = "main"
        pr.head = "h"
        return pr

    def test_new_pull_request_is_empty(self):
        pr = self.github.new_pull_request()
        self.assertIsInstance(pr, PullRequest)
        self.assertIsNone(pr.title)
        self.assertEqual(pr.reviewers, "")

    def test_create_without_reviewers(self):
        self.pull_request().create()
        self.assertEqual(
            self.runner.calls,
            [
                {
                    "args": ["gh", "pr", "create", "--title", "t", "--body", "b", "--base", "main", "--head", "h"],
                    "cwd": "/c",
                    "capture": False,
                }
            ],
        )

    def test_create_with_reviewers(self):
        pr = self.pull_request()
        pr.reviewers = "a,b"
        pr.create()
        self.assertEqual(
            self.runner.commands,
            [["gh", "pr", "create", "--title", "t", "--body", "b", "--base", "main", "--head", "h", "-r", "a,b"]],
        )
        self.assertEqual(self.runner.calls[0]["cwd"], "/c")

    def test_create_missing_fields_raises_and_runs_nothing(self):
        for field in ("title", "body", "base", "head"):
            with self.subTest(field=field):
                pr = self.pull_request()
                setattr(pr, field, None)
                with self.assertRaises(ValueError) as ctx:
                    pr.create()
                self.assertIn(field, str(ctx.exception))
        self.assertEqual(self.runner.calls, [])

    def test_create_lists_every_missing_field(self):
        with self.assertRaises(ValueError) as ctx:
            self.github.new_pull_request().create()
        for field in ("title", "body", "base", "head"):
            self.assertIn(field, str(ctx.exception))

    def test_add_label(self):
        self.pull_request().add_label("bug")
        self.assertEqual(
            self.runner.calls,
            [{"args": ["gh", "pr", "edit", "h", "--add-label", "bug"], "cwd": "/c", "capture": False}],
        )

    def test_add_label_without_head_raises_and_runs_nothing(self):
        pr = self.github.new_pull_request()
        with self.assertRaises(ValueError):
            pr.add_label("bug")
        self.assertEqual(self.runner.calls, [])


if __name__ == "__main__":
    unittest.main()
