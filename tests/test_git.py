import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from repo_sync.commands import CommandError
from repo_sync.previous_prs import PreviousPr
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
        self.git.push("br", force_with_lease=True)
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
                {"args": ["git", "push", "--force-with-lease", "-u", "origin", "HEAD:br"], "cwd": "/tmp/c", "capture": False},
            ],
        )

    def test_diff_stat(self):
        runner = FakeRunner(outputs={("git", "diff"): " a | 1 +\n"})
        self.assertEqual(GitClient("/tmp/c", runner).diff_stat(), " a | 1 +\n")
        self.assertEqual(
            runner.calls,
            [{"args": ["git", "diff", "--stat", "HEAD~1"], "cwd": "/tmp/c", "capture": True}],
        )

    def test_diff_stat_returns_empty_on_failure(self):
        runner = FakeRunner(failures={("git", "diff"): 128})
        self.assertEqual(GitClient("/tmp/c", runner).diff_stat(), "")

    def test_remote_branch_exists_on_exit_0(self):
        git = GitClient("/tmp/c", FakeRunner(remote_branch_exists=True))
        self.assertTrue(git.remote_branch_exists("br"))

    def test_remote_branch_missing_on_exit_1(self):
        self.assertFalse(self.git.remote_branch_exists("br"))
        self.assertEqual(
            self.runner.calls,
            [
                {
                    "args": ["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/br"],
                    "cwd": "/tmp/c",
                    "capture": False,
                }
            ],
        )

    def test_remote_branch_check_other_failure_propagates(self):
        git = GitClient("/tmp/c", FakeRunner(failures={("git", "rev-parse"): 128}))
        with self.assertRaises(CommandError):
            git.remote_branch_exists("br")

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

    def listing(self, *items: tuple[int, str, str, bool]) -> str:
        return json.dumps(
            [
                {
                    "number": n,
                    "title": f"t{n}",
                    "body": f"b{n}",
                    "headRefName": head,
                    "createdAt": created,
                    "isCrossRepository": fork,
                }
                for n, head, created, fork in items
            ]
        )

    def test_list_previous_prs_title_mode(self):
        listing = self.listing(
            (3, "pre", "2024-01-01T00:00:00Z", False),
            (12, "pre-20240101000000", "2024-02-01T00:00:00Z", False),
            (13, "pre-20240101000001", "2024-03-01T00:00:00Z", True),
            (14, "pre-other", "2024-04-01T00:00:00Z", False),
            (15, "pre-20240101000002", "2024-05-01T00:00:00Z", False),
        )
        expected = [
            PreviousPr(12, "t12", "b12", "pre-20240101000000", "2024-02-01T00:00:00Z"),
            PreviousPr(3, "t3", "b3", "pre", "2024-01-01T00:00:00Z"),
        ]
        for output, want in {"": [], "[]": [], listing: expected}.items():
            runner = FakeRunner(outputs={("gh", "pr", "list"): output})
            with self.subTest(output=output):
                self.assertEqual(GitHubClient("/c", runner).list_previous_prs("pre", "pre-20240101000002", []), want)
                self.assertEqual(
                    runner.calls[0],
                    {
                        "args": [
                            "gh", "pr", "list",
                            "--search", "is:pr is:open pre in:title",
                            "--limit", "100",
                            "--json", "number,title,body,headRefName,createdAt,isCrossRepository",
                        ],
                        "cwd": "/c",
                        "capture": True,
                    },
                )

    def test_list_previous_prs_label_mode(self):
        listing = self.listing(
            (1, "anything", "2024-01-01T00:00:00Z", False),
            (2, "other", "2024-03-01T00:00:00Z", False),
            (3, "fork", "2024-04-01T00:00:00Z", True),
            (4, "current", "2024-05-01T00:00:00Z", False),
            (5, "pre-x", "2024-02-01T00:00:00Z", False),
        )
        runner = FakeRunner(outputs={("gh", "pr", "list"): listing})
        result = GitHubClient("/c", runner).list_previous_prs("pre", "current", ["a*b", "[x] y?"])
        self.assertEqual([pr.number for pr in result], [2, 5, 1])
        self.assertEqual(
            runner.calls[0],
            {
                "args": [
                    "gh", "pr", "list", "--state", "open",
                    "--label", "a*b",
                    "--label", "[x] y?",
                    "--limit", "100",
                    "--json", "number,title,body,headRefName,createdAt,isCrossRepository",
                ],
                "cwd": "/c",
                "capture": True,
            },
        )

    def test_list_previous_prs_null_body_becomes_empty(self):
        output = json.dumps(
            [{"number": 1, "title": "t", "body": None, "headRefName": "pre", "createdAt": "x", "isCrossRepository": False}]
        )
        runner = FakeRunner(outputs={("gh", "pr", "list"): output})
        self.assertEqual(GitHubClient("/c", runner).list_previous_prs("pre", "cur", [])[0].body, "")

    def test_list_previous_prs_failure_raises(self):
        runner = FakeRunner(failures={("gh", "pr", "list"): 1})
        with self.assertRaises(CommandError):
            GitHubClient("/c", runner).list_previous_prs("pre", "cur", [])

    def test_get_pr(self):
        output = json.dumps(
            {"number": 7, "title": "T", "body": "B", "headRefName": "h", "createdAt": "2024-01-01T00:00:00Z"}
        )
        runner = FakeRunner(outputs={("gh", "pr", "view"): output})
        self.assertEqual(GitHubClient("/c", runner).get_pr(7), PreviousPr(7, "T", "B", "h", "2024-01-01T00:00:00Z"))
        self.assertEqual(
            runner.calls,
            [
                {
                    "args": ["gh", "pr", "view", "7", "--json", "number,title,body,headRefName,createdAt"],
                    "cwd": "/c",
                    "capture": True,
                }
            ],
        )

    def test_edit_pr_passes_body_file(self):
        seen = {}

        class Reading(FakeRunner):
            def __call__(self, args, cwd=None, capture=False):
                seen["path"] = args[args.index("--body-file") + 1]
                with open(seen["path"], encoding="utf-8") as f:
                    seen["body"] = f.read()
                return super().__call__(args, cwd, capture)

        runner = Reading()
        body = "line\n" + "é" * 70000
        GitHubClient("/c", runner).edit_pr(9, "New title", body)
        self.assertEqual(
            runner.calls,
            [
                {
                    "args": ["gh", "pr", "edit", "9", "--title", "New title", "--body-file", seen["path"]],
                    "cwd": "/c",
                    "capture": False,
                }
            ],
        )
        self.assertEqual(seen["body"], body)
        self.assertFalse(os.path.exists(seen["path"]))

    def test_api(self):
        runner = FakeRunner(outputs={("gh", "api"): "{}"})
        self.assertEqual(GitHubClient("/c", runner).api("repos/o/r/pulls/1"), "{}")
        self.assertEqual(
            runner.calls,
            [{"args": ["gh", "api", "repos/o/r/pulls/1"], "cwd": "/c", "capture": True}],
        )

    def test_find_open_pr(self):
        cases = {
            "": None,
            "[]": None,
            json.dumps([{"number": 4, "isCrossRepository": True}]): None,
            json.dumps([{"number": 4, "isCrossRepository": True}, {"number": 6, "isCrossRepository": False}]): 6,
        }
        for output, expected in cases.items():
            runner = FakeRunner(outputs={("gh", "pr", "list"): output})
            with self.subTest(output=output):
                self.assertEqual(GitHubClient("/c", runner).find_open_pr("br"), expected)
                self.assertEqual(
                    runner.calls[0],
                    {
                        "args": [
                            "gh", "pr", "list",
                            "--head", "br",
                            "--state", "open",
                            "--json", "number,isCrossRepository",
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
                    "args": ["gh", "pr", "create", "--title", "t", "--body-file", mock.ANY, "--base", "main", "--head", "h"],
                    "cwd": "/c",
                    "capture": True,
                }
            ],
        )

    def test_create_body_file_holds_body_and_is_removed(self):
        seen = {}

        class Reading(FakeRunner):
            def __call__(self, args, cwd=None, capture=False):
                seen["path"] = args[args.index("--body-file") + 1]
                with open(seen["path"], encoding="utf-8") as f:
                    seen["body"] = f.read()
                return super().__call__(args, cwd, capture)

        github = GitHubClient("/c", Reading())
        pr = github.new_pull_request()
        pr.title, pr.body, pr.base, pr.head = "t", "multi\nline body", "main", "h"
        pr.create()
        self.assertEqual(seen["body"], "multi\nline body")
        self.assertFalse(os.path.exists(seen["path"]))

    def test_create_with_reviewers(self):
        pr = self.pull_request()
        pr.reviewers = "a,b"
        pr.create()
        self.assertEqual(
            self.runner.commands,
            [["gh", "pr", "create", "--title", "t", "--body-file", mock.ANY, "--base", "main", "--head", "h", "-r", "a,b"]],
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

    def test_add_label_uses_pr_number(self):
        pr = self.pull_request()
        pr.number = 7
        pr.add_label("bug")
        self.assertEqual(
            self.runner.calls,
            [{"args": ["gh", "pr", "edit", "7", "--add-label", "bug"], "cwd": "/c", "capture": False}],
        )

    def test_create_parses_number_from_url(self):
        runner = FakeRunner(outputs={("gh", "pr", "create"): "Creating...\nhttps://github.com/org/repo/pull/42\n"})
        pr = GitHubClient("/c", runner).new_pull_request()
        pr.title, pr.body, pr.base, pr.head = "t", "b", "main", "h"
        with contextlib.redirect_stdout(io.StringIO()):
            pr.create()
        self.assertEqual(pr.number, 42)

    def test_create_without_url_leaves_number_unset(self):
        pr = self.pull_request()
        pr.create()
        self.assertIsNone(pr.number)

    def test_create_prints_captured_output(self):
        runner = FakeRunner(outputs={("gh", "pr", "create"): "https://github.com/org/repo/pull/42\n"})
        pr = GitHubClient("/c", runner).new_pull_request()
        pr.title, pr.body, pr.base, pr.head = "t", "b", "main", "h"
        with contextlib.redirect_stdout(io.StringIO()) as out:
            pr.create()
        self.assertEqual(out.getvalue(), "https://github.com/org/repo/pull/42\n")

    def test_create_without_output_prints_nothing(self):
        pr = self.pull_request()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            pr.create()
        self.assertEqual(out.getvalue(), "")

    def test_add_label_without_number_raises_and_runs_nothing(self):
        pr = self.pull_request()
        with self.assertRaises(ValueError):
            pr.add_label("bug")
        self.assertEqual(self.runner.calls, [])


if __name__ == "__main__":
    unittest.main()
