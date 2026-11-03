#!/bin/sh

set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CLOSE="$ROOT/close_prs.sh"
PREV="$ROOT/previous_pr_body.sh"
BUILD="$ROOT/build_pr_body.sh"
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT

PASSED=0
FAILED=0
CURRENT=""
TEST_OK=1

fail_assert() {
  echo "  FAIL [$CURRENT]: $1"
  TEST_OK=0
}

assert_eq() {
  [ "$1" = "$2" ] || fail_assert "$3: expected '$1', got '$2'"
}

assert_nonzero() {
  [ "$1" -ne 0 ] || fail_assert "$2: expected non-zero exit"
}

assert_file_contains() {
  grep -qF -- "$2" "$1" || fail_assert "$3: '$2' not found in $1"
}

assert_file_lacks() {
  if grep -qF -- "$2" "$1"
  then
    fail_assert "$3: '$2' unexpectedly found in $1"
  fi
}

setup() {
  CASE_DIR="$SANDBOX/$CURRENT"
  mkdir -p "$CASE_DIR/bin"
  BIN="$CASE_DIR/bin"
  LIST_JSON="$CASE_DIR/list.json"
  LIST_EXIT="$CASE_DIR/list_exit"
  FAIL_CLOSE="$CASE_DIR/fail_close"
  GH_LIST_CALLS="$CASE_DIR/gh_list_calls"
  GH_CLOSE_CALLS="$CASE_DIR/gh_close_calls"
  OUTPUT="$CASE_DIR/closed.json"
  OUT="$CASE_DIR/stdout"
  ERR="$CASE_DIR/stderr"
  echo '[]' > "$LIST_JSON"
  echo 0 > "$LIST_EXIT"
  : > "$FAIL_CLOSE"
  : > "$GH_LIST_CALLS"
  : > "$GH_CLOSE_CALLS"

  cat > "$BIN/gh" <<STUB
#!/bin/sh
case "\$1 \$2" in
  "pr list")
    shift 2
    printf '%s\n' "\$*" >> "$GH_LIST_CALLS"
    code=\$(cat "$LIST_EXIT")
    [ "\$code" -eq 0 ] || exit "\$code"
    cat "$LIST_JSON"
    ;;
  "pr close")
    printf '%s\n' "\$3" >> "$GH_CLOSE_CALLS"
    ! grep -qx "\$3" "$FAIL_CLOSE"
    ;;
esac
STUB
  chmod +x "$BIN/gh"
}

run_close() {
  PATH="$BIN:$PATH" sh "$CLOSE" "$1" "$2" "$OUTPUT" > "$OUT" 2> "$ERR"
  STATUS=$?
}

pr_json() {
  jq -n --argjson n "$1" --arg created "$2" --arg body "${3-body $1}" \
    '{number: $n, title: "title \($n)", body: $body, headRefName: "branch-\($n)", createdAt: $created}'
}

test_label_mode_passes_every_label_and_no_search() {
  run_close "prefix" "sync, automated"
  assert_eq 0 "$STATUS" "exit status"
  assert_file_contains "$GH_LIST_CALLS" "--state open --label sync --label automated --limit 100" "labels"
  assert_file_lacks "$GH_LIST_CALLS" "--search" "no title search"
  assert_file_contains "$GH_LIST_CALLS" "--json number,title,body,headRefName,createdAt" "json fields"
}

test_labels_are_trimmed_and_empties_skipped() {
  run_close "prefix" " a ,, b ,  "
  assert_eq 0 "$STATUS" "exit status"
  assert_file_contains "$GH_LIST_CALLS" "--label a --label b --limit" "trimmed labels"
  assert_eq 2 "$(grep -o -e '--label' "$GH_LIST_CALLS" | wc -l | tr -d ' ')" "label flag count"
}

test_label_with_space_stays_one_argument() {
  cat > "$BIN/gh" <<STUB
#!/bin/sh
for a in "\$@"
do
  printf '[%s]' "\$a" >> "$GH_LIST_CALLS"
done
echo '[]'
STUB
  run_close "prefix" "needs review"
  assert_file_contains "$GH_LIST_CALLS" "[--label][needs review]" "single arg"
}

test_no_labels_falls_back_to_title_search() {
  run_close "my-prefix" ""
  assert_eq 0 "$STATUS" "exit status"
  assert_file_contains "$GH_LIST_CALLS" '--search is:pr is:open my-prefix in:title' "title search"
  assert_file_lacks "$GH_LIST_CALLS" "--label" "no labels"
}

