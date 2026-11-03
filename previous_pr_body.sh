#!/bin/sh

# Usage: previous_pr_body.sh CLOSED_PRS_FILE [SECTION_FILE]
# Picks the newest closed PR whose body is not blank or the default body (the branch name),
# then prints that body, a `---` line, and the contents of SECTION_FILE.
# If that would exceed MAX_BODY_BYTES, an `Earlier updates: #N` line takes the body's place.
# Without SECTION_FILE, prints nothing; the exit status says whether a body was found.
# Exits 1 when no closed PR has a real body.

set -u

CLOSED_PRS_FILE=${1:-}
SECTION_FILE=${2:-}
MAX_BODY_BYTES=60000

[ -f "$CLOSED_PRS_FILE" ] || exit 1

# Prints the PR number on the first line and its body after it.
# \A and \z trim only the ends of the whole body; ^ and $ would also match at each line.
PICKED=$(jq -r '
  def trim: sub("\\A[[:space:]]+"; "") | sub("[[:space:]]+\\z"; "");
  first(if type == "array" then .[] else empty end | objects
    | select(((.body // "") | tostring | trim) as $b
        | $b != "" and $b != ((.headRefName // "") | tostring | trim)))
  | "\(.number // "")\n\(.body | tostring | sub("[[:space:]]+\\z"; ""))"
' "$CLOSED_PRS_FILE" 2>/dev/null) || exit 1
[ -n "$PICKED" ] || exit 1

[ -n "$SECTION_FILE" ] || exit 0

NUMBER=$(printf '%s\n' "$PICKED" | head -n 1)
BODY=$(printf '%s\n' "$PICKED" | tail -n +2)
SECTION=$(cat "$SECTION_FILE") || exit 1

SIZE=$(printf '%s\n\n---\n\n%s\n' "$BODY" "$SECTION" | LC_ALL=C wc -c | tr -d ' ')
if [ "$SIZE" -gt "$MAX_BODY_BYTES" ]
then
  printf 'Earlier updates: #%s\n\n%s\n' "$NUMBER" "$SECTION"
else
  printf '%s\n\n---\n\n%s\n' "$BODY" "$SECTION"
fi
