import os
import unittest

from repo_sync.commands import CommandError
from repo_sync.settings import Settings
from repo_sync.sync import RepoSync, SyncError
from tests.helpers import FakeRunner, FlowTestCase


class RepoSyncTest(FlowTestCase):
    def test_happy_path_order(self):
        runner = FakeRunner(
            outputs={("gh", "pr", "list"): "3\n9\n"}, failures={("git", "diff"): 1}
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
                ["gh", "pr", "list", "--search", "is:pr is:open sync-branch in:title",
                 "--json", "number", "--jq", ".[].number"],
                ["gh", "pr", "close", "3"],
                ["gh", "pr", "close", "9"],
                ["git", "push", "-u", "origin", "HEAD:sync-branch"],
                ["gh", "pr", "create", "--title", "sync-branch", "--body", "sync-branch",
                 "--base", "dev", "--head", "sync-branch", "-r", "r1,r2"],
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
        runner = FakeRunner(failures={("git", "diff"): 1})
        self.sync(runner, INPUT_PULL_REQUEST_LABELS=" bug , ,docs,, ")
        edits = [c for c in runner.commands if c[:3] == ["gh", "pr", "edit"]]
        self.assertEqual(
            edits,
            [
                ["gh", "pr", "edit", "sync-branch", "--add-label", "bug"],
                ["gh", "pr", "edit", "sync-branch", "--add-label", "docs"],
            ],
        )

    def test_failing_label_warns_and_continues(self):
        runner = FakeRunner(failures={("git", "diff"): 1, ("gh", "pr", "edit", "sync-branch", "--add-label", "bad"): 1})
        _, out = self.sync(runner, INPUT_PULL_REQUEST_LABELS="bad,good")
        self.assertIn("::warning::could not add label 'bad' to the pull request, skipping", out)
        self.assertEqual(runner.commands[-1], ["gh", "pr", "edit", "sync-branch", "--add-label", "good"])

    def test_failing_command_propagates(self):
        for prefix in [("git", "clone"), ("git", "push"), ("gh", "pr", "create"), ("gh", "auth")]:
            runner = FakeRunner(failures={("git", "diff"): 1, prefix: 5})
            with self.subTest(prefix=prefix):
                with self.assertRaises(CommandError):
                    self.sync(runner)

    def test_copy_error_becomes_sync_error(self):
        settings = Settings.from_env(self.env(INPUT_SOURCE_FILES="nothing.txt"))
        with self.assertRaises(SyncError) as ctx:
            RepoSync(settings, None, None).copy_files(self.root)
        self.assertIn("matched nothing", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
