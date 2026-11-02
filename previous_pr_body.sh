#!/bin/sh

set -u

CLOSED_PRS_FILE=${1:-}
REPOSITORY=${2:-}
SHA=${3:-}

[ -f "$CLOSED_PRS_FILE" ] || exit 1

BODY=$(jq -r 'map(select((.body // "") | test("\\S")))
  | map(select(.body != .headRefName))
  | .[0].body // empty' "$CLOSED_PRS_FILE") || exit 1
[ -n "$BODY" ] || exit 1

printf '%s\n' "$BODY"
printf '\n---\n\nSource commit: https://github.com/%s/commit/%s\n' "$REPOSITORY" "$SHA"
