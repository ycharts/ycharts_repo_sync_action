import glob
import os
import shutil


def _is_within(path, root):
    return os.path.commonpath([path, root]) == root


def _copy_file(source, target):
    # Unlink a destination symlink first, so copy2 can't write through it
    # to a file outside the repo.
    if os.path.lexists(target) and (os.path.islink(target) or os.path.islink(source)):
        os.unlink(target)
    shutil.copy2(source, target, follow_symlinks=False)


def _clear_symlink_targets(source_dir, target_dir):
    # copytree(symlinks=True) creates links with os.symlink,
    # which fails if the destination entry already exists.
    for dirpath, dirnames, filenames in os.walk(source_dir):
        relative = os.path.relpath(dirpath, source_dir)
        for name in dirnames + filenames:
            if os.path.islink(os.path.join(dirpath, name)):
                existing = os.path.normpath(os.path.join(target_dir, relative, name))
                if os.path.lexists(existing):
                    os.unlink(existing)


class CopyError(Exception):
    # Not SyncError: sync.py imports this module, so importing SyncError here would be a cycle.
    # RepoSync.copy_files converts this to SyncError.
    pass


def copy_files(source_folder, source_files, destination_folder, clone_dir):
    source_root = os.path.realpath(source_folder)
    clone_root = os.path.realpath(clone_dir)
    relative_destination = destination_folder.lstrip("/" + (os.altsep or ""))
    destination_root = os.path.realpath(os.path.join(clone_root, relative_destination))
    if not _is_within(destination_root, clone_root):
        raise CopyError("destination_folder must stay inside the destination repository")
    if not os.path.isdir(source_root):
        raise CopyError(f"source_folder '{source_folder}' is not a directory")

    current = destination_root
    try:
        os.makedirs(destination_root, exist_ok=True)
        for pattern in source_files:
            matches = glob.glob(pattern, root_dir=source_root, recursive=True)
            if not matches:
                raise CopyError(f"source file pattern '{pattern}' matched nothing in {source_folder}")
            for match in matches:
                # Copy the unresolved path so a symlink stays a link, never followed.
                # The realpath check only rejects links that point outside the source.
                lexical = os.path.join(source_root, match)
                current = lexical
                if not _is_within(os.path.realpath(lexical), source_root):
                    raise CopyError(f"'{match}' resolves outside the source folder")
                target = os.path.join(destination_root, os.path.normpath(match))
                current = target
                if os.path.isdir(lexical) and not os.path.islink(lexical):
                    _clear_symlink_targets(lexical, target)
                    shutil.copytree(
                        lexical,
                        target,
                        symlinks=True,
                        copy_function=_copy_file,
                        dirs_exist_ok=True,
                    )
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    _copy_file(lexical, target)
    except OSError as exc:
        raise CopyError(f"failed to copy '{current}': {exc}") from exc
