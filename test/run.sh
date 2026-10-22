#!/bin/sh

set -u

# Keep the caller's Anthropic settings and git config (signing, hooks) out of the tests.
unset ANTHROPIC_MODEL ANTHROPIC_API_KEY DESCRIBE_PR_EXCLUDE
unset GITHUB_SHA GITHUB_EVENT_PATH GITHUB_WORKSPACE GITHUB_REPOSITORY
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=Test GIT_AUTHOR_EMAIL=test@example.com
export GIT_COMMITTER_NAME=Test GIT_COMMITTER_EMAIL=test@example.com

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT="$ROOT/describe_pr.sh"
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT

FAKE_KEY="sk-ant-test-secret-1234567890"
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
  mkdir -p "$CASE_DIR/bin" "$CASE_DIR/repo"
  BIN="$CASE_DIR/bin"
  REPO="$CASE_DIR/repo"
  RESPONSE="$CASE_DIR/response.json"
  HTTP_STATUS_FILE="$CASE_DIR/status"
  CURL_EXIT_FILE="$CASE_DIR/curl_exit"
  CURL_CALLS="$CASE_DIR/curl_calls"
  CAPTURED_REQUEST="$CASE_DIR/request.json"
  CAPTURED_ARGS="$CASE_DIR/curl_args"
  CAPTURED_STDIN="$CASE_DIR/curl_stdin"
  OUT="$CASE_DIR/stdout"
  ERR="$CASE_DIR/stderr"
  echo 200 > "$HTTP_STATUS_FILE"
  echo 0 > "$CURL_EXIT_FILE"

  # The curl stub saves its args and stdin (where describe_pr.sh sends the key header),
  # copies the --data-binary file for jq checks, and prints the status as -w would.
  cat > "$BIN/curl" <<STUB
#!/bin/sh
echo called >> "$CURL_CALLS"
out=""
data=""
: > "$CAPTURED_ARGS"
cat > "$CAPTURED_STDIN"
while [ \$# -gt 0 ]
do
  printf '%s\n' "\$1" >> "$CAPTURED_ARGS"
  case "\$1" in
    -o) out="\$2"; printf '%s\n' "\$2" >> "$CAPTURED_ARGS"; shift ;;
    --data-binary) data="\$2"; printf '%s\n' "\$2" >> "$CAPTURED_ARGS"; shift ;;
  esac
  shift
done
code=\$(cat "$CURL_EXIT_FILE")
[ "\$code" -eq 0 ] || exit "\$code"
data=\${data#@}
[ -z "\$data" ] || cp "\$data" "$CAPTURED_REQUEST"
[ -z "\$out" ] || cp "$RESPONSE" "\$out"
cat "$HTTP_STATUS_FILE" | tr -d '\n'
STUB
  chmod +x "$BIN/curl"

  (
    cd "$REPO" || exit 1
    git init -q .
    git config user.email test@example.com
    git config user.name Test
    git config commit.gpgsign false
    git config core.hooksPath /dev/null
    printf 'line one\n' > file.txt
    git add file.txt
    git commit -q -m first
    printf 'line one\nchanged content marker\n' > file.txt
    git add file.txt
    git commit -q -m second
  ) || fail_assert "could not create temp repo"
}

write_response() {
  jq -n --arg stop "$1" --arg text "$2" \
    '{stop_reason: $stop, content: [{type: "text", text: $text}]}' > "$RESPONSE"
}

good_text='{"title":"Add widget support","body":"## Summary\n\nAdds widgets."}'

run_script() {
  (
    cd "$REPO" || exit 1
    PATH="$BIN:$PATH" sh "$SCRIPT" "org/source" "abc123" > "$OUT" 2> "$ERR"
  )
  STATUS=$?
}

curl_call_count() {
  if [ -f "$CURL_CALLS" ]
  then
    wc -l < "$CURL_CALLS" | tr -d ' '
  else
    echo 0
  fi
}

