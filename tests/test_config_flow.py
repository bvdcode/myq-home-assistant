from unittest.mock import MagicMock

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myq.const import (
    CONF_EMAIL,
    CONF_MFA_METHOD,
    CONF_TOKENS,
    DOMAIN,
    MFA_METHOD_EMAIL,
    MFA_METHOD_SMS,
)
from custom_components.myq.exceptions import (
    MyQCloudflareChallengeError,
    MyQInvalidCredentialsError,
    MyQInvalidMfaError,
)
from custom_components.myq.models import GarageDoor, OAuthTokens

EMAIL = "driver@example.com"
PASSWORD = "secret"
TOKENS = OAuthTokens("access", "refresh", 1234567890.0)
DOOR = GarageDoor("account-1", "door-1", "Garage", "Wi-Fi GDO", "closed", True)


async def test_user_flow_creates_token_backed_entry_after_email_mfa(
    hass: HomeAssistant,
    mock_login_session: MagicMock,
    mock_myq_client: MagicMock,
) -> None:
    mock_login_session.async_start.return_value = None
    mock_login_session.async_submit_mfa.return_value = TOKENS
    mock_myq_client.async_get_garage_doors.return_value = (DOOR,)

    result = await _submit_credentials(hass, mfa_method=MFA_METHOD_EMAIL)

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "mfa"
    mock_login_session.async_start.assert_awaited_once_with(
        EMAIL,
        PASSWORD,
        MFA_METHOD_EMAIL,
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"code": "123456"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {
        CONF_EMAIL: EMAIL,
        CONF_MFA_METHOD: MFA_METHOD_EMAIL,
        CONF_TOKENS: {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": 1234567890.0,
        },
    }
    assert "password" not in result["data"]
    assert result["result"].unique_id == EMAIL
    mock_login_session.http_session.detach.assert_called_once_with()


async def test_user_flow_can_select_sms(
    hass: HomeAssistant,
    mock_login_session: MagicMock,
) -> None:
    mock_login_session.async_start.return_value = None

    await _submit_credentials(hass, mfa_method=MFA_METHOD_SMS)

    mock_login_session.async_start.assert_awaited_once_with(
        EMAIL,
        PASSWORD,
        MFA_METHOD_SMS,
    )


async def test_invalid_credentials_remain_on_user_form(
    hass: HomeAssistant,
    mock_login_session: MagicMock,
) -> None:
    mock_login_session.async_start.side_effect = MyQInvalidCredentialsError

    result = await _submit_credentials(hass)

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    mock_login_session.http_session.detach.assert_called_once_with()


async def test_cloudflare_challenge_has_specific_error(
    hass: HomeAssistant,
    mock_login_session: MagicMock,
) -> None:
    mock_login_session.async_start.side_effect = MyQCloudflareChallengeError

    result = await _submit_credentials(hass)

    assert result["errors"] == {"base": "cloudflare_challenge"}


async def test_invalid_mfa_can_be_retried(
    hass: HomeAssistant,
    mock_login_session: MagicMock,
) -> None:
    mock_login_session.async_start.return_value = None
    mock_login_session.async_submit_mfa.side_effect = MyQInvalidMfaError
    result = await _submit_credentials(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"code": "123456"},
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_mfa"}
    mock_login_session.http_session.detach.assert_not_called()


async def test_user_flow_aborts_duplicate_account(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL},
    ).add_to_hass(hass)

    result = await _submit_credentials(hass)

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauthentication_updates_tokens_and_mfa_method(
    hass: HomeAssistant,
    mock_login_session: MagicMock,
    mock_myq_client: MagicMock,
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=EMAIL,
        data=_entry_data(),
    )
    entry.add_to_hass(hass)
    mock_login_session.async_start.return_value = TOKENS
    mock_myq_client.async_get_garage_doors.return_value = (DOOR,)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"password": PASSWORD, CONF_MFA_METHOD: MFA_METHOD_SMS},
    )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_MFA_METHOD] == MFA_METHOD_SMS
    assert entry.data[CONF_TOKENS]["refresh_token"] == "refresh"
    assert "password" not in entry.data


async def _submit_credentials(
    hass: HomeAssistant,
    *,
    mfa_method: str = MFA_METHOD_EMAIL,
) -> config_entries.ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "email": " Driver@Example.com ",
            "password": PASSWORD,
            CONF_MFA_METHOD: mfa_method,
        },
    )


def _entry_data() -> dict[str, object]:
    return {
        CONF_EMAIL: EMAIL,
        CONF_MFA_METHOD: MFA_METHOD_EMAIL,
        CONF_TOKENS: {
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": 0.0,
        },
    }