test_blank_labels_fall_back_to_title_search() {
  run_close "my-prefix" " , "
  assert_file_contains "$GH_LIST_CALLS" '--search is:pr is:open my-prefix in:title' "title search"
  assert_file_lacks "$GH_LIST_CALLS" "--label" "no labels"
}

test_output_sorted_newest_first() {
  {
    pr_json 1 2024-01-01T00:00:00Z
    pr_json 3 2024-03-01T00:00:00Z
    pr_json 2 2024-02-01T00:00:00Z
  } | jq -s . > "$LIST_JSON"
  run_close "p" ""
  assert_eq 0 "$STATUS" "exit status"
  assert_eq "3 2 1" "$(jq -r '[.[].number] | join(" ")' "$OUTPUT")" "order"
  assert_eq "title 3" "$(jq -r '.[0].title' "$OUTPUT")" "title kept"
  assert_eq "body 3" "$(jq -r '.[0].body' "$OUTPUT")" "body kept"
}

test_output_capped_at_ten_but_all_closed() {
  i=1
  : > "$CASE_DIR/items"
  while [ "$i" -le 12 ]
  do
    pr_json "$i" "2024-01-$(printf '%02d' "$i")T00:00:00Z" >> "$CASE_DIR/items"
    i=$((i + 1))
  done
  jq -s . "$CASE_DIR/items" > "$LIST_JSON"
  run_close "p" ""
  assert_eq 0 "$STATUS" "exit status"
  assert_eq 10 "$(jq length "$OUTPUT")" "kept count"
  assert_eq 12 "$(jq '.[0].number' "$OUTPUT")" "newest first"
  assert_eq 3 "$(jq '.[-1].number' "$OUTPUT")" "oldest kept"
  assert_eq 12 "$(wc -l < "$GH_CLOSE_CALLS" | tr -d ' ')" "close calls"
  assert_file_contains "$GH_CLOSE_CALLS" "1" "oldest closed"
}

test_empty_match_writes_empty_array_and_closes_nothing() {
  run_close "p" ""
  assert_eq 0 "$STATUS" "exit status"
  assert_eq "[]" "$(jq -c . "$OUTPUT")" "empty array"
  assert_eq 0 "$(wc -l < "$GH_CLOSE_CALLS" | tr -d ' ')" "close calls"
}

test_list_failure_exits_nonzero_and_closes_nothing() {
  echo 4 > "$LIST_EXIT"
  run_close "p" "label"
  assert_nonzero "$STATUS" "exit status"
  assert_eq 0 "$(wc -l < "$GH_CLOSE_CALLS" | tr -d ' ')" "close calls"
}

test_failing_close_warns_and_continues() {
  {
    pr_json 1 2024-01-01T00:00:00Z
    pr_json 2 2024-02-01T00:00:00Z
    pr_json 3 2024-03-01T00:00:00Z
  } | jq -s . > "$LIST_JSON"
  echo 2 > "$FAIL_CLOSE"
  run_close "p" ""
  assert_eq 0 "$STATUS" "exit status"
  assert_eq 3 "$(wc -l < "$GH_CLOSE_CALLS" | tr -d ' ')" "all close attempts"
  assert_file_contains "$ERR" "could not close pull request #2" "warning"
  assert_eq 3 "$(jq length "$OUTPUT")" "output unaffected"
}

run_prev() {
  printf 'Source commit: https://github.com/org/source/commit/abc123\n' > "$CASE_DIR/section"
  sh "$PREV" "$1" "$CASE_DIR/section" > "$OUT" 2> "$ERR"
  STATUS=$?
}

test_previous_body_without_section_only_reports_status() {
  pr_json 3 2024-03-01T00:00:00Z "Real body" | jq -s . > "$CASE_DIR/closed.json"
  sh "$PREV" "$CASE_DIR/closed.json" > "$OUT" 2> "$ERR"
  assert_eq 0 "$?" "found"
  assert_eq "" "$(cat "$OUT")" "no output"
  pr_json 3 2024-03-01T00:00:00Z "" | jq -s . > "$CASE_DIR/closed.json"
  sh "$PREV" "$CASE_DIR/closed.json" > "$OUT" 2> "$ERR"
  assert_nonzero "$?" "not found"
}