assert_key_not_leaked() {
  assert_file_lacks "$OUT" "$FAKE_KEY" "key in stdout"
  assert_file_lacks "$ERR" "$FAKE_KEY" "key in stderr"
}

test_success_prints_title_then_body() {
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  assert_eq "Add widget support" "$(sed -n 1p "$OUT")" "title line"
  assert_eq "## Summary" "$(sed -n 2p "$OUT")" "first body line"
  assert_file_contains "$OUT" "Adds widgets." "body content"
  assert_key_not_leaked
}

test_request_defaults_model_and_includes_diff() {
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq claude-opus-5-5 "$(jq -r .model "$CAPTURED_REQUEST")" "default model"
  assert_eq json_schema "$(jq -r .output_config.format.type "$CAPTURED_REQUEST")" "format type"
  jq -e '.messages[0].content | contains("changed content marker")' "$CAPTURED_REQUEST" > /dev/null \
    || fail_assert "diff content missing from prompt"
  jq -e '.messages[0].content | contains("org/source") and contains("abc123")' \
    "$CAPTURED_REQUEST" > /dev/null || fail_assert "source context missing from prompt"
  assert_key_not_leaked
}

test_request_honors_model_override() {
  write_response end_turn "$good_text"
  ANTHROPIC_MODEL="claude-test-model" ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  assert_eq claude-test-model "$(jq -r .model "$CAPTURED_REQUEST")" "overridden model"
}

test_api_key_sent_via_stdin_not_argv() {
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_file_contains "$CAPTURED_STDIN" "x-api-key: $FAKE_KEY" "key header on stdin"
  assert_file_lacks "$CAPTURED_ARGS" "$FAKE_KEY" "key in curl argv"
}

test_refusal_fails() {
  write_response refusal "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_nonzero "$STATUS" "refusal"
  assert_eq "" "$(cat "$OUT")" "stdout empty"
  assert_key_not_leaked
}

test_max_tokens_fails() {
  write_response max_tokens '{"title":"Cut off'
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_nonzero "$STATUS" "max_tokens"
  assert_eq "" "$(cat "$OUT")" "stdout empty"
  assert_key_not_leaked
}

test_malformed_json_text_fails() {
  write_response end_turn '{"title": "oops", '
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_nonzero "$STATUS" "malformed json"
  assert_eq "" "$(cat "$OUT")" "stdout empty"
  assert_key_not_leaked
}

test_empty_title_fails() {
  write_response end_turn '{"title":"   ","body":"b"}'
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_nonzero "$STATUS" "empty title"
}

test_http_500_fails() {
  echo '{"error":{"message":"server exploded"}}' > "$RESPONSE"
  echo 500 > "$HTTP_STATUS_FILE"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_nonzero "$STATUS" "http 500"
  assert_eq "" "$(cat "$OUT")" "stdout empty"
  assert_file_contains "$ERR" "500" "status reported"
  assert_key_not_leaked
}

test_http_500_with_valid_looking_body_fails() {
  write_response end_turn "$good_text"
  echo 500 > "$HTTP_STATUS_FILE"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_nonzero "$STATUS" "http 500 with success body"
}

test_curl_failure_fails() {
  write_response end_turn "$good_text"
  echo 7 > "$CURL_EXIT_FILE"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_nonzero "$STATUS" "curl failure"
  assert_eq "" "$(cat "$OUT")" "stdout empty"
  assert_key_not_leaked
}

test_missing_api_key_fails_without_calling_curl() {
  write_response end_turn "$good_text"
  (
    cd "$REPO" || exit 1
    PATH="$BIN:$PATH" sh "$SCRIPT" a b c > "$OUT" 2> "$ERR"
  )
  STATUS=$?
  assert_nonzero "$STATUS" "missing key"
  assert_eq 0 "$(curl_call_count)" "curl call count"
}

