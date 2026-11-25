from gripper.exceptions import GripperError


class MissingCredentials(GripperError):
    """Indicate that the selected API client has no access credentials."""


class APIRequestFailed(GripperError):
    """Report a failed HTTP request without exposing credentials or payloads."""

    def __init__(self, message: str, status_code: int | None = None, *, error_code: str | None = None) -> None:
        """Keep HTTP status and provider error code available for endpoint-specific handling."""

        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class InvalidAPIResponse(GripperError):
    """Indicate that an API response does not match its documented contract."""
