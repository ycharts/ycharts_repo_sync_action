#!/bin/sh

if [ "$#" -lt 3 ]
then
  echo "Usage: commit_files.sh <source_folder> <source_commit> <source_files...>" >&2
  exit 2
fi

SOURCE_FOLDER=$1
SOURCE_COMMIT=$2
shift 2

cd "$SOURCE_FOLDER" || exit 1

g() {
  GIT_TERMINAL_PROMPT=0 git -c safe.directory='*' -c core.quotepath=off "$@"
}

is_shallow() {
  [ "$(g rev-parse --is-shallow-repository 2>/dev/null)" = true ]
}

TMP_DIR=$(mktemp -d) || exit 1
trap 'rm -rf "$TMP_DIR"' EXIT

if ! g rev-parse --verify --quiet "$SOURCE_COMMIT^{commit}" >/dev/null 2>&1 && is_shallow
then
  g fetch --no-tags --quiet --depth=2 origin "$SOURCE_COMMIT" >/dev/null 2>&1
fi

if ! COMMIT=$(g rev-parse --verify --quiet "$SOURCE_COMMIT^{commit}" 2>/dev/null)
then
  echo "Error: commit '$SOURCE_COMMIT' not found in $SOURCE_FOLDER" >&2
  exit 1
fi

if ! g rev-parse --verify --quiet "$COMMIT^1" >/dev/null 2>&1 && is_shallow
then
  g fetch --no-tags --quiet --deepen=1 origin "$COMMIT" >/dev/null 2>&1 ||
    g fetch --no-tags --quiet --depth=2 origin "$COMMIT" >/dev/null 2>&1
fi

if g rev-parse --verify --quiet "$COMMIT^1" >/dev/null 2>&1
then
  g diff --name-only --relative --diff-filter=d "$COMMIT^1" "$COMMIT" > "$TMP_DIR/diff" || {
    echo "Error: could not diff commit '$COMMIT'" >&2
    exit 1
  }
elif is_shallow
then
  echo "Error: could not fetch the parent of commit '$COMMIT' in this shallow checkout" >&2
  exit 1
else
  g diff-tree --root -r --no-commit-id --name-only --relative --diff-filter=d "$COMMIT" > "$TMP_DIR/diff" || {
    echo "Error: could not list files of commit '$COMMIT'" >&2
    exit 1
  }
fi

find $* \( -type f -o -type l \) 2>/dev/null | sed 's|^\./||' > "$TMP_DIR/found"

grep -Fx -f "$TMP_DIR/diff" "$TMP_DIR/found" || true
exit 0
