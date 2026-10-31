"""Protocol and local stream checks for TC cameras."""

from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from custom_components.myq.tend import (
    CxsConnection,
    CxsPacket,
    TendCamera,
    TendStreamError,
    VideoOffer,
    VideoTransport,
    _Channel,
    _sdnk,
    decode_video_payload,
    encode_cxs_packet,
    load_properties,
    parse_cameras,
    parse_peer_packet,
    register_packet,
    store_properties,
)
from custom_components.myq.tend_stream import TendLiveStream, _nals, _Viewer

# Shape of Tend's login reply: "<account alias>:<camera MAC>,<caps>", GSN = attached opener.
DEVICE_INFO_LIST = (
    "myqalias:00:00:5E:00:53:01,AES=0123456789abcdef+GSN=door-2+LIP=192.168.1.20\n"
    "myqalias:00:00:5E:00:53:02,AES=abcdef0123456789+GSN=door-1+LIP=192.168.1.21"
)


def test_tend_cxs_and_udp_packets_match_protocol_layout() -> None:
    properties = {"CorrelationID": "LOG-test", "CX_DSTID": "myq:test", "multiline": "one\ntwo"}
    assert load_properties(store_properties(properties)) == properties

    packet = encode_cxs_packet(CxsPacket(519, b"body", source=b"source"))
    assert packet[:2] == b"A\x03"
    assert struct.unpack_from("<H", packet, 13)[0] == 519
    assert struct.unpack_from("<I", packet, 7)[0] == 4
    assert packet[27 + 16 : 27 + 16 + 6] == b"source"

    register = register_packet("myq123", "10.0.1.2", 20000, False)
    assert register[:4] == b"SDNK"
    assert register[7] == 1
    assert struct.unpack_from(">I", register, 16)[0] == len(register) - 20
    assert register[24] == ord("V")
    assert struct.unpack_from(">H", register, 45)[0] == 20000
    assert parse_peer_packet(bytes(109)) == []


def test_load_properties_follows_java_rules() -> None:
    text = (
        b"# a comment cannot continue \\\n"
        b"key1 = = value\n"
        b"key2==abc\n"
        b"key3:long \\\n"
        b"    value\n"
        b"key4 = ends with backslash \\\\\n"
        b"esc\\=aped=v\n"
        b"\fkey5\tv5\r\n"
        b"! another comment\n"
    )
    assert load_properties(text) == {
        "key1": "= value",
        "key2": "=abc",
        "key3": "long value",
        "key4": "ends with backslash \\",
        "esc=aped": "v",
        "key5": "v5",
    }


def test_viewer_end_marker_survives_a_full_queue() -> None:
    viewer = _Viewer()
    for _ in range(viewer.frames.maxsize):
        viewer.put(b"frame")
    viewer.end()
    viewer.put(b"late")
    queued = [viewer.frames.get_nowait() for _ in range(viewer.frames.qsize())]
    assert queued[-1] == b""
    assert b"late" not in queued


def test_tend_video_payload_decrypts_h264_record() -> None:
    frame = b"\x00\x00\x00\x01\x65\x88\x84"
    combined = struct.pack("<I", len(frame)) + frame
    padder = PKCS7(128).padder()
    padded = padder.update(combined) + padder.finalize()
    iv = bytes(range(16))
    key = b"0123456789abcdef"
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = iv + encryptor.update(padded) + encryptor.finalize()
    body = b"\x20\x00\x00\x00" + encrypted
    payload = b"w1" + struct.pack("<I", len(body)) + body
    assert decode_video_payload(payload, key.decode()) == [frame]
    assert decode_video_payload(payload, "wrong") == []

    frames: list[bytes] = []
    transport = VideoTransport(
        TendCamera("12345", "12345:aa:bb", key.decode(), "192.168.1.20"),
        VideoOffer("192.168.1.30", 30000, key.decode(), "LOG-test"),
        frames.append,
    )
    rtp = b"\x80\x60" + struct.pack(">HII", 7, 1000, 123) + payload
    transport._handle_media(struct.pack("<II", 1, 0) + rtp[:12])
    transport._handle_media(struct.pack("<II", 1, 1) + rtp[12:])
    transport._handle_media(struct.pack("<II", 2, 0) + b"next")
    assert frames == [frame]
    report = transport._receiver_report()
    assert report is not None
    assert report[:2] == b"\x81\xc9"


