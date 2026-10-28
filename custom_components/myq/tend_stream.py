"""Expose a TC camera's Tend video as a loopback H.264 source for HA stream."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import ClientError

from .exceptions import MyQError
from .tend import (
    CxsConnection,
    TendCamera,
    TendStreamError,
    VideoOffer,
    VideoTransport,
    parse_cameras,
    request_video,
)

_LOGGER = logging.getLogger(__name__)
_IDLE_SECONDS = 60


def _nals(data: bytes) -> list[tuple[int, bytes]]:
    """Return the H.264 NAL units in one Annex B access unit."""
    starts: list[tuple[int, int]] = []
    index = data.find(b"\x00\x00\x01")
    while index != -1:
        # A zero before the 3-byte start code makes it the 4-byte form.
        starts.append((index - 1, 4) if index and data[index - 1] == 0 else (index, 3))
        index = data.find(b"\x00\x00\x01", index + 3)
    units: list[tuple[int, bytes]] = []
    for position, (start, prefix) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(data)
        if end > start + prefix:
            units.append((data[start + prefix] & 0x1F, data[start:end]))
    return units


class _Viewer:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=60)
        self.ready = False
        self.ended = False

    def put(self, frame: bytes) -> None:
        if self.ended:
            return
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(frame)

    def end(self) -> None:
        """Queue the end marker; later frames cannot push it out of the queue."""
        self.put(b"")
        self.ended = True


async def _stop_video(connection: CxsConnection, camera: TendCamera, offer: VideoOffer) -> None:
    with contextlib.suppress(OSError, TendStreamError):
        await connection.command(
            f"stop-video-receive|{camera.device_id}|9|", camera.destination, offer.correlation
        )


class TendLiveStream:
    """Share one Tend session among Home Assistant stream consumers."""

    def __init__(
        self,
        serial_number: str,
        opener_serial: str,
        token_supplier: Callable[[], Awaitable[str]],
    ) -> None:
        self.serial_number = serial_number
        self._opener_serial = opener_serial
        self._token_supplier = token_supplier
        self._path = "/" + secrets.token_urlsafe(24) + ".h264"
        self._server: asyncio.Server | None = None
        self._server_lock = asyncio.Lock()
        self._connection: CxsConnection | None = None
        self._transport: VideoTransport | None = None
        self._camera: TendCamera | None = None
        self._offer: VideoOffer | None = None
        self._lock = asyncio.Lock()
        self._viewers: set[_Viewer] = set()
        self._idle_task: asyncio.Task[None] | None = None
        self._sps: bytes | None = None
        self._pps: bytes | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._handlers: set[asyncio.Task[Any]] = set()
        self._closed = False

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise TendStreamError("Tend camera stream is closed")

    async def source_url(self) -> str:
        """Return an unguessable loopback URL for HA's FFmpeg stream reader."""
        self._raise_if_closed()
        async with self._server_lock:
            self._raise_if_closed()
            if self._server is None:
                self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        address = self._server.sockets[0].getsockname()
        return f"http://127.0.0.1:{address[1]}{self._path}"

    async def _open(self) -> None:
        async with self._lock:
            if self._transport is not None:
                return
            connection = CxsConnection(self._on_signaling_lost)
            transport: VideoTransport | None = None
            try:
                await connection.login(await self._token_supplier())
                # Tend signaling lists cameras by account alias and MAC, not by serial number.
                camera = next(
                    (
                        item
                        for item in parse_cameras(connection.properties)
                        if item.opener_serial == self._opener_serial
                    ),
                    None,
                )
                if camera is None:
                    raise TendStreamError("TC camera was not listed by Tend signaling")
                last_error: TendStreamError | None = None
                for attempt in range(2):
                    offer = await request_video(connection, camera)
                    transport = VideoTransport(camera, offer, self._on_frame)
                    try:
                        await transport.punch(8)
                    except TendStreamError as error:
                        last_error = error
                        transport.close()
                        transport = None
                        # Release the camera's side of the failed offer before asking again.
                        await _stop_video(connection, camera, offer)
                        if attempt == 0:
                            await asyncio.sleep(4)
                        continue
                    await connection.command(
                        f"start-video-receive|{camera.device_id}|9|",
                        camera.destination,
                        offer.correlation,
                    )
                    await connection.command(
                        f"modify-device|{camera.device_id}|QTY=3+FPS=20+SIZE=0+SAVE=0|",
                        camera.destination,
                    )
                    self._connection = connection
                    self._transport = transport
                    self._camera = camera
                    self._offer = offer
                    _LOGGER.debug("Connected Tend TC camera video")
                    return
                raise last_error or TendStreamError("TC camera media connection failed")
            except (ClientError, MyQError, OSError, TimeoutError, ValueError) as error:
                raise TendStreamError("TC camera media connection failed") from error
            finally:
                if self._transport is None:
                    if transport is not None:
                        transport.close()
                    connection.close()

    def _on_frame(self, frame: bytes) -> None:
        units = _nals(frame)
        types = {nal_type for nal_type, _ in units}
        if not types:
            return
        for nal_type, nal in units:
            if nal_type == 7:
                self._sps = nal
            elif nal_type == 8:
                self._pps = nal
        for viewer in self._viewers:
            if not viewer.ready:
                if 5 not in types or self._sps is None or self._pps is None:
                    continue
                if 7 not in types:
                    viewer.put(self._sps)
                if 8 not in types:
                    viewer.put(self._pps)
                viewer.ready = True
            viewer.put(frame)

    def _on_signaling_lost(self, connection: CxsConnection) -> None:
        """End viewers at once when Tend drops signaling, so they reconnect to a new session."""
        if connection is not self._connection:
            return
        _LOGGER.warning("TC camera live video unavailable: Tend signaling closed")
        for viewer in self._viewers:
            viewer.end()
        self._close_task = asyncio.create_task(self._close_session())

    async def _subscribe(self) -> _Viewer:
        self._raise_if_closed()
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        await self._open()
        if self._closed:
            await self._close_session()
            raise TendStreamError("Tend camera stream is closed")
        viewer = _Viewer()
        self._viewers.add(viewer)
        return viewer

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        viewer: _Viewer | None = None
        stalled = False
        handler = asyncio.current_task()
        if handler is not None:
            self._handlers.add(handler)
        try:
            request = await asyncio.wait_for(reader.readline(), 5)
            if request != f"GET {self._path} HTTP/1.1\r\n".encode() and request != (
                f"GET {self._path} HTTP/1.0\r\n".encode()
            ):
                writer.write(b"HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return
            while await asyncio.wait_for(reader.readline(), 5) not in (b"\r\n", b"\n", b""):
                pass
            writer.write(
                b"HTTP/1.0 200 OK\r\nContent-Type: video/h264\r\nCache-Control: no-store\r\n\r\n"
            )
            await writer.drain()
            viewer = await self._subscribe()
            disconnect = asyncio.create_task(reader.read(1))
            try:
                while not disconnect.done():
                    next_frame = asyncio.create_task(viewer.frames.get())
                    try:
                        done, _ = await asyncio.wait(
                            (disconnect, next_frame),
                            timeout=30,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if not done:
                            raise TimeoutError("TC camera stopped sending video")
                        if disconnect in done:
                            break
                        frame = next_frame.result()
                        if not frame:
                            break
                        writer.write(frame)
                        await writer.drain()
                    finally:
                        next_frame.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await next_frame
            finally:
                disconnect.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await disconnect
        except (TimeoutError, TendStreamError) as error:
            _LOGGER.warning("TC camera live video unavailable: %s", error)
            stalled = isinstance(error, TimeoutError)
        except (ConnectionError, OSError) as error:
            _LOGGER.debug("Tend TC live stream client ended: %s", error)
        finally:
            if viewer is not None:
                self._viewers.discard(viewer)
                if not self._viewers and not self._closed:
                    if stalled:
                        await self._close_session()
                    else:
                        self._idle_task = asyncio.create_task(self._idle_close())
            if handler is not None:
                self._handlers.discard(handler)
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _idle_close(self) -> None:
        try:
            await asyncio.sleep(_IDLE_SECONDS)
        except asyncio.CancelledError:
            return
        self._idle_task = None
        if not self._viewers:
            await self._close_session()

    async def _close_session(self) -> None:
        async with self._lock:
            connection = self._connection
            camera = self._camera
            offer = self._offer
            self._connection = None
            self._camera = None
            self._offer = None
            if connection is not None and camera is not None and offer is not None:
                await _stop_video(connection, camera, offer)
            if self._transport is not None:
                self._transport.close()
                self._transport = None
            if connection is not None:
                connection.close()
            self._sps = None
            self._pps = None

    async def close(self) -> None:
        """Stop accepting viewers, end connected ones, and close the Tend session."""
        self._closed = True
        if self._idle_task is not None:
            self._idle_task.cancel()
        # Cancelling handlers also aborts a Tend login in progress, so unload never waits on it,
        # and lets Server.wait_closed() finish even when a reader has stopped consuming.
        for handler in self._handlers:
            handler.cancel()
        async with self._server_lock:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
        await self._close_session()
