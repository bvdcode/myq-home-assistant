from __future__ import annotations

import urllib.parse
from unittest.mock import patch

import pytest

from custom_components.myq.browser_auth import _BrowserAuthorization
from custom_components.myq.const import BROWSER_AUTH_TIMEOUT
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
        f"com.myqops://other?code=authorization-code&state={{state}}&iss={ISSUER}",
        f"com.myqops://android/callback?code=authorization-code&state={{state}}&iss={ISSUER}",
        f"com.myqops://android?code=authorization-code&state=wrong&iss={ISSUER}",
        f"com.myqops://android?code=one&code=two&state={{state}}&iss={ISSUER}",
        f"com.myqops://android?state={{state}}&iss={ISSUER}",
        f"com.myqops://android?code=&state={{state}}&iss={ISSUER}",
        f"com.myqops://android?code=authorization-code&iss={ISSUER}",
        f"com.myqops://android?code=authorization-code&state={{state}}&state=wrong&iss={ISSUER}",
        f"com.myqops://android?code=authorization-code&state={{state}}&iss={ISSUER}&iss={ISSUER}",
        f"com.myqops://android?code=authorization-code&state={{state}}&iss={ISSUER}#fragment",
        f"com.myqops://android?error=access_denied&state={{state}}&iss={ISSUER}",
        "com.myqops://android?code=authorization-code&state={state}",
        "com.myqops://android?code=authorization-code&state={state}&iss=",
        "com.myqops://android?code=authorization-code&state={state}&iss=https%3A%2F%2Fevil.example",
    ],
)
def test_browser_authorization_rejects_invalid_callback(callback_url: str) -> None:
    authorization = _BrowserAuthorization.create(AUTHORIZATION_URL)
    state = _state(authorization.url)

    with pytest.raises(MyQInvalidCallbackError):
        authorization.consume(callback_url.format(state=state))

    valid_callback = f"com.myqops://android?code=authorization-code&state={state}&iss={ISSUER}"
    assert authorization.consume(valid_callback) == "authorization-code"


@pytest.mark.parametrize("expiry_offset", [0.0, 1.0])
def test_browser_authorization_expires(expiry_offset: float) -> None:
    with patch("custom_components.myq.browser_auth.time.monotonic", return_value=100.0):
        authorization = _BrowserAuthorization.create(AUTHORIZATION_URL)
    callback_url = (
        "com.myqops://android?code=authorization-code"
        f"&state={_state(authorization.url)}&iss={ISSUER}"
    )

    with (
        patch(
            "custom_components.myq.browser_auth.time.monotonic",
            return_value=100.0 + BROWSER_AUTH_TIMEOUT.total_seconds() + expiry_offset,
        ),
        pytest.raises(MyQBrowserSessionExpiredError),
    ):
        authorization.consume(callback_url)


def test_browser_authorization_preserves_existing_query_parameters() -> None:
    authorization = _BrowserAuthorization.create(
        f"{AUTHORIZATION_URL}&scope=openid&scope=offline_access&empty="
    )
    parameters = urllib.parse.parse_qs(
        urllib.parse.urlsplit(authorization.url).query,
        keep_blank_values=True,
    )

    assert parameters["client_id"] == ["myq"]
    assert parameters["scope"] == ["openid", "offline_access"]
    assert parameters["empty"] == [""]
    assert len(parameters["state"]) == 1
    assert parameters["state"][0]


def _state(authorization_url: str) -> str:
    parameters = urllib.parse.parse_qs(urllib.parse.urlsplit(authorization_url).query)
    return parameters["state"][0]
