import json
from unittest import mock
from pathlib import Path
import os
import tempfile
import unittest
from collections.abc import Sequence

from repo_sync.commands import CommandError
from repo_sync.describe import HttpPost
from repo_sync.settings import Settings
from repo_sync.sync import RepoSync, SyncError
from tests.helpers import FakeRunner, FlowTestCase, base_env, patch_github_token


def prs(*items: tuple[str, str] | tuple[str, str, bool]) -> str:
    return json.dumps(
        [
            {
                "number": int(i[0]),
                "title": f"t{i[0]}",
                "body": "",
                "headRefName": i[1],
                "createdAt": "2024-01-01T00:00:00Z",
                "isCrossRepository": i[2] if len(i) > 2 else False,
            }
            for i in items
        ]
    )


LIST_FIELDS = "number,title,body,headRefName,createdAt,isCrossRepository"
CREATED = {("gh", "pr", "create"): "https://github.com/org/repo/pull/42\n"}


class RepoSyncTest(FlowTestCase):
    def test_happy_path_order(self):
        runner = FakeRunner(
            outputs={("gh", "pr", "list", "--search"): prs(("3", "sync-branch-20240101000000"), ("9", "sync-branch"))},
            failures={("git", "diff"): 1},
        )
        clone, _ = self.sync(runner, INPUT_PULL_REQUEST_REVIEWERS="r1,r2", INPUT_DESTINATION_BASE_BRANCH="dev")
        self.assertEqual(
            runner.commands,
            [
                ["git", "config", "--global", "user.email", "me@example.com"],
                ["git", "config", "--global", "user.name", "me"],
                ["gh", "auth", "setup-git"],
                ["git", "clone", "https://github.com/org/repo.git", clone],
                ["git", "checkout", "-b", "sync-branch"],
                ["git", "add", "."],
                ["git", "diff", "--cached", "--quiet"],
                ["git", "commit", "--message", "Update from https://github.com/org/source/commit/abc123"],
                ["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/sync-branch"],
                ["git", "push", "-u", "origin", "HEAD:sync-branch"],
                ["gh", "pr", "list", "--search", "is:pr is:open sync-branch in:title",
                 "--limit", "100", "--json", LIST_FIELDS],
                ["gh", "pr", "list", "--head", "sync-branch", "--state", "open",
                 "--json", "number,isCrossRepository"],
                ["gh", "pr", "create", "--title", "sync-branch", "--body-file", mock.ANY,
                 "--base", "dev", "--head", "sync-branch", "-r", "r1,r2"],
                ["gh", "pr", "close", "3"],
            ],
        )
        for call in runner.calls[4:]:
            self.assertEqual(call["cwd"], clone)
        self.assertTrue(os.path.isfile(os.path.join(clone, "a.txt")))

    def test_no_changes_stops_after_check(self):
        runner = FakeRunner()
        _, out = self.sync(runner)
        self.assertEqual(runner.commands[-1], ["git", "diff", "--cached", "--quiet"])
        self.assertIn("No changes detected", out)
        flat = [c[1] for c in runner.commands]
        self.assertNotIn("commit", flat)
        self.assertNotIn("push", flat)
        self.assertNotIn("pr", flat)

    def test_no_reviewers_omits_flag(self):
        runner = FakeRunner(failures={("git", "diff"): 1})
        self.sync(runner)
        create = [c for c in runner.commands if c[:3] == ["gh", "pr", "create"]][0]
        self.assertNotIn("-r", create)

    def test_labels_trimmed_and_added_one_at_a_time(self):
        runner = FakeRunner(outputs=CREATED, failures={("git", "diff"): 1})
        self.sync(runner, INPUT_PULL_REQUEST_LABELS=" bug , ,docs,, ")
        edits = [c for c in runner.commands if c[:3] == ["gh", "pr", "edit"]]
        self.assertEqual(
            edits,
            [
                ["gh", "pr", "edit", "42", "--add-label", "bug"],
                ["gh", "pr", "edit", "42", "--add-label", "docs"],
            ],
        )

    def test_unparsed_url_falls_back_to_lookup_for_labels(self):
        # FakeRunner matches outputs by prefix only, so it can't make the
        # pre-create `gh pr list --head` find nothing and the later one find the PR.
        class LateListRunner(FakeRunner):
            def __call__(self, args: Sequence[str], cwd: str | None = None, capture: bool = False) -> str:
                listed = sum(1 for c in self.commands if c[:3] == ["gh", "pr", "list"] and "--head" in c)
                result = super().__call__(args, cwd, capture)
                if list(args)[:3] == ["gh", "pr", "list"] and "--head" in args and listed == 0:
                    return ""
                return result

        runner = LateListRunner(
            outputs={
                ("gh", "pr", "create"): "created\n",
                ("gh", "pr", "list", "--head"): prs(("9", "sync-branch")),
            },
            failures={("git", "diff"): 1},
        )
        self.sync(runner, INPUT_PULL_REQUEST_LABELS="bug")
        self.assertIn(["gh", "pr", "edit", "9", "--add-label", "bug"], runner.commands)

    def test_unparsed_url_and_no_lookup_warns_and_skips_labels(self):
        runner = FakeRunner(outputs={("gh", "pr", "create"): "created\n"}, failures={("git", "diff"): 1})
        _, out = self.sync(runner, INPUT_PULL_REQUEST_LABELS="bug")
        self.assertIn("::warning::could not determine the pull request number, skipping labels", out)
        self.assertFalse([c for c in runner.commands if c[:3] == ["gh", "pr", "edit"]])

    def test_unparsed_url_without_labels_does_not_look_up_number(self):
        runner = FakeRunner(outputs={("gh", "pr", "create"): "created\n"}, failures={("git", "diff"): 1})
        _, out = self.sync(runner)
        self.assertNotIn("::warning::", out)
        lists = [c for c in runner.commands if c[:3] == ["gh", "pr", "list"] and "--head" in c]
        self.assertEqual(len(lists), 1)

    def test_failing_label_warns_and_continues(self):
        runner = FakeRunner(
            outputs=CREATED, failures={("git", "diff"): 1, ("gh", "pr", "edit", "42", "--add-label", "bad"): 1}
        )
        _, out = self.sync(runner, INPUT_PULL_REQUEST_LABELS="bad,good")
        self.assertIn("::warning::could not add label 'bad' to the pull request, skipping", out)
        self.assertEqual(runner.commands[-1], ["gh", "pr", "edit", "42", "--add-label", "good"])

    def test_failing_command_propagates(self):
        for prefix in [("git", "clone"), ("git", "push"), ("gh", "pr", "create"), ("gh", "auth")]:
            runner = FakeRunner(failures={("git", "diff"): 1, prefix: 5})
            with self.subTest(prefix=prefix):
                with self.assertRaises(CommandError):
                    self.sync(runner)

    def index(self, runner: FakeRunner, *prefix: str) -> int:
        return next(i for i, c in enumerate(runner.commands) if c[: len(prefix)] == list(prefix))

    def closed(self, runner: FakeRunner) -> list[str]:
        return [c[3] for c in runner.commands if c[:3] == ["gh", "pr", "close"]]

    def test_push_then_create_then_close(self):
        runner = FakeRunner(
            outputs={("gh", "pr", "list", "--search"): prs(("3", "sync-branch-20240101000000"))},
            failures={("git", "diff"): 1},
        )
        self.sync(runner)
        push = self.index(runner, "git", "push")
        create = self.index(runner, "gh", "pr", "create")
        listing = self.index(runner, "gh", "pr", "list", "--search")
        close = self.index(runner, "gh", "pr", "close")
        self.assertLess(push, listing)
        self.assertLess(listing, create)
        self.assertLess(create, close)

    def test_reuses_open_pr_on_head_and_still_labels(self):
        runner = FakeRunner(
            outputs={
                ("gh", "pr", "list", "--head"): json.dumps([{"number": 5, "isCrossRepository": False}]),
                ("gh", "pr", "view"): json.dumps(pr_json(5, "sync-branch", "sync-branch", "sync-branch")),
                ("gh", "pr", "list", "--state"): prs(("5", "sync-branch"), ("8", "other-branch")),
            },
            failures={("git", "diff"): 1},
        )
        self.sync(runner, INPUT_PULL_REQUEST_LABELS="bug")
        self.assertIsNone(next((c for c in runner.commands if c[:3] == ["gh", "pr", "create"]), None))
        self.assertIn(["gh", "pr", "edit", "5", "--add-label", "bug"], runner.commands)
        self.assertEqual(self.closed(runner), ["8"])

    def test_fork_pr_on_head_is_not_reused(self):
        runner = FakeRunner(
            outputs={
                ("gh", "pr", "list", "--head"): json.dumps([{"number": 5, "isCrossRepository": True}]),
                **CREATED,
            },
            failures={("git", "diff"): 1},
        )
        self.sync(runner, INPUT_PULL_REQUEST_LABELS="bug")
        self.assertIn(
            ["gh", "pr", "create", "--title", "sync-branch", "--body-file", mock.ANY,
             "--base", "main", "--head", "sync-branch"],
            runner.commands,
        )
        self.assertIn(["gh", "pr", "edit", "42", "--add-label", "bug"], runner.commands)
        self.assertNotIn(["gh", "pr", "edit", "5", "--add-label", "bug"], runner.commands)

    def test_only_matching_heads_closed(self):
        listing = prs(
            ("1", "other-branch"),
            ("2", "sync-branch-20240101000000"),
            ("3", "sync-branch-other"),
            ("4", "sync-branch-20240101000000", True),
            ("5", "sync-branch-2024"),
            ("6", "sync-branch"),
            ("7", "xsync-branch-20240101000000"),
        )
        runner = FakeRunner(
            outputs={("gh", "pr", "list", "--search"): listing}, failures={("git", "diff"): 1}
        )
        self.sync(runner)
        self.assertEqual(self.closed(runner), ["2"])

    def test_timestamped_head_closes_prefix_and_older_but_not_current(self):
        class CurrentHeadRunner(FakeRunner):
            def __call__(self, args: Sequence[str], cwd: str | None = None, capture: bool = False) -> str:
                if list(args[:4]) == ["gh", "pr", "list", "--search"]:
                    current = next(c[3] for c in self.commands if c[:3] == ["git", "checkout", "-b"])
                    self.outputs = {
                        ("gh", "pr", "list", "--search"): prs(
                            ("1", "sync-branch"), ("2", "sync-branch-20240101000000"), ("3", current)
                        )
                    }
                return super().__call__(args, cwd, capture)

        runner = CurrentHeadRunner(failures={("git", "diff"): 1})
        self.sync(runner, INPUT_TIMESTAMP_HEAD_BRANCH="true")
        self.assertEqual(self.closed(runner), ["1", "2"])

    def test_existing_remote_branch_pushes_with_lease(self):
        runner = FakeRunner(failures={("git", "diff"): 1}, remote_branch_exists=True)
        self.sync(runner)
        self.assertIn(["git", "push", "--force-with-lease", "-u", "origin", "HEAD:sync-branch"], runner.commands)
        self.assertLess(
            self.index(runner, "git", "rev-parse"), self.index(runner, "git", "push")
        )

    def test_absent_remote_branch_pushes_plainly(self):
        runner = FakeRunner(failures={("git", "diff"): 1})
        self.sync(runner)
        self.assertIn(["git", "push", "-u", "origin", "HEAD:sync-branch"], runner.commands)
        self.assertNotIn("--force-with-lease", [a for c in runner.commands for a in c])

    def test_remote_branch_check_failure_propagates_before_push(self):
        runner = FakeRunner(failures={("git", "diff"): 1, ("git", "rev-parse"): 128})
        with self.assertRaises(CommandError):
            self.sync(runner)
        self.assertFalse(any(c[:2] == ["git", "push"] for c in runner.commands))

    def test_push_failure_creates_and_closes_nothing(self):
        runner = FakeRunner(failures={("git", "diff"): 1, ("git", "push"): 1})
        with self.assertRaises(CommandError):
            self.sync(runner)
        self.assertEqual(self.closed(runner), [])
        self.assertFalse(any(c[:3] == ["gh", "pr", "create"] for c in runner.commands))

    def test_create_failure_closes_nothing(self):
        runner = FakeRunner(
            outputs={("gh", "pr", "list", "--search"): prs(("2", "sync-branch-20240101000000"))},
            failures={("git", "diff"): 1, ("gh", "pr", "create"): 1},
        )
        with self.assertRaises(CommandError):
            self.sync(runner)
        self.assertEqual(self.closed(runner), [])

    def test_copy_files_raises_sync_error_when_pattern_matches_nothing(self):
        settings = Settings.from_env(self.env(INPUT_SOURCE_FILES="nothing.txt"))
        with self.assertRaises(SyncError) as ctx:
            RepoSync(settings).copy_files(self.root)
        self.assertIn("matched nothing", str(ctx.exception))


