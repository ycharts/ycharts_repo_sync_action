from repo_sync.commands import CommandError


class GitClient:
    def __init__(self, runner):
        self.runner = runner

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
