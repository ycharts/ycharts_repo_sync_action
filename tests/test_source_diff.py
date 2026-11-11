import json
import os
import tempfile
import unittest

from repo_sync.source_diff import SourceDiff, SourceDiffError, before_sha, build_source_diff

BEFORE = "a" * 40
SHA = "b" * 40


def file_entry(name: str, patch: str = "@@ patch", status: str = "modified") -> dict[str, object]:
    return {"filename": name, "status": status, "additions": 1, "deletions": 2, "patch": patch}


class BeforeShaTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def event(self, body: str | dict) -> str:
        path = os.path.join(self.dir.name, "event.json")
        with open(path, "w") as handle:
            handle.write(body if isinstance(body, str) else json.dumps(body))
        return path

    def test_valid_before(self):
        self.assertEqual(before_sha(self.event({"before": BEFORE})), BEFORE)

    def test_none_for_unusable_events(self):
        cases = {
            "zeros": {"before": "0" * 40},
            "forced": {"before": BEFORE, "forced": True},
            "short": {"before": "abc"},
            "non_hex": {"before": "g" * 40},
            "missing_before": {},
            "not_object": [1],
        }
        for name, body in cases.items():
            with self.subTest(name):
                self.assertIsNone(before_sha(self.event(body)))

    def test_none_for_missing_or_bad_event_file(self):
        for path in (None, os.path.join(self.dir.name, "nope.json"), self.event("{not json")):
            with self.subTest(path=path):
                self.assertIsNone(before_sha(path))


class BuildSourceDiffTest(unittest.TestCase):
    def build(
        self,
        payload: dict,
        synced: tuple[str, ...] = ("a.py",),
        excludes: tuple[str, ...] = (),
    ) -> SourceDiff:
        return build_source_diff(payload, set(synced), list(excludes))

    def test_keeps_only_synced_files(self):
        payload = {"files": [file_entry("a.py", "PA"), file_entry("b.py", "PB")]}
        result = self.build(payload)
        self.assertIn("### a.py (modified, +1/-2)\nPA", result.patches)
        self.assertNotIn("b.py", result.patches)
        self.assertTrue(result.synced_files_changed)

    def test_synced_files_changed_false_when_none_match(self):
        payload = {"files": [file_entry("b.py")]}
        result = self.build(payload)
        self.assertFalse(result.synced_files_changed)
        self.assertEqual(result.patches, "")

    def test_builtin_excludes_drop_patch_but_count_as_changed(self):
        names = ["package-lock.json", "web/yarn.lock", "app.min.js", "x.map", "s.min.css"]
        payload = {"files": [file_entry(n, "SECRETPATCH") for n in names]}
        result = self.build(payload, synced=names)
        self.assertNotIn("SECRETPATCH", result.patches)
        self.assertTrue(result.synced_files_changed)
        for name in names:
            self.assertIn(f"### {name}", result.patches)

    def test_user_excludes_including_subfolder(self):
        names = ["a.py", "tests/b.snap", "docs/c.md"]
        payload = {"files": [file_entry(n, f"P-{n}") for n in names]}
        result = self.build(payload, synced=names, excludes=["*.snap", "docs/*"])
        self.assertIn("P-a.py", result.patches)
        self.assertNotIn("P-tests/b.snap", result.patches)
        self.assertNotIn("P-docs/c.md", result.patches)

    def test_partial_flag(self):
        many = [file_entry(f"f{i}.py") for i in range(300)]
        self.assertTrue(self.build({"files": many}).partial)
        self.assertFalse(self.build({"files": many[:299]}).partial)

    def test_commit_messages_capped_to_newest_50(self):
        commits = [{"sha": "x", "commit": {"message": f"m{i}"}} for i in range(60)]
        payload = {"files": [], "commits": commits}
        result = self.build(payload)
        self.assertEqual(len(result.commit_messages), 50)
        self.assertEqual(result.commit_messages[0], "m10")
        self.assertEqual(result.commit_messages[-1], "m59")

    def test_single_commit_message(self):
        payload = {"sha": "x", "commit": {"message": "hello"}, "files": []}
        self.assertEqual(self.build(payload).commit_messages, ["hello"])

    def test_non_list_files_raises(self):
        with self.assertRaises(SourceDiffError):
            self.build({"files": "nope"})
