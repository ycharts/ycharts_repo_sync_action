import contextlib
import io
import os
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from typing import TypedDict

from repo_sync.commands import CommandError, Runner
from repo_sync.git import GitClient, GitHubClient
from repo_sync.settings import Settings
from repo_sync.sync import RepoSync


class Call(TypedDict):
    args: list[str]
    cwd: str | None
    capture: bool


class FakeRunner:
    def __init__(
        self,
        outputs: Mapping[tuple[str, ...], str] | None = None,
        failures: Mapping[tuple[str, ...], int] | None = None,
    ) -> None:
        self.calls: list[Call] = []
        self.outputs: Mapping[tuple[str, ...], str] = outputs or {}
        self.failures: Mapping[tuple[str, ...], int] = failures or {}

    def __call__(self, args: Sequence[str], cwd: str | None = None, capture: bool = False) -> str:
        args = list(args)
        self.calls.append({"args": args, "cwd": cwd, "capture": capture})
        for prefix, code in self.failures.items():
            if args[: len(prefix)] == list(prefix):
                raise CommandError(args, code)
        if capture:
            for prefix, output in self.outputs.items():
                if args[: len(prefix)] == list(prefix):
                    return output
            return ""
        return ""

    @property
    def commands(self) -> list[list[str]]:
        return [call["args"] for call in self.calls]


def base_env(**overrides: str) -> dict[str, str]:
    env: dict[str, str] = {
        "INPUT_SOURCE_FOLDER": "src",
        "INPUT_SOURCE_FILES": "a.txt",
        "INPUT_DESTINATION_REPO": "org/repo",
        "INPUT_DESTINATION_HEAD_BRANCH": "sync-branch",
        "INPUT_USER_EMAIL": "me@example.com",
        "INPUT_USER_NAME": "me",
        "GITHUB_REPOSITORY": "org/source",
        "GITHUB_SHA": "abc123",
        "API_TOKEN_GITHUB": "secret-token",
    }
    env.update(overrides)
    return env


class FlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.src = os.path.join(self.root, "src")
        os.makedirs(self.src)
        with open(os.path.join(self.src, "a.txt"), "w") as f:
            f.write("a")

    def env(self, **overrides: str) -> dict[str, str]:
        return base_env(INPUT_SOURCE_FOLDER=self.src, **overrides)

    def sync(self, runner: Runner, **overrides: str) -> tuple[str, str]:
        settings = Settings.from_env(self.env(**overrides))
        clone = os.path.join(self.root, "clone")
        os.makedirs(clone, exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            RepoSync(settings, GitClient(runner), GitHubClient(runner)).run(clone_dir=clone)
        return clone, out.getvalue()
