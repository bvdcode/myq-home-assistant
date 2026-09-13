import pytest

from custom_components.myq.auth import HttpPage, _login_form
from custom_components.myq.auth_forms import parse_forms as _parse_forms


@pytest.mark.parametrize(
    ("markup", "field_name"),
    [
        ('<input name="Otp" type="number">', "Otp"),
        ("<INPUT NAME='SecurityCode' TYPE='TEL'>", "SecurityCode"),
        ('<input name="verification_code" type="text">', "verification_code"),
        ('<input name="pin" autocomplete="one-time-code">', "pin"),
        ('<input name="pin" id="login_otp_input">', "pin"),
        ('<input name="Otp" data-disabled="false">', "Otp"),
        ('<input title="Enter > 0" name="Otp">', "Otp"),
        ('<input data-name="not-a-field" name="Otp">', "Otp"),
        ('<input name="Otp"><input name="OtpToken" type="hidden">', "Otp"),
    ],
)
def test_recognizes_editable_otp_field(markup: str, field_name: str) -> None:
    forms = _parse_forms(f'<form action="/verify">{markup}</form>')

    assert len(forms) == 1
    assert forms[0].otp_field == field_name


@pytest.mark.parametrize(
    "markup",
    [
        '<input name="Otp" type="hidden">',
        '<input name="Otp" disabled>',
        '<input name="Otp" disabled="false">',
        '<input data-name="Otp">',
        '<input name="Otp" type="checkbox">',
        '<input name="Otp" type="radio">',
        '<input name="Otp1"><input name="Otp2">',
        '<input name="PhoneNumber" type="tel">',
    ],
)
def test_does_not_guess_an_otp_field(markup: str) -> None:
    form = _parse_forms(f'<form action="/verify">{markup}</form>')[0]

    assert form.otp_field is None


def test_ignores_commented_out_forms() -> None:
    forms = _parse_forms(
        '<!-- <form action="/old"><input name="Otp"></form> -->'
        '<form action="/current"><input name="SecurityCode"></form>'
    )

    assert len(forms) == 1
    assert forms[0].action == "/current"


def test_preserves_hidden_values_and_checked_controls() -> None:
    form = _parse_forms(
        '<form action="/verify?returnUrl=%2Fconnect&amp;method=Sms">'
        '<input name="csrf" type="hidden" value="a&gt;b&amp;c">'
        '<input name="SelectedMfaMethod" type="radio" value="Sms" checked>'
        '<input name="SelectedMfaMethod" type="radio" value="Email">'
        '<input name="Remember" type="checkbox" checked>'
        '<input name="Empty" type="checkbox" value="" checked>'
        '<input name="Disabled" disabled value="no">'
        '<input name="Otp">'
        "</form>"
    )[0]

    assert form.action == "/verify?returnUrl=%2Fconnect&method=Sms"
    assert form.fields == {
        "csrf": "a>b&c",
        "SelectedMfaMethod": "Sms",
        "Remember": "on",
        "Empty": "",
        "Otp": "",
    }


@pytest.mark.parametrize("action", ["", 'action=""'])
def test_login_form_can_submit_to_the_current_page(action: str) -> None:
    page = HttpPage(
        "https://example.com/login",
        200,
        None,
        f'<form method="post" {action}><input type="email" name="Email">'
        '<input type="password" name="Password"></form>',
    )

    assert _login_form(page).action == ""


def test_ignores_incomplete_login_forms() -> None:
    page = HttpPage(
        "https://example.com/login",
        200,
        None,
        '<form action="/other"><input name="password" type="password"></form>'
        '<form action="/login"><input name="Email" type="email">'
        '<input name="Password" type="password"></form>',
    )

    assert _login_form(page).action == "/login"


def test_does_not_treat_an_email_field_as_an_otp_on_the_verification_page() -> None:
    form = _parse_forms(
        '<form><input name="Email"></form>',
        "https://example.com/AccountMfa/VerifyOtp",
    )[0]

    assert form.otp_field is None
