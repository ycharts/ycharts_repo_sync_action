import os
import subprocess
import sys


class CommandError(Exception):
    def __init__(self, command, returncode):
        self.command = list(command)
        self.returncode = returncode
        super().__init__(f"command {' '.join(self.command)!r} failed with exit code {returncode}")


class CommandRunner:
    """Runs every git and gh command, so tests can swap in a fake and run nothing.

    The child env gets GITHUB_TOKEN from the token argument,
    or else from API_TOKEN_GITHUB in base_env.
    A non-zero exit raises CommandError, as does a missing binary (exit code 127).
    Output is flushed before each command so the action's log lines
    stay in order with the command's output.
    """

    def __init__(self, token=None, base_env=None):
        self.env = dict(os.environ if base_env is None else base_env)
        token = token or self.env.get("API_TOKEN_GITHUB")
        # Pass the token through the env, never args:
        # CommandError messages include the args and are printed in ::error:: lines.
        if token:
            self.env["GITHUB_TOKEN"] = token

    def __call__(self, args, cwd=None, capture=False):
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
