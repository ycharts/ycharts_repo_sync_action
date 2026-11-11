import unittest

from repo_sync.pr_body import MAX_BODY_BYTES, build_body, build_title
from repo_sync.previous_prs import PreviousPr


def pr(number, body, head_branch="sync/x"):
    return PreviousPr(
        number=number,
        title=f"t{number}",
        body=body,
        head_branch=head_branch,
        created_at="2024-01-01T00:00:00Z",
    )


class BuildTitleTest(unittest.TestCase):
    def test_with_generated_title(self):
        self.assertEqual(build_title("main", "Add thing"), "main: Add thing")

    def test_without_generated_title(self):
        self.assertEqual(build_title("main", None), "main")
        self.assertEqual(build_title("main", ""), "main")


class BuildBodyTest(unittest.TestCase):
    def test_picks_newest_real_body(self):
        prs = [pr(3, "newest"), pr(2, "older")]
        self.assertEqual(build_body(prs, "S", True, "b"), "newest\n\n---\n\nS")

    def test_skips_blank_and_branch_name_bodies(self):
        prs = [
            pr(4, ""),
            pr(3, " \n\t "),
            pr(2, "  sync/x \n", head_branch=" sync/x"),
            pr(1, "real"),
        ]
        self.assertEqual(build_body(prs, "S", False, "b"), "real\n\n---\n\nS")

    def test_branch_name_body_of_other_branch_is_real(self):
        prs = [pr(1, "other", head_branch="sync/x")]
        self.assertEqual(build_body(prs, "S", True, "b"), "other\n\n---\n\nS")

    def test_separator_layout_trims_trailing_whitespace(self):
        prs = [pr(1, "body\n\n")]
        self.assertEqual(build_body(prs, "S", True, "b"), "body\n\n---\n\nS")

    def test_size_cap_boundary(self):
        section = "S"
        overhead = len("\n\n---\n\n") + len(section)
        at_cap = pr(7, "a" * (MAX_BODY_BYTES - overhead))
        over_cap = pr(7, "a" * (MAX_BODY_BYTES - overhead + 1))
        self.assertEqual(
            len(build_body([at_cap], section, True, "b").encode()), MAX_BODY_BYTES
        )
        self.assertEqual(
            build_body([over_cap], section, True, "b"), "Earlier updates: #7\n\nS"
        )

    def test_size_cap_counts_utf8_bytes(self):
        prs = [pr(9, "é" * (MAX_BODY_BYTES // 2))]
        self.assertEqual(build_body(prs, "S", True, "b"), "Earlier updates: #9\n\nS")

    def test_falls_back_to_section_when_generation_succeeded(self):
        self.assertEqual(build_body([], "S", True, "b"), "S")
        self.assertEqual(build_body([pr(1, "b", head_branch="b")], "S", True, "b"), "S")

    def test_falls_back_to_branch_when_generation_failed(self):
        self.assertEqual(build_body([], "Source commit: x", False, "feat"), "feat")


if __name__ == "__main__":
    unittest.main()
