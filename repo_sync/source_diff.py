import fnmatch
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

MAX_SOURCE_COMMITS = 50
MAX_API_FILES = 300

BUILTIN_EXCLUDES = (
    "package-lock.json",
    "*/package-lock.json",
    "yarn.lock",
    "*/yarn.lock",
    "pnpm-lock.yaml",
    "*/pnpm-lock.yaml",
    "*.min.js",
    "*.min.css",
    "*.map",
)

_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")


class SourceDiffError(Exception):
    pass


@dataclass(frozen=True)
class SourceDiff:
    patches: str
    commit_messages: list[str]
    partial: bool
    synced_files_changed: bool


def _before_sha(event_path: str | None) -> str | None:
    if not event_path:
        return None
    try:
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(event, dict) or event.get("forced") is True:
        return None
    before = event.get("before")
    if not isinstance(before, str) or not _SHA_RE.fullmatch(before):
        return None
    if set(before) == {"0"}:
        return None
    return before


def _is_excluded(filename: str, excludes: list[str]) -> bool:
    patterns = [*BUILTIN_EXCLUDES, *excludes]
    return any(fnmatch.fnmatchcase(filename, pattern) for pattern in patterns)


def _as_dict(value: object) -> dict:
    if not isinstance(value, dict):
        raise SourceDiffError("unexpected response shape from GitHub")
    return value


def _commit_message(commit: dict) -> str:
    inner = commit.get("commit")
    message = inner.get("message") if isinstance(inner, dict) else None
    return message if isinstance(message, str) else ""


def fetch_source_diff(
    gh_api: Callable[[str], str],
    repository: str,
    sha: str,
    event_path: str | None,
    synced_files: set[str],
    excludes: list[str],
) -> SourceDiff:
    before = _before_sha(event_path)
    if before:
        endpoint = f"repos/{repository}/compare/{before}...{sha}"
    else:
        endpoint = f"repos/{repository}/commits/{sha}"
    try:
        data = _as_dict(json.loads(gh_api(endpoint)))
    except SourceDiffError:
        raise
    except Exception as error:
        raise SourceDiffError(f"could not fetch {endpoint}: {type(error).__name__}") from error

    files = data.get("files") or []
    if not isinstance(files, list):
        raise SourceDiffError("unexpected files in response from GitHub")
    files = [entry for entry in files if isinstance(entry, dict)]

    kept = [entry for entry in files if entry.get("filename") in synced_files]
    sections: list[str] = []
    for entry in kept:
        filename = entry["filename"]
        header = (
            f"### {filename} ({entry.get('status') or 'modified'}, "
            f"+{entry.get('additions') or 0}/-{entry.get('deletions') or 0})"
        )
        patch = entry.get("patch") or ""
        if patch and not _is_excluded(filename, excludes):
            header = f"{header}\n{patch}"
        sections.append(header)

    commits = data.get("commits")
    if isinstance(commits, list):
        messages = [_commit_message(c) for c in commits if isinstance(c, dict)]
        messages = messages[-MAX_SOURCE_COMMITS:]
    else:
        messages = [_commit_message(data)]

    return SourceDiff(
        patches="\n".join(sections),
        commit_messages=messages,
        partial=len(files) >= MAX_API_FILES,
        synced_files_changed=bool(kept),
    )
