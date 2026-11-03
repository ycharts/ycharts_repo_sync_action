#!/usr/bin/env python3
import os
import sys

from repo_sync.commands import CommandError
from repo_sync.git import GitClient, GitHubClient
from repo_sync.settings import ConfigError, Settings
from repo_sync.sync import RepoSync, SyncError


def escape(message):
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(env=None, runner=None, clock=None):
    env = os.environ if env is None else env
    try:
        settings = Settings.from_env(env, now=clock)
        if runner is None:
            # The default CommandRunner reads API_TOKEN_GITHUB from os.environ, not env.
            sync = RepoSync(settings)
        else:
            sync = RepoSync(settings, GitClient(runner), GitHubClient(runner))
        sync.run()
    except ConfigError as exc:
        for message in exc.messages:
            print(f"::error::{escape(message)}", flush=True)
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
