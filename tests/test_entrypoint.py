import contextlib
import io
import os
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from unittest import mock

import entrypoint
from repo_sync.commands import Runner
from tests.helpers import FakeRunner, FlowTestCase, base_env


class EntrypointTest(FlowTestCase):
    def main(self, runner: Runner, env: Mapping[str, str]) -> tuple[int, str]:
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
        code, _ = self.main(runner, self.env(INPUT_DESTINATION_HEAD_BRANCH="master"))
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

    def test_missing_source_folder_failure(self):
        code, out = self.main(FakeRunner(), base_env(INPUT_SOURCE_FOLDER=os.path.join(self.root, "nope")))
        self.assertEqual(code, 1)
        self.assertIn("::error::", out)
        self.assertIn("is not a directory", out)

    def test_source_folder_is_file_failure(self):
        with tempfile.NamedTemporaryFile() as f:
            code, out = self.main(FakeRunner(), base_env(INPUT_SOURCE_FOLDER=f.name))
        self.assertEqual(code, 1)
        self.assertIn("::error::", out)
        self.assertIn("is not a directory", out)

    def test_copy_os_error_failure(self):
        # main() clones into its own temp dir, so the blocking file
        # can only be created once the clone command names that dir.
        class BlockingRunner(FakeRunner):
            def __call__(self, args: Sequence[str], cwd: str | None = None, capture: bool = False) -> str:
                if args[:2] == ["git", "clone"]:
                    with open(os.path.join(args[3], "blocker"), "w") as f:
                        f.write("file")
                return super().__call__(args, cwd=cwd, capture=capture)

        runner = BlockingRunner()
        code, out = self.main(runner, self.env(INPUT_DESTINATION_FOLDER="blocker/sub"))
        self.assertEqual(code, 1)
        self.assertIn("::error::failed to copy", out)
        self.assertIn("blocker", out)
        self.assertNotIn(["git", "checkout", "-b", "sync-branch"], runner.commands)

    def test_exports_api_token_as_github_token(self):
        os.environ.pop("GITHUB_TOKEN")
        code, _ = self.main(FakeRunner(), self.env(API_TOKEN_GITHUB="exported"))
        self.assertEqual(code, 0)
        self.assertEqual(os.environ["GITHUB_TOKEN"], "exported")

    def test_empty_api_token_keeps_existing_github_token(self):
        code, _ = self.main(FakeRunner(), self.env(API_TOKEN_GITHUB=""))
        self.assertEqual(code, 0)
        self.assertEqual(os.environ["GITHUB_TOKEN"], "test-token")

    def test_missing_token_fails_before_any_command(self):
        os.environ.pop("GITHUB_TOKEN")
        env = self.env()
        del env["API_TOKEN_GITHUB"]
        runner = FakeRunner()
        code, out = self.main(runner, env)
        self.assertEqual(code, 1)
        self.assertIn("::error::", out)
        self.assertIn("API_TOKEN_GITHUB", out)
        self.assertEqual(runner.calls, [])

    def test_token_value_never_printed(self):
        code, out = self.main(FakeRunner(failures={("git", "clone"): 128}), self.env(API_TOKEN_GITHUB="s3cr3t"))
        self.assertEqual(code, 128)
        self.assertNotIn("s3cr3t", out)

    def test_no_runner_builds_repo_sync_from_settings_only(self):
        with mock.patch("entrypoint.RepoSync") as repo_sync:
            code = entrypoint.main(env=self.env())
        self.assertEqual(code, 0)
        repo_sync.assert_called_once()
        args, kwargs = repo_sync.call_args
        self.assertEqual(len(args), 2)
        self.assertIsNone(args[1])
        self.assertEqual(kwargs, {})
        repo_sync.return_value.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
