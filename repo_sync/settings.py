import dataclasses
import datetime
from collections.abc import Callable, Iterable, Mapping


class ConfigError(Exception):
    def __init__(self, messages: Iterable[str]) -> None:
        self.messages: list[str] = list(messages)
        super().__init__("; ".join(self.messages))


@dataclasses.dataclass
class Settings:
    source_folder: str
    source_files: list[str]
    destination_repo: str
    destination_folder: str
    user_email: str
    user_name: str
    head_branch: str
    head_branch_prefix: str
    base_branch: str
    reviewers: str
    labels: list[str]
    github_repository: str
    github_sha: str

    @classmethod
    def from_env(
        cls, env: Mapping[str, str], now: Callable[[], datetime.datetime] | None = None
    ) -> Settings:
        def get(name: str) -> str:
            return env.get(name, "").strip()

        required: dict[str, str] = {
            "INPUT_SOURCE_FOLDER": "source_folder",
            "INPUT_SOURCE_FILES": "source_files",
            "INPUT_DESTINATION_REPO": "destination_repo",
            "INPUT_DESTINATION_HEAD_BRANCH": "destination_head_branch",
            "INPUT_USER_EMAIL": "user_email",
            "INPUT_USER_NAME": "user_name",
        }
        errors = [f"{label} must be defined" for var, label in required.items() if not get(var)]
        prefix = get("INPUT_DESTINATION_HEAD_BRANCH")
        if prefix in ("main", "master"):
            errors.append("destination_head_branch cannot be 'main' nor 'master'")
        if errors:
            raise ConfigError(errors)

        head = prefix
        if get("INPUT_TIMESTAMP_HEAD_BRANCH").lower() == "true":
            moment = now() if now else datetime.datetime.now(datetime.timezone.utc)
            head = f"{prefix}-{moment.strftime('%Y%m%d%H%M%S')}"

        labels = [part.strip() for part in env.get("INPUT_PULL_REQUEST_LABELS", "").split(",")]
        return cls(
            source_folder=get("INPUT_SOURCE_FOLDER"),
            source_files=get("INPUT_SOURCE_FILES").split(),
            destination_repo=get("INPUT_DESTINATION_REPO"),
            destination_folder=get("INPUT_DESTINATION_FOLDER"),
            user_email=get("INPUT_USER_EMAIL"),
            user_name=get("INPUT_USER_NAME"),
            head_branch=head,
            head_branch_prefix=prefix,
            base_branch=get("INPUT_DESTINATION_BASE_BRANCH") or "main",
            reviewers=get("INPUT_PULL_REQUEST_REVIEWERS"),
            labels=[label for label in labels if label],
            github_repository=get("GITHUB_REPOSITORY"),
            github_sha=get("GITHUB_SHA"),
        )
