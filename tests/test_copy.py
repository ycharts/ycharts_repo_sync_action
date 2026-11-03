import os
import tempfile
import unittest

from sync import RepoSync, Settings, SyncError
from tests.helpers import FakeRunner, base_env


class CopyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.src = os.path.join(self.root, "src")
        self.clone = os.path.join(self.root, "clone")
        os.makedirs(self.src)
        os.makedirs(self.clone)

    def write(self, path, content="x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def copy(self, files, dest=""):
        settings = Settings.from_env(
            base_env(INPUT_SOURCE_FOLDER=self.src, INPUT_SOURCE_FILES=files, INPUT_DESTINATION_FOLDER=dest)
        )
        RepoSync(settings, None, None).copy_files(self.clone)

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


if __name__ == "__main__":
    unittest.main()
