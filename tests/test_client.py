from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientSession

from custom_components.myq.auth import MyQAuth
from custom_components.myq.client import MyQClient
from custom_components.myq.exceptions import MyQApiError, MyQAuthenticationError
from custom_components.myq.models import GarageDoor

from .test_auth import FakeResponse, FakeSession


async def test_client_discovers_garage_doors() -> None:
    session = FakeSession(
        request_responses=[
            FakeResponse("", body='{"accounts":[{"id":"account-1","name":"Home"}]}'),
            FakeResponse(
                "",
                body=(
                    '{"items":['
                    '{"device_family":"garagedoor","serial_number":"door-1",'
                    '"name":"Main garage","device_model":"Wi-Fi GDO",'
                    '"state":{"door_state":"closed","online":true,'
                    '"battery_backup_state":"charged","in_vacation_mode":false,'
                    '"attached_worklight_on":true,"active_fault_codes":["1-2",3],'
                    '"absolute_cycle_count":123,"service_cycle_count":45,'
                    '"last_device_activation_source":"myq_app"}},'
                    '{"device_family":"gateway","serial_number":"hub-1"}'
                    "]}"
                ),
            ),
        ]
    )
    auth = MagicMock(spec=MyQAuth)
    auth.async_access_token = AsyncMock(return_value="access")
    client = MyQClient(cast(ClientSession, session), auth)

    doors = await client.async_get_garage_doors()

    assert doors == (
        GarageDoor(
            account_id="account-1",
            serial_number="door-1",
            name="Main garage",
            device_model="Wi-Fi GDO",
            door_state="closed",
            online=True,
            battery_backup_state="charged",
            in_vacation_mode=False,
            attached_worklight_on=True,
            active_fault_codes=("1-2",),
            absolute_cycle_count=123,
            service_cycle_count=45,
            last_device_activation_source="myq_app",
        ),
    )
    assert session.calls[1].kwargs["headers"]["Authorization"] == "Bearer access"


async def test_commands_use_put_endpoints() -> None:
    session = FakeSession(
        request_responses=[
            FakeResponse("", status=204),
            FakeResponse("", status=204),
        ]
    )
    auth = MagicMock(spec=MyQAuth)
    auth.async_access_token = AsyncMock(return_value="access")
    client = MyQClient(cast(ClientSession, session), auth)
    door = GarageDoor("account-1", "door-1", "Garage", None, "closed", True)

    await client.async_open_door(door)
    await client.async_close_door(door)

    assert [(call.method, call.url) for call in session.calls] == [
        (
            "PUT",
            "https://account-devices-gdo.myq-cloud.com/api/v6.0/"
            "accounts/account-1/door_openers/door-1/open",
        ),
        (
            "PUT",
            "https://account-devices-gdo.myq-cloud.com/api/v6.0/"
            "accounts/account-1/door_openers/door-1/close",
        ),
    ]


@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (401, MyQAuthenticationError),
        (403, MyQAuthenticationError),
        (500, MyQApiError),
    ],
)
async def test_client_translates_http_errors(
    status: int,
    exception: type[Exception],
) -> None:
    session = FakeSession(request_responses=[FakeResponse("", status=status)])
    auth = MagicMock(spec=MyQAuth)
    auth.async_access_token = AsyncMock(return_value="access")
    client = MyQClient(cast(ClientSession, session), auth)

    with pytest.raises(exception):
        await client.async_get_accounts()
