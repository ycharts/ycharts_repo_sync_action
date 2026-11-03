import unittest

from repo_sync.github import GitHubClient
from tests.helpers import FakeRunner


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


if __name__ == "__main__":
    unittest.main()
