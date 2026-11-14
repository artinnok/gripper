from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

import httpx
from pydantic import JsonValue


class APIClient(ABC):
    """Define HTTP requests and connection ownership for provider clients."""

    @abstractmethod
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, JsonValue] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Send an authenticated request and raise an API error on failure."""

    @abstractmethod
    def close(self) -> None:
        """Close the client's HTTP connections."""

    def __enter__(self) -> Self:
        """Return the client for use within a context manager."""

        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release connections when leaving the context manager."""

        self.close()
