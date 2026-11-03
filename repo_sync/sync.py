import tempfile

from repo_sync.commands import CommandError
from repo_sync.files import CopyError, copy_files


class SyncError(Exception):
    pass


class RepoSync:
    def __init__(self, settings, git, github):
        self.settings = settings
        self.git = git
        self.github = github

    def copy_files(self, clone_dir):
        s = self.settings
        try:
            copy_files(s.source_folder, s.source_files, s.destination_folder, clone_dir)
        except CopyError as exc:
            raise SyncError(str(exc)) from exc

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
