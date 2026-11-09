import json
import os
import re
from typing import Any

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

    def remote_branch_exists(self, branch: str) -> bool:
        # Reads the remote-tracking ref from the clone, without contacting the remote.
        # --force-with-lease leases against this same ref.
        try:
            self.runner(
                ["git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                cwd=self.cwd,
            )
        except CommandError as exc:
            if exc.returncode == 1:
                return False
            raise
        return True

    def push(self, branch: str, force_with_lease: bool = False) -> None:
        args = ["git", "push"]
        if force_with_lease:
            args.append("--force-with-lease")
        args += ["-u", "origin", f"HEAD:{branch}"]
        self.runner(args, cwd=self.cwd)


class GitHubClient:
    def __init__(self, cwd: str | os.PathLike[str], runner: Runner | None = None) -> None:
        require_token()
        self.cwd = os.fspath(cwd)
        self.runner = runner or CommandRunner()

    def setup_git_auth(self) -> None:
        # Not a token in the clone URL: libcurl 8.23 sends URL credentials
        # only on the first request, so the clone fails with a 401 partway through.
        self.runner(["gh", "auth", "setup-git"])

    def find_open_pr(self, head: str) -> int | None:
        output = self.runner(
            [
                "gh", "pr", "list",
                "--head", head,
                "--state", "open",
                "--json", "number,isCrossRepository",
            ],
            cwd=self.cwd,
            capture=True,
        )
        # --head also matches fork PRs whose branch has the same name.
        for pr in self._parse_prs(output):
            if not pr["isCrossRepository"]:
                return int(pr["number"])
        return None

    def list_open_prs(self, prefix: str, current_head: str) -> list[int]:
        # The title search only narrows candidates; titles can be edited,
        # so the head branch decides which PRs this action opened.
        # gh returns 30 results unless given --limit.
        output = self.runner(
            [
                "gh", "pr", "list",
                "--search", f"is:pr is:open {prefix} in:title",
                "--limit", "100",
                "--json", "number,headRefName,isCrossRepository",
            ],
            cwd=self.cwd,
            capture=True,
        )
        timestamped = re.compile(rf"{re.escape(prefix)}-\d{{14}}")
        numbers = []
        for pr in self._parse_prs(output):
            head = pr["headRefName"]
            if pr["isCrossRepository"] or head == current_head:
                continue
            if head == prefix or timestamped.fullmatch(head):
                numbers.append(int(pr["number"]))
        return numbers

    @staticmethod
    def _parse_prs(output: str) -> list[dict[str, Any]]:
        if not output.strip():
            return []
        return json.loads(output)

    def close_pr(self, number: int) -> None:
        self.runner(["gh", "pr", "close", str(number)], cwd=self.cwd)

    def new_pull_request(self) -> PullRequest:
        return PullRequest(self)

    def _create_pull_request(self, pr: PullRequest) -> None:
        title, body, base, head = pr.title, pr.body, pr.base, pr.head
        if title is None or body is None or base is None or head is None:
            required = {"title": title, "body": body, "base": base, "head": head}
            missing = [name for name, value in required.items() if value is None]
            raise ValueError(f"pull request is missing: {', '.join(missing)}")
        args = [
            "gh", "pr", "create",
            "--title", title,
            "--body", body,
            "--base", base,
            "--head", head,
        ]
        if pr.reviewers:
            args += ["-r", pr.reviewers]
        # gh prints the new PR's URL. Without one, number stays None,
        # which only matters if add_label is called later.
        output = self.runner(args, cwd=self.cwd, capture=True)
        match = re.search(r"/pull/(\d+)\s*$", output)
        pr.number = int(match.group(1)) if match else None
        if output.strip():
            print(output.strip(), flush=True)

    def _add_label(self, pr: PullRequest, label: str) -> None:
        if pr.number is None:
            raise ValueError("pull request is missing: number")
        self.runner(
            ["gh", "pr", "edit", str(pr.number), "--add-label", label],
            cwd=self.cwd,
        )


class PullRequest:
    title: str | None = None
    body: str | None = None
    base: str | None = None
    head: str | None = None
    number: int | None = None

    def __init__(self, github: GitHubClient) -> None:
        self.github = github
        self.reviewers = ""

    def create(self) -> None:
        self.github._create_pull_request(self)

    def add_label(self, label: str) -> None:
        self.github._add_label(self, label)
