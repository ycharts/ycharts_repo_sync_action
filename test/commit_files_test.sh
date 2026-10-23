#!/bin/sh

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
COMMIT_FILES="$SCRIPT_DIR/../commit_files.sh"

export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=Test GIT_AUTHOR_EMAIL=test@example.com
export GIT_COMMITTER_NAME=Test GIT_COMMITTER_EMAIL=test@example.com
export GIT_TERMINAL_PROMPT=0

SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT

PASSED=0
FAILED=0

new_repo() {
  rm -rf "$SANDBOX/repo"
  mkdir -p "$SANDBOX/repo"
  cd "$SANDBOX/repo" || exit 1
  git init -q -b main .
}

commit_all() {
  git add -A
  git commit -q -m "$1"
}

write() {
  mkdir -p "$(dirname "$1")"
  printf '%s\n' "${2:-content}" > "$1"
}

check() {
  name=$1
  expected=$2
  actual=$3
  if [ "$expected" = "$actual" ]
  then
    PASSED=$((PASSED + 1))
    echo "PASS: $name"
  else
    FAILED=$((FAILED + 1))
    echo "FAIL: $name"
    echo "  expected: $expected"
    echo "  actual:   $actual"
  fi
}

run() {
  out=$("$COMMIT_FILES" "$@" 2>"$SANDBOX/stderr")
  STATUS=$?
  printf '%s\n' "$out" | sort
}

test_single_parent() {
  new_repo
  write a.txt; write b.txt; commit_all one
  write b.txt changed; write c.txt; commit_all two
  check "single parent commit" "b.txt
c.txt" "$(run . "$(git rev-parse HEAD)" '*' | sed '/^$/d')"
}

test_merge_commit() {
  new_repo
  write base.txt; commit_all base
  git checkout -q -b feature
  write f1.txt; commit_all f1
  write f2.txt; commit_all f2
  git checkout -q main
  write m1.txt; commit_all m1
  git merge -q --no-ff -m merge feature
  check "merge commit uses first parent" "f1.txt
f2.txt" "$(run . "$(git rev-parse HEAD)" '*' | sed '/^$/d')"
}

test_root_commit() {
  new_repo
  write a.txt; write d/b.txt; commit_all root
  check "root commit" "a.txt
d/b.txt" "$(run . "$(git rev-parse HEAD)" '*' | sed '/^$/d')"
}

test_source_folder() {
  new_repo
  write outside.txt; write src/in.txt; write src/sub/deep.txt; commit_all one
  write outside.txt changed; write src/in.txt changed; write src/sub/deep.txt changed; commit_all two
  check "source folder prefix" "in.txt
sub/deep.txt" "$(run src "$(git rev-parse HEAD)" '*' | sed '/^$/d')"
}

test_globs_and_dirs() {
  new_repo
  write src/a.css; write src/b.js; write src/lib/c.js; write src/lib/d.css; commit_all one
  write src/a.css x; write src/b.js x; write src/lib/c.js x; write src/lib/d.css x; commit_all two
  sha=$(git rev-parse HEAD)
  check "glob narrows" "a.css" "$(run src "$sha" '*.css' | sed '/^$/d')"
  check "directory entry" "lib/c.js
lib/d.css" "$(run src "$sha" lib | sed '/^$/d')"
  check "glob and directory" "a.css
lib/c.js
lib/d.css" "$(run src "$sha" '*.css' lib | sed '/^$/d')"
}

test_delete_and_rename() {
  new_repo
  write gone.txt; write old.txt "rename me please, enough content"; write keep.txt; commit_all one
  git rm -q gone.txt
  git mv old.txt new.txt
  commit_all two
  check "deleted and renamed" "new.txt" "$(run . "$(git rev-parse HEAD)" '*' | sed '/^$/d')"
}

test_spaces() {
  new_repo
  write "my file.txt"; write "dir with space/x y.txt"; commit_all one
  write "my file.txt" changed; write "dir with space/x y.txt" changed; commit_all two
  check "filenames with spaces" "dir with space/x y.txt
my file.txt" "$(run . "$(git rev-parse HEAD)" '*' | sed '/^$/d')"
}

test_no_match() {
  new_repo
  write a.txt; write b.md; commit_all one
  write b.md changed; commit_all two
  out=$("$COMMIT_FILES" . "$(git rev-parse HEAD)" '*.txt' 2>/dev/null)
  check "no match output empty" "" "$out"
  check "no match exit 0" "0" "$?"
}

test_outside_folder_only() {
  new_repo
  write src/a; write src/b; write out; commit_all one
  write out changed; commit_all two
  out=$("$COMMIT_FILES" src "$(git rev-parse HEAD)" '*' 2>/dev/null)
  check "only outside source folder output empty" "" "$out"
  check "only outside source folder exit 0" "0" "$?"
}

test_invalid_sha() {
  new_repo
  write a.txt; commit_all one
  "$COMMIT_FILES" . deadbeefdeadbeefdeadbeefdeadbeefdeadbeef '*' >/dev/null 2>&1
  status=$?
  check "invalid sha exits non-zero" "1" "$status"
}

make_shallow() {
  new_repo
  write a.txt; commit_all one
  write b.txt; commit_all two
  SHA=$(git rev-parse HEAD)
  rm -rf "$SANDBOX/shallow"
  git clone -q --depth 1 "file://$SANDBOX/repo" "$SANDBOX/shallow"
  cd "$SANDBOX/shallow" || exit 1
}

test_shallow_fetch() {
  make_shallow
  out=$("$COMMIT_FILES" . "$SHA" '*' 2>/dev/null)
  check "shallow clone fetches parent" "b.txt" "$out"
}

test_shallow_unfetchable() {
  make_shallow
  git remote remove origin
  "$COMMIT_FILES" . "$SHA" '*' >/dev/null 2>"$SANDBOX/stderr"
  status=$?
  check "shallow clone without remote fails" "1" "$status"
}

test_single_parent
test_merge_commit
test_root_commit
test_source_folder
test_globs_and_dirs
test_delete_and_rename
test_spaces
test_no_match
test_outside_folder_only
test_invalid_sha
test_shallow_fetch
test_shallow_unfetchable

echo "$PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
