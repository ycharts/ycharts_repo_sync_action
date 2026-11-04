import subprocess
import sys
from collections.abc import Sequence
from typing import Protocol


class CommandError(Exception):
    """A command exited non-zero, or its binary was missing (exit code 127)."""

    def __init__(self, command: Sequence[str], returncode: int) -> None:
        self.command = list(command)
        self.returncode = returncode
        super().__init__(f"command {' '.join(self.command)!r} failed with exit code {returncode}")


class Runner(Protocol):
    """Runs a command, raising CommandError if it fails.

    The arguments go straight to the program, never through a shell.
    With capture set, returns the command's stdout;
    otherwise the output goes to the log and the call returns "".
    """

    def __call__(self, args: Sequence[str], cwd: str | None = None, capture: bool = False) -> str: ...


class CommandRunner:
    """Runs every git and gh command, so tests can swap in a fake and run nothing.

    A non-zero exit raises CommandError, as does a missing binary (exit code 127).
    Output is flushed before each command so the action's log lines
    stay in order with the command's output.
    """

    def __call__(self, args: Sequence[str], cwd: str | None = None, capture: bool = False) -> str:
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            result = subprocess.run(
                list(args),
                cwd=cwd,
                stdout=subprocess.PIPE if capture else None,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CommandError(args, 127) from exc
        if result.returncode != 0:
            raise CommandError(args, result.returncode)
        return result.stdout if capture else ""
