import json
import os
import re
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from repo_sync.commands import CommandError, CommandRunner, Runner


@dataclass(frozen=True)
class PreviousPr:
    number: int
    title: str
    body: str
    head_branch: str
    created_at: str


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

    def diff_stat(self) -> str:
        try:
            return self.runner(["git", "diff", "--stat", "HEAD~1"], cwd=self.cwd, capture=True)
        except CommandError:
            return ""

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

    def list_previous_prs(self, prefix: str, current_head: str, labels: list[str]) -> list[PreviousPr]:
        fields = "number,title,body,headRefName,createdAt,isCrossRepository"
        if labels:
            args = ["gh", "pr", "list", "--state", "open"]
            for label in labels:
                args += ["--label", label]
            args += ["--limit", "100", "--json", fields]
        else:
            # The title search only narrows candidates; titles can be edited,
            # so the head branch decides which PRs this action opened.
            # gh returns 30 results unless given --limit.
            args = [
                "gh", "pr", "list",
                "--search", f"is:pr is:open {prefix} in:title",
                "--limit", "100",
                "--json", fields,
            ]
        output = self.runner(args, cwd=self.cwd, capture=True)
        timestamped = re.compile(rf"{re.escape(prefix)}-\d{{14}}")
        previous = []
        for pr in self._parse_prs(output):
            head = pr["headRefName"]
            if pr["isCrossRepository"] or head == current_head:
                continue
            # With labels, gh already filtered by label, so the head name isn't checked.
            if not labels and head != prefix and not timestamped.fullmatch(head):
                continue
            previous.append(self._to_previous_pr(pr))
        return sorted(previous, key=lambda pr: pr.created_at, reverse=True)

    def get_pr(self, number: int) -> PreviousPr:
        output = self.runner(
            [
                "gh", "pr", "view", str(number),
                "--json", "number,title,body,headRefName,createdAt",
            ],
            cwd=self.cwd,
            capture=True,
        )
        return self._to_previous_pr(json.loads(output))

    @staticmethod
    def _to_previous_pr(pr: dict[str, Any]) -> PreviousPr:
        return PreviousPr(
            number=int(pr["number"]),
            title=pr["title"],
            body=pr["body"] or "",
            head_branch=pr["headRefName"],
            created_at=pr["createdAt"],
        )

    def edit_pr(self, number: int, title: str, body: str) -> None:
        with self._body_file(body) as path:
            self.runner(
                ["gh", "pr", "edit", str(number), "--title", title, "--body-file", path],
                cwd=self.cwd,
            )

    def source_push(self, repository: str, sha: str, before: str | None) -> dict[str, Any]:
        if before:
            endpoint = f"repos/{repository}/compare/{before}...{sha}"
        else:
            endpoint = f"repos/{repository}/commits/{sha}"
        output = self.runner(["gh", "api", endpoint], cwd=self.cwd, capture=True)
        try:
            data = json.loads(output)
        except ValueError as error:
            raise ValueError(f"unexpected response from GitHub for {endpoint}: not JSON") from error
        if not isinstance(data, dict):
            raise ValueError(f"unexpected response from GitHub for {endpoint}: not an object")
        return data

    @staticmethod
    @contextmanager
    def _body_file(body: str) -> Generator[str]:
        # A file, not --body: bodies near 60 KB are too large to pass safely in argv.
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "body.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
            yield path

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
        with self._body_file(body) as body_path:
            args = [
                "gh", "pr", "create",
                "--title", title,
                "--body-file", body_path,
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
