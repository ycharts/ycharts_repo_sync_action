#!/bin/sh

set -u

TITLE_PREFIX=${1:-}
LABELS=${2:-}
OUTPUT=${3:-}

if [ -z "$OUTPUT" ]
then
  echo "Usage: close_prs.sh TITLE_PREFIX LABELS OUTPUT_JSON" >&2
  exit 2
fi

FIELDS="number,title,body,headRefName,createdAt"

# Label flags collect in "$@" so a label containing spaces stays one argument.
set --
OLD_IFS=$IFS
IFS=','
set -f
for label in $LABELS
do
  label=$(printf '%s' "$label" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  [ -n "$label" ] && set -- "$@" --label "$label"
done
set +f
IFS=$OLD_IFS

if [ $# -gt 0 ]
then
  LIST=$(gh pr list --state open "$@" --limit 100 --json "$FIELDS") || exit 1
else
  LIST=$(gh pr list --state open --search "is:pr is:open $TITLE_PREFIX in:title" --limit 100 --json "$FIELDS") || exit 1
fi

# Every match is closed, but only the 10 newest are recorded for the description prompt.
printf '%s\n' "$LIST" | jq 'sort_by(.createdAt) | reverse | .[:10]' > "$OUTPUT" || exit 1

NUMBERS=$(printf '%s\n' "$LIST" | jq -r '.[].number') || exit 1
for number in $NUMBERS
do
  if ! gh pr close "$number"
  then
    echo "Warning: could not close pull request #$number, continuing" >&2
  fi
done
