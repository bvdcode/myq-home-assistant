"""Tend/Seedonk TC camera signaling and video transport.

Protocol behavior adapted from Kyle Boyer's homebridge-myq-camera (MIT license).
See THIRD_PARTY_NOTICES.md in this integration directory.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
import secrets
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from homeassistant.util.ssl import client_context

from .const import TEND_PARTNER_ID

_LOGGER = logging.getLogger(__name__)
_LOGIN_HOST = "server.tend-us.tendplatform.com"
_LOGIN_PORT = 5104
_CXS_HEADER = 27
_MAX_CXS_PACKET = 2 * 1024 * 1024
_SDNK_MAGIC = b"SDNK"
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_PROPERTY_SPACE = " \t\f"


class TendStreamError(Exception):
    """A TC camera stream could not be opened or read."""


@dataclass(slots=True)
class CxsPacket:
    action: int
    body: bytes = b""
    options: bytes = bytes(16)
    source: bytes = b""
    result: int = 2
    destination: bytes = b"**"


def encode_cxs_packet(packet: CxsPacket) -> bytes:
    """Encode a CXS command or login packet."""
    if len(packet.options) % 16 or len(packet.destination) != 2:
        raise ValueError("Invalid CXS packet options")
    header_length = _CXS_HEADER + len(packet.options) + len(packet.source)
    if header_length > 65535 or len(packet.body) > _MAX_CXS_PACKET:
        raise ValueError("CXS packet is too large")
    header = bytearray(_CXS_HEADER)
    header[0:2] = b"A\x03"
    struct.pack_into(
        "<HHBIHHH",
        header,
        2,
        header_length,
        len(packet.options),
        0,
        len(packet.body),
        121,
        packet.action,
        packet.result,
    )
    header[17:19] = packet.destination
    return bytes(header) + packet.options + packet.source + packet.body


def _escape_property(value: str, *, key: bool = False) -> str:
    escaped = []
    for index, char in enumerate(value):
        if char == "\\":
            escaped.append("\\\\")
        elif char in "\t\n\r\f":
            escaped.append("\\" + {"\t": "t", "\n": "n", "\r": "r", "\f": "f"}[char])
        elif char in "=:" or (key and char in " #!") or (index == 0 and char == " "):
            escaped.append("\\" + char)
        elif ord(char) < 32 or ord(char) > 126:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def store_properties(values: dict[str, str]) -> bytes:
    """Encode the Java properties payload expected by the CXS broker."""
    lines = ["#myQ TC camera"]
    lines.extend(
        f"{_escape_property(k, key=True)}={_escape_property(v)}" for k, v in values.items()
    )
    return ("\n".join(lines) + "\n").encode("latin1")


def _unescape_property(value: str) -> str:
    return re.sub(
        r"\\(u[0-9a-fA-F]{4}|.)",
        lambda item: (
            chr(int(item[1][1:], 16))
            if item[1].startswith("u") and len(item[1]) == 5
            else {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}.get(item[1], item[1])
        ),
        value,
    )


def load_properties(data: bytes) -> dict[str, str]:
    """Decode Java properties: comments, continuations, separators, and escapes."""
    result: dict[str, str] = {}
    lines = iter(_LINE_BREAK.split(data.decode("latin1")))
    for physical in lines:
        line = physical.lstrip(_PROPERTY_SPACE)
        if not line or line[0] in "#!":
            continue
        # An odd run of trailing backslashes continues the line; its leading space is dropped.
        while (len(line) - len(line.rstrip("\\"))) % 2:
            line = line[:-1] + next(lines, "").lstrip(_PROPERTY_SPACE)
        key_end = 0
        while key_end < len(line) and line[key_end] not in "=:" + _PROPERTY_SPACE:
            key_end += 2 if line[key_end] == "\\" else 1
        value = line[key_end:].lstrip(_PROPERTY_SPACE)
        if value[:1] in ("=", ":"):
            value = value[1:].lstrip(_PROPERTY_SPACE)
        result[_unescape_property(line[:key_end])] = _unescape_property(value)
    return result


class CxsConnection:
    """TLS signaling connection to the Tend camera broker."""

    def __init__(self, on_lost: Callable[[CxsConnection], None] | None = None) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._options = bytes(16)
        self.properties: dict[str, str] = {}
        self._keepalive_task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[None] | None = None
        self._replies: list[tuple[bytes, asyncio.Future[CxsPacket]]] = []
        self._on_lost = on_lost
        self._lost = False

    async def _connect(self, host: str) -> None:
        self.close_transport()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host, _LOGIN_PORT, ssl=client_context(), server_hostname=_LOGIN_HOST
                ),
                10,
            )
        except (OSError, TimeoutError) as error:
            raise TendStreamError("Tend signaling connection failed") from error

    async def _send(self, packet: CxsPacket) -> None:
        if self._writer is None or self._lost:
            raise TendStreamError("Tend signaling is disconnected")
        self._writer.write(encode_cxs_packet(packet))
        await self._writer.drain()

    async def _next(self, wait_seconds: float | None = 10) -> tuple[CxsPacket, bytes]:
        reader = self._reader
        if reader is None:
            raise TendStreamError("Tend signaling is disconnected")
        try:
            async with asyncio.timeout(wait_seconds):
                header = await reader.readexactly(_CXS_HEADER)
                if header[0] != 0x41:
                    raise TendStreamError("Invalid Tend signaling packet")
                header_length, options_length = struct.unpack_from("<HH", header, 2)
                body_length = struct.unpack_from("<I", header, 7)[0]
                total = header_length + body_length
                if header_length < _CXS_HEADER + options_length or total > _MAX_CXS_PACKET:
                    raise TendStreamError("Invalid Tend signaling packet size")
                rest = await reader.readexactly(total - _CXS_HEADER)
        except (TimeoutError, asyncio.IncompleteReadError) as error:
            raise TendStreamError("Tend signaling timed out or closed") from error
        options = rest[:options_length]
        source = rest[options_length : header_length - _CXS_HEADER]
        body = rest[header_length - _CXS_HEADER :]
        return CxsPacket(
            struct.unpack_from("<H", header, 13)[0],
            body,
            options,
            source,
            struct.unpack_from("<H", header, 15)[0],
        ), body

    async def login(self, access_token: str) -> dict[str, str]:
        """Follow the front door redirect and authenticate with a broker."""
        source = store_properties(
            {
                "partner-id": TEND_PARTNER_ID,
                "CX_UNAME": "",
                "client_type": "Python myqcam",
                "sdk_version": "2.1.0.56",
                "CX_PASSWD": f"myQ.accessToken:{access_token}",
                "current_version": "5.243.1.73243",
                "os_name": "Python",
                "app-id": "com.chamberlain.android.liftmaster.myq",
                "os_version": "device-free",
            }
        )
        mode = store_properties({"CX_UNAME": "", "mode": "0"})
        login = CxsPacket(519, mode + mode, source=source)
        await self._connect(_LOGIN_HOST)
        await self._send(login)
        _, redirect_body = await self._next(5)
        redirect = load_properties(redirect_body)
        host = redirect.get("redirectHost")
        if host is None:
            host = next(
                (value.rsplit(":", 1)[0] for key, value in redirect.items() if key.isdecimal()),
                None,
            )
        if not host:
            raise TendStreamError("Tend did not return a camera broker")
        await self._connect(host)
        await asyncio.sleep(0.25)
        await self._send(login)
        for _ in range(5):
            response, body = await self._next(5)
            properties = load_properties(body)
            if "sid" in properties or "session-id" in properties:
                self._options = response.options
                self.properties = properties
                self._keepalive_task = asyncio.create_task(self._keepalive())
                self._read_task = asyncio.create_task(self._read_loop())
                return properties
        raise TendStreamError("Tend broker did not return a session")

    async def _keepalive(self) -> None:
        try:
            while True:
                await asyncio.sleep(20)
                await self._send(CxsPacket(526, options=self._options))
        except asyncio.CancelledError:
            pass
        except OSError, TendStreamError:
            _LOGGER.debug("Tend camera signaling keepalive ended")

    async def _read_loop(self) -> None:
        """Own the socket after login so broker traffic never piles up unread."""
        try:
            while True:
                packet, _ = await self._next(None)
                for prefix, reply in self._replies:
                    if not reply.done() and packet.body.startswith(prefix):
                        reply.set_result(packet)
                        break
        except OSError, TendStreamError:
            _LOGGER.debug("Tend camera signaling closed")
        finally:
            self._lost = True
            for _, reply in self._replies:
                if not reply.done():
                    reply.set_exception(TendStreamError("Tend signaling closed"))
            if self._on_lost is not None:
                self._on_lost(self)

    async def command(self, command: str, destination: str, correlation: str | None = None) -> str:
        """Send a command to a camera through the broker."""
        correlation = correlation or "LOG-" + uuid4().hex
        await self._send(
            CxsPacket(
                411,
                command.encode(),
                options=self._options,
                source=store_properties({"CorrelationID": correlation, "CX_DSTID": destination}),
            )
        )
        return correlation

    async def request(
        self, command: str, destination: str, reply_prefix: bytes, wait_seconds: float = 15
    ) -> tuple[str, CxsPacket]:
        """Send a camera command and wait for the reply that starts with reply_prefix."""
        reply: asyncio.Future[CxsPacket] = asyncio.get_running_loop().create_future()
        entry = (reply_prefix, reply)
        # Register before sending, so a fast reply cannot slip past the read loop.
        self._replies.append(entry)
        try:
            correlation = await self.command(command, destination)
            async with asyncio.timeout(wait_seconds):
                return correlation, await reply
        except TimeoutError as error:
            raise TendStreamError("Tend camera did not reply in time") from error
        finally:
            self._replies.remove(entry)

    def close_transport(self) -> None:
        if self._writer is not None:
            self._writer.close()
        self._writer = None
        self._reader = None

    def close(self) -> None:
        self._on_lost = None
        for task in (self._keepalive_task, self._read_task):
            if task is not None:
                task.cancel()
        self._keepalive_task = None
        self._read_task = None
        self.close_transport()


@dataclass(slots=True)
class TendCamera:
    device_id: str
    destination: str
    aes_key: str
    local_ip: str
    opener_serial: str = ""


def parse_cameras(properties: dict[str, str]) -> list[TendCamera]:
    """Read the camera capabilities supplied by the Tend broker.

    Each destination is "<account alias>:<camera MAC>"; GSN names the attached opener.
    """
    cameras = []
    for line in properties.get("device-info-list", "").splitlines():
        destination, separator, capabilities = line.partition(",")
        if not separator or ":" not in destination:
            continue
        device_id = destination.split(":", 1)[0]
        raw = dict(item.split("=", 1) for item in capabilities.split("+") if "=" in item)
        cameras.append(
            TendCamera(
                device_id,
                destination,
                raw.get("AES", ""),
                raw.get("LIP", ""),
                raw.get("GSN", ""),
            )
        )
    return cameras


@dataclass(slots=True)
class VideoOffer:
    relay_host: str
    relay_port: int
    aes_key: str
    correlation: str


async def request_video(connection: CxsConnection, camera: TendCamera) -> VideoOffer:
    """Ask the camera for a Tend video session."""
    correlation, response = await connection.request(
        f"require-video-send|{camera.device_id}|9|",
        camera.destination,
        b"accept-video-receive|",
    )
    match = re.search(rb"rtp://([^:/]+):(\d+)", response.source)
    if match is None:
        raise TendStreamError("Tend video offer has no relay endpoint")
    return VideoOffer(match[1].decode(), int(match[2]), camera.aes_key, correlation)


def _sdnk(kind: int, payload: bytes) -> bytes:
    header = bytearray(20)
    header[:4] = _SDNK_MAGIC
    header[4:7] = b"\x01\x00\x00"
    header[7] = kind
    struct.pack_into(">I", header, 16, len(payload))
    return bytes(header) + payload


def _media_id(device_id: str) -> bytes:
    return ("V_" + device_id).encode("ascii")[:32].ljust(32, b"\x00")


def register_packet(device_id: str, local_ip: str, port: int, control: bool) -> bytes:
    """Register an RTP or RTCP UDP port with the Tend relay."""
    media_id = _media_id(device_id)
    payload = (
        bytes((1, 0, 0, int(control)))
        + b"V"
        + ipaddress.IPv4Address(local_ip).packed
        + bytes(16)
        + struct.pack(">H", port)
        + media_id
        + media_id
    )
    return _sdnk(1, payload)


def parse_peer_packet(data: bytes) -> list[tuple[str, int]]:
    """Read candidate camera IP and UDP ports from the relay response."""
    if len(data) != 110 or data[:4] != _SDNK_MAGIC or data[7] != 2:
        return []
    peers = []
    for offset in range(20, 108, 22):
        address = data[offset : offset + 4]
        port = struct.unpack_from(">H", data, offset + 20)[0]
        if any(address) and port:
            peers.append((str(ipaddress.IPv4Address(address)), port))
    return peers


def _decrypt(aes_key: str, blob: bytes) -> bytes:
    if len(aes_key) != 16 or len(blob) < 32:
        raise ValueError("Invalid Tend video encryption parameters")
    decryptor = Cipher(algorithms.AES(aes_key.encode("ascii")), modes.CBC(blob[:16])).decryptor()
    padded = decryptor.update(blob[16:]) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def decode_video_payload(payload: bytes, aes_key: str) -> list[bytes]:
    """Extract H.264 access units from a Tend RTP media payload."""
    if len(payload) < 10 or payload[:2] != b"w1":
        return []
    length = struct.unpack_from("<I", payload, 2)[0]
    if length + 6 != len(payload):
        return []
    combined = payload[10 : 10 + length - 4]
    if (payload[6] & 0xE0) >> 5 == 1:
        try:
            combined = _decrypt(aes_key, combined)
        except ValueError:
            return []
    records = []
    offset = 0
    while offset + 4 <= len(combined):
        record_length = struct.unpack_from("<I", combined, offset)[0]
        offset += 4
        if record_length == 0 or offset + record_length > len(combined):
            break
        records.append(combined[offset : offset + record_length])
        offset += record_length
    return records


@dataclass(slots=True)
class _Channel:
    transport: asyncio.DatagramTransport
    port: int
    control: bool
    peer: tuple[str, int] | None = None
    connected: bool = False


class _DatagramHandler(asyncio.DatagramProtocol):
    def __init__(self, owner: VideoTransport, control: bool) -> None:
        self.owner = owner
        self.control = control

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.owner.on_datagram(self.control, data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Tend video UDP error: %s", type(exc).__name__)


class VideoTransport:
    """LAN hole punching, RTP assembly, AES decoding, and RTCP reports."""

    def __init__(
        self, camera: TendCamera, offer: VideoOffer, on_frame: Callable[[bytes], None]
    ) -> None:
        self.camera = camera
        self.offer = offer
        self.on_frame = on_frame
        self.channels: list[_Channel] = []
        self.local_ip = ""
        self._relay_ip = ""
        self._camera_ips = {camera.local_ip} if camera.local_ip else set[str]()
        self._connected = asyncio.Event()
        self._report_task: asyncio.Task[None] | None = None
        self._frame_number: int | None = None
        self._fragments: list[bytes] = []
        self._local_ssrc = secrets.randbits(32)
        self._source_ssrc: int | None = None
        self._base_seq = 0
        self._max_seq = 0
        self._cycles = 0
        self._received = 0
        self._expected_prior = 0
        self._received_prior = 0
        self._last_transit: float | None = None
        self._jitter = 0.0
        self._last_sr = 0
        self._last_sr_at = 0.0

    async def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        addresses = await loop.getaddrinfo(
            self.offer.relay_host,
            self.offer.relay_port,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
        relay_ip = str(addresses[0][4][0])
        self._relay_ip = relay_ip
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.setblocking(False)
            await loop.sock_connect(probe, (relay_ip, 9))
            self.local_ip = probe.getsockname()[0]
        finally:
            probe.close()
        for _ in range(100):
            media = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                media.bind(("0.0.0.0", 0))
                media_port = media.getsockname()[1]
                if media_port == 65535:
                    media.close()
                    control.close()
                    continue
                control.bind(("0.0.0.0", media_port + 1))
                for sock, is_control in ((media, False), (control, True)):

                    def protocol_factory(control_channel: bool = is_control) -> _DatagramHandler:
                        return _DatagramHandler(self, control_channel)

                    transport, _ = await loop.create_datagram_endpoint(protocol_factory, sock=sock)
                    self.channels.append(_Channel(transport, sock.getsockname()[1], is_control))
                return
            except OSError:
                for channel in self.channels:
                    channel.transport.close()
                self.channels.clear()
                media.close()
                control.close()
        raise TendStreamError("Could not allocate Tend video UDP ports")

    def _send(self, channel: _Channel, data: bytes, peer: tuple[str, int]) -> None:
        channel.transport.sendto(data, peer)

    async def punch(self, wait_seconds: float = 8) -> None:
        """Establish media and control UDP paths to the camera."""
        await self._bind()
        relay = (self._relay_ip, self.offer.relay_port)

        async def drive() -> None:
            sequence = 0
            while not self._connected.is_set():
                for channel in self.channels:
                    if channel.peer is None:
                        self._send(
                            channel,
                            register_packet(
                                self.camera.device_id, self.local_ip, channel.port, channel.control
                            ),
                            relay,
                        )
                    elif not channel.connected:
                        media_id = _media_id(self.camera.device_id)
                        self._send(
                            channel,
                            _sdnk(3, bytes((1, sequence)) + media_id + media_id),
                            channel.peer,
                        )
                        self._send(channel, _sdnk(4, b"\x01"), channel.peer)
                sequence = min(sequence + 1, 2)
                await asyncio.sleep(0.1)

        driver = asyncio.create_task(drive())
        try:
            await asyncio.wait_for(self._connected.wait(), wait_seconds)
        except TimeoutError as error:
            raise TendStreamError("Tend video UDP connection timed out") from error
        finally:
            driver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await driver
        self._report_task = asyncio.create_task(self._report_loop())

    def on_datagram(self, control: bool, data: bytes, remote: tuple[str, int]) -> None:
        """Handle a Seedonk setup packet or an RTP/RTCP datagram."""
        if len(self.channels) < 2:
            return
        channel = self.channels[int(control)]
        # Only the relay and the camera addresses it announced may set peers or send media.
        if remote[0] != self._relay_ip and remote[0] not in self._camera_ips:
            return
        if data[:4] == _SDNK_MAGIC and len(data) >= 20:
            kind = data[7]
            if kind == 2:
                if remote[0] != self._relay_ip:
                    return
                peers = parse_peer_packet(data)
                self._camera_ips.update(address for address, _ in peers)
                private = [
                    peer
                    for peer in peers
                    if ipaddress.ip_address(peer[0]).is_private and peer[0] != self.local_ip
                ]
                channel.peer = (
                    next((peer for peer in peers if peer[0] == self.camera.local_ip), None)
                    or (private[0] if private else None)
                    or (peers[-1] if peers else None)
                )
                if channel.peer is not None:
                    media_id = _media_id(self.camera.device_id)
                    self._send(
                        channel,
                        _sdnk(100, bytes((1, int(control))) + b"V" + media_id + media_id),
                        (self._relay_ip, self.offer.relay_port),
                    )
            elif kind == 3:
                channel.peer = remote
                media_id = _media_id(self.camera.device_id)
                for sequence in range(3):
                    self._send(
                        channel, _sdnk(3, bytes((1, sequence)) + media_id + media_id), remote
                    )
            elif kind == 4:
                channel.peer = remote
                self._send(channel, _sdnk(5, data[20:21] or b"\x01"), remote)
            elif kind == 5:
                channel.peer = remote
                channel.connected = True
                if all(item.connected for item in self.channels):
                    self._connected.set()
            return
        if control:
            self._handle_rtcp(data)
        else:
            self._handle_media(data)

    def _handle_media(self, data: bytes) -> None:
        if len(data) < 8:
            return
        frame_number = struct.unpack_from("<I", data)[0]
        if self._frame_number is None:
            self._frame_number = frame_number
        elif frame_number != self._frame_number:
            self._flush_rtp()
            self._frame_number = frame_number
        self._fragments.append(data[8:])

    def _flush_rtp(self) -> None:
        if not self._fragments:
            return
        rtp = b"".join(self._fragments)
        self._fragments.clear()
        if len(rtp) < 12 or rtp[0] >> 6 != 2:
            return
        self._observe_rtp(rtp)
        for record in decode_video_payload(rtp[12:], self.offer.aes_key):
            self.on_frame(record)

    def _observe_rtp(self, rtp: bytes) -> None:
        seq, timestamp, ssrc = struct.unpack_from(">HII", rtp, 2)
        if ssrc != self._source_ssrc:
            self._source_ssrc = ssrc
            self._base_seq = seq
            self._max_seq = seq
            self._cycles = 0
            self._received = 0
            self._expected_prior = 0
            self._received_prior = 0
            self._last_transit = None
            self._jitter = 0
        elif seq < self._max_seq and self._max_seq - seq > 0x8000:
            self._cycles += 0x10000
            self._max_seq = seq
        elif seq > self._max_seq or self._max_seq - seq > 0x8000:
            self._max_seq = seq
        self._received += 1
        transit = time.monotonic() * 90000 - timestamp
        if self._last_transit is not None:
            delta = abs(transit - self._last_transit)
            self._jitter += (delta - self._jitter) / 16
        self._last_transit = transit

    def _handle_rtcp(self, data: bytes) -> None:
        offset = 0
        while offset + 4 <= len(data):
            size = (struct.unpack_from(">H", data, offset + 2)[0] + 1) * 4
            if data[offset] >> 6 != 2 or size < 4 or offset + size > len(data):
                return
            if data[offset + 1] == 200 and size >= 20:
                msw, lsw = struct.unpack_from(">II", data, offset + 8)
                self._last_sr = ((msw & 0xFFFF) << 16) | (lsw >> 16)
                self._last_sr_at = time.monotonic()
            offset += size

    async def _report_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(2)
                report = self._receiver_report()
                if report is not None and self.channels[1].peer is not None:
                    self._send(self.channels[1], report, self.channels[1].peer)
        except asyncio.CancelledError:
            pass

    def _receiver_report(self) -> bytes | None:
        if self._source_ssrc is None:
            return None
        expected = self._cycles + self._max_seq - self._base_seq + 1
        lost = max(-(1 << 23), min((1 << 23) - 1, expected - self._received))
        expected_interval = expected - self._expected_prior
        lost_interval = expected_interval - (self._received - self._received_prior)
        fraction = (
            min(255, lost_interval * 256 // expected_interval)
            if expected_interval > 0 and lost_interval > 0
            else 0
        )
        self._expected_prior = expected
        self._received_prior = self._received
        report = bytearray(32)
        report[:2] = b"\x81\xc9"
        struct.pack_into(">HII", report, 2, 7, self._local_ssrc, self._source_ssrc)
        report[12] = fraction
        report[13:16] = (lost & 0xFFFFFF).to_bytes(3, "big")
        struct.pack_into(
            ">IIII",
            report,
            16,
            self._cycles + self._max_seq,
            int(self._jitter) & 0xFFFFFFFF,
            self._last_sr,
            min(int((time.monotonic() - self._last_sr_at) * 65536), 0xFFFFFFFF)
            if self._last_sr_at
            else 0,
        )
        return bytes(report)

    def close(self) -> None:
        if self._report_task is not None:
            self._report_task.cancel()
            self._report_task = None
        if len(self.channels) == 2 and self.channels[1].peer is not None:
            bye = struct.pack(">BBHI", 0x81, 203, 1, self._local_ssrc)
            with contextlib.suppress(OSError):
                self._send(self.channels[1], bye, self.channels[1].peer)
                self._send(self.channels[1], bye, self.channels[1].peer)
        for channel in self.channels:
            channel.transport.close()
        self.channels.clear()
