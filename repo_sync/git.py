import os

from repo_sync.commands import CommandError, CommandRunner, Runner


class GitClient:
    def __init__(self, runner: Runner | None = None) -> None:
        self.runner: Runner = runner or CommandRunner()

    def configure_user(self, email: str, name: str) -> None:
        self.runner(["git", "config", "--global", "user.email", email])
        self.runner(["git", "config", "--global", "user.name", name])

    def clone(self, url: str, destination: str | os.PathLike[str]) -> None:
        self.runner(["git", "clone", url, str(destination)])

    def checkout_new_branch(self, cwd: str, branch: str) -> None:
        self.runner(["git", "checkout", "-b", branch], cwd=cwd)

    def stage_all(self, cwd: str) -> None:
        self.runner(["git", "add", "."], cwd=cwd)

    def has_staged_changes(self, cwd: str) -> bool:
        try:
            self.runner(["git", "diff", "--cached", "--quiet"], cwd=cwd)
        except CommandError as exc:
            if exc.returncode == 1:
                return True
            raise
        return False

    def commit(self, cwd: str, message: str) -> None:
        self.runner(["git", "commit", "--message", message], cwd=cwd)

    def push(self, cwd: str, branch: str) -> None:
        self.runner(["git", "push", "-u", "origin", f"HEAD:{branch}"], cwd=cwd)


class GitHubClient:
    def __init__(self, runner: Runner | None = None) -> None:
        self.runner: Runner = runner or CommandRunner()

    def setup_git_auth(self) -> None:
        # Not a token in the clone URL: libcurl 8.23 sends URL credentials
        # only on the first request, so the clone fails with a 401 partway through.
        self.runner(["gh", "auth", "setup-git"])

    def list_open_prs(self, cwd: str, title_search: str) -> list[int]:
        output = self.runner(
            [
                "gh", "pr", "list",
                "--search", f"is:pr is:open {title_search} in:title",
                "--json", "number",
                "--jq", ".[].number",
            ],
            cwd=cwd,
            capture=True,
        )
        return [int(line) for line in output.split() if line.strip()]

    def close_pr(self, cwd: str, number: int) -> None:
        self.runner(["gh", "pr", "close", str(number)], cwd=cwd)

    def create_pr(
        self, cwd: str, title: str, body: str, base: str, head: str, reviewers: str = ""
    ) -> None:
        args = [
            "gh", "pr", "create",
            "--title", title,
            "--body", body,
            "--base", base,
            "--head", head,
        ]
        if reviewers:
            args += ["-r", reviewers]
        self.runner(args, cwd=cwd)

    def add_label(self, cwd: str, pr: str, label: str) -> None:
        self.runner(["gh", "pr", "edit", pr, "--add-label", label], cwd=cwd)
