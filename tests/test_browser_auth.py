from __future__ import annotations

import urllib.parse
from unittest.mock import patch

import pytest

from custom_components.myq.browser_auth import _BrowserAuthorization
from custom_components.myq.exceptions import (
    MyQBrowserSessionExpiredError,
    MyQInvalidCallbackError,
)

AUTHORIZATION_URL = "https://partner-identity.myq-cloud.com/connect/authorize?client_id=myq"
ISSUER = "https%3A%2F%2Fpartner-identity.myq-cloud.com"


def test_browser_authorization_accepts_matching_callback_once() -> None:
    authorization = _BrowserAuthorization.create(AUTHORIZATION_URL)
    state = _state(authorization.url)
    callback_url = f"com.myqops://android?code=authorization-code&state={state}&iss={ISSUER}"

    assert authorization.consume(callback_url) == "authorization-code"

    with pytest.raises(MyQBrowserSessionExpiredError):
        authorization.consume(callback_url)


@pytest.mark.parametrize(
    "callback_url",
    [
        f"https://example.com/?code=authorization-code&state={{state}}&iss={ISSUER}",
        f"com.myqops://android?code=authorization-code&state=wrong&iss={ISSUER}",
        f"com.myqops://android?code=one&code=two&state={{state}}&iss={ISSUER}",
        f"com.myqops://android?code=authorization-code&state={{state}}&iss={ISSUER}#fragment",
        f"com.myqops://android?error=access_denied&state={{state}}&iss={ISSUER}",
        "com.myqops://android?code=authorization-code&state={state}",
        "com.myqops://android?code=authorization-code&state={state}&iss=https%3A%2F%2Fevil.example",
    ],
)
def test_browser_authorization_rejects_invalid_callback(callback_url: str) -> None:
    authorization = _BrowserAuthorization.create(AUTHORIZATION_URL)

    with pytest.raises(MyQInvalidCallbackError):
        authorization.consume(callback_url.format(state=_state(authorization.url)))


def test_browser_authorization_expires() -> None:
    with patch("custom_components.myq.browser_auth.time.monotonic", return_value=100.0):
        authorization = _BrowserAuthorization.create(AUTHORIZATION_URL)
    callback_url = (
        "com.myqops://android?code=authorization-code"
        f"&state={_state(authorization.url)}&iss={ISSUER}"
    )

    with (
        patch("custom_components.myq.browser_auth.time.monotonic", return_value=701.0),
        pytest.raises(MyQBrowserSessionExpiredError),
    ):
        authorization.consume(callback_url)


def _state(authorization_url: str) -> str:
    parameters = urllib.parse.parse_qs(urllib.parse.urlsplit(authorization_url).query)
    return parameters["state"][0]
