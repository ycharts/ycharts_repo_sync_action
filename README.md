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
            source_files: '*'
            destination_repo: 'user-name/repository-name'
            destination_folder: 'folder-name'
            destination_base_branch: 'branch-name'
            destination_head_branch: 'branch-name'
            user_email: 'user-name@paygo.com.br'
            user_name: 'user-name'
            pull_request_reviewers: 'reviewers'
            pull_request_labels: 'label1,label2'
            close_existing_pull_requests: false
            source_commit: ${{ github.sha }}
            generate_pr_description: true
            pr_description_exclude: 'dist/**,**/*.snap'

## Variables
* source_folder: The folder to be moved. Uses the same syntax as the `cp` command. Incude the path for any files not in the repositories root directory.
* source_files: The files, directories, or shell globs to copy, separated by spaces. Paths are relative to `source_folder`. Globs expand inside `source_folder`, directories are copied recursively, and each path keeps its location below `source_folder` in the destination. `*` does not match dotfiles, so name them explicitly (for example `.github`).
* destination_repo: The repository to place the file or directory in.
* destination_folder: [optional] The folder in the destination repository to place the file in, if not the root directory.
* user_email: The GitHub user email associated with the API token secret.
* user_name: The GitHub username associated with the API token secret.
* destination_base_branch: [optional] The branch into which you want your code merged. Default is `main`.
* destination_head_branch: The branch to create to push the changes. Cannot be `master` or `main`.
* pull_request_reviewers: [optional] The pull request reviewers. It can be only one (just like 'reviewer') or many (just like 'reviewer1,reviewer2,...')
* pull_request_labels: [optional] Labels to add to the pull request, comma-separated (just like 'label1,label2,...'). Labels that don't exist in the destination repository are skipped with a warning.
* close_existing_pull_requests: [optional] Set to `false` to leave open the pull requests in the destination repository whose title contains `destination_head_branch`. When `true`, the action closes them before it creates the new pull request. Default is `true`.
* source_commit: [optional] A commit hash, usually `${{ github.sha }}`. When set, only the files the commit added or modified, compared with its first parent, are copied, filtered by `source_folder` and `source_files`. Deleted files are not removed from the destination. Default is empty, which copies every file matching `source_files`.
* generate_pr_description: [optional] Set to `true` to generate the pull request title and description with the Claude API. The title is `<destination_head_branch>: <generated title>`. If generation fails for any reason, the default title and body are used. The diff of the synced files, the source commit messages, and a context diff of the other source changes are sent to Anthropic. Default is `false`.
* anthropic_model: [optional] The Claude model used when `generate_pr_description` is `true`. Default is `claude-opus-5-5`.
* pr_description_exclude: [optional] Comma-separated globs of files to leave out of the diff sent to Claude, added to the built-in exclusions. Whitespace around each glob is trimmed and empty entries are skipped. Globs are matched from the repository root, so `dist/**` matches the root `dist` folder and `*.snap` matches only root-level files; use `**/*.snap` to match at any depth. Excluded files still appear in the diff stat.

## ENV
* API_TOKEN_GITHUB: You must create a personal access token in you account. Follow the link:
- [Personal access token](https://docs.github.com/en/free-pro-team@latest/github/authenticating-to-github/creating-a-personal-access-token)

> You must select the scopes: 'repo = Full control of private repositories', 'admin:org = read:org' and 'write:discussion = Read:discussion'; 

* ANTHROPIC_API_KEY: [optional] Anthropic API key, required only when `generate_pr_description` is `true`. Store it as a secret.


## Behavior Notes
The action will create any destination paths if they don't exist. It will also overwrite existing files if they already exist in the locations being copied to. It will not delete the entire destination repository.

### Pull request description details
* The diff sent to the LLM excludes these files at any directory depth. These paths are still sent in the prompt for additional context.
  * `package-lock.json`
  * `yarn.lock`
  * `pnpm-lock.yaml`
  * `*.min.js`
  * `*.min.css` 
  * `*.map`.
* The diff is capped at 200 KB. When the filtered diff is larger, the description ends with a note such as `_Generated from a partial diff (first 200 KB of 437 KB)._`.

### Source context
Only the files this action syncs are part of the pull request, so the body describes just those changes. The model also sees the rest of the source change as context, and uses it for the title and to explain why the synced files changed.

* The prompt includes the source commit messages (oldest first, up to 50 commits and 20 KB), a stat of the whole source change, a context diff, and the synced diff. The context diff holds the source changes outside the synced files and is marked as not part of the pull request.
* The source range runs from the `before` commit of the push event through the pushed commit. For a new branch, a missing or unreadable event payload, or a force push where `before` is not an ancestor, the range is the pushed commit alone.
* The context diff uses the same built-in and `pr_description_exclude` exclusions as the synced diff and is capped at 100 KB.
* `actions/checkout` fetches one commit by default, so the action deepens the source checkout itself (up to four fetches of 50 commits, non-interactive) using the credentials `actions/checkout` persists. Keep its default `persist-credentials: true`.
* If the base commit can't be reached, the context diff is skipped with a warning in the log, only the pushed commit's message is sent, and the description is built from the synced diff.

## Tests

Run `sh test/run.sh` (requires `git` and `jq`; no network calls are made).
