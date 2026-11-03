import os
import tempfile
import unittest

from repo_sync.files import CopyError, copy_files
from repo_sync.settings import Settings
from tests.helpers import base_env


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
        copy_files(settings.source_folder, settings.source_files, settings.destination_folder, self.clone)

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
        with self.assertRaises(CopyError):
            self.copy("missing.txt")

    def test_second_pattern_no_match_errors(self):
        self.write(os.path.join(self.src, "a.txt"))
        with self.assertRaises(CopyError):
            self.copy("a.txt nope*")

    def test_escape_via_dotdot_rejected(self):
        self.write(os.path.join(self.root, "secret.txt"))
        with self.assertRaises(CopyError):
            self.copy("../secret.txt")
        self.assertFalse(os.path.exists(os.path.join(self.clone, "secret.txt")))

    def test_escape_via_symlink_rejected(self):
        self.write(os.path.join(self.root, "secret.txt"))
        os.symlink(os.path.join(self.root, "secret.txt"), os.path.join(self.src, "link.txt"))
        with self.assertRaises(CopyError):
            self.copy("link.txt")

    def test_destination_escape_rejected(self):
        self.write(os.path.join(self.src, "a.txt"))
        with self.assertRaises(CopyError):
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
        with self.assertRaises(CopyError):
            self.copy("a.txt", dest="/../x")
        with self.assertRaises(CopyError):
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
        with self.assertRaises(CopyError) as ctx:
            self.copy("a.txt")
        self.assertIn(f"source_folder '{self.src}' is not a directory", str(ctx.exception))

    def test_source_folder_is_file_errors(self):
        self.src = os.path.join(self.root, "file.txt")
        self.write(self.src)
        with self.assertRaises(CopyError) as ctx:
            self.copy("a.txt")
        self.assertIn(f"source_folder '{self.src}' is not a directory", str(ctx.exception))

    def test_os_error_wrapped_with_path(self):
        self.write(os.path.join(self.src, "a.txt"))
        self.write(os.path.join(self.clone, "blocker"), "file")
        with self.assertRaises(CopyError) as ctx:
            self.copy("a.txt", dest="blocker/sub")
        self.assertIn("blocker", str(ctx.exception))

    def test_copy_failure_in_directory_wrapped(self):
        self.write(os.path.join(self.src, "d", "a.txt"))
        self.write(os.path.join(self.clone, "d"), "file")
        with self.assertRaises(CopyError):
            self.copy("d")


if __name__ == "__main__":
    unittest.main()
