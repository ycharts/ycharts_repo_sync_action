import contextlib
import io
import os
import tempfile
import unittest

import entrypoint
from tests.helpers import FakeRunner, FlowTestCase, base_env


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
            def __call__(self, args, cwd=None, capture=False):
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


if __name__ == "__main__":
    unittest.main()
