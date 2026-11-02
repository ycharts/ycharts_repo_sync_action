#!/bin/sh

set -u

CLOSED_PRS_FILE=${1:-}
REPOSITORY=${2:-}
SHA=${3:-}

[ -f "$CLOSED_PRS_FILE" ] || exit 1

# Takes the newest closed PR with a real description,
# skipping blank bodies and the default body (the branch name).
PICK='map(select(((.body // "") | gsub("^\\s+|\\s+$"; "")) as $b
    | ($b != "") and ($b != ((.headRefName // "") | gsub("^\\s+|\\s+$"; "")))))
  | .[0]'
BODY=$(jq -r "$PICK | .body // empty" "$CLOSED_PRS_FILE") || exit 1
[ -n "$BODY" ] || exit 1
NUMBER=$(jq -r "$PICK | .number // empty" "$CLOSED_PRS_FILE") || exit 1

SOURCE_LINE="Source commit: https://github.com/$REPOSITORY/commit/$SHA"
MAX_BODY_BYTES=60000

SIZE=$(printf '%s\n\n---\n\n%s\n' "$BODY" "$SOURCE_LINE" | LC_ALL=C wc -c | tr -d ' ')
if [ "$SIZE" -gt "$MAX_BODY_BYTES" ]
then
  printf 'Earlier updates: #%s\n\n%s\n' "$NUMBER" "$SOURCE_LINE"
  exit 0
fi

printf '%s\n' "$BODY"
printf '\n---\n\n%s\n' "$SOURCE_LINE"
