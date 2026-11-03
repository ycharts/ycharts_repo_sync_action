import os
import tempfile
import unittest
from unittest import mock

from repo_sync.commands import CommandError
from repo_sync.git import GitClient, GitHubClient
from repo_sync.settings import Settings
from repo_sync.sync import RepoSync, SyncError
from tests.helpers import FakeRunner, FlowTestCase, base_env, patch_github_token


class RepoSyncTest(FlowTestCase):
    def test_happy_path_order(self) -> None:
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

    def test_no_changes_stops_after_check(self) -> None:
        runner = FakeRunner()
        _, out = self.sync(runner)
        self.assertEqual(runner.commands[-1], ["git", "diff", "--cached", "--quiet"])
        self.assertIn("No changes detected", out)
        flat = [c[1] for c in runner.commands]
        self.assertNotIn("commit", flat)
        self.assertNotIn("push", flat)
        self.assertNotIn("pr", flat)

    def test_no_reviewers_omits_flag(self) -> None:
        runner = FakeRunner(failures={("git", "diff"): 1})
        self.sync(runner)
        create = [c for c in runner.commands if c[:3] == ["gh", "pr", "create"]][0]
        self.assertNotIn("-r", create)

    def test_labels_trimmed_and_added_one_at_a_time(self) -> None:
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

    def test_failing_label_warns_and_continues(self) -> None:
        runner = FakeRunner(failures={("git", "diff"): 1, ("gh", "pr", "edit", "sync-branch", "--add-label", "bad"): 1})
        _, out = self.sync(runner, INPUT_PULL_REQUEST_LABELS="bad,good")
        self.assertIn("::warning::could not add label 'bad' to the pull request, skipping", out)
        self.assertEqual(runner.commands[-1], ["gh", "pr", "edit", "sync-branch", "--add-label", "good"])

    def test_failing_command_propagates(self) -> None:
        for prefix in [("git", "clone"), ("git", "push"), ("gh", "pr", "create"), ("gh", "auth")]:
            runner = FakeRunner(failures={("git", "diff"): 1, prefix: 5})
            with self.subTest(prefix=prefix):
                with self.assertRaises(CommandError):
                    self.sync(runner)

    def test_copy_files_raises_sync_error_when_pattern_matches_nothing(self) -> None:
        settings = Settings.from_env(self.env(INPUT_SOURCE_FILES="nothing.txt"))
        with self.assertRaises(SyncError) as ctx:
            RepoSync(settings, GitClient(FakeRunner()), GitHubClient(FakeRunner())).copy_files(self.root)
        self.assertIn("matched nothing", str(ctx.exception))

    def test_default_clients(self) -> None:
        settings = Settings.from_env(self.env())
        with mock.patch("repo_sync.sync.GitClient") as git_class, mock.patch(
            "repo_sync.sync.GitHubClient"
        ) as github_class:
            sync = RepoSync(settings)
        git_class.assert_called_once_with()
        github_class.assert_called_once_with()
        self.assertIs(sync.git, git_class.return_value)
        self.assertIs(sync.github, github_class.return_value)

    def test_explicit_clients_are_used(self) -> None:
        settings = Settings.from_env(self.env())
        git, github = GitClient(FakeRunner()), GitHubClient(FakeRunner())
        sync = RepoSync(settings, git, github)
        self.assertIs(sync.git, git)
        self.assertIs(sync.github, github)


