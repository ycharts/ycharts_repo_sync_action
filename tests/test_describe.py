import io
import json
import unittest
import urllib.error

from repo_sync.describe import (
    API_URL,
    MAX_DIFF_BYTES,
    MAX_MESSAGES_BYTES,
    MAX_PREVIOUS_PRS_BYTES,
    MAX_STAT_BYTES,
    DescribeError,
    PrDescriber,
)
from repo_sync.git import PreviousPr
from repo_sync.source_diff import SourceDiff

KEY = "sk-ant-test-secret-1234567890"


def make_diff(patches="### a.txt (modified, +1/-0)\n@@ UNIQUE_PATCH", messages=("--- abc1234\nfix thing",), partial=False, changed=True):
    return SourceDiff(patches=patches, commit_messages=list(messages), partial=partial, synced_files_changed=changed)


def make_pr(number=1, title="Old title", body="Old body"):
    return PreviousPr(number=number, title=title, body=body, head_branch="b", created_at="2026-01-01T00:00:00Z")


def response(text, stop="end_turn"):
    return json.dumps({"stop_reason": stop, "content": [{"type": "text", "text": text}]}).encode()


GOOD = response(json.dumps({"title": "Add widgets", "body": "## Summary\n\nAdds widgets."}))


class FakePost:
    def __init__(self, result=GOOD, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        if self.error:
            raise self.error
        return self.result

    @property
    def request(self):
        return json.loads(self.calls[0][2])

    @property
    def prompt(self):
        return self.request["messages"][0]["content"]


def run(post, **overrides):
    kwargs = dict(previous_prs=[], source_diff=make_diff(), diff_stat=" a.txt | 1 +")
    api_key = overrides.pop("api_key", KEY)
    model = overrides.pop("model", "claude-opus-5-5")
    kwargs.update(overrides)
    return PrDescriber(model, api_key, post).describe(**kwargs)


class GenerateDescriptionTest(unittest.TestCase):
    def test_request_shape(self):
        post = FakePost()
        result = run(post)
        url, headers, _ = post.calls[0]
        self.assertEqual(url, API_URL)
        self.assertEqual(headers["x-api-key"], KEY)
        self.assertEqual(headers["anthropic-beta"], "server-side-fallback-2026-07-01")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        request = post.request
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertEqual(request["fallbacks"], "default")
        self.assertEqual(request["output_config"]["effort"], "low")
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(request["output_config"]["format"]["schema"]["required"], ["title", "body"])
        self.assertNotIn(KEY, json.dumps(request))
        self.assertIn("UNIQUE_PATCH", post.prompt)
        self.assertIn("fix thing", post.prompt)
        self.assertIn(" a.txt | 1 +", post.prompt)
        self.assertEqual(result.title, "Add widgets")
        self.assertEqual(result.body, "## Summary\n\nAdds widgets.")

    def test_previous_prs_included(self):
        post = FakePost()
        run(post, previous_prs=[make_pr(7, "Newer", "newer body"), make_pr(3, "Older", "older body")])
        self.assertIn("### Pull request #7: Newer\nnewer body", post.prompt)
        self.assertLess(post.prompt.index("#7"), post.prompt.index("#3"))
        self.assertNotIn("truncated and is incomplete; the oldest", post.prompt)

    def test_no_previous_prs_section_when_empty(self):
        post = FakePost()
        run(post)
        self.assertNotIn("<previous_pull_requests>", post.prompt)

    def test_previous_prs_capped_drops_oldest(self):
        post = FakePost()
        run(post, previous_prs=[make_pr(7, "Newer", "x" * MAX_PREVIOUS_PRS_BYTES), make_pr(3, "Older", "older body")])
        self.assertIn("#7", post.prompt)
        self.assertNotIn("#3", post.prompt)
        self.assertIn("the oldest pull requests are missing", post.prompt)

    def test_messages_and_stat_capped(self):
        post = FakePost()
        run(post, source_diff=make_diff(messages=["m" * (MAX_MESSAGES_BYTES + 500)]), diff_stat="s" * (MAX_STAT_BYTES + 500))
        self.assertNotIn("m" * (MAX_MESSAGES_BYTES + 1), post.prompt)
        self.assertNotIn("s" * (MAX_STAT_BYTES + 1), post.prompt)

    def test_large_diff_capped_flagged_and_noted(self):
        post = FakePost()
        result = run(post, source_diff=make_diff(patches="d" * (MAX_DIFF_BYTES + 1000)))
        self.assertNotIn("d" * (MAX_DIFF_BYTES + 1), post.prompt)
        self.assertIn("The diff below was truncated and is incomplete.", post.prompt)
        self.assertTrue(result.body.endswith("_Generated from a partial diff (first 200 KB of 201 KB)._"))

    def test_small_diff_has_no_note(self):
        post = FakePost()
        result = run(post)
        self.assertNotIn("truncated", post.prompt)
        self.assertNotIn("partial diff", result.body)

    def test_partial_note_and_prompt(self):
        post = FakePost()
        result = run(post, source_diff=make_diff(patches="", partial=True, changed=False))
        self.assertIn("incomplete", post.prompt)
        self.assertIn("first 300 files", post.prompt)
        self.assertNotIn("changed none of the synced files", post.prompt)
        self.assertIn("partial diff (GitHub returned only the first 300 files of the push)", result.body)

    def test_no_synced_files_changed(self):
        post = FakePost()
        result = run(post, source_diff=make_diff(patches="", changed=False))
        self.assertIn("The latest source push changed none of the synced files.", post.prompt)
        self.assertNotIn("partial diff", result.body)

    def test_title_is_first_line_trimmed(self):
        post = FakePost(response(json.dumps({"title": "  Add widgets \nextra", "body": "b"})))
        self.assertEqual(run(post).title, "Add widgets")

    def test_missing_key(self):
        post = FakePost()
        for key in (None, ""):
            with self.assertRaises(DescribeError):
                run(post, api_key=key)
        self.assertEqual(post.calls, [])

    def test_http_error_hides_key(self):
        payload = json.dumps({"error": {"message": "bad request"}}).encode()
        error = urllib.error.HTTPError(API_URL, 400, "Bad", {}, io.BytesIO(payload))
        with self.assertRaises(DescribeError) as ctx:
            run(FakePost(error=error))
        self.assertIn("400", str(ctx.exception))
        self.assertIn("bad request", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))

    def test_generic_failure_hides_key(self):
        for error in (TimeoutError("timed out"), urllib.error.URLError("refused"), ValueError(KEY)):
            with self.assertRaises(DescribeError) as ctx:
                run(FakePost(error=error))
            self.assertNotIn(KEY, str(ctx.exception))

    def test_url_error_reports_reason_capped(self):
        with self.assertRaises(DescribeError) as ctx:
            run(FakePost(error=urllib.error.URLError("name resolution failed")))
        self.assertIn("URLError: name resolution failed", str(ctx.exception))
        with self.assertRaises(DescribeError) as ctx:
            run(FakePost(error=ConnectionResetError("reset by peer")))
        self.assertIn("ConnectionResetError: reset by peer", str(ctx.exception))
        with self.assertRaises(DescribeError) as ctx:
            run(FakePost(error=urllib.error.URLError("x" * 1000)))
        self.assertLess(len(str(ctx.exception)), 400)
        with self.assertRaises(DescribeError) as ctx:
            run(FakePost(error=ValueError("anything")))
        self.assertEqual(str(ctx.exception), "API request failed: ValueError")

    def test_bad_responses(self):
        cases = [
            b"not json",
            b"[]",
            response("not json"),
            response("[]"),
            response(json.dumps({"title": "t", "body": "b"}), stop="max_tokens"),
            response(json.dumps({"title": "t"})),
            response(json.dumps({"title": 5, "body": "b"})),
            response(json.dumps({"title": "   ", "body": "b"})),
            json.dumps({"stop_reason": "end_turn", "content": []}).encode(),
        ]
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(DescribeError) as ctx:
                    run(FakePost(body))
                self.assertNotIn(KEY, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