test_previous_body_handles_non_array_and_non_string_body() {
  echo '{"number":1,"body":"x","headRefName":"b"}' > "$CASE_DIR/closed.json"
  run_prev "$CASE_DIR/closed.json"
  assert_nonzero "$STATUS" "object input"
  echo '[{"number":4,"body":123,"headRefName":"b"}]' > "$CASE_DIR/closed.json"
  run_prev "$CASE_DIR/closed.json"
  assert_eq 0 "$STATUS" "numeric body"
  assert_eq "123" "$(sed -n 1p "$OUT")" "numeric body kept as text"
}

test_previous_body_uses_most_recent_pr_with_source_line() {
  {
    pr_json 3 2024-03-01T00:00:00Z "Newest body"
    pr_json 2 2024-02-01T00:00:00Z "Older body"
  } | jq -s . > "$CASE_DIR/closed.json"
  run_prev "$CASE_DIR/closed.json"
  assert_eq 0 "$STATUS" "exit status"
  expected=$(printf 'Newest body\n\n---\n\nSource commit: https://github.com/org/source/commit/abc123')
  assert_eq "$expected" "$(cat "$OUT")" "body"
}

test_previous_body_skips_empty_and_default_bodies() {
  {
    pr_json 3 2024-03-01T00:00:00Z ""
    pr_json 2 2024-02-01T00:00:00Z "branch-2"
    pr_json 1 2024-01-01T00:00:00Z "Real body"
  } | jq -s . > "$CASE_DIR/closed.json"
  run_prev "$CASE_DIR/closed.json"
  assert_eq 0 "$STATUS" "exit status"
  assert_file_contains "$OUT" "Real body" "usable body"
  assert_file_lacks "$OUT" "branch-2" "default body skipped"
}

test_previous_body_fails_when_nothing_usable() {
  {
    pr_json 2 2024-02-01T00:00:00Z ""
    pr_json 1 2024-01-01T00:00:00Z "branch-1"
  } | jq -s . > "$CASE_DIR/closed.json"
  run_prev "$CASE_DIR/closed.json"
  assert_nonzero "$STATUS" "exit status"
}

test_previous_body_fails_for_empty_array_null_body_and_missing_file() {
  echo '[]' > "$CASE_DIR/closed.json"
  run_prev "$CASE_DIR/closed.json"
  assert_nonzero "$STATUS" "empty array"
  echo '[{"number":1,"body":null,"headRefName":"b"}]' > "$CASE_DIR/closed.json"
  run_prev "$CASE_DIR/closed.json"
  assert_nonzero "$STATUS" "null body"
  run_prev "$CASE_DIR/missing.json"
  assert_nonzero "$STATUS" "missing file"
}

test_wildcard_labels_are_passed_literally() {
  mkdir -p "$CASE_DIR/cwd"
  : > "$CASE_DIR/cwd/sync-one"
  : > "$CASE_DIR/cwd/sync-two"
  (cd "$CASE_DIR/cwd" && PATH="$BIN:$PATH" sh "$CLOSE" "prefix" "sync-*" "$OUTPUT" > "$OUT" 2> "$ERR")
  assert_eq 0 "$?" "exit status"
  assert_file_contains "$GH_LIST_CALLS" "--label sync-* --limit" "literal wildcard"
  assert_file_lacks "$GH_LIST_CALLS" "sync-one" "no expansion"
}

test_previous_body_treats_default_body_with_trailing_newline_as_default() {
  {
    pr_json 2 2024-02-01T00:00:00Z "$(printf 'branch-2\n')"
    jq -n '{number: 1, body: "  branch-1 \n\n", headRefName: "branch-1", createdAt: "2024-01-01T00:00:00Z"}'
    pr_json 0 2023-12-01T00:00:00Z "Real body"
  } | jq -s . > "$CASE_DIR/closed.json"
  jq '.[0].body = "branch-2\n"' "$CASE_DIR/closed.json" > "$CASE_DIR/c2.json"
  run_prev "$CASE_DIR/c2.json"
  assert_eq 0 "$STATUS" "exit status"
  assert_file_contains "$OUT" "Real body" "usable body"
  assert_file_lacks "$OUT" "branch-" "defaults skipped"
}

test_previous_body_over_cap_starts_fresh() {
  big=$(head -c 60000 /dev/zero | tr '\0' 'x')
  {
    pr_json 7 2024-03-01T00:00:00Z "$big"
    pr_json 6 2024-02-01T00:00:00Z "Older"
  } | jq -s . > "$CASE_DIR/closed.json"
  run_prev "$CASE_DIR/closed.json"
  assert_eq 0 "$STATUS" "exit status"
  expected=$(printf 'Earlier updates: #7\n\nSource commit: https://github.com/org/source/commit/abc123')
  assert_eq "$expected" "$(cat "$OUT")" "fresh body"
}

