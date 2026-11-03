from repo_sync.commands import CommandError, CommandRunner


class GitClient:
    def __init__(self, runner=None):
        self.runner = runner or CommandRunner()

    def configure_user(self, email, name):
        self.runner(["git", "config", "--global", "user.email", email])
        self.runner(["git", "config", "--global", "user.name", name])

    def clone(self, url, destination):
        self.runner(["git", "clone", url, str(destination)])

    def checkout_new_branch(self, cwd, branch):
        self.runner(["git", "checkout", "-b", branch], cwd=cwd)

    def stage_all(self, cwd):
        self.runner(["git", "add", "."], cwd=cwd)

    def has_staged_changes(self, cwd):
        try:
            self.runner(["git", "diff", "--cached", "--quiet"], cwd=cwd)
        except CommandError as exc:
            if exc.returncode == 1:
                return True
            raise
        return False

    def commit(self, cwd, message):
        self.runner(["git", "commit", "--message", message], cwd=cwd)

    def push(self, cwd, branch):
        self.runner(["git", "push", "-u", "origin", f"HEAD:{branch}"], cwd=cwd)


class GitHubClient:
    def __init__(self, runner=None):
        self.runner = runner or CommandRunner()

    def setup_git_auth(self):
        # Not a token in the clone URL: libcurl 8.23 sends URL credentials
        # only on the first request, so the clone fails with a 401 partway through.
        self.runner(["gh", "auth", "setup-git"])

    def list_open_prs(self, cwd, title_search):
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

    def close_pr(self, cwd, number):
        self.runner(["gh", "pr", "close", str(number)], cwd=cwd)

    def create_pr(self, cwd, title, body, base, head, reviewers=""):
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

    def add_label(self, cwd, pr, label):
        self.runner(["gh", "pr", "edit", pr, "--add-label", label], cwd=cwd)
