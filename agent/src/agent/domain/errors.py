"""Общие ошибки границы приложения."""


class AgentError(RuntimeError):
    def __init__(self, detail: str, *, status: int = 502) -> None:
        super().__init__(detail)

        self.status = status
