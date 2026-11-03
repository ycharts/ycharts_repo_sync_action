#!/bin/sh -l

set -e
set -x

if [ -z "$INPUT_SOURCE_FOLDER" ]
then
  echo "Source folder must be defined"
  return -1
fi

if [ -z "$INPUT_SOURCE_FILES" ]
then
  echo "Source files must be defined"
  return -1
fi

if [ $INPUT_DESTINATION_HEAD_BRANCH == "main" ] || [ $INPUT_DESTINATION_HEAD_BRANCH == "master"]
then
  echo "Destination head branch cannot be 'main' nor 'master'"
  return -1
fi

INPUT_DESTINATION_HEAD_BRANCH_PREFIX="$INPUT_DESTINATION_HEAD_BRANCH"
if [ "$INPUT_TIMESTAMP_HEAD_BRANCH" = true ]
then
  INPUT_DESTINATION_HEAD_BRANCH="$INPUT_DESTINATION_HEAD_BRANCH-$(date +%Y%m%d%H%M%S)"
fi

if [ -z "$INPUT_PULL_REQUEST_REVIEWERS" ]
then
  PULL_REQUEST_REVIEWERS=$INPUT_PULL_REQUEST_REVIEWERS
else
  PULL_REQUEST_REVIEWERS='-r '$INPUT_PULL_REQUEST_REVIEWERS
fi

CLONE_DIR=$(mktemp -d)

echo "Setting git variables"
export GITHUB_TOKEN=$API_TOKEN_GITHUB
git config --global user.email "$INPUT_USER_EMAIL"
git config --global user.name "$INPUT_USER_NAME"

echo "Cloning destination git repository"
git clone "https://$API_TOKEN_GITHUB@github.com/$INPUT_DESTINATION_REPO.git" "$CLONE_DIR"

echo "Copying contents to git repo"
mkdir -p $CLONE_DIR/$INPUT_DESTINATION_FOLDER/
cd $INPUT_SOURCE_FOLDER
cp -r --parents $INPUT_SOURCE_FILES "$CLONE_DIR/$INPUT_DESTINATION_FOLDER/"
SYNCED_FILES_LIST=""
SOURCE_PREFIX=""
if [ "$INPUT_GENERATE_PR_DESCRIPTION" = true ]
then
  SYNCED_FILES_LIST="$CLONE_DIR.synced_files"
  # describe_pr.sh needs paths relative to the source repo root, not INPUT_SOURCE_FOLDER.
  # Asking git finds the repo wherever it was checked out, not only at GITHUB_WORKSPACE.
  # safe.directory is needed because the workspace is owned by another user.
  if ! SOURCE_PREFIX=$(git -c safe.directory='*' rev-parse --show-prefix 2>/dev/null)
  then
    echo "Warning: source_folder is not inside a git repository, describing the pull request without source commits"
    SOURCE_PREFIX=""
  fi
  # Left unquoted so find sees the same word-splitting and globbing as the cp above.
  # Filenames containing newlines are not handled.
  { find $INPUT_SOURCE_FILES \( -type f -o -type l \) -print 2>/dev/null || true; } \
    | sed "s#^\./##; s#^#$SOURCE_PREFIX#; s#//*#/#g" > "$SYNCED_FILES_LIST" || true
fi
cd -
cd "$CLONE_DIR"
git checkout -b "$INPUT_DESTINATION_HEAD_BRANCH"

echo "Adding git commit"
git add .
if git status | grep -q "Changes to be committed"
then
  git commit --message "Update from https://github.com/$GITHUB_REPOSITORY/commit/$GITHUB_SHA"
  echo "Closing existing PRs"
  CLOSED_PRS_FILE="$CLONE_DIR.closed_prs.json"
  /close_prs.sh "$INPUT_DESTINATION_HEAD_BRANCH_PREFIX" "$INPUT_PULL_REQUEST_LABELS" "$CLOSED_PRS_FILE"
  echo "Pushing git commit"
  git push -u origin HEAD:$INPUT_DESTINATION_HEAD_BRANCH
  echo "Creating a pull request"
  PR_GENERATED=false
  if [ "$INPUT_GENERATE_PR_DESCRIPTION" = true ]
  then
    echo "Generating pull request description"
    set +x
    if GENERATED=$(ANTHROPIC_MODEL="$INPUT_ANTHROPIC_MODEL" DESCRIBE_PR_EXCLUDE="$INPUT_PR_DESCRIPTION_EXCLUDE" /describe_pr.sh "$GITHUB_REPOSITORY" "$GITHUB_SHA" "${GITHUB_EVENT_PATH:-}" "$SYNCED_FILES_LIST" "$CLOSED_PRS_FILE")
    then
      PR_GENERATED=true
    else
      echo "Warning: could not generate the pull request description, using the default title and body"
    fi
    set -x
  fi
  SECTION_FILE="$CLONE_DIR.section"
  SOURCE_LINE="Source commit: https://github.com/$GITHUB_REPOSITORY/commit/$GITHUB_SHA"
  if [ "$PR_GENERATED" = true ]
  then
    PR_TITLE="$INPUT_DESTINATION_HEAD_BRANCH: $(printf '%s\n' "$GENERATED" | head -n 1)"
    {
      printf '%s\n' "$GENERATED" | tail -n +2
      printf '\n%s\n' "$SOURCE_LINE"
    } > "$SECTION_FILE"
  else
    PR_TITLE="$INPUT_DESTINATION_HEAD_BRANCH"
    printf '%s\n' "$SOURCE_LINE" > "$SECTION_FILE"
  fi
  PR_BODY_FILE="$CLONE_DIR.pr_body"
  /build_pr_body.sh "$CLOSED_PRS_FILE" "$SECTION_FILE" "$PR_GENERATED" "$INPUT_DESTINATION_HEAD_BRANCH" > "$PR_BODY_FILE"
  gh pr create --title "$PR_TITLE" \
               --body-file "$PR_BODY_FILE" \
               --base $INPUT_DESTINATION_BASE_BRANCH \
               --head $INPUT_DESTINATION_HEAD_BRANCH \
                  $PULL_REQUEST_REVIEWERS
  # Labels are added one at a time after creation, not with `gh pr create --label`,
  # because a label missing from the destination repo would fail the whole create.
  # The `if !` guard keeps a failed label from aborting the script under `set -e`.
  if [ -n "$INPUT_PULL_REQUEST_LABELS" ]
  then
    echo "Adding labels to the pull request"
    echo "$INPUT_PULL_REQUEST_LABELS" | tr ',' '\n' | while IFS= read -r label; do
      label=$(echo "$label" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
      if [ -z "$label" ]
      then
        continue
      fi
      if ! gh pr edit "$INPUT_DESTINATION_HEAD_BRANCH" --add-label "$label"
      then
        echo "Warning: could not add label '$label' to the pull request, skipping"
      fi
    done
  fi
else
  echo "No changes detected"
fi
