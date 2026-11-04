import glob
import os
import shutil
import tempfile
from pathlib import Path

from repo_sync.commands import CommandError, Runner
from repo_sync.git import GitClient, GitHubClient
from repo_sync.settings import Settings


class SyncError(Exception):
    pass


class RepoSync:
    def __init__(self, settings: Settings, runner: Runner | None = None) -> None:
        self.settings = settings
        self.runner = runner

    def copy_files(self, clone_dir: str | Path) -> None:
        s = self.settings
        source_folder, source_files, destination_folder = s.source_folder, s.source_files, s.destination_folder
        source_root = Path(source_folder).resolve()
        clone_root = Path(clone_dir).resolve()
        relative_destination = destination_folder.lstrip("/" + (os.altsep or ""))
        destination_root = (clone_root / relative_destination).resolve()
        if not destination_root.is_relative_to(clone_root):
            raise SyncError("destination_folder must stay inside the destination repository")
        if not source_root.is_dir():
            raise SyncError(f"source_folder '{source_folder}' is not a directory")

        current = destination_root
        try:
            destination_root.mkdir(parents=True, exist_ok=True)
            for pattern in source_files:
                # Not Path.glob: it matches dotfiles, symlinks,
                # and trailing-slash patterns differently.
                matches = glob.glob(pattern, root_dir=source_root, recursive=True)
                if not matches:
                    raise SyncError(f"source file pattern '{pattern}' matched nothing in {source_folder}")
                for match in matches:
                    # Copy the unresolved path so a symlink stays a link, never followed.
                    # The resolve check only rejects links that point outside the source.
                    lexical = source_root / match
                    current = lexical
                    if not lexical.resolve().is_relative_to(source_root):
                        raise SyncError(f"'{match}' resolves outside the source folder")
                    target = destination_root / os.path.normpath(match)
                    current = target
                    if lexical.is_dir() and not lexical.is_symlink():
                        self._clear_symlink_targets(lexical, target)
                        shutil.copytree(
                            lexical,
                            target,
                            symlinks=True,
                            copy_function=self._copy_file,
                            dirs_exist_ok=True,
                        )
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        self._copy_file(lexical, target)
        except OSError as exc:
            raise SyncError(f"failed to copy '{current}': {exc}") from exc

    @staticmethod
    def _copy_file(source: str | Path, target: str | Path) -> None:
        source, target = Path(source), Path(target)
        # Unlink a destination symlink first, so copy2 can't write through it
        # to a file outside the repo.
        if target.exists(follow_symlinks=False) and (target.is_symlink() or source.is_symlink()):
            target.unlink()
        shutil.copy2(source, target, follow_symlinks=False)

    @staticmethod
    def _clear_symlink_targets(source_dir: Path, target_dir: Path) -> None:
        # copytree(symlinks=True) creates links with os.symlink,
        # which fails if the destination entry already exists.
        for dirpath, dirnames, filenames in source_dir.walk():
            relative = dirpath.relative_to(source_dir)
            for name in dirnames + filenames:
                if (dirpath / name).is_symlink():
                    existing = target_dir / relative / name
                    if existing.exists(follow_symlinks=False):
                        existing.unlink()

    def run(self, clone_dir: str | None = None) -> None:
        if clone_dir is not None:
            return self._run(clone_dir)
        with tempfile.TemporaryDirectory() as temp:
            return self._run(temp)

    def _run(self, clone_dir: str) -> None:
        git = GitClient(clone_dir, self.runner)
        github = GitHubClient(clone_dir, self.runner)

        print("Setting git variables", flush=True)
        git.configure_user(self.settings.user_email, self.settings.user_name)
        github.setup_git_auth()

        print("Cloning destination git repository", flush=True)
        git.clone(f"https://github.com/{self.settings.destination_repo}.git")

        print("Copying contents to git repo", flush=True)
        self.copy_files(clone_dir)
        git.checkout_new_branch(self.settings.head_branch)

        print("Adding git commit", flush=True)
        git.stage_all()
        if not git.has_staged_changes():
            print("No changes detected", flush=True)
            return
        git.commit(
            f"Update from https://github.com/{self.settings.github_repository}"
            f"/commit/{self.settings.github_sha}",
        )

        print("Pushing git commit", flush=True)
        git.push(self.settings.head_branch)

        pr = github.new_pull_request()
        pr.title = self.settings.head_branch
        pr.body = self.settings.head_branch
        pr.base = self.settings.base_branch
        pr.head = self.settings.head_branch
        pr.reviewers = self.settings.reviewers
        existing = github.find_open_pr(self.settings.head_branch)
        if existing is None:
            print("Creating a pull request", flush=True)
            pr.create()
        else:
            pr.number = existing
            print("Pull request already open for this branch, reusing it", flush=True)

        # Only after the push and PR succeed, so a failure never leaves no PR open.
        print("Closing older PRs", flush=True)
        for number in github.list_open_prs(self.settings.head_branch_prefix, self.settings.head_branch):
            github.close_pr(number)

        # The number is unset when `gh pr create` printed no PR URL.
        if self.settings.labels and pr.number is None:
            pr.number = github.find_open_pr(self.settings.head_branch)
            if pr.number is None:
                print(
                    "::warning::could not determine the pull request number, skipping labels",
                    flush=True,
                )
                return

        if self.settings.labels:
            print("Adding labels to the pull request", flush=True)
        # One at a time after creation: a label missing from the destination repo
        # would fail `gh pr create --label` outright.
        for label in self.settings.labels:
            try:
                pr.add_label(label)
            except CommandError:
                print(
                    f"::warning::could not add label '{label}' to the pull request, skipping",
                    flush=True,
                )
