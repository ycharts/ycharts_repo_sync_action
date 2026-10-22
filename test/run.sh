#!/bin/sh

set -u

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
    PATH="$BIN:$PATH" sh "$SCRIPT" "org/source" "abc123" "source message" > "$OUT" 2> "$ERR"
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
  jq -e '.messages[0].content | contains("org/source") and contains("abc123") and contains("source message")' \
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
    env -u ANTHROPIC_API_KEY PATH="$BIN:$PATH" sh "$SCRIPT" a b c > "$OUT" 2> "$ERR"
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
  size=$(jq -r '.messages[0].content | length' "$CAPTURED_REQUEST")
  [ "$size" -lt 210000 ] || fail_assert "prompt too large: $size"
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
  test_large_diff_is_truncated_and_flagged
do
  run_test "$t"
done

echo "$PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