SOURCE_LINE = "Source commit: https://github.com/org/source/commit/abc123"
COMMIT_RESPONSE = json.dumps(
    {"commit": {"message": "Change a"}, "files": [{"filename": "src/a.txt", "status": "modified", "patch": "+x"}]}
)


def anthropic_reply(title: str = "Update a", body: str = "Changed a.") -> bytes:
    text = json.dumps({"title": title, "body": body})
    return json.dumps({"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}).encode()


def pr_json(number: int, head: str, title: str, body: str, created: str = "2024-01-01T00:00:00Z") -> dict:
    return {
        "number": number,
        "title": title,
        "body": body,
        "headRefName": head,
        "createdAt": created,
        "isCrossRepository": False,
    }


class DescriptionTest(FlowTestCase):
    def setUp(self):
        super().setUp()
        self.requests: list[dict] = []

    def http_post(self, url: str, headers: dict[str, str], body: bytes) -> bytes:
        self.requests.append(json.loads(body))
        return anthropic_reply()

    def runner(self, outputs: dict[tuple[str, ...], str] | None = None, **kwargs) -> FakeRunner:
        return FakeRunner(
            outputs={("gh", "api"): COMMIT_RESPONSE, **CREATED, **(outputs or {})},
            failures={("git", "diff"): 1},
            **kwargs,
        )

    def run_capturing(
        self, runner: FakeRunner, http_post: HttpPost | None = None, **overrides: str
    ) -> tuple[list[tuple[list[str], str]], str]:
        captured: list[tuple[list[str], str]] = []
        original = FakeRunner.__call__

        def spy(self_, args, cwd=None, capture=False):
            args = list(args)
            if "--body-file" in args:
                with open(args[args.index("--body-file") + 1], encoding="utf-8") as f:
                    captured.append((args, f.read()))
            return original(self_, args, cwd, capture)

        with mock.patch.object(FakeRunner, "__call__", spy):
            _, out = self.sync(runner, http_post or self.http_post, **overrides)
        return captured, out

    def closed(self, runner: FakeRunner) -> list[str]:
        return [c[3] for c in runner.commands if c[:3] == ["gh", "pr", "close"]]

    def generation(self, **overrides: str) -> dict[str, str]:
        return {"INPUT_GENERATE_PR_DESCRIPTION": "true", "ANTHROPIC_API_KEY": "sk-secret", **overrides}

    def test_generation_off_makes_no_api_calls_and_uses_branch_as_body(self):
        runner = self.runner()
        [(args, body)], _ = self.run_capturing(runner)
        self.assertNotIn(["gh", "api"], [c[:2] for c in runner.commands])
        self.assertEqual(self.requests, [])
        self.assertEqual(args[args.index("--title") + 1], "sync-branch")
        self.assertEqual(body, "sync-branch")

    def test_generation_off_appends_source_commit_to_previous_body(self):
        listing = json.dumps([pr_json(3, "sync-branch-20240101000000", "t3", "Older body")])
        runner = self.runner({("gh", "pr", "list", "--search"): listing})
        [(args, body)], _ = self.run_capturing(runner)
        self.assertEqual(args[args.index("--title") + 1], "sync-branch")
        self.assertEqual(body, f"Older body\n\n---\n\n{SOURCE_LINE}")

    def test_generation_on_prefixes_title_and_appends_body(self):
        runner = self.runner()
        [(args, body)], _ = self.run_capturing(runner, **self.generation())
        self.assertEqual(args[args.index("--title") + 1], "sync-branch: Update a")
        self.assertEqual(body, f"Changed a.\n\n{SOURCE_LINE}")
        self.assertEqual(len(self.requests), 1)

    def test_generation_on_passes_previous_prs_to_claude(self):
        listing = json.dumps([pr_json(3, "sync-branch-20240101000000", "Old title", "Older body")])
        runner = self.runner({("gh", "pr", "list", "--search"): listing})
        [(_, body)], _ = self.run_capturing(runner, **self.generation())
        prompt = self.requests[0]["messages"][0]["content"]
        self.assertIn("Pull request #3: Old title", prompt)
        self.assertIn("Older body", prompt)
        self.assertEqual(body, f"Older body\n\n---\n\nChanged a.\n\n{SOURCE_LINE}")

    def test_previous_prs_given_to_claude_are_capped_at_ten(self):
        listing = json.dumps(
            [pr_json(n, f"sync-branch-2024010100{n:04d}", f"title{n}", "b", f"2024-01-{n:02d}T00:00:00Z") for n in range(1, 13)]
        )
        runner = self.runner({("gh", "pr", "list", "--search"): listing})
        self.run_capturing(runner, **self.generation())
        prompt = self.requests[0]["messages"][0]["content"]
        self.assertEqual(prompt.count("### Pull request #"), 10)
        self.assertEqual(self.closed(runner), [str(n) for n in range(12, 0, -1)])

    def test_generation_failure_warns_and_falls_back(self):
        def failing(url: str, headers: dict[str, str], body: bytes) -> bytes:
            raise OSError("boom")

        captured, out = self.run_capturing(self.runner(), failing, **self.generation())
        [(args, body)] = captured
        self.assertIn("::warning::could not generate the pull request description", out)
        self.assertNotIn("sk-secret", out)
        self.assertEqual(args[args.index("--title") + 1], "sync-branch")
        self.assertEqual(body, "sync-branch")

    def test_source_diff_failure_warns_and_falls_back(self):
        runner = FakeRunner(outputs=CREATED, failures={("git", "diff"): 1, ("gh", "api"): 1})
        _, out = self.sync(runner, self.http_post, **self.generation())
        self.assertIn("::warning::could not generate the pull request description", out)
        self.assertEqual(self.requests, [])
        self.assertTrue([c for c in runner.commands if c[:3] == ["gh", "pr", "create"]])

    def test_reused_pr_is_edited_with_appended_body(self):
        runner = self.runner(
            {
                ("gh", "pr", "list", "--head"): json.dumps([{"number": 5, "isCrossRepository": False}]),
                ("gh", "pr", "view"): json.dumps(pr_json(5, "sync-branch", "sync-branch: Old", "Current body")),
            }
        )
        [(args, body)], _ = self.run_capturing(runner, **self.generation())
        self.assertEqual(args[:4], ["gh", "pr", "edit", "5"])
        self.assertEqual(args[args.index("--title") + 1], "sync-branch: Update a")
        self.assertEqual(body, f"Current body\n\n---\n\nChanged a.\n\n{SOURCE_LINE}")
        self.assertFalse([c for c in runner.commands if c[:3] == ["gh", "pr", "create"]])
        prompt = self.requests[0]["messages"][0]["content"]
        self.assertIn("Pull request #5: sync-branch: Old", prompt)

    def test_reused_pr_keeps_its_title_without_a_generated_one(self):
        runner = self.runner(
            {
                ("gh", "pr", "list", "--head"): json.dumps([{"number": 5, "isCrossRepository": False}]),
                ("gh", "pr", "view"): json.dumps(pr_json(5, "sync-branch", "Hand edited", "Current body")),
            }
        )
        [(args, body)], _ = self.run_capturing(runner)
        self.assertEqual(args[args.index("--title") + 1], "Hand edited")
        self.assertIn(SOURCE_LINE, body)

    def test_reused_pr_keeps_its_title_when_generation_fails(self):
        runner = FakeRunner(
            outputs={
                **CREATED,
                ("gh", "pr", "list", "--head"): json.dumps([{"number": 5, "isCrossRepository": False}]),
                ("gh", "pr", "view"): json.dumps(pr_json(5, "sync-branch", "Hand edited", "Current body")),
            },
            failures={("git", "diff"): 1, ("gh", "api"): 1},
        )
        [(args, _)], _ = self.run_capturing(runner, **self.generation())
        self.assertEqual(args[args.index("--title") + 1], "Hand edited")

    def test_label_mode_closes_label_matches(self):
        listing = json.dumps([pr_json(7, "unrelated-branch", "t7", "b7")])
        runner = self.runner({("gh", "pr", "list", "--state"): listing})
        self.sync(runner, self.http_post, INPUT_PULL_REQUEST_LABELS="sync")
        self.assertEqual(self.closed(runner), ["7"])
        self.assertIn(
            ["gh", "pr", "list", "--state", "open", "--label", "sync", "--limit", "100", "--json", LIST_FIELDS],
            runner.commands,
        )

    def test_previous_prs_closed_after_create(self):
        listing = json.dumps([pr_json(3, "sync-branch-20240101000000", "t3", "b3")])
        runner = self.runner({("gh", "pr", "list", "--search"): listing})
        self.sync(runner, self.http_post)
        create = next(i for i, c in enumerate(runner.commands) if c[:3] == ["gh", "pr", "create"])
        close = next(i for i, c in enumerate(runner.commands) if c[:3] == ["gh", "pr", "close"])
        self.assertLess(create, close)


class CopyFilesTest(unittest.TestCase):
    def setUp(self):
        patch_github_token(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.src = os.path.join(self.root, "src")
        self.clone = os.path.join(self.root, "clone")
        os.makedirs(self.src)
        os.makedirs(self.clone)

    def write(self, path: str, content: str = "x") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def read(self, path: str) -> str:
        with open(path) as f:
            return f.read()

    def copy(self, files: str, dest: str = "") -> list[str]:
        settings = Settings.from_env(
            base_env(INPUT_SOURCE_FOLDER=self.src, INPUT_SOURCE_FILES=files, INPUT_DESTINATION_FOLDER=dest)
        )
        return RepoSync(settings).copy_files(self.clone)

    def test_single_file(self):
        self.write(os.path.join(self.src, "a.txt"), "hello")
        self.copy("a.txt")
        self.assertEqual(self.read(os.path.join(self.clone, "a.txt")), "hello")

    def test_nested_path_preserved(self):
        self.write(os.path.join(self.src, "x", "y", "a.txt"), "n")
        self.copy("x/y/a.txt")
        self.assertEqual(self.read(os.path.join(self.clone, "x", "y", "a.txt")), "n")

    def test_glob(self):
        self.write(os.path.join(self.src, "a.md"))
        self.write(os.path.join(self.src, "b.md"))
        self.write(os.path.join(self.src, "c.txt"))
        self.copy("*.md")
        self.assertEqual(sorted(os.listdir(self.clone)), ["a.md", "b.md"])

    def test_directory_recursion(self):
        self.write(os.path.join(self.src, "d", "a.txt"), "1")
        self.write(os.path.join(self.src, "d", "sub", "b.txt"), "2")
        self.copy("d")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "sub", "b.txt")), "2")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "a.txt")), "1")

    def test_overwrite_without_deleting(self):
        self.write(os.path.join(self.src, "a.txt"), "new")
        self.write(os.path.join(self.clone, "a.txt"), "old")
        self.write(os.path.join(self.clone, "keep.txt"), "keep")
        self.copy("a.txt")
        self.assertEqual(self.read(os.path.join(self.clone, "a.txt")), "new")
        self.assertEqual(self.read(os.path.join(self.clone, "keep.txt")), "keep")

    def test_directory_overwrite_keeps_existing(self):
        self.write(os.path.join(self.src, "d", "a.txt"), "new")
        self.write(os.path.join(self.clone, "d", "a.txt"), "old")
        self.write(os.path.join(self.clone, "d", "keep.txt"), "keep")
        self.copy("d")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "a.txt")), "new")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "keep.txt")), "keep")

    def test_destination_folder_created(self):
        self.write(os.path.join(self.src, "a.txt"), "z")
        self.copy("a.txt", dest="out/deep")
        self.assertEqual(self.read(os.path.join(self.clone, "out", "deep", "a.txt")), "z")

    def test_empty_destination_folder_is_repo_root(self):
        self.write(os.path.join(self.src, "a.txt"), "z")
        self.copy("a.txt", dest="")
        self.assertTrue(os.path.isfile(os.path.join(self.clone, "a.txt")))

    def test_no_match_errors(self):
        with self.assertRaises(SyncError):
            self.copy("missing.txt")

    def test_second_pattern_no_match_errors(self):
        self.write(os.path.join(self.src, "a.txt"))
        with self.assertRaises(SyncError):
            self.copy("a.txt nope*")

    def test_escape_via_dotdot_rejected(self):
        self.write(os.path.join(self.root, "secret.txt"))
        with self.assertRaises(SyncError):
            self.copy("../secret.txt")
        self.assertFalse(os.path.exists(os.path.join(self.clone, "secret.txt")))

    def test_escape_via_symlink_rejected(self):
        self.write(os.path.join(self.root, "secret.txt"))
        os.symlink(os.path.join(self.root, "secret.txt"), os.path.join(self.src, "link.txt"))
        with self.assertRaises(SyncError):
            self.copy("link.txt")

    def test_destination_escape_rejected(self):
        self.write(os.path.join(self.src, "a.txt"))
        with self.assertRaises(SyncError):
            self.copy("a.txt", dest="../outside")

    def test_leading_slash_destination(self):
        self.write(os.path.join(self.src, "a.txt"), "z")
        self.copy("a.txt", dest="/docs")
        self.assertEqual(self.read(os.path.join(self.clone, "docs", "a.txt")), "z")

    def test_slash_destination_is_repo_root(self):
        self.write(os.path.join(self.src, "a.txt"), "z")
        self.copy("a.txt", dest="/")
        self.assertEqual(self.read(os.path.join(self.clone, "a.txt")), "z")

    def test_dotdot_destination_with_slash_rejected(self):
        self.write(os.path.join(self.src, "a.txt"))
        with self.assertRaises(SyncError):
            self.copy("a.txt", dest="/../x")
        with self.assertRaises(SyncError):
            self.copy("a.txt", dest="../x")

    def test_symlink_in_directory_to_outside_copied_as_link(self):
        secret = os.path.join(self.root, "secret.txt")
        self.write(secret, "secret")
        self.write(os.path.join(self.src, "d", "a.txt"), "1")
        os.symlink(secret, os.path.join(self.src, "d", "link.txt"))
        self.copy("d")
        copied = os.path.join(self.clone, "d", "link.txt")
        self.assertTrue(os.path.islink(copied))
        self.assertEqual(os.readlink(copied), secret)
        self.assertEqual(self.read(secret), "secret")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "a.txt")), "1")

    def test_dangling_symlink_in_directory_copied_as_link(self):
        self.write(os.path.join(self.src, "d", "a.txt"), "1")
        os.symlink("nowhere", os.path.join(self.src, "d", "dangling"))
        self.copy("d")
        copied = os.path.join(self.clone, "d", "dangling")
        self.assertTrue(os.path.islink(copied))
        self.assertEqual(os.readlink(copied), "nowhere")

    def test_directory_symlink_overwrites_existing_file_and_link(self):
        os.makedirs(os.path.join(self.src, "d"))
        os.symlink("a", os.path.join(self.src, "d", "l1"))
        os.symlink("b", os.path.join(self.src, "d", "l2"))
        self.write(os.path.join(self.clone, "d", "l1"), "old")
        os.symlink("elsewhere", os.path.join(self.clone, "d", "l2"))
        self.copy("d")
        self.assertEqual(os.readlink(os.path.join(self.clone, "d", "l1")), "a")
        self.assertEqual(os.readlink(os.path.join(self.clone, "d", "l2")), "b")

    def test_file_does_not_write_through_existing_destination_link(self):
        outside = os.path.join(self.root, "outside.txt")
        self.write(outside, "keep")
        self.write(os.path.join(self.src, "d", "a.txt"), "new")
        os.makedirs(os.path.join(self.clone, "d"))
        os.symlink(outside, os.path.join(self.clone, "d", "a.txt"))
        self.copy("d")
        self.assertEqual(self.read(outside), "keep")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "a.txt")), "new")
        self.assertFalse(os.path.islink(os.path.join(self.clone, "d", "a.txt")))

    def test_top_level_symlink_within_source_copied_as_link(self):
        self.write(os.path.join(self.src, "real.txt"), "r")
        os.symlink("real.txt", os.path.join(self.src, "link.txt"))
        self.copy("link.txt")
        copied = os.path.join(self.clone, "link.txt")
        self.assertTrue(os.path.islink(copied))
        self.assertEqual(os.readlink(copied), "real.txt")

    def test_top_level_symlink_overwrites_existing_file(self):
        self.write(os.path.join(self.src, "real.txt"), "r")
        os.symlink("real.txt", os.path.join(self.src, "link.txt"))
        self.write(os.path.join(self.clone, "link.txt"), "old")
        self.copy("link.txt")
        self.assertTrue(os.path.islink(os.path.join(self.clone, "link.txt")))

    def test_missing_source_folder_errors(self):
        self.src = os.path.join(self.root, "nope")
        with self.assertRaises(SyncError) as ctx:
            self.copy("a.txt")
        self.assertIn(f"source_folder '{self.src}' is not a directory", str(ctx.exception))

    def test_source_folder_is_file_errors(self):
        self.src = os.path.join(self.root, "file.txt")
        self.write(self.src)
        with self.assertRaises(SyncError) as ctx:
            self.copy("a.txt")
        self.assertIn(f"source_folder '{self.src}' is not a directory", str(ctx.exception))

    def test_os_error_wrapped_with_path(self):
        self.write(os.path.join(self.src, "a.txt"))
        self.write(os.path.join(self.clone, "blocker"), "file")
        with self.assertRaises(SyncError) as ctx:
            self.copy("a.txt", dest="blocker/sub")
        self.assertIn("blocker", str(ctx.exception))

    def test_copy_failure_in_directory_wrapped(self):
        self.write(os.path.join(self.src, "d", "a.txt"))
        self.write(os.path.join(self.clone, "d"), "file")
        with self.assertRaises(SyncError):
            self.copy("d")

    def test_returns_files_relative_to_source_root_outside_a_repository(self):
        self.write(os.path.join(self.src, "a.txt"))
        self.write(os.path.join(self.src, "d", "b.txt"))
        self.write(os.path.join(self.src, "d", "e", "c.txt"))
        self.assertEqual(self.copy("a.txt\nd"), ["a.txt", "d/b.txt", "d/e/c.txt"])

    def test_paths_are_relative_to_the_checkout_root(self):
        self.write(os.path.join(self.src, "docs", "a.txt"))
        os.makedirs(os.path.join(self.root, ".git"))
        self.assertEqual(self.copy("docs"), ["src/docs/a.txt"])

    def test_nested_checkout_ignores_cwd_without_git(self):
        app = os.path.join(self.root, "workspace", "app")
        self.write(os.path.join(app, "docs", "a.txt"))
        os.makedirs(os.path.join(app, ".git"))
        settings = Settings.from_env(
            base_env(INPUT_SOURCE_FOLDER=os.path.join(app, "docs"), INPUT_SOURCE_FILES="a.txt")
        )
        with mock.patch("pathlib.Path.cwd", return_value=Path(self.root, "workspace")):
            copied = RepoSync(settings).copy_files(self.clone)
        self.assertEqual(copied, ["docs/a.txt"])

    def test_returned_paths_include_symlinks_in_directory(self):
        self.write(os.path.join(self.src, "d", "a.txt"))
        os.symlink("a.txt", os.path.join(self.src, "d", "link.txt"))
        self.assertEqual(self.copy("d"), ["d/a.txt", "d/link.txt"])


if __name__ == "__main__":
    unittest.main()