class CopyFilesTest(unittest.TestCase):
    def setUp(self) -> None:
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

    def copy(self, files: str, dest: str = "") -> None:
        settings = Settings.from_env(
            base_env(INPUT_SOURCE_FOLDER=self.src, INPUT_SOURCE_FILES=files, INPUT_DESTINATION_FOLDER=dest)
        )
        RepoSync(settings, GitClient(FakeRunner()), GitHubClient(FakeRunner())).copy_files(self.clone)

    def test_single_file(self) -> None:
        self.write(os.path.join(self.src, "a.txt"), "hello")
        self.copy("a.txt")
        self.assertEqual(self.read(os.path.join(self.clone, "a.txt")), "hello")

    def test_nested_path_preserved(self) -> None:
        self.write(os.path.join(self.src, "x", "y", "a.txt"), "n")
        self.copy("x/y/a.txt")
        self.assertEqual(self.read(os.path.join(self.clone, "x", "y", "a.txt")), "n")

    def test_glob(self) -> None:
        self.write(os.path.join(self.src, "a.md"))
        self.write(os.path.join(self.src, "b.md"))
        self.write(os.path.join(self.src, "c.txt"))
        self.copy("*.md")
        self.assertEqual(sorted(os.listdir(self.clone)), ["a.md", "b.md"])

    def test_directory_recursion(self) -> None:
        self.write(os.path.join(self.src, "d", "a.txt"), "1")
        self.write(os.path.join(self.src, "d", "sub", "b.txt"), "2")
        self.copy("d")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "sub", "b.txt")), "2")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "a.txt")), "1")

    def test_overwrite_without_deleting(self) -> None:
        self.write(os.path.join(self.src, "a.txt"), "new")
        self.write(os.path.join(self.clone, "a.txt"), "old")
        self.write(os.path.join(self.clone, "keep.txt"), "keep")
        self.copy("a.txt")
        self.assertEqual(self.read(os.path.join(self.clone, "a.txt")), "new")
        self.assertEqual(self.read(os.path.join(self.clone, "keep.txt")), "keep")

    def test_directory_overwrite_keeps_existing(self) -> None:
        self.write(os.path.join(self.src, "d", "a.txt"), "new")
        self.write(os.path.join(self.clone, "d", "a.txt"), "old")
        self.write(os.path.join(self.clone, "d", "keep.txt"), "keep")
        self.copy("d")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "a.txt")), "new")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "keep.txt")), "keep")

    def test_destination_folder_created(self) -> None:
        self.write(os.path.join(self.src, "a.txt"), "z")
        self.copy("a.txt", dest="out/deep")
        self.assertEqual(self.read(os.path.join(self.clone, "out", "deep", "a.txt")), "z")

    def test_empty_destination_folder_is_repo_root(self) -> None:
        self.write(os.path.join(self.src, "a.txt"), "z")
        self.copy("a.txt", dest="")
        self.assertTrue(os.path.isfile(os.path.join(self.clone, "a.txt")))

    def test_no_match_errors(self) -> None:
        with self.assertRaises(SyncError):
            self.copy("missing.txt")

    def test_second_pattern_no_match_errors(self) -> None:
        self.write(os.path.join(self.src, "a.txt"))
        with self.assertRaises(SyncError):
            self.copy("a.txt nope*")

    def test_escape_via_dotdot_rejected(self) -> None:
        self.write(os.path.join(self.root, "secret.txt"))
        with self.assertRaises(SyncError):
            self.copy("../secret.txt")
        self.assertFalse(os.path.exists(os.path.join(self.clone, "secret.txt")))

    def test_escape_via_symlink_rejected(self) -> None:
        self.write(os.path.join(self.root, "secret.txt"))
        os.symlink(os.path.join(self.root, "secret.txt"), os.path.join(self.src, "link.txt"))
        with self.assertRaises(SyncError):
            self.copy("link.txt")

    def test_destination_escape_rejected(self) -> None:
        self.write(os.path.join(self.src, "a.txt"))
        with self.assertRaises(SyncError):
            self.copy("a.txt", dest="../outside")

    def test_leading_slash_destination(self) -> None:
        self.write(os.path.join(self.src, "a.txt"), "z")
        self.copy("a.txt", dest="/docs")
        self.assertEqual(self.read(os.path.join(self.clone, "docs", "a.txt")), "z")

    def test_slash_destination_is_repo_root(self) -> None:
        self.write(os.path.join(self.src, "a.txt"), "z")
        self.copy("a.txt", dest="/")
        self.assertEqual(self.read(os.path.join(self.clone, "a.txt")), "z")

    def test_dotdot_destination_with_slash_rejected(self) -> None:
        self.write(os.path.join(self.src, "a.txt"))
        with self.assertRaises(SyncError):
            self.copy("a.txt", dest="/../x")
        with self.assertRaises(SyncError):
            self.copy("a.txt", dest="../x")

    def test_symlink_in_directory_to_outside_copied_as_link(self) -> None:
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

    def test_dangling_symlink_in_directory_copied_as_link(self) -> None:
        self.write(os.path.join(self.src, "d", "a.txt"), "1")
        os.symlink("nowhere", os.path.join(self.src, "d", "dangling"))
        self.copy("d")
        copied = os.path.join(self.clone, "d", "dangling")
        self.assertTrue(os.path.islink(copied))
        self.assertEqual(os.readlink(copied), "nowhere")

    def test_directory_symlink_overwrites_existing_file_and_link(self) -> None:
        os.makedirs(os.path.join(self.src, "d"))
        os.symlink("a", os.path.join(self.src, "d", "l1"))
        os.symlink("b", os.path.join(self.src, "d", "l2"))
        self.write(os.path.join(self.clone, "d", "l1"), "old")
        os.symlink("elsewhere", os.path.join(self.clone, "d", "l2"))
        self.copy("d")
        self.assertEqual(os.readlink(os.path.join(self.clone, "d", "l1")), "a")
        self.assertEqual(os.readlink(os.path.join(self.clone, "d", "l2")), "b")

    def test_file_does_not_write_through_existing_destination_link(self) -> None:
        outside = os.path.join(self.root, "outside.txt")
        self.write(outside, "keep")
        self.write(os.path.join(self.src, "d", "a.txt"), "new")
        os.makedirs(os.path.join(self.clone, "d"))
        os.symlink(outside, os.path.join(self.clone, "d", "a.txt"))
        self.copy("d")
        self.assertEqual(self.read(outside), "keep")
        self.assertEqual(self.read(os.path.join(self.clone, "d", "a.txt")), "new")
        self.assertFalse(os.path.islink(os.path.join(self.clone, "d", "a.txt")))

    def test_top_level_symlink_within_source_copied_as_link(self) -> None:
        self.write(os.path.join(self.src, "real.txt"), "r")
        os.symlink("real.txt", os.path.join(self.src, "link.txt"))
        self.copy("link.txt")
        copied = os.path.join(self.clone, "link.txt")
        self.assertTrue(os.path.islink(copied))
        self.assertEqual(os.readlink(copied), "real.txt")

    def test_top_level_symlink_overwrites_existing_file(self) -> None:
        self.write(os.path.join(self.src, "real.txt"), "r")
        os.symlink("real.txt", os.path.join(self.src, "link.txt"))
        self.write(os.path.join(self.clone, "link.txt"), "old")
        self.copy("link.txt")
        self.assertTrue(os.path.islink(os.path.join(self.clone, "link.txt")))

    def test_missing_source_folder_errors(self) -> None:
        self.src = os.path.join(self.root, "nope")
        with self.assertRaises(SyncError) as ctx:
            self.copy("a.txt")
        self.assertIn(f"source_folder '{self.src}' is not a directory", str(ctx.exception))

    def test_source_folder_is_file_errors(self) -> None:
        self.src = os.path.join(self.root, "file.txt")
        self.write(self.src)
        with self.assertRaises(SyncError) as ctx:
            self.copy("a.txt")
        self.assertIn(f"source_folder '{self.src}' is not a directory", str(ctx.exception))

    def test_os_error_wrapped_with_path(self) -> None:
        self.write(os.path.join(self.src, "a.txt"))
        self.write(os.path.join(self.clone, "blocker"), "file")
        with self.assertRaises(SyncError) as ctx:
            self.copy("a.txt", dest="blocker/sub")
        self.assertIn("blocker", str(ctx.exception))

    def test_copy_failure_in_directory_wrapped(self) -> None:
        self.write(os.path.join(self.src, "d", "a.txt"))
        self.write(os.path.join(self.clone, "d"), "file")
        with self.assertRaises(SyncError):
            self.copy("d")


if __name__ == "__main__":
    unittest.main()
