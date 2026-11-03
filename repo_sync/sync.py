import glob
import os
import shutil
import tempfile

from repo_sync.commands import CommandError
from repo_sync.git import GitClient, GitHubClient
from repo_sync.settings import Settings


def _is_within(path: str, root: str) -> bool:
    return os.path.commonpath([path, root]) == root


class SyncError(Exception):
    pass


class RepoSync:
    def __init__(
        self,
        settings: Settings,
        git: GitClient | None = None,
        github: GitHubClient | None = None,
    ) -> None:
        self.settings: Settings = settings
        self.git: GitClient = git or GitClient()
        self.github: GitHubClient = github or GitHubClient()

    def copy_files(self, clone_dir: str) -> None:
        s = self.settings
        source_folder, source_files, destination_folder = s.source_folder, s.source_files, s.destination_folder
        source_root = os.path.realpath(source_folder)
        clone_root = os.path.realpath(clone_dir)
        relative_destination = destination_folder.lstrip("/" + (os.altsep or ""))
        destination_root = os.path.realpath(os.path.join(clone_root, relative_destination))
        if not _is_within(destination_root, clone_root):
            raise SyncError("destination_folder must stay inside the destination repository")
        if not os.path.isdir(source_root):
            raise SyncError(f"source_folder '{source_folder}' is not a directory")

        current = destination_root
        try:
            os.makedirs(destination_root, exist_ok=True)
            for pattern in source_files:
                matches = glob.glob(pattern, root_dir=source_root, recursive=True)
                if not matches:
                    raise SyncError(f"source file pattern '{pattern}' matched nothing in {source_folder}")
                for match in matches:
                    # Copy the unresolved path so a symlink stays a link, never followed.
                    # The realpath check only rejects links that point outside the source.
                    lexical = os.path.join(source_root, match)
                    current = lexical
                    if not _is_within(os.path.realpath(lexical), source_root):
                        raise SyncError(f"'{match}' resolves outside the source folder")
                    target = os.path.join(destination_root, os.path.normpath(match))
                    current = target
                    if os.path.isdir(lexical) and not os.path.islink(lexical):
                        self._clear_symlink_targets(lexical, target)
                        shutil.copytree(
                            lexical,
                            target,
                            symlinks=True,
                            copy_function=self._copy_file,
                            dirs_exist_ok=True,
                        )
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        self._copy_file(lexical, target)
        except OSError as exc:
            raise SyncError(f"failed to copy '{current}': {exc}") from exc

    @staticmethod
    def _copy_file(source: str, target: str) -> None:
        # Unlink a destination symlink first, so copy2 can't write through it
        # to a file outside the repo.
        if os.path.lexists(target) and (os.path.islink(target) or os.path.islink(source)):
            os.unlink(target)
        shutil.copy2(source, target, follow_symlinks=False)

    @staticmethod
    def _clear_symlink_targets(source_dir: str, target_dir: str) -> None:
        # copytree(symlinks=True) creates links with os.symlink,
        # which fails if the destination entry already exists.
        for dirpath, dirnames, filenames in os.walk(source_dir):
            relative = os.path.relpath(dirpath, source_dir)
            for name in dirnames + filenames:
                if os.path.islink(os.path.join(dirpath, name)):
                    existing = os.path.normpath(os.path.join(target_dir, relative, name))
                    if os.path.lexists(existing):
                        os.unlink(existing)

    def run(self, clone_dir: str | None = None) -> None:
        if clone_dir is not None:
            return self._run(clone_dir)
        with tempfile.TemporaryDirectory() as temp:
            return self._run(temp)

    def _run(self, clone_dir: str) -> None:
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