test_empty_api_key_fails_without_calling_curl() {
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="" run_script
  assert_nonzero "$STATUS" "empty key"
  assert_eq 0 "$(curl_call_count)" "curl call count"
}

test_small_diff_is_not_marked_truncated() {
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  jq -e '.messages[0].content | contains("truncated") | not' "$CAPTURED_REQUEST" > /dev/null \
    || fail_assert "small diff flagged as truncated"
}

test_large_diff_is_truncated_and_flagged() {
  (
    cd "$REPO" || exit 1
    # About 1 MB, well past the 200000-byte diff cap in describe_pr.sh.
    awk 'BEGIN { for (i = 0; i < 30000; i++) print "padding line number " i " xxxxxxxxxx" }' > big.txt
    git add big.txt
    git commit -q -m big
  ) || fail_assert "could not create large commit"
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  jq -e '.messages[0].content | contains("The diff below was truncated and is incomplete.")' \
    "$CAPTURED_REQUEST" > /dev/null || fail_assert "truncation notice missing"
  jq -e '.messages[0].content | contains("padding line number 29999") | not' \
    "$CAPTURED_REQUEST" > /dev/null || fail_assert "diff tail was not cut"
  jq -e '.messages[0].content | contains("padding line number 0 ") and contains("padding line number 4000 ")' \
    "$CAPTURED_REQUEST" > /dev/null || fail_assert "early diff content missing"
  size=$(jq -r '.messages[0].content | length' "$CAPTURED_REQUEST")
  [ "$size" -lt 210000 ] || fail_assert "prompt too large: $size"
  [ "$size" -gt 190000 ] || fail_assert "prompt too small: $size"
}

# Each file gets a UNIQUE_<name> marker so tests can check the diff body
# apart from the filename, which also appears in diff headers and the stat.
commit_files() {
  (
    cd "$REPO" || exit 1
    for f in "$@"
    do
      mkdir -p "$(dirname "$f")"
      printf 'content of %s UNIQUE_%s\n' "$f" "$(basename "$f" | tr -c 'A-Za-z0-9\n' '_')" > "$f"
      git add "$f"
    done
    git commit -q -m "add files"
  ) || fail_assert "could not create commit"
}

prompt_section() {
  jq -r --arg start "$1" --arg end "$2" \
    '.messages[0].content | split($start)[1] | split($end)[0]' "$CAPTURED_REQUEST"
}

assert_in_stat() {
  prompt_section "<diff_stat>" "</diff_stat>" | grep -qF -- "$1" || fail_assert "'$1' missing from stat"
}

assert_in_diff() {
  prompt_section "<diff>" "</diff>" | grep -qF -- "$1" || fail_assert "'$1' missing from diff"
}

assert_not_in_diff() {
  if prompt_section "<diff>" "</diff>" | grep -qF -- "$1"
  then
    fail_assert "'$1' unexpectedly in diff"
  fi
}

assert_prompt_contains() {
  jq -e --arg s "$1" '.messages[0].content | contains($s)' "$CAPTURED_REQUEST" > /dev/null \
    || fail_assert "prompt missing '$1'"
}

assert_prompt_lacks() {
  jq -e --arg s "$1" '.messages[0].content | contains($s) | not' "$CAPTURED_REQUEST" > /dev/null \
    || fail_assert "prompt unexpectedly contains '$1'"
}

test_root_package_lock_excluded_from_diff_but_in_stat() {
  commit_files package-lock.json
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  assert_in_stat "package-lock.json"
  assert_not_in_diff "UNIQUE_package_lock_json"
  assert_not_in_diff "package-lock.json"
}

test_nested_excluded_files_absent_from_diff_but_in_stat() {
  commit_files sub/dir/yarn.lock assets/app.min.js assets/app.js.map assets/site.min.css sub/pnpm-lock.yaml
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  for f in yarn.lock app.min.js app.js.map site.min.css pnpm-lock.yaml
  do
    assert_in_stat "$f"
  done
  for m in UNIQUE_yarn_lock UNIQUE_app_min_js UNIQUE_app_js_map UNIQUE_site_min_css UNIQUE_pnpm_lock_yaml
  do
    assert_not_in_diff "$m"
  done
}

