from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    del enable_custom_integrations


@pytest.fixture
def mock_login_session() -> Generator[MagicMock]:
    with (
        patch("custom_components.myq.config_flow.MyQLoginSession") as login_class,
        patch("custom_components.myq.config_flow.async_create_clientsession") as create_session,
    ):
        session = create_session.return_value
        login = login_class.return_value
        login.async_start = AsyncMock()
        login.async_submit_mfa = AsyncMock()
        login.http_session = session
        yield login


@pytest.fixture
def mock_myq_client() -> Generator[MagicMock]:
    with patch("custom_components.myq.config_flow.MyQClient") as client_class:
        client = client_class.return_value
        client.async_get_garage_doors = AsyncMock()
        yield client
