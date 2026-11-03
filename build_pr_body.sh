#!/bin/sh

# Usage: build_pr_body.sh CLOSED_PRS_FILE SECTION_FILE GENERATED BRANCH

set -u

CLOSED_PRS_FILE=${1:-}
SECTION_FILE=${2:-}
GENERATED=${3:-false}
BRANCH=${4:-}

HERE=$(dirname "$0")

if "$HERE/previous_pr_body.sh" "$CLOSED_PRS_FILE" "$SECTION_FILE" 2>/dev/null
then
  exit 0
fi

if [ "$GENERATED" = true ]
then
  cat "$SECTION_FILE"
else
  printf '%s' "$BRANCH"
fi
