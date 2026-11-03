#!/bin/sh

# Usage: build_pr_body.sh CLOSED_PRS_FILE SECTION_FILE GENERATION_SUCCEEDED BRANCH

set -u

CLOSED_PRS_FILE=${1:-}
SECTION_FILE=${2:-}
GENERATION_SUCCEEDED=${3:-false}
BRANCH=${4:-}

HERE=$(dirname "$0")

if "$HERE/previous_pr_body.sh" "$CLOSED_PRS_FILE" "$SECTION_FILE"
then
  exit 0
fi

if [ "$GENERATION_SUCCEEDED" = true ]
then
  cat "$SECTION_FILE"
else
  # previous_pr_body.sh skips a body equal to the branch name, so later runs ignore this one.
  printf '%s' "$BRANCH"
fi
