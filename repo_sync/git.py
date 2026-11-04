import os

from repo_sync.commands import CommandError, CommandRunner, Runner


class MissingTokenError(Exception):
    def __init__(self) -> None:
        super().__init__("GITHUB_TOKEN is not set; set the API_TOKEN_GITHUB env var on the step")


def require_token() -> None:
    if not os.environ.get("GITHUB_TOKEN"):
        raise MissingTokenError()


class GitClient:
    def __init__(self, cwd: str | os.PathLike[str], runner: Runner | None = None) -> None:
        require_token()
        self.cwd = os.fspath(cwd)
        self.runner = runner or CommandRunner()

    def configure_user(self, email: str, name: str) -> None:
        self.runner(["git", "config", "--global", "user.email", email])
        self.runner(["git", "config", "--global", "user.name", name])

    def clone(self, url: str) -> None:
        # No cwd here: self.cwd is the clone target and may not exist yet.
        self.runner(["git", "clone", url, self.cwd])

    def checkout_new_branch(self, branch: str) -> None:
        self.runner(["git", "checkout", "-b", branch], cwd=self.cwd)

    def stage_all(self) -> None:
        self.runner(["git", "add", "."], cwd=self.cwd)

    def has_staged_changes(self) -> bool:
        try:
            self.runner(["git", "diff", "--cached", "--quiet"], cwd=self.cwd)
        except CommandError as exc:
            if exc.returncode == 1:
                return True
            raise
        return False

    def commit(self, message: str) -> None:
        self.runner(["git", "commit", "--message", message], cwd=self.cwd)

    def push(self, branch: str) -> None:
        self.runner(["git", "push", "-u", "origin", f"HEAD:{branch}"], cwd=self.cwd)


class GitHubClient:
    def __init__(self, cwd: str | os.PathLike[str], runner: Runner | None = None) -> None:
        require_token()
        self.cwd = os.fspath(cwd)
        self.runner = runner or CommandRunner()

    def setup_git_auth(self) -> None:
        # Not a token in the clone URL: libcurl 8.23 sends URL credentials
        # only on the first request, so the clone fails with a 401 partway through.
        self.runner(["gh", "auth", "setup-git"])

    def list_open_prs(self, title_search: str) -> list[int]:
        output = self.runner(
            [
                "gh", "pr", "list",
                "--search", f"is:pr is:open {title_search} in:title",
                "--json", "number",
                "--jq", ".[].number",
            ],
            cwd=self.cwd,
            capture=True,
        )
        return [int(line) for line in output.split() if line.strip()]

    def close_pr(self, number: int) -> None:
        self.runner(["gh", "pr", "close", str(number)], cwd=self.cwd)

    def new_pull_request(self) -> "PullRequest":
        return PullRequest(self)


class PullRequest:
    title: str | None = None
    body: str | None = None
    base: str | None = None
    head: str | None = None

    def __init__(self, github: GitHubClient) -> None:
        self.github = github
        self.reviewers = ""

    def create(self) -> None:
        required = {
            "title": self.title,
            "body": self.body,
            "base": self.base,
            "head": self.head,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"pull request is missing: {', '.join(missing)}")
        args = [
            "gh", "pr", "create",
            "--title", str(self.title),
            "--body", str(self.body),
            "--base", str(self.base),
            "--head", str(self.head),
        ]
        if self.reviewers:
            args += ["-r", self.reviewers]
        self.github.runner(args, cwd=self.github.cwd)

    def add_label(self, label: str) -> None:
        # gh pr edit takes a PR number or its head branch; this passes the branch.
        self.github.runner(["gh", "pr", "edit", str(self.head), "--add-label", label], cwd=self.github.cwd)
