import dataclasses
import datetime
import glob
import os
import shutil
import subprocess
import sys
import tempfile


class CommandError(Exception):
    def __init__(self, command, returncode):
        self.command = list(command)
        self.returncode = returncode
        super().__init__(f"command {' '.join(self.command)!r} failed with exit code {returncode}")


class ConfigError(Exception):
    def __init__(self, messages):
        self.messages = list(messages)
        super().__init__("; ".join(self.messages))


class SyncError(Exception):
    pass


class CommandRunner:
    def __init__(self, token=None, base_env=None):
        self.env = dict(os.environ if base_env is None else base_env)
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


class GitHubClient:
    def __init__(self, runner):
        self.runner = runner

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


@dataclasses.dataclass
class Settings:
    source_folder: str
    source_files: list
    destination_repo: str
    destination_folder: str
    user_email: str
    user_name: str
    head_branch: str
    head_branch_prefix: str
    base_branch: str
    reviewers: str
    labels: list
    github_repository: str
    github_sha: str

    @classmethod
    def from_env(cls, env, now=None):
        def get(name):
            return env.get(name, "").strip()

        required = {
            "INPUT_SOURCE_FOLDER": "source_folder",
            "INPUT_SOURCE_FILES": "source_files",
            "INPUT_DESTINATION_REPO": "destination_repo",
            "INPUT_DESTINATION_HEAD_BRANCH": "destination_head_branch",
            "INPUT_USER_EMAIL": "user_email",
            "INPUT_USER_NAME": "user_name",
        }
        errors = [f"{label} must be defined" for var, label in required.items() if not get(var)]
        prefix = get("INPUT_DESTINATION_HEAD_BRANCH")
        if prefix in ("main", "master"):
            errors.append("destination_head_branch cannot be 'main' nor 'master'")
        if errors:
            raise ConfigError(errors)

        head = prefix
        if get("INPUT_TIMESTAMP_HEAD_BRANCH").lower() == "true":
            moment = now() if now else datetime.datetime.now(datetime.timezone.utc)
            head = f"{prefix}-{moment.strftime('%Y%m%d%H%M%S')}"

        labels = [part.strip() for part in env.get("INPUT_PULL_REQUEST_LABELS", "").split(",")]
        return cls(
            source_folder=get("INPUT_SOURCE_FOLDER"),
            source_files=get("INPUT_SOURCE_FILES").split(),
            destination_repo=get("INPUT_DESTINATION_REPO"),
            destination_folder=get("INPUT_DESTINATION_FOLDER"),
            user_email=get("INPUT_USER_EMAIL"),
            user_name=get("INPUT_USER_NAME"),
            head_branch=head,
            head_branch_prefix=prefix,
            base_branch=get("INPUT_DESTINATION_BASE_BRANCH") or "main",
            reviewers=get("INPUT_PULL_REQUEST_REVIEWERS"),
            labels=[label for label in labels if label],
            github_repository=get("GITHUB_REPOSITORY"),
            github_sha=get("GITHUB_SHA"),
        )


def _is_within(path, root):
    return os.path.commonpath([path, root]) == root


class RepoSync:
    def __init__(self, settings, git, github):
        self.settings = settings
        self.git = git
        self.github = github

    def copy_files(self, clone_dir):
        source_root = os.path.realpath(self.settings.source_folder)
        clone_root = os.path.realpath(clone_dir)
        destination_root = os.path.realpath(os.path.join(clone_root, self.settings.destination_folder))
        if not _is_within(destination_root, clone_root):
            raise SyncError("destination_folder must stay inside the destination repository")
        os.makedirs(destination_root, exist_ok=True)

        for pattern in self.settings.source_files:
            matches = glob.glob(pattern, root_dir=source_root, recursive=True)
            if not matches:
                raise SyncError(f"source file pattern '{pattern}' matched nothing in {self.settings.source_folder}")
            for match in matches:
                source = os.path.realpath(os.path.join(source_root, match))
                if not _is_within(source, source_root):
                    raise SyncError(f"'{match}' resolves outside the source folder")
                relative = os.path.normpath(match)
                target = os.path.join(destination_root, relative)
                if os.path.isdir(source):
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(source, target)

    def run(self, clone_dir=None):
        if clone_dir is not None:
            return self._run(clone_dir)
        with tempfile.TemporaryDirectory() as temp:
            return self._run(temp)

    def _run(self, clone_dir):
        s = self.settings
        print("Setting git variables", flush=True)
        self.git.configure_user(s.user_email, s.user_name)
        self.github.setup_git_auth()

        print("Cloning destination git repository", flush=True)
        self.git.clone(f"https://github.com/{s.destination_repo}.git", clone_dir)

        print("Copying contents to git repo", flush=True)
        self.copy_files(clone_dir)
        self.git.checkout_new_branch(clone_dir, s.head_branch)

        print("Adding git commit", flush=True)
        self.git.stage_all(clone_dir)
        if not self.git.has_staged_changes(clone_dir):
            print("No changes detected", flush=True)
            return
        self.git.commit(
            clone_dir,
            f"Update from https://github.com/{s.github_repository}/commit/{s.github_sha}",
        )

        print("Closing existing PRs", flush=True)
        for number in self.github.list_open_prs(clone_dir, s.head_branch_prefix):
            self.github.close_pr(clone_dir, number)

        print("Pushing git commit", flush=True)
        self.git.push(clone_dir, s.head_branch)

        print("Creating a pull request", flush=True)
        self.github.create_pr(
            clone_dir, s.head_branch, s.head_branch, s.base_branch, s.head_branch, s.reviewers
        )

        if s.labels:
            print("Adding labels to the pull request", flush=True)
        # One at a time after creation: a label missing from the destination repo
        # would fail `gh pr create --label` outright.
        for label in s.labels:
            try:
                self.github.add_label(clone_dir, s.head_branch, label)
            except CommandError:
                print(
                    f"::warning::could not add label '{label}' to the pull request, skipping",
                    flush=True,
                )
