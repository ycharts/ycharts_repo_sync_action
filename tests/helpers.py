from sync import CommandError


class FakeRunner:
    def __init__(self, outputs=None, failures=None):
        self.calls = []
        self.outputs = outputs or {}
        self.failures = failures or {}

    def __call__(self, args, cwd=None, capture=False):
        args = list(args)
        self.calls.append({"args": args, "cwd": cwd, "capture": capture})
        for prefix, code in self.failures.items():
            if args[: len(prefix)] == list(prefix):
                raise CommandError(args, code)
        if capture:
            for prefix, output in self.outputs.items():
                if args[: len(prefix)] == list(prefix):
                    return output
            return ""
        return ""

    @property
    def commands(self):
        return [call["args"] for call in self.calls]


def base_env(**overrides):
    env = {
        "INPUT_SOURCE_FOLDER": "src",
        "INPUT_SOURCE_FILES": "a.txt",
        "INPUT_DESTINATION_REPO": "org/repo",
        "INPUT_DESTINATION_HEAD_BRANCH": "sync-branch",
        "INPUT_USER_EMAIL": "me@example.com",
        "INPUT_USER_NAME": "me",
        "GITHUB_REPOSITORY": "org/source",
        "GITHUB_SHA": "abc123",
        "API_TOKEN_GITHUB": "secret-token",
    }
    env.update(overrides)
    return env