run_build() {
  printf 'Generated section\n\nSource commit: https://github.com/org/source/commit/abc123\n' > "$CASE_DIR/section"
  sh "$BUILD" "$CASE_DIR/closed.json" "$CASE_DIR/section" "$1" "my-branch" > "$OUT" 2> "$ERR"
  STATUS=$?
}

test_build_generated_with_previous_body() {
  pr_json 3 2024-03-01T00:00:00Z "Old body" | jq -s . > "$CASE_DIR/closed.json"
  run_build true
  assert_eq 0 "$STATUS" "exit status"
  expected=$(printf 'Old body\n\n---\n\nGenerated section\n\nSource commit: https://github.com/org/source/commit/abc123')
  assert_eq "$expected" "$(cat "$OUT")" "body"
}

test_build_generated_without_previous_body() {
  echo '[]' > "$CASE_DIR/closed.json"
  run_build true
  assert_eq 0 "$STATUS" "exit status"
  assert_eq "$(cat "$CASE_DIR/section")" "$(cat "$OUT")" "section alone"
  assert_file_lacks "$OUT" "---" "no separator"
}

test_build_failed_with_previous_body() {
  pr_json 3 2024-03-01T00:00:00Z "Old body" | jq -s . > "$CASE_DIR/closed.json"
  printf 'Source commit: https://github.com/org/source/commit/abc123\n' > "$CASE_DIR/section"
  sh "$BUILD" "$CASE_DIR/closed.json" "$CASE_DIR/section" false "my-branch" > "$OUT" 2> "$ERR"
  expected=$(printf 'Old body\n\n---\n\nSource commit: https://github.com/org/source/commit/abc123')
  assert_eq "$expected" "$(cat "$OUT")" "body"
}

test_build_failed_without_previous_body_uses_default() {
  echo '[]' > "$CASE_DIR/closed.json"
  run_build false
  assert_eq 0 "$STATUS" "exit status"
  assert_eq "6d792d6272616e6368" "$(od -An -tx1 "$OUT" | tr -d ' \n')" "branch name, no newline"
}

test_build_over_cap_previous_body() {
  big=$(head -c 60000 /dev/zero | tr '\0' 'x')
  pr_json 7 2024-03-01T00:00:00Z "$big" | jq -s . > "$CASE_DIR/closed.json"
  run_build true
  assert_eq 0 "$STATUS" "exit status"
  expected=$(printf 'Earlier updates: #7\n\nGenerated section\n\nSource commit: https://github.com/org/source/commit/abc123')
  assert_eq "$expected" "$(cat "$OUT")" "body"
}

run_test() {
  CURRENT="$1"
  TEST_OK=1
  setup
  "$1"
  if [ "$TEST_OK" -eq 1 ]
  then
    PASSED=$((PASSED + 1))
    echo "ok   $1"
  else
    FAILED=$((FAILED + 1))
    echo "FAIL $1"
  fi
}

for t in \
  test_label_mode_passes_every_label_and_no_search \
  test_labels_are_trimmed_and_empties_skipped \
  test_label_with_space_stays_one_argument \
  test_no_labels_falls_back_to_title_search \
  test_blank_labels_fall_back_to_title_search \
  test_output_sorted_newest_first \
  test_output_capped_at_ten_but_all_closed \
  test_empty_match_writes_empty_array_and_closes_nothing \
  test_list_failure_exits_nonzero_and_closes_nothing \
  test_failing_close_warns_and_continues \
  test_previous_body_uses_most_recent_pr_with_source_line \
  test_previous_body_skips_empty_and_default_bodies \
  test_previous_body_fails_when_nothing_usable \
  test_previous_body_fails_for_empty_array_null_body_and_missing_file \
  test_wildcard_labels_are_passed_literally \
  test_previous_body_treats_default_body_with_trailing_newline_as_default \
  test_previous_body_over_cap_starts_fresh \
  test_previous_body_without_section_only_reports_status \
  test_previous_body_handles_non_array_and_non_string_body \
  test_build_generated_with_previous_body \
  test_build_generated_without_previous_body \
  test_build_failed_with_previous_body \
  test_build_failed_without_previous_body_uses_default \
  test_build_over_cap_previous_body
do
  run_test "$t"
done

echo "$PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
