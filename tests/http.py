from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FakeResponse:
    url: str
    status: int = 200
    body: str | bytes = ""
    headers: Mapping[str, str] = field(default_factory=dict)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def text(self) -> str:
        return self.body.decode() if isinstance(self.body, bytes) else self.body

    async def read(self) -> bytes:
        return self.body.encode() if isinstance(self.body, str) else self.body


@dataclass(frozen=True, slots=True)
class RecordedCall:
    method: str
    url: str
    kwargs: dict[str, Any]


class FakeSession:
    def __init__(
        self,
        *,
        request_responses: list[FakeResponse] | None = None,
        post_responses: list[FakeResponse] | None = None,
    ) -> None:
        self.request_responses = request_responses or []
        self.post_responses = post_responses or []
        self.calls: list[RecordedCall] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(RecordedCall(method, url, kwargs))
        response = self.request_responses.pop(0)
        response.url = url
        return response

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(RecordedCall("POST", url, kwargs))
        response = self.post_responses.pop(0)
        response.url = url
        return response
