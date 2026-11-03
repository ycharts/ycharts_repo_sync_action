import contextlib
import io
import os
import tempfile
import unittest

import entrypoint
from sync import CommandError, GitClient, GitHubClient, RepoSync, Settings
from tests.helpers import FakeRunner, base_env


class FlowTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "a.txt"), "w") as f:
            f.write("a")

    def env(self, **overrides):
        return base_env(INPUT_SOURCE_FOLDER=self.src, **overrides)

    def sync(self, runner, **overrides):
        settings = Settings.from_env(self.env(**overrides))
        clone = os.path.join(self.root, "clone")
        os.makedirs(clone, exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            RepoSync(settings, GitClient(runner), GitHubClient(runner)).run(clone_dir=clone)
        return clone, out.getvalue()


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


class EntrypointTest(FlowTestCase):
    def main(self, runner, env):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = entrypoint.main(env=env, runner=runner)
        return code, out.getvalue()

    def test_success(self):
        code, _ = self.main(FakeRunner(failures={("git", "diff"): 1}), self.env())
        self.assertEqual(code, 0)

    def test_no_changes_success(self):
        code, _ = self.main(FakeRunner(), self.env())
        self.assertEqual(code, 0)

    def test_validation_error_runs_nothing(self):
        runner = FakeRunner()
        code, out = self.main(runner, self.env(INPUT_USER_NAME=""))
        self.assertNotEqual(code, 0)
        self.assertIn("::error::", out)
        self.assertEqual(runner.calls, [])

    def test_master_rejected(self):
        runner = FakeRunner()
        code, out = self.main(runner, self.env(INPUT_DESTINATION_HEAD_BRANCH="master"))
        self.assertNotEqual(code, 0)
        self.assertEqual(runner.calls, [])

    def test_command_failure(self):
        code, out = self.main(FakeRunner(failures={("git", "clone"): 128}), self.env())
        self.assertEqual(code, 128)
        self.assertIn("::error::", out)
        self.assertIn("git", out)
        self.assertNotIn("secret-token", out)

    def test_no_match_failure(self):
        code, out = self.main(FakeRunner(), self.env(INPUT_SOURCE_FILES="nothing.txt"))
        self.assertNotEqual(code, 0)
        self.assertIn("::error::", out)


if __name__ == "__main__":
    unittest.main()
