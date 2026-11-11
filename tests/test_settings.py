import datetime
import unittest

from repo_sync.settings import ConfigError, Settings
from tests.helpers import base_env


def fixed_clock() -> datetime.datetime:
    return datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)


class SettingsTest(unittest.TestCase):
    def test_valid_defaults(self):
        s = Settings.from_env(base_env())
        self.assertEqual(s.base_branch, "main")
        self.assertEqual(s.destination_folder, "")
        self.assertEqual(s.head_branch, "sync-branch")
        self.assertEqual(s.head_branch_prefix, "sync-branch")
        self.assertEqual(s.reviewers, "")
        self.assertEqual(s.labels, [])

    def test_each_required_input_missing(self):
        for name in [
            "INPUT_SOURCE_FOLDER",
            "INPUT_SOURCE_FILES",
            "INPUT_DESTINATION_REPO",
            "INPUT_DESTINATION_HEAD_BRANCH",
            "INPUT_USER_EMAIL",
            "INPUT_USER_NAME",
        ]:
            for value in (None, "", "   "):
                env = base_env()
                if value is None:
                    del env[name]
                else:
                    env[name] = value
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ConfigError):
                        Settings.from_env(env)

    def test_main_and_master_rejected(self):
        for branch in ("main", "master"):
            with self.subTest(branch=branch):
                with self.assertRaises(ConfigError) as ctx:
                    Settings.from_env(base_env(INPUT_DESTINATION_HEAD_BRANCH=branch))
                self.assertIn("main", ctx.exception.messages[0])

    def test_base_branch_override(self):
        s = Settings.from_env(base_env(INPUT_DESTINATION_BASE_BRANCH="develop"))
        self.assertEqual(s.base_branch, "develop")

    def test_timestamp_on(self):
        s = Settings.from_env(base_env(INPUT_TIMESTAMP_HEAD_BRANCH="true"), now=fixed_clock)
        self.assertEqual(s.head_branch, "sync-branch-20260102030405")
        self.assertEqual(s.head_branch_prefix, "sync-branch")

    def test_timestamp_case_insensitive(self):
        s = Settings.from_env(base_env(INPUT_TIMESTAMP_HEAD_BRANCH="TRUE"), now=fixed_clock)
        self.assertEqual(s.head_branch, "sync-branch-20260102030405")

    def test_timestamp_off(self):
        for value in ("false", "", "no"):
            s = Settings.from_env(base_env(INPUT_TIMESTAMP_HEAD_BRANCH=value), now=fixed_clock)
            self.assertEqual(s.head_branch, "sync-branch")

    def test_source_files_split_on_whitespace(self):
        s = Settings.from_env(base_env(INPUT_SOURCE_FILES="a.txt  b/*.md\nc"))
        self.assertEqual(s.source_files, ["a.txt", "b/*.md", "c"])

    def test_labels_trimmed(self):
        s = Settings.from_env(base_env(INPUT_PULL_REQUEST_LABELS=" a , ,b,, "))
        self.assertEqual(s.labels, ["a", "b"])

    def test_pr_description_defaults(self):
        s = Settings.from_env(base_env())
        self.assertFalse(s.generate_pr_description)
        self.assertEqual(s.anthropic_model, "claude-opus-5-5")
        self.assertEqual(s.pr_description_exclude, [])
        self.assertIsNone(s.anthropic_api_key)
        self.assertIsNone(s.github_event_path)

    def test_generate_pr_description_parsing(self):
        for value, expected in (("true", True), ("TRUE", True), ("false", False), ("", False), ("yes", False)):
            with self.subTest(value=value):
                s = Settings.from_env(base_env(INPUT_GENERATE_PR_DESCRIPTION=value))
                self.assertEqual(s.generate_pr_description, expected)

    def test_anthropic_model_override(self):
        s = Settings.from_env(base_env(INPUT_ANTHROPIC_MODEL=" claude-x "))
        self.assertEqual(s.anthropic_model, "claude-x")

    def test_pr_description_exclude_trimmed(self):
        s = Settings.from_env(base_env(INPUT_PR_DESCRIPTION_EXCLUDE=" *.lock , ,docs/**,, "))
        self.assertEqual(s.pr_description_exclude, ["*.lock", "docs/**"])

    def test_api_key_and_event_path(self):
        s = Settings.from_env(
            base_env(ANTHROPIC_API_KEY="sk-secret", GITHUB_EVENT_PATH="/tmp/event.json")
        )
        self.assertEqual(s.anthropic_api_key, "sk-secret")
        self.assertEqual(s.github_event_path, "/tmp/event.json")
        self.assertNotIn("sk-secret", repr(s))

    def test_blank_api_key_is_none(self):
        s = Settings.from_env(base_env(ANTHROPIC_API_KEY="  ", GITHUB_EVENT_PATH=""))
        self.assertIsNone(s.anthropic_api_key)
        self.assertIsNone(s.github_event_path)


if __name__ == "__main__":
    unittest.main()
