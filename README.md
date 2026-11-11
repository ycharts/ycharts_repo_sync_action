# Action pull request another repository 
This GitHub Action copies a folder from the current repository to a location in another repository and create a pull request

## Example Workflow
    name: Push File

    on: push

    jobs:
      pull-request:
        runs-on: ubuntu-latest
        steps:
        - name: Checkout
          uses: actions/checkout@v2

        - name: Create pull request
          uses: ./.github/actions/pull-request-another-repo-action
          env:
            API_TOKEN_GITHUB: ${{ secrets.API_TOKEN_GITHUB }}
            ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          with:
            source_folder: 'source-folder'
            source_files: 'file1 dir/file2'
            destination_repo: 'user-name/repository-name'
            destination_folder: 'folder-name'
            destination_base_branch: 'branch-name'
            destination_head_branch: 'branch-name'
            user_email: 'user-name@paygo.com.br'
            user_name: 'user-name'
            pull_request_reviewers: 'reviewers'
            pull_request_labels: 'label1,label2'
            generate_pr_description: true
            pr_description_exclude: 'source-folder/dist/*,*.snap'

## Variables
* source_folder: The folder, relative to the workspace, that `source_files` are resolved against.
* source_files: Whitespace-separated files, directories, or glob patterns (e.g. `docs/*.md`) relative to `source_folder`. Each match is copied to the same relative path in the destination. A pattern that matches nothing fails the action.
* destination_repo: The repository to place the file or directory in.
* destination_folder: [optional] The folder in the destination repository to place the file in, if not the root directory.
* user_email: The GitHub user email associated with the API token secret.
* user_name: The GitHub username associated with the API token secret.
* destination_base_branch: [optional] The branch into which you want your code merged. Default is `main`.
* destination_head_branch: The branch to create to push the changes. Cannot be `master` or `main`.
* pull_request_reviewers: [optional] The pull request reviewers. It can be only one (just like 'reviewer') or many (just like 'reviewer1,reviewer2,...')
* pull_request_labels: [optional] Labels to add to the pull request, comma-separated (just like 'label1,label2,...'). Labels that don't exist in the destination repository are skipped with a warning. When set, these labels also decide which existing pull requests the action closes (see Behavior Notes).
* generate_pr_description: [optional] Set to `true` to generate the pull request title and description with the Claude API. Default is `false`. See Pull request description below.
* anthropic_model: [optional] The Claude model used when `generate_pr_description` is `true`. Default is `claude-opus-5-5`.
* pr_description_exclude: [optional] Comma-separated globs of files whose patches are left out of what is sent to Claude, added to the built-in exclusions. Each glob is matched against the path from the source repository root, and `*` also matches `/`.

## ENV
* API_TOKEN_GITHUB: Required; the action fails if it is missing. You must create a personal access token in you account. When `generate_pr_description` is `true`, the token must also be able to read the source repository. Follow the link:
- [Personal access token](https://docs.github.com/en/free-pro-team@latest/github/authenticating-to-github/creating-a-personal-access-token)

> You must select the scopes: 'repo = Full control of private repositories', 'admin:org = read:org' and 'write:discussion = Read:discussion'; 

* ANTHROPIC_API_KEY: [optional] Anthropic API key, required only when `generate_pr_description` is `true`. Store it as a secret.


## Behavior Notes
The action will create any destination paths if they don't exist. It will also overwrite existing files if they already exist in the locations being copied to. It will not delete the entire destination repository.

The action pushes the branch, creates the pull request (or updates the open one for the head branch), then closes the old pull requests it replaces. If the sync changes nothing, it stops after logging `No changes detected`.

### Existing pull requests
The action closes the open pull requests in the destination repository that it replaces, without checking who created them:

* If `pull_request_labels` is set, every open pull request that has all of the labels.
* Otherwise, every open pull request whose head branch is `destination_head_branch`, or `destination_head_branch` followed by `-` and 14 digits (the `timestamp_head_branch` format).

> A label shared with other workflows, or with pull requests that target the same repository, makes the action close those pull requests too. Use labels only this action applies.

### Pull request description
The body keeps the previous body, which comes from the newest earlier pull request with a real description (empty or branch-name-only bodies don't count). A pull request already open for the head branch is reused and counts as the newest, so its body accumulates on each push. Each update appends `---` and the new section. If the result would exceed 60,000 bytes, an `Earlier updates: #N` link to that pull request replaces the previous body and the `---` line.

Without generation, the title is the head branch (with the timestamp suffix when `timestamp_head_branch` is true) and the new section is a `Source commit:` link (with no previous body, the body is the head branch). A reused pull request keeps its current title.

With `generate_pr_description: true`, the title is `<head branch>: <generated title>`, and the new section is a generated description of the current changes followed by the `Source commit:` link.

What is sent to Anthropic:

* The titles and bodies of the earlier pull requests.
* The commit messages of the push.
* The patches of the synced files, minus lock files, minified files, source maps, and `pr_description_exclude` matches.
* The diff stat of the sync commit.

If generation fails (missing `ANTHROPIC_API_KEY`, a GitHub or Anthropic error, unparseable output), the action logs a warning and uses the non-generated title and body.

## Development
The action is written in Python using only the standard library. Run the tests from the repository root:

    python3 -m unittest discover -s tests

`entrypoint.py` is the launcher the Docker image runs. The logic lives in the `repo_sync/` package:

- `commands.py`: `CommandRunner` and `CommandError`
- `git.py`: `GitClient` and `GitHubClient`, wrappers over `git` and `gh`
- `settings.py`: `Settings` and `ConfigError`
- `source_diff.py`: builds the filtered diff from the GitHub API response
- `describe.py`: `PrDescriber`, a service class that calls the Anthropic API
- `sync.py`: `RepoSync` (including copying source files into the clone and building the pull request title and body) and `SyncError`

The tests in `tests/` mirror these modules.
