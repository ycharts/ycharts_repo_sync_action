from dataclasses import dataclass


@dataclass(frozen=True)
class PreviousPr:
    number: int
    title: str
    body: str
    head_branch: str
    created_at: str
