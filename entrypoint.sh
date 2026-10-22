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
cd -
cd "$CLONE_DIR"
git checkout -b "$INPUT_DESTINATION_HEAD_BRANCH"

echo "Adding git commit"
git add .
if git status | grep -q "Changes to be committed"
then
  git commit --message "Update from https://github.com/$GITHUB_REPOSITORY/commit/$GITHUB_SHA"
  if [ "${INPUT_CLOSE_EXISTING_PULL_REQUESTS:-true}" = true ]
  then
    echo "Closing existing PRs"
    gh pr list --search "is:pr is:open $INPUT_DESTINATION_HEAD_BRANCH_PREFIX in:title"| while IFS= read -r line; do
      prid=$(echo $line | awk '{print $1;}')
      gh pr close $prid
    done
  fi
  echo "Pushing git commit"
  git push -u origin HEAD:$INPUT_DESTINATION_HEAD_BRANCH
  echo "Creating a pull request"
  gh pr create --title $INPUT_DESTINATION_HEAD_BRANCH \
               --body $INPUT_DESTINATION_HEAD_BRANCH \
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
