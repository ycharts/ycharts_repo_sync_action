import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Protocol


class CommandError(Exception):
    def __init__(self, command: Sequence[str], returncode: int) -> None:
        self.command: list[str] = list(command)
        self.returncode: int = returncode
        super().__init__(f"command {' '.join(self.command)!r} failed with exit code {returncode}")


class Runner(Protocol):
    def __call__(self, args: Sequence[str], cwd: str | None = None, capture: bool = False) -> str: ...


class CommandRunner:
    """Runs every git and gh command, so tests can swap in a fake and run nothing.

    The child env gets GITHUB_TOKEN from the token argument,
    or else from API_TOKEN_GITHUB in base_env.
    A non-zero exit raises CommandError, as does a missing binary (exit code 127).
    Output is flushed before each command so the action's log lines
    stay in order with the command's output.
    """

    def __init__(self, token: str | None = None, base_env: Mapping[str, str] | None = None) -> None:
        self.env: dict[str, str] = dict(os.environ if base_env is None else base_env)
        token = token or self.env.get("API_TOKEN_GITHUB")
        # Pass the token through the env, never args:
        # CommandError messages include the args and are printed in ::error:: lines.
        if token:
            self.env["GITHUB_TOKEN"] = token

    def __call__(self, args: Sequence[str], cwd: str | None = None, capture: bool = False) -> str:
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            result = subprocess.run(
                list(args),
                cwd=cwd,
                env=self.env,
                stdout=subprocess.PIPE if capture else None,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CommandError(args, 127) from exc
        if result.returncode != 0:
            raise CommandError(args, result.returncode)
        return result.stdout if capture else ""