test_describe_pr_exclude_env_adds_globs() {
  commit_files dist/out.js a.snap tests/b.snap src/keep.txt
  write_response end_turn "$good_text"
  DESCRIBE_PR_EXCLUDE='dist/**, *.snap' ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  assert_in_stat "dist/out.js"
  assert_in_stat "a.snap"
  assert_not_in_diff "UNIQUE_out_js"
  assert_not_in_diff "UNIQUE_a_snap"
  # A slashless glob matches only at the root; `**/*.snap` is needed for any depth.
  assert_in_diff "UNIQUE_b_snap"
  assert_in_diff "UNIQUE_keep_txt"
}

test_only_excluded_files_still_succeeds_and_notes_exclusion() {
  commit_files package-lock.json yarn.lock
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  assert_eq "Add widget support" "$(sed -n 1p "$OUT")" "title line"
  assert_file_contains "$OUT" "Adds widgets." "body content"
  assert_prompt_contains "Files excluded from the diff below"
  assert_prompt_contains "<excluded_files>"
  assert_not_in_diff "UNIQUE_"
  assert_prompt_lacks "The diff below was truncated"
}

test_no_exclusion_notice_when_nothing_excluded() {
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_prompt_lacks "<excluded_files>"
}

test_truncated_diff_appends_partial_note_as_last_line() {
  (
    cd "$REPO" || exit 1
    awk 'BEGIN { for (i = 0; i < 30000; i++) print "padding line number " i " xxxxxxxxxx" }' > big.txt
    git add big.txt
    git commit -q -m big
  ) || fail_assert "could not create large commit"
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  last=$(tail -n 1 "$OUT")
  case "$last" in
    "_Generated from a partial diff (first 200 KB of "*" KB)._") ;;
    *) fail_assert "last line is not the partial-diff note: '$last'" ;;
  esac
  total=$(printf '%s' "$last" | sed 's/.* of \([0-9]*\) KB.*/\1/')
  [ "$total" -gt 200 ] && [ "$total" -lt 2000 ] || fail_assert "implausible total KB: $total"
  assert_file_contains "$OUT" "Adds widgets." "body preserved"
}

test_untruncated_diff_has_no_partial_note() {
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  assert_file_lacks "$OUT" "partial diff" "partial note"
}

test_large_lockfile_with_small_file_is_not_truncated() {
  (
    cd "$REPO" || exit 1
    awk 'BEGIN { for (i = 0; i < 30000; i++) print "lock entry " i " xxxxxxxxxxxxxxxxxxxx" }' > package-lock.json
    printf 'small file UNIQUE_small_marker\n' > small.txt
    git add package-lock.json small.txt
    git commit -q -m "lock and small"
  ) || fail_assert "could not create commit"
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_eq 0 "$STATUS" "exit status"
  assert_in_diff "UNIQUE_small_marker"
  assert_not_in_diff "lock entry 0 "
  assert_in_stat "package-lock.json"
  assert_prompt_lacks "The diff below was truncated"
  assert_file_lacks "$OUT" "partial diff" "partial note"
}

src_init() {
  ORIGIN="$CASE_DIR/origin"
  SRC="$CASE_DIR/src"
  mkdir -p "$ORIGIN"
  (
    cd "$ORIGIN" || exit 1
    git init -q .
    git config commit.gpgsign false
    git config core.hooksPath /dev/null
  ) || fail_assert "could not create source origin"
  src_commit "source base" base.txt
  SRC_C1=$(git -C "$ORIGIN" rev-parse HEAD)
}