def test_tend_camera_selection_data_and_nals() -> None:
    cameras = parse_cameras({"device-info-list": DEVICE_INFO_LIST})
    assert [camera.opener_serial for camera in cameras] == ["door-2", "door-1"]
    assert [camera.device_id for camera in cameras] == ["myqalias", "myqalias"]
    assert cameras[1].destination == "myqalias:00:00:5E:00:53:02"
    assert cameras[0].local_ip == "192.168.1.20"
    assert _nals(b"\x00\x00\x01\x67abc\x00\x00\x00\x01\x68def") == [
        (7, b"\x00\x00\x01\x67abc"),
        (8, b"\x00\x00\x00\x01\x68def"),
    ]


async def test_live_stream_selects_camera_by_attached_opener() -> None:
    offer = VideoOffer("relay.example", 30000, "abcdef0123456789", "LOG-test")
    with (
        patch("custom_components.myq.tend_stream.CxsConnection") as connection_class,
        patch(
            "custom_components.myq.tend_stream.request_video", AsyncMock(return_value=offer)
        ) as request_video,
        patch("custom_components.myq.tend_stream.VideoTransport") as transport_class,
    ):
        connection = connection_class.return_value
        connection.login = AsyncMock()
        connection.command = AsyncMock()
        connection.properties = {"device-info-list": DEVICE_INFO_LIST}
        transport_class.return_value.punch = AsyncMock(
            side_effect=[TendStreamError("UDP timed out"), None]
        )

        live = TendLiveStream("TC-0005-12345", "door-1", AsyncMock(return_value="token"))
        with patch("custom_components.myq.tend_stream.asyncio.sleep", AsyncMock()):
            await live._open()
        assert request_video.await_args is not None
        assert request_video.await_args.args[1].destination == "myqalias:00:00:5E:00:53:02"
        # The failed first offer is released before the camera is asked again.
        assert request_video.await_count == 2
        connection.command.assert_any_await(
            "stop-video-receive|myqalias|9|", "myqalias:00:00:5E:00:53:02", "LOG-test"
        )
        connection.close.assert_not_called()

        # Losing signaling ends viewers at once so they reconnect to a fresh session.
        viewer = _Viewer()
        live._viewers.add(viewer)
        connection_class.call_args.args[0](connection)
        assert live._close_task is not None
        await live._close_task
        assert viewer.frames.get_nowait() == b""
        assert live._connection is None
        connection.close.assert_called_once()

        with pytest.raises(TendStreamError):
            await TendLiveStream("TC-0005-1", "door-9", AsyncMock(return_value="token"))._open()
        assert connection.close.call_count == 2


async def test_signaling_reader_routes_replies_and_reports_loss() -> None:
    lost: list[CxsConnection] = []
    connection = CxsConnection(lost.append)
    reader = asyncio.StreamReader()
    reply_body = b"accept-video-receive|myqalias|9|"
    reply_packet = encode_cxs_packet(CxsPacket(411, reply_body, source=b"rtp://relay:5000"))
    connection._reader = reader
    # The camera answers the instant the command is sent.
    connection._writer = MagicMock(
        drain=AsyncMock(side_effect=lambda: reader.feed_data(reply_packet))
    )
    read_task = asyncio.create_task(connection._read_loop())
    connection._read_task = read_task

    reader.feed_data(encode_cxs_packet(CxsPacket(2000, b"device-info|unrequested")))
    correlation, reply = await asyncio.wait_for(
        connection.request(
            "require-video-send|myqalias|9|", "myqalias:mac", b"accept-video-receive|"
        ),
        2,
    )
    assert correlation.startswith("LOG-")
    assert reply.source == b"rtp://relay:5000"

    reader.feed_eof()
    await asyncio.wait_for(read_task, 2)
    assert lost == [connection]
    with pytest.raises(TendStreamError):
        await connection.command("stop-video-receive|myqalias|9|", "myqalias:mac")
    connection.close()


