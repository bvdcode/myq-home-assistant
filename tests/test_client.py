import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientSession

from custom_components.myq.client import MyQClient
from custom_components.myq.exceptions import MyQApiError, MyQAuthenticationError
from custom_components.myq.models import GarageDoor, MyQCamera
from custom_components.myq.oauth import MyQAuth

from .http import FakeResponse, FakeSession, RecordedCall

CAMERA = MyQCamera("TC-0005-10000001", True)


@pytest.mark.parametrize(
    ("serial_number", "prefix"),
    [
        ("TC-0005-10000001", "TC"),
        ("GW-12345", "GW"),
        ("12345", None),
        ("UNIQUECAMERA-12345", None),
    ],
)
def test_camera_serial_prefix_never_exposes_full_serial(
    serial_number: str,
    prefix: str | None,
) -> None:
    assert MyQCamera(serial_number).serial_prefix == prefix


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


async def test_client_attaches_cameras_to_garage_doors() -> None:
    items = [
        {
            "device_family": "garagedoor",
            "serial_number": "door-1",
            "state": {"has_camera": True, "attached_camera_serial_number": "TC-0005-10000001"},
        },
        {
            "device_family": "camera",
            "serial_number": "TC-0005-10000001",
            "device_model": "Integrated camera",
            "state": {"online": False},
        },
        {
            "device_family": "garagedoor",
            "serial_number": "door-2",
            "state": {"attached_camera_serial_number": "TC-0005-10000002"},
        },
        {
            "device_family": "garagedoor",
            "serial_number": "door-3",
            "state": {"has_camera": False, "attached_camera_serial_number": ""},
        },
    ]
    session = FakeSession(
        request_responses=[
            FakeResponse("", body='{"accounts":[{"id":"account-1","name":"Home"}]}'),
            FakeResponse("", body=json.dumps({"items": items})),
        ]
    )
    auth = MagicMock(spec=MyQAuth)
    auth.async_access_token = AsyncMock(return_value="access")
    client = MyQClient(cast(ClientSession, session), auth)

    doors = await client.async_get_garage_doors()

    assert [(door.serial_number, door.camera) for door in doors] == [
        ("door-1", MyQCamera("TC-0005-10000001", False, "Integrated camera")),
        ("door-2", MyQCamera("TC-0005-10000002", None)),
        ("door-3", None),
    ]


async def test_camera_image_uses_tend_snapshot_endpoint() -> None:
    session = FakeSession(request_responses=[FakeResponse("", body=b"\xff\xd8\xff\xe0jpeg")])
    auth = MagicMock(spec=MyQAuth)
    auth.async_access_token = AsyncMock(return_value="access")
    client = MyQClient(cast(ClientSession, session), auth)

    image = await client.async_get_camera_image(CAMERA)

    assert image == b"\xff\xd8\xff\xe0jpeg"
    assert session.calls == [
        RecordedCall(
            "GET",
            "https://media.hubs.tend-us.tendplatform.com/cxs/api/"
            "devices/10000001/cam/recentImage.jpg",
            {
                "headers": {
                    "AntiCSRF": "AntiCSRF",
                    "Authorization": "Bearer access",
                    "SeedonkLocale": "en-US",
                    "partnerId": "myQ",
                }
            },
        )
    ]


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


@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (401, MyQAuthenticationError),
        (403, MyQAuthenticationError),
        (404, MyQApiError),
        (500, MyQApiError),
    ],
)
async def test_camera_image_translates_http_errors(
    status: int,
    exception: type[Exception],
) -> None:
    session = FakeSession(request_responses=[FakeResponse("", status=status)])
    auth = MagicMock(spec=MyQAuth)
    auth.async_access_token = AsyncMock(return_value="access")
    client = MyQClient(cast(ClientSession, session), auth)

    with pytest.raises(exception):
        await client.async_get_camera_image(CAMERA)


async def test_camera_image_rejects_a_response_that_is_not_jpeg() -> None:
    session = FakeSession(request_responses=[FakeResponse("", body=b'{"error":"camera-offline"}')])
    auth = MagicMock(spec=MyQAuth)
    auth.async_access_token = AsyncMock(return_value="access")
    client = MyQClient(cast(ClientSession, session), auth)

    with pytest.raises(MyQApiError):
        await client.async_get_camera_image(CAMERA)
