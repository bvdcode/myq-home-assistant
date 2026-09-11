from __future__ import annotations

import hmac
import secrets
import time
import urllib.parse
from dataclasses import dataclass

from .const import BROWSER_AUTH_TIMEOUT, IDENTITY_BASE_URL, OAUTH_REDIRECT_URI
from .exceptions import MyQBrowserSessionExpiredError, MyQInvalidCallbackError


@dataclass(slots=True)
class _BrowserAuthorization:
    url: str
    _state: str
    _expires_at: float
    _consumed: bool = False

    @classmethod
    def create(cls, authorization_url: str) -> _BrowserAuthorization:
        state = secrets.token_urlsafe(32)
        split_url = urllib.parse.urlsplit(authorization_url)
        query = urllib.parse.parse_qsl(split_url.query, keep_blank_values=True)
        query.append(("state", state))
        url = urllib.parse.urlunsplit(
            (
                split_url.scheme,
                split_url.netloc,
                split_url.path,
                urllib.parse.urlencode(query),
                split_url.fragment,
            )
        )
        return cls(
            url=url,
            _state=state,
            _expires_at=time.monotonic() + BROWSER_AUTH_TIMEOUT.total_seconds(),
        )

    def consume(self, callback_url: str) -> str:
        if self._consumed or time.monotonic() >= self._expires_at:
            raise MyQBrowserSessionExpiredError

        callback = urllib.parse.urlsplit(callback_url)
        expected = urllib.parse.urlsplit(OAUTH_REDIRECT_URI)
        if (
            callback.scheme != expected.scheme
            or callback.netloc != expected.netloc
            or callback.path != expected.path
            or callback.fragment
        ):
            raise MyQInvalidCallbackError

        parameters = urllib.parse.parse_qs(callback.query, keep_blank_values=True)
        if "error" in parameters:
            raise MyQInvalidCallbackError

        code = _single_parameter(parameters, "code")
        state = _single_parameter(parameters, "state")
        issuer = _single_parameter(parameters, "iss")
        if not code or not hmac.compare_digest(state, self._state) or issuer != IDENTITY_BASE_URL:
            raise MyQInvalidCallbackError

        self._consumed = True
        return code


def _single_parameter(parameters: dict[str, list[str]], name: str) -> str:
    values = parameters.get(name, [])
    if len(values) != 1:
        raise MyQInvalidCallbackError
    return values[0]
