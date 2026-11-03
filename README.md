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
* source_files: The files or folders inside `source_folder` to copy, separated by spaces. They are passed to `cp --parents`, so their relative paths are kept in the destination.
* source_folder: The folder to be moved. Uses the same syntax as the `cp` command. Incude the path for any files not in the repositories root directory.
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
* pr_description_exclude: [optional] Comma-separated globs of files whose patches are left out of what is sent to Claude, added to the built-in exclusions. Whitespace around each glob is trimmed and empty entries are skipped. Each glob is matched against the whole file path using shell `case` rules, so `*` also matches across `/`: `*.snap` matches `.snap` files at any depth and `dist/*` covers everything under `dist`. Avoid `**/*.snap`, which does not match a root-level `a.snap`. Excluded files still appear in the diff stat and as a header line without a patch.

## ENV
* API_TOKEN_GITHUB: You must create a personal access token in you account. When `generate_pr_description` is `true`, the token must also be able to read the source repository, because the action reads the source push through the GitHub API. Follow the link:
- [Personal access token](https://docs.github.com/en/free-pro-team@latest/github/authenticating-to-github/creating-a-personal-access-token)

> You must select the scopes: 'repo = Full control of private repositories', 'admin:org = read:org' and 'write:discussion = Read:discussion'; 

* ANTHROPIC_API_KEY: [optional] Anthropic API key, required only when `generate_pr_description` is `true`. Store it as a secret.


## Behavior Notes
The action will create any destination paths if they don't exist. It will also overwrite existing files if they already exist in the locations being copied to. It will not delete the entire destination repository.

### Existing pull requests
When the action has changes to push, it closes the open pull requests in the destination repository that match the search below, then opens a new one. The action does not check who created a pull request. It finds them this way:

* If `pull_request_labels` is set, every open pull request that has all of the labels.
* Otherwise, every open pull request whose title contains `destination_head_branch`.

Either search looks at up to 100 pull requests. A pull request that can't be closed is skipped with a warning. The action records the title and body of the 10 newest matched pull requests for the description step, including any that failed to close.

> A label shared with other workflows, or with pull requests that target the same repository, makes the action close those pull requests too. Use labels only this action applies. With no labels, any open pull request whose title contains `destination_head_branch` is closed, even one this action didn't open.

### Pull request description
By default the pull request title is `destination_head_branch` and the body is the previous pull request's body, so reviewers don't lose earlier notes. With `generate_pr_description: true`, the action asks Claude for the title and body instead:

* The title is `<destination_head_branch>: <generated title>`. It summarizes the changes of the closed pull requests plus the current change.
* The body is the previous body (see below), then a `---` line, then a generated section that describes only the current changes, then a `Source commit:` link. If the latest push changed none of the synced files, the generated section is one line saying so. If there is no previous body, the body is the generated section and the link.

The action reads the source push from the GitHub API, not from the local checkout. The source repository is the repository the workflow runs in (`$GITHUB_REPOSITORY` at `$GITHUB_SHA`), and synced paths are taken relative to its root. The push is `compare/{before}...{sha}`, where `before` comes from the push event. When the push created the branch, was forced, or the event payload is missing, it is the single pushed commit. The compare API returns at most 300 files and 250 commits, so a larger push is described from what it returns. At 300 files or more the diff is marked partial, and the "changed none of the synced files" notice is not sent.

What is sent to Anthropic:

* The titles and bodies of the closed pull requests, newest first (up to 30 KB).
* This push's commit messages (up to 50 commits and 20 KB).
* This push's per-file patches for the synced files only (up to 200 KB). Files matching the built-in or `pr_description_exclude` globs keep their header line but drop their patch.
* The diff stat of the sync commit against the cloned default branch, as `git diff --stat HEAD~1` reports it (up to 20 KB).

The built-in exclusions are `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `*.min.js`, `*.min.css`, and `*.map`, all matched at any depth. When the patches are larger than 200 KB, the generated content ends with a note such as `_Generated from a partial diff (first 200 KB of 437 KB)._`.

If generation fails for any reason (missing `ANTHROPIC_API_KEY`, a failed GitHub API call, an Anthropic error, unparseable output), the action logs a warning and uses the default title with the non-generated body: the previous body, a `---` line, and the `Source commit:` link. With no previous body, the body is `destination_head_branch`.

### Previous body
The previous body comes from the newest closed pull request with a real description. A body that is empty or just the branch name doesn't count. This applies whether or not generation succeeded.

Nothing caps the body as a whole. The 60,000-byte limit only decides whether the previous body is kept:

* If the previous body, the `---` line, and what follows it would exceed the limit, an `Earlier updates: #N` link replaces the previous body and the `---` line.
* What follows is the generated section and `Source commit:` link after a successful generation, and the `Source commit:` link alone otherwise.
