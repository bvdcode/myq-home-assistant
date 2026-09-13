from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


@dataclass(frozen=True, slots=True)
class ParsedForm:
    action: str
    fields: dict[str, str]
    email_field: str | None
    password_field: str | None
    otp_field: str | None


class _FormParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[ParsedForm] = []
        self._page_url = page_url
        self._action: str | None = None
        self._inputs: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and self._action is None:
            self._action = attributes.get("action") or ""
            self._inputs = []
        elif tag == "input" and self._action is not None:
            self._inputs.append(attributes)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._finish_form()

    def close(self) -> None:
        super().close()
        self._finish_form()

    def _finish_form(self) -> None:
        if self._action is None:
            return
        self.forms.append(_parse_form(self._action, self._inputs, self._page_url))
        self._action = None
        self._inputs = []


def parse_forms(page_html: str, page_url: str = "") -> list[ParsedForm]:
    """Read sign-in forms and identify unambiguous credential fields."""
    parser = _FormParser(page_url)
    parser.feed(page_html)
    parser.close()
    return parser.forms


def _parse_form(
    action: str,
    inputs: list[dict[str, str | None]],
    page_url: str,
) -> ParsedForm:
    fields: dict[str, str] = {}
    email_fields: list[str] = []
    password_fields: list[str] = []
    otp_fields: list[str] = []
    visible_fields: list[str] = []

    for attributes in inputs:
        name = attributes.get("name")
        if not name or "disabled" in attributes:
            continue
        field_type = (attributes.get("type") or "text").casefold()
        if field_type in {"button", "image", "reset", "submit", "file"}:
            continue
        if field_type in {"checkbox", "radio"}:
            if "checked" in attributes:
                value = attributes.get("value")
                fields[name] = "on" if value is None else value
            continue
        fields[name] = attributes.get("value") or ""
        if field_type == "hidden":
            continue
        identity = " ".join(
            (name, attributes.get("id") or "", attributes.get("autocomplete") or "")
        ).casefold()
        if field_type == "email" or "email" in identity:
            email_fields.append(name)
        if field_type == "password":
            password_fields.append(name)
        if field_type in {"number", "tel", "text"} and name not in email_fields:
            visible_fields.append(name)
            if _is_otp_field(identity):
                otp_fields.append(name)

    if not otp_fields and "verifyotp" in urlsplit(urljoin(page_url, action)).path.casefold():
        otp_fields = [name for name in visible_fields if name.casefold().endswith("code")]
        if not otp_fields and len(visible_fields) == 1:
            otp_fields = visible_fields
    return ParsedForm(
        action,
        fields,
        _single_field(email_fields),
        _single_field(password_fields),
        _single_field(otp_fields),
    )


def _is_otp_field(identity: str) -> bool:
    return (
        "otp" in identity
        or "one-time-code" in identity
        or re.search(r"(^|\W)(verification|security)[_-]?code($|\W)", identity) is not None
    )


def _single_field(fields: list[str]) -> str | None:
    if len(fields) == 1:
        return fields[0]
    return None
