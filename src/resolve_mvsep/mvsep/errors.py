"""Sanitized failures: never retain a request, URL, credentials or raw response."""


class MVSEPError(RuntimeError):
    """An API operation failed."""


class NetworkError(MVSEPError):
    """Transport failure; creation must not be automatically retried."""


class DeadlineExceeded(MVSEPError):
    """The local wait budget expired; a remote job may still be running."""


class APIError(MVSEPError):
    def __init__(self, message: str, *, status_code: int = 0, retry_after: float = 0):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status_code == 429 or 500 <= self.status_code <= 599