src_commit() {
  msg="$1"
  shift
  (
    cd "$ORIGIN" || exit 1
    for f in "$@"
    do
      mkdir -p "$(dirname "$f")"
      printf 'content of %s UNIQUE_%s\n' "$f" "$(basename "$f" | tr -c 'A-Za-z0-9\n' '_')" > "$f"
      git add -- "$f"
    done
    git commit -q -m "$msg"
  ) || fail_assert "could not create source commit"
}

# A shallow file:// clone makes describe_pr.sh run its real `git fetch --deepen`.
src_clone() {
  SRC_HEAD=$(git -C "$ORIGIN" rev-parse HEAD)
  git clone -q --depth 1 "file://$ORIGIN" "$SRC" 2>/dev/null || fail_assert "could not clone source origin"
}

write_event() {
  EVENT="$CASE_DIR/event.json"
  jq -n --arg before "$1" '{before: $before}' > "$EVENT"
}

write_synced_list() {
  SYNCED_LIST="$CASE_DIR/synced_files"
  : > "$SYNCED_LIST"
  for f in "$@"
  do
    printf '%s\n' "$f" >> "$SYNCED_LIST"
  done
}

run_with_source() {
  (
    cd "$REPO" || exit 1
    PATH="$BIN:$PATH" sh "$SCRIPT" "org/source" "$SRC_HEAD" "$SRC" "${EVENT:-}" "${SYNCED_LIST:-}" > "$OUT" 2> "$ERR"
  )
  STATUS=$?
}

assert_in_context() {
  prompt_section "<context_diff>" "</context_diff>" | grep -qF -- "$1" || fail_assert "'$1' missing from context diff"
}

assert_not_in_context() {
  if prompt_section "<context_diff>" "</context_diff>" | grep -qF -- "$1"
  then
    fail_assert "'$1' unexpectedly in context diff"
  fi
}

assert_system_contains() {
  jq -e --arg s "$1" '.system | contains($s)' "$CAPTURED_REQUEST" > /dev/null \
    || fail_assert "system prompt missing '$1'"
}

assert_system_lacks() {
  jq -e --arg s "$1" '.system | contains($s) | not' "$CAPTURED_REQUEST" > /dev/null \
    || fail_assert "system prompt unexpectedly contains '$1'"
}

assert_success_with_title() {
  assert_eq 0 "$STATUS" "exit status"
  assert_eq "Add widget support" "$(sed -n 1p "$OUT")" "title line"
  assert_file_contains "$OUT" "Adds widgets." "body content"
}

test_synced_files_are_in_synced_section_and_not_in_context_diff() {
  commit_files synced.txt
  src_init
  src_commit "sync and tweak" synced.txt unsynced.txt
  src_clone
  write_event "$SRC_C1"
  write_synced_list synced.txt
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_in_diff "UNIQUE_synced_txt"
  assert_not_in_context "UNIQUE_synced_txt"
  assert_not_in_context "synced.txt b/synced.txt"
  assert_in_context "UNIQUE_unsynced_txt"
  assert_not_in_diff "UNIQUE_unsynced_txt"
}

test_multi_commit_push_includes_all_messages_and_changes() {
  src_init
  src_commit "second source commit" other2.txt
  src_commit "third source commit" other3.txt
  src_clone
  write_event "$SRC_C1"
  write_synced_list
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_prompt_contains "second source commit"
  assert_prompt_contains "third source commit"
  assert_prompt_lacks "source base"
  assert_in_context "UNIQUE_other2_txt"
  assert_in_context "UNIQUE_other3_txt"
}

test_before_all_zeros_falls_back_to_previous_commit() {
  src_init
  src_commit "second source commit" other2.txt
  src_commit "third source commit" other3.txt
  src_clone
  write_event 0000000000000000000000000000000000000000
  write_synced_list
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_prompt_contains "third source commit"
  assert_prompt_lacks "second source commit"
  assert_in_context "UNIQUE_other3_txt"
  assert_not_in_context "UNIQUE_other2_txt"
}

