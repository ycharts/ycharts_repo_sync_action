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
          with:
            source_folder: 'source-folder'
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

## Variables
* source_folder: The folder to be moved. Uses the same syntax as the `cp` command. Incude the path for any files not in the repositories root directory.
* destination_repo: The repository to place the file or directory in.
* destination_folder: [optional] The folder in the destination repository to place the file in, if not the root directory.
* user_email: The GitHub user email associated with the API token secret.
* user_name: The GitHub username associated with the API token secret.
* destination_base_branch: [optional] The branch into which you want your code merged. Default is `main`.
* destination_head_branch: The branch to create to push the changes. Cannot be `master` or `main`.
* pull_request_reviewers: [optional] The pull request reviewers. It can be only one (just like 'reviewer') or many (just like 'reviewer1,reviewer2,...')
* pull_request_labels: [optional] Labels to add to the pull request, comma-separated (just like 'label1,label2,...'). Labels that don't exist in the destination repository are skipped with a warning.
* close_existing_pull_requests: [optional] Set to `false` to leave open the pull requests in the destination repository whose title contains `destination_head_branch`. When `true`, the action closes them before it creates the new pull request. Default is `true`.
* source_commit: [optional] A commit hash, usually `${{ github.sha }}`. When set, the action copies only the files that commit added or modified, compared with its first parent, and still filters them by `source_folder` and `source_files`. This works the same for squash commits and merge commits; for a merge commit, that is every file the merged branch brought in. Deleted files and the old path of a renamed file are not removed from the destination. Each file is copied in full, not as a patch. If the source checkout is shallow, which is the default for `actions/checkout`, the action fetches the commit's parent and fails if it can't. With rebase-and-merge or a push of several commits, only the given commit's files are copied. When empty, every file matching `source_files` is copied. Default is empty.

## ENV
* API_TOKEN_GITHUB: You must create a personal access token in you account. Follow the link:
- [Personal access token](https://docs.github.com/en/free-pro-team@latest/github/authenticating-to-github/creating-a-personal-access-token)

> You must select the scopes: 'repo = Full control of private repositories', 'admin:org = read:org' and 'write:discussion = Read:discussion'; 


## Behavior Notes
The action will create any destination paths if they don't exist. It will also overwrite existing files if they already exist in the locations being copied to. It will not delete the entire destination repository.
