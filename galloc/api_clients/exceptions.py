from galloc.exceptions import GallocError


class MissingCredentials(GallocError):
    """Indicate that the selected API client has no access credentials."""


class APIRequestFailed(GallocError):
    """Report a failed HTTP request without exposing credentials or payloads."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Keep the HTTP status available for endpoint-specific handling."""

        super().__init__(message)
        self.status_code = status_code


class InvalidAPIResponse(GallocError):
    """Indicate that an API response does not match its documented contract."""