test_missing_event_path_falls_back_to_previous_commit() {
  src_init
  src_commit "second source commit" other2.txt
  src_commit "third source commit" other3.txt
  src_clone
  EVENT=""
  write_synced_list
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_prompt_contains "third source commit"
  assert_prompt_lacks "second source commit"
  assert_in_context "UNIQUE_other3_txt"
  assert_not_in_context "UNIQUE_other2_txt"
}

test_event_before_not_in_source_falls_back_to_previous_commit() {
  src_init
  src_commit "second source commit" other2.txt
  src_commit "third source commit" other3.txt
  src_clone
  write_event 1234567890123456789012345678901234567890
  write_synced_list
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_prompt_contains "third source commit"
  assert_prompt_lacks "second source commit"
  assert_in_context "UNIQUE_other3_txt"
}

test_shallow_source_checkout_is_deepened_for_context() {
  src_init
  src_commit "second source commit" other2.txt
  src_commit "third source commit" other3.txt
  src_clone
  assert_eq true "$(git -C "$SRC" rev-parse --is-shallow-repository)" "clone is shallow"
  write_event "$SRC_C1"
  write_synced_list
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_prompt_contains "<context_diff>"
  assert_prompt_contains "second source commit"
  assert_in_context "UNIQUE_other2_txt"
}

test_failed_deepen_skips_context_with_warning() {
  src_init
  src_commit "second source commit" other2.txt
  src_commit "third source commit" other3.txt
  src_clone
  git -C "$SRC" remote set-url origin "$CASE_DIR/does-not-exist"
  write_event "$SRC_C1"
  write_synced_list
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_file_contains "$ERR" "warning" "warning on stderr"
  assert_prompt_lacks "<context_diff>"
  assert_prompt_lacks "<source_diff_stat>"
  assert_prompt_lacks "UNIQUE_other2_txt"
  assert_in_diff "changed content marker"
}

test_no_source_info_describes_synced_diff_only() {
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_success_with_title
  assert_prompt_lacks "<context_diff>"
  assert_prompt_lacks "<source_diff_stat>"
  assert_prompt_lacks "<commit_messages>"
  assert_in_diff "changed content marker"
  assert_eq "" "$(cat "$ERR")" "stderr empty"
}

test_large_context_diff_is_truncated_without_affecting_synced_diff() {
  commit_files synced.txt
  src_init
  src_commit "sync and add big file" synced.txt
  (
    cd "$ORIGIN" || exit 1
    awk 'BEGIN { for (i = 0; i < 30000; i++) print "context padding " i " xxxxxxxxxx" }' > big.txt
    git add big.txt
    git commit -q -m "add big unsynced file"
  ) || fail_assert "could not create large source commit"
  src_clone
  write_event "$SRC_C1"
  write_synced_list synced.txt
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_prompt_contains "The context diff below was truncated and is incomplete."
  assert_prompt_lacks "The diff below was truncated"
  assert_in_context "context padding 0 "
  assert_not_in_context "context padding 29999 "
  assert_in_diff "UNIQUE_synced_txt"
  assert_file_lacks "$OUT" "partial diff" "partial note"
  size=$(prompt_section "<context_diff>" "</context_diff>" | wc -c | tr -d ' ')
  [ "$size" -lt 105000 ] || fail_assert "context diff too large: $size"
  [ "$size" -gt 95000 ] || fail_assert "context diff too small: $size"
}

test_small_context_diff_is_not_marked_truncated() {
  src_init
  src_commit "second source commit" other2.txt
  src_clone
  write_event "$SRC_C1"
  write_synced_list
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_prompt_contains "<context_diff>"
  assert_prompt_lacks "The context diff below was truncated"
}

test_system_prompt_omits_excluded_sentence_when_nothing_excluded() {
  commit_files synced.txt
  src_init
  src_commit "sync and tweak" synced.txt unsynced.txt
  src_clone
  write_event "$SRC_C1"
  write_synced_list synced.txt
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_system_lacks "excluded from the diffs"
}