def test_video_transport_ignores_datagrams_from_unknown_hosts() -> None:
    frames: list[bytes] = []
    transport = VideoTransport(
        TendCamera("myqalias", "myqalias:mac", "0123456789abcdef", "192.168.1.20"),
        VideoOffer("relay.example", 30000, "0123456789abcdef", "LOG-test"),
        frames.append,
    )
    transport._relay_ip = "203.0.113.5"
    transport.channels = [_Channel(MagicMock(), 20000, False), _Channel(MagicMock(), 20001, True)]
    media = struct.pack("<II", 1, 0) + bytes(16)
    announced = bytearray(110)
    announced[:4] = b"SDNK"
    announced[7] = 2
    announced[20:24] = bytes((198, 51, 100, 7))
    struct.pack_into(">H", announced, 40, 9808)

    stranger = ("192.168.1.99", 9808)
    transport.on_datagram(False, bytes(announced), stranger)
    transport.on_datagram(False, _sdnk(5, b"\x01"), stranger)
    transport.on_datagram(False, media, stranger)
    transport.on_datagram(False, _sdnk(5, b"\x01"), ("198.51.100.7", 9808))
    assert transport.channels[0].peer is None
    assert transport._fragments == []

    transport.on_datagram(False, bytes(announced), ("203.0.113.5", 30000))
    transport.on_datagram(False, _sdnk(5, b"\x01"), ("198.51.100.7", 9808))
    assert transport.channels[0].connected
    transport.on_datagram(False, media, ("192.168.1.20", 9808))
    assert transport._fragments == [bytes(16)]


async def test_loopback_stream_shares_one_session_and_waits_for_keyframe(
    socket_enabled: None,
) -> None:
    del socket_enabled
    live = TendLiveStream("TC-0005-12345", "door-1", AsyncMock(return_value="unused"))
    with patch.object(live, "_open", new_callable=AsyncMock) as open_session:
        url = urlsplit(await live.source_url())
        reader, writer = await asyncio.open_connection(url.hostname, url.port)
        writer.write(f"GET {url.path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
        await writer.drain()
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
        assert b"200 OK" in header
        await asyncio.sleep(0)
        live._on_frame(b"\x00\x00\x00\x01\x61p")
        live._on_frame(b"\x00\x00\x00\x01\x67s")
        live._on_frame(b"\x00\x00\x00\x01\x68q")
        assert reader.at_eof() is False
        live._on_frame(b"\x00\x00\x00\x01\x65i")
        assert await asyncio.wait_for(reader.readexactly(6), 2) == b"\x00\x00\x00\x01\x67s"
        assert await asyncio.wait_for(reader.readexactly(6), 2) == b"\x00\x00\x00\x01\x68q"
        assert await asyncio.wait_for(reader.readexactly(6), 2) == b"\x00\x00\x00\x01\x65i"
        open_session.assert_awaited_once()
        await asyncio.wait_for(live.close(), 2)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()


async def test_close_does_not_wait_for_a_hung_tend_login(socket_enabled: None) -> None:
    del socket_enabled
    live = TendLiveStream("TC-0005-12345", "door-1", AsyncMock(return_value="unused"))
    opening = asyncio.Event()

    async def hang() -> None:
        opening.set()
        await asyncio.Event().wait()

    with patch.object(live, "_open", side_effect=hang):
        url = urlsplit(await live.source_url())
        reader, writer = await asyncio.open_connection(url.hostname, url.port)
        writer.write(f"GET {url.path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
        await writer.drain()
        await asyncio.wait_for(opening.wait(), 2)

        await asyncio.wait_for(live.close(), 2)
        await asyncio.wait_for(reader.read(), 2)
        writer.close()
        await writer.wait_closed()
