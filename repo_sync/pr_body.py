from repo_sync.git import PreviousPr

MAX_BODY_BYTES = 60000


def build_title(branch: str, generated_title: str | None) -> str:
    if generated_title:
        return f"{branch}: {generated_title}"
    return branch


def _newest_real_body(previous_prs: list[PreviousPr]) -> PreviousPr | None:
    # A body equal to the head branch is the default placeholder, not a description.
    for pr in previous_prs:
        body = pr.body.strip()
        if body and body != pr.head_branch.strip():
            return pr
    return None


def build_body(
    previous_prs: list[PreviousPr],
    section: str,
    generation_succeeded: bool,
    branch: str,
) -> str:
    previous = _newest_real_body(previous_prs)
    if previous is not None:
        body = f"{previous.body.rstrip()}\n\n---\n\n{section}"
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            return f"Earlier updates: #{previous.number}\n\n{section}"
        return body
    if generation_succeeded:
        return section
    return branch
