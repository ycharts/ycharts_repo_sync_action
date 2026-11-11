import glob
import os
import shutil
import tempfile
from pathlib import Path

from repo_sync.commands import CommandError, Runner
from repo_sync.describe import DescribeError, GeneratedDescription, HttpPost, generate_description
from repo_sync.git import GitClient, GitHubClient, PreviousPr
from repo_sync.pr_body import build_body, build_title
from repo_sync.settings import Settings
from repo_sync.source_diff import SourceDiffError, before_sha, build_source_diff

MAX_PREVIOUS_PRS = 10


class SyncError(Exception):
    pass


class RepoSync:
    def __init__(
        self,
        settings: Settings,
        runner: Runner | None = None,
        http_post: HttpPost | None = None,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.http_post = http_post

    def copy_files(self, clone_dir: str | Path) -> list[str]:
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

        repo_root = self._repository_root(source_root)
        copied: set[str] = set()
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
                        for dirpath, dirnames, filenames in lexical.walk():
                            for name in dirnames + filenames:
                                entry = dirpath / name
                                if entry.is_symlink() or not entry.is_dir():
                                    copied.add(entry.relative_to(repo_root).as_posix())
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
                        copied.add(lexical.relative_to(repo_root).as_posix())
        except OSError as exc:
            raise SyncError(f"failed to copy '{current}': {exc}") from exc
        return sorted(copied)

    @staticmethod
    def _repository_root(source_root: Path) -> Path:
        # Copied paths must match the file names GitHub reports for the source push,
        # which are relative to the repository root.
        # Walks up for .git rather than running git, so copying makes no runner calls.
        for candidate in (source_root, *source_root.parents):
            if (candidate / ".git").exists():
                return candidate
        return source_root

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

    def _generate(
        self,
        git: GitClient,
        github: GitHubClient,
        previous_prs: list[PreviousPr],
        synced_files: set[str],
    ) -> GeneratedDescription:
        data = github.source_push(
            self.settings.github_repository,
            self.settings.github_sha,
            before_sha(self.settings.github_event_path),
        )
        source_diff = build_source_diff(
            data,
            synced_files,
            self.settings.pr_description_exclude,
        )
        return generate_description(
            api_key=self.settings.anthropic_api_key,
            model=self.settings.anthropic_model,
            previous_prs=previous_prs,
            source_diff=source_diff,
            diff_stat=git.diff_stat(),
            http_post=self.http_post,
        )

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
        synced_files = self.copy_files(clone_dir)
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
        git.push(self.settings.head_branch, force_with_lease=git.remote_branch_exists(self.settings.head_branch))

        head = self.settings.head_branch
        previous = github.list_previous_prs(
            self.settings.head_branch_prefix, head, self.settings.labels
        )
        existing = github.find_open_pr(head)
        # The open PR on this head is the newest earlier PR, so its body keeps accumulating.
        # list_previous_prs excludes the current head, so it is never closed below.
        described = list(previous)
        existing_title: str | None = None
        if existing is not None:
            existing_pr = github.get_pr(existing)
            existing_title = existing_pr.title
            described.insert(0, existing_pr)
        described = described[:MAX_PREVIOUS_PRS]

        section = (
            f"Source commit: https://github.com/{self.settings.github_repository}"
            f"/commit/{self.settings.github_sha}"
        )
        generated_title: str | None = None
        generation_succeeded = False
        if self.settings.generate_pr_description:
            print("Generating pull request description", flush=True)
            try:
                generated = self._generate(git, github, described, set(synced_files))
            except (SourceDiffError, DescribeError, CommandError, ValueError) as exc:
                print(
                    f"::warning::could not generate the pull request description, using the default title and body: {exc}",
                    flush=True,
                )
            else:
                generated_title = generated.title
                section = f"{generated.body}\n\n{section}"
                generation_succeeded = True

        pr = github.new_pull_request()
        pr.title = build_title(head, generated_title)
        pr.body = build_body(described, section, generation_succeeded, head)
        pr.base = self.settings.base_branch
        pr.head = head
        pr.reviewers = self.settings.reviewers
        if existing is None:
            print("Creating a pull request", flush=True)
            pr.create()
        else:
            pr.number = existing
            print("Pull request already open for this branch, reusing it", flush=True)
            keep_title = generated_title is None and existing_title is not None
            github.edit_pr(existing, existing_title if keep_title else pr.title, pr.body)

        # Only after the push and PR succeed, so a failure never leaves no PR open.
        print("Closing older PRs", flush=True)
        for old in previous:
            github.close_pr(old.number)

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
