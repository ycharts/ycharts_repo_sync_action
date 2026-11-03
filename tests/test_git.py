import unittest

from repo_sync.commands import CommandError
from repo_sync.git import GitClient
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


if __name__ == "__main__":
    unittest.main()
