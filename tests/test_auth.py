from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from aiohttp import ClientSession

from custom_components.myq.auth import MyQAuth, MyQLoginSession
from custom_components.myq.const import MFA_METHOD_EMAIL
from custom_components.myq.exceptions import MyQInvalidMfaError
from custom_components.myq.models import OAuthTokens


@dataclass(slots=True)
class FakeResponse:
    url: str
    status: int = 200
    body: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def text(self) -> str:
        return self.body


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
        self.closed = False

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

    async def close(self) -> None:
        self.closed = True


LOGIN_HTML = """
<form method="post" action="/Account/Login?returnUrl=auth">
  <input type="hidden" name="__RequestVerificationToken" value="csrf-login">
  <input type="email" name="Email">
  <input type="password" name="Password">
</form>
"""


def _mfa_html(method: str, *, error: str | None = None) -> str:
    validation = (
        f'<div class="validation-summary-errors"><ul><li>{error}</li></ul></div>' if error else ""
    )
    return f"""
    {validation}
    <form method="post" action="/AccountMfa/VerifyOtp?returnUrl=auth">
      <input type="hidden" name="__RequestVerificationToken" value="csrf-mfa">
      <input type="hidden" name="SelectedMfaMethod" value="{method}">
      <input type="number" id="login_otp_input" name="Otp">
    </form>
    """


async def test_login_selects_email_and_exchanges_code() -> None:
    session = FakeSession(
        request_responses=[
            FakeResponse("", 302, headers={"Location": "/Account/Login?returnUrl=auth"}),
            FakeResponse("", body=LOGIN_HTML),
            FakeResponse(
                "",
                302,
                headers={"Location": "/AccountMfa/VerifyOtp?returnUrl=auth"},
            ),
            FakeResponse("", body=_mfa_html("Sms")),
            FakeResponse("", body=_mfa_html("Email")),
            FakeResponse(
                "",
                302,
                headers={"Location": "/connect/authorize/callback?code=fresh-code"},
            ),
            FakeResponse(
                "",
                302,
                headers={"Location": "com.myqops://android?code=fresh-code"},
            ),
        ],
        post_responses=[
            FakeResponse("", body='{"token":"app-check","ttl":"3600s"}'),
            FakeResponse(
                "",
                body=('{"access_token":"access","refresh_token":"refresh","expires_in":3600}'),
            ),
        ],
    )
    login = MyQLoginSession(cast(ClientSession, session))

    assert await login.async_start("driver@example.com", "secret", MFA_METHOD_EMAIL) is None
    switch_call = session.calls[4]
    assert switch_call.method == "GET"
    assert "selectedMfaMethod=Email" in switch_call.url

    tokens = await login.async_submit_mfa("123456")

    assert tokens.access_token == "access"
    assert tokens.refresh_token == "refresh"
    assert tokens.expires_at > time.time() + 3500
    token_call = session.calls[-1]
    assert token_call.kwargs["headers"]["Firebase-AppCheck-Token"] == "app-check"
    assert token_call.kwargs["data"]["code_verifier"]


async def test_invalid_mfa_can_be_retried() -> None:
    session = FakeSession(
        request_responses=[
            FakeResponse("", 302, headers={"Location": "/Account/Login?returnUrl=auth"}),
            FakeResponse("", body=LOGIN_HTML),
            FakeResponse(
                "",
                302,
                headers={"Location": "/AccountMfa/VerifyOtp?returnUrl=auth"},
            ),
            FakeResponse("", body=_mfa_html("Email")),
            FakeResponse("", body=_mfa_html("Email", error="Incorrect one-time password.")),
            FakeResponse(
                "",
                302,
                headers={"Location": "/connect/authorize/callback?code=retry-code"},
            ),
            FakeResponse(
                "",
                302,
                headers={"Location": "com.myqops://android?code=retry-code"},
            ),
        ],
        post_responses=[
            FakeResponse("", body='{"token":"app-check"}'),
            FakeResponse(
                "",
                body=('{"access_token":"access","refresh_token":"refresh","expires_in":3600}'),
            ),
        ],
    )
    login = MyQLoginSession(cast(ClientSession, session))
    await login.async_start("driver@example.com", "secret", MFA_METHOD_EMAIL)

    with pytest.raises(MyQInvalidMfaError, match="Incorrect one-time password"):
        await login.async_submit_mfa("000000")

    tokens = await login.async_submit_mfa("123456")
    assert tokens.refresh_token == "refresh"


async def test_expired_access_token_refreshes_once_for_concurrent_callers() -> None:
    session = FakeSession(
        post_responses=[
            FakeResponse(
                "",
                body=(
                    '{"access_token":"new-access","refresh_token":"new-refresh","expires_in":3600}'
                ),
            )
        ]
    )
    persisted: list[OAuthTokens] = []
    auth = MyQAuth(
        cast(ClientSession, session),
        OAuthTokens("expired", "old-refresh", 0),
        persisted.append,
    )

    import asyncio

    first, second = await asyncio.gather(
        auth.async_access_token(),
        auth.async_access_token(),
    )

    assert (first, second) == ("new-access", "new-access")
    assert len(session.calls) == 1
    assert len(persisted) == 1
    assert persisted[0].access_token == "new-access"
    assert persisted[0].refresh_token == "new-refresh"
