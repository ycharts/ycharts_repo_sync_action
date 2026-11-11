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
            pr_description_exclude: 'dist/*,*.snap'

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
* pr_description_exclude: [optional] Comma-separated globs of files whose patches are left out of what is sent to Claude, added to the built-in exclusions. Whitespace around each glob is trimmed and empty entries are skipped. Each glob is matched against the whole file path with `fnmatch` rules, so `*` also matches across `/`: `*.snap` matches `.snap` files at any depth and `dist/*` covers everything under `dist`. Avoid `**/*.snap`, which does not match a root-level `a.snap`. Excluded files still appear in the diff stat and as a header line without a patch.

## ENV
* API_TOKEN_GITHUB: Required; the action fails if it is missing. You must create a personal access token in you account. When `generate_pr_description` is `true`, the token must also be able to read the source repository, because the action reads the source push through the GitHub compare API. Follow the link:
- [Personal access token](https://docs.github.com/en/free-pro-team@latest/github/authenticating-to-github/creating-a-personal-access-token)

> You must select the scopes: 'repo = Full control of private repositories', 'admin:org = read:org' and 'write:discussion = Read:discussion'; 

* ANTHROPIC_API_KEY: [optional] Anthropic API key, required only when `generate_pr_description` is `true`. Store it as a secret.


## Behavior Notes
The action will create any destination paths if they don't exist. It will also overwrite existing files if they already exist in the locations being copied to. It will not delete the entire destination repository.

The action runs in this order: push the branch, create the pull request (or update the open one for the branch), then close the old pull requests. A failure before the new pull request exists never leaves the destination without an open one. If the sync changes nothing, the action stops after logging `No changes detected`.

### Existing pull requests
After the push, the action finds the open pull requests in the destination repository that it replaces, and closes them once the new pull request exists. It does not check who created a pull request. It finds them this way:

* If `pull_request_labels` is set, every open pull request that has all of the labels.
* Otherwise, every open pull request whose head branch is `destination_head_branch`, or `destination_head_branch` followed by `-` and 14 digits (the `timestamp_head_branch` format). The search looks for the branch name in titles first, then keeps only those head branches.

Either search looks at up to 100 pull requests. Pull requests from forks and the pull request for the current head branch are excluded. A pull request that can't be closed fails the action.

> A label shared with other workflows, or with pull requests that target the same repository, makes the action close those pull requests too. Use labels only this action applies.

If a pull request is already open for the current head branch, the action reuses it: it edits the title and body instead of creating a new one.

### Pull request description
By default the pull request title is `destination_head_branch` and the body is the previous pull request's body (see below), so reviewers don't lose earlier notes. With `generate_pr_description: true`, the action asks Claude for the title and body instead:

* The title is `<destination_head_branch>: <generated title>`. It summarizes the earlier pull requests plus the current change.
* The body is the previous body, then a `---` line, then a generated section that describes only the current changes, then a `Source commit:` link. If the latest push changed none of the synced files, the generated section is one line saying so. If there is no previous body, the body is the generated section and the link.

The action reads the source push from the GitHub API, not from the local checkout. The source repository is the repository the workflow runs in (`GITHUB_REPOSITORY` at `GITHUB_SHA`), and synced paths are taken relative to its root. The push is `compare/{before}...{sha}`, where `before` comes from the push event. When the push created the branch, was forced, or the event payload is missing, it is the single pushed commit. The compare API returns at most 300 files, so a push with 300 or more files is described from what it returns. The diff is then marked partial and the "changed none of the synced files" notice is not sent.

What is sent to Anthropic:

* The titles and bodies of the earlier pull requests, newest first (up to 30 KB). These are the pull requests the action closes plus the reused pull request, capped at the 10 newest.
* This push's commit messages, the last 50 (up to 20 KB).
* This push's per-file patches for the synced files only (up to 200 KB). Files matching the built-in or `pr_description_exclude` globs keep their header line but drop their patch.
* The diff stat of the sync commit against the cloned default branch, as `git diff --stat HEAD~1` reports it (up to 20 KB).

The built-in exclusions are `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `*.min.js`, `*.min.css`, and `*.map`. The lock files match at any depth. When the patches are larger than 200 KB, the generated content ends with a note such as `_Generated from a partial diff (first 200 KB of 437 KB)._`.

If generation fails (missing `ANTHROPIC_API_KEY`, a failed GitHub API call, an Anthropic error, unparseable output), the action logs a warning and uses the default title with the non-generated body: the previous body, a `---` line, and the `Source commit:` link. With no previous body, the body is `destination_head_branch`.

### Previous body
The previous body comes from the newest earlier pull request with a real description. A body that is empty or just the branch name doesn't count. The reused pull request counts as the newest, so its body keeps accumulating on each push. This applies whether or not generation succeeded.

The 60,000-byte limit only decides whether the previous body is kept. If the previous body, the `---` line, and what follows it would exceed the limit, an `Earlier updates: #N` link to that pull request replaces the previous body and the `---` line. What follows is the generated section and `Source commit:` link after a successful generation, and the `Source commit:` link alone otherwise.

## Development
The action is written in Python using only the standard library. Run the tests from the repository root:

    python3 -m unittest discover -s tests

`entrypoint.py` is the launcher the Docker image runs. The logic lives in the `repo_sync/` package:

- `commands.py`: `CommandRunner` and `CommandError`
- `git.py`: `GitClient` and `GitHubClient`, wrappers over `git` and `gh`, including listing, editing, and closing pull requests and the compare API call
- `settings.py`: `Settings` and `ConfigError`
- `previous_prs.py`: `PreviousPr`, the title and body of an earlier pull request
- `source_diff.py`: reads the source push from the GitHub API and applies the exclude globs
- `describe.py`: builds the Anthropic API request and parses the generated title and body
- `pr_body.py`: builds the pull request title and the accumulated body
- `sync.py`: `RepoSync` (including copying source files into the clone) and `SyncError`

The tests in `tests/` mirror these modules.
