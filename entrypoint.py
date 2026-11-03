#!/usr/bin/env python3
import os
import sys
from collections.abc import Callable, Mapping
from datetime import datetime

from repo_sync.commands import CommandError, Runner
from repo_sync.git import GitClient, GitHubClient, MissingTokenError
from repo_sync.settings import ConfigError, Settings
from repo_sync.sync import RepoSync, SyncError


def escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(
    env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    clock: Callable[[], datetime] | None = None,
) -> int:
    env = os.environ if env is None else env
    if env.get("API_TOKEN_GITHUB"):
        os.environ["GITHUB_TOKEN"] = env["API_TOKEN_GITHUB"]
    try:
        settings = Settings.from_env(env, now=clock)
        if runner is None:
            sync = RepoSync(settings)
        else:
            sync = RepoSync(settings, GitClient(runner), GitHubClient(runner))
        sync.run()
    except ConfigError as exc:
        for message in exc.messages:
            print(f"::error::{escape(message)}", flush=True)
        return 1
    except MissingTokenError as exc:
        print(f"::error::{escape(str(exc))}", flush=True)
        return 1
    except CommandError as exc:
        print(f"::error::{escape(str(exc))}", flush=True)
        return exc.returncode or 1
    except SyncError as exc:
        print(f"::error::{escape(str(exc))}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