test_system_prompt_includes_excluded_sentence_for_excluded_destination_file() {
  commit_files package-lock.json
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_script
  assert_system_contains "excluded from the diffs"
}

test_system_prompt_includes_excluded_sentence_for_excluded_source_file() {
  src_init
  src_commit "second source commit" other2.txt package-lock.json
  src_clone
  write_event "$SRC_C1"
  write_synced_list
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_system_contains "excluded from the diffs"
  assert_prompt_contains "<excluded_context_files>"
  assert_not_in_context "UNIQUE_package_lock_json"
}

test_synced_names_with_glob_characters_and_spaces_are_excluded_literally() {
  src_init
  # Each glob-like synced name has a sibling it would match if treated as a glob,
  # and the sibling must stay in the context diff.
  # src_commit turns each non-alphanumeric character into `_`, so `star*.txt` is UNIQUE_star__txt.
  src_commit "glob names" 'star*.txt' starX.txt '[ab].txt' a.txt 'sp ace.txt' 'sp ace2.txt'
  src_clone
  write_event "$SRC_C1"
  write_synced_list 'star*.txt' '[ab].txt' 'sp ace.txt'
  write_response end_turn "$good_text"
  ANTHROPIC_API_KEY="$FAKE_KEY" run_with_source
  assert_success_with_title
  assert_not_in_context "UNIQUE_star__txt"
  assert_not_in_context "UNIQUE__ab__txt"
  assert_not_in_context "UNIQUE_sp_ace_txt"
  assert_in_context "UNIQUE_starX_txt"
  assert_in_context "UNIQUE_a_txt"
  assert_in_context "UNIQUE_sp_ace2_txt"
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
  test_success_prints_title_then_body \
  test_request_defaults_model_and_includes_diff \
  test_request_honors_model_override \
  test_api_key_sent_via_stdin_not_argv \
  test_refusal_fails \
  test_max_tokens_fails \
  test_malformed_json_text_fails \
  test_empty_title_fails \
  test_http_500_fails \
  test_http_500_with_valid_looking_body_fails \
  test_curl_failure_fails \
  test_missing_api_key_fails_without_calling_curl \
  test_empty_api_key_fails_without_calling_curl \
  test_small_diff_is_not_marked_truncated \
  test_large_diff_is_truncated_and_flagged \
  test_root_package_lock_excluded_from_diff_but_in_stat \
  test_nested_excluded_files_absent_from_diff_but_in_stat \
  test_describe_pr_exclude_env_adds_globs \
  test_only_excluded_files_still_succeeds_and_notes_exclusion \
  test_no_exclusion_notice_when_nothing_excluded \
  test_truncated_diff_appends_partial_note_as_last_line \
  test_untruncated_diff_has_no_partial_note \
  test_large_lockfile_with_small_file_is_not_truncated \
  test_synced_files_are_in_synced_section_and_not_in_context_diff \
  test_multi_commit_push_includes_all_messages_and_changes \
  test_before_all_zeros_falls_back_to_previous_commit \
  test_missing_event_path_falls_back_to_previous_commit \
  test_event_before_not_in_source_falls_back_to_previous_commit \
  test_shallow_source_checkout_is_deepened_for_context \
  test_failed_deepen_skips_context_with_warning \
  test_no_source_info_describes_synced_diff_only \
  test_large_context_diff_is_truncated_without_affecting_synced_diff \
  test_small_context_diff_is_not_marked_truncated \
  test_system_prompt_omits_excluded_sentence_when_nothing_excluded \
  test_system_prompt_includes_excluded_sentence_for_excluded_destination_file \
  test_system_prompt_includes_excluded_sentence_for_excluded_source_file \
  test_synced_names_with_glob_characters_and_spaces_are_excluded_literally
do
  run_test "$t"
done

echo "$PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
