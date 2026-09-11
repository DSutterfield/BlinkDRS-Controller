"""
liveview_bridge.py

Bridge one Blink live-view stream to browser-compatible MJPEG frames.

Developer note — 2026-08-04, Dan and Sage:
Blinkpy exposes live view as a local MPEG transport stream over TCP. Web
browsers cannot display that TCP stream directly, so this module runs FFmpeg
to decode the transport stream and emit JPEG frames. Flask can later publish
those frames as multipart MJPEG without putting Blink or FFmpeg details in the
HTML code.

Developer correction — 2026-08-04, Dan and Sage:
The first bridge version discovered cameras with one temporary Blink connection
and then opened a second connection to start live view. Blinkpy did not always
return the same camera-key mapping on that second immediate connection, which
caused "Camera not found" even though the camera had just been listed.

This version keeps one Blink connection for camera discovery and live-view
startup. Stopping a live-view session leaves that Blink connection available;
shutdown closes everything.

Current design limits the Dashboard to one active live-view camera at a time.
Selecting another camera, selecting a recorded clip, or clearing the preview
will stop the existing session before a new one begins.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import ssl
import subprocess
import threading
import time
from liveview_diagnostics import StartupDiagnostics
import os
from collections import deque
from pathlib import Path
from typing import Iterator

from aiohttp import ClientSession
from blinkpy.auth import Auth
from blinkpy.blinkpy import Blink


ROOT = Path(__file__).parent
CREDS_PATH = ROOT / "config" / "credentials.json"
FIRST_FRAME_TIMEOUT_SECONDS = 30
FFMPEG_STOP_TIMEOUT_SECONDS = 5
MJPEG_FRAME_RATE = 5
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"
MAX_PARSE_BUFFER_BYTES = 8 * 1024 * 1024


class LiveViewBridge:
    """Own one Blink live-view session and publish its latest JPEG frame."""

    def __init__(self) -> None:
        self._startup = None
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._control_lock = threading.Lock()
        self._frame_condition = threading.Condition()
        self._audio_condition = threading.Condition()

        self._thread = threading.Thread(
            target=self._run_event_loop,
            daemon=True,
            name="BlinkLiveViewBridge",
        )

        self._camera_name: str | None = None
        self._active = False
        self._latest_frame: bytes | None = None
        self._frame_number = 0
        self._frame_history = deque(maxlen=20)
        self._last_error: str | None = None
        self._ffmpeg_messages: deque[str] = deque(maxlen=25)
        self._audio_chunks: deque[bytes] = deque(maxlen=256)

        # Objects below are created and used only on the bridge event loop.
        self._session: ClientSession | None = None
        self._blink: Blink | None = None
        self._stream = None
        self._feed_task: asyncio.Task | None = None
        self._frame_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._ffmpeg: asyncio.subprocess.Process | None = None
        self._audio_task: asyncio.Task | None = None
        self._audio_read_fd: int | None = None
        self._first_frame_event: asyncio.Event | None = None

        self._thread.start()
        if not self._loop_ready.wait(timeout=5):
            raise RuntimeError("The live-view background event loop did not start.")

    def _run_event_loop(self) -> None:
        """Run the asyncio loop owned by this bridge."""
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()

        if pending:
            self._loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )

        self._loop.close()

    async def _ensure_blink_async(self) -> None:
        """Create one persistent Blink connection for discovery and live view."""
        if (
            self._session is not None
            and not self._session.closed
            and self._blink is not None
        ):
            return

        saved = self._read_credentials()
        self._session = ClientSession()

        try:
            self._blink = Blink(session=self._session)
            self._blink.auth = Auth(
                saved,
                no_prompt=True,
                session=self._session,
            )
            await self._blink.start()
        except Exception:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None
            self._blink = None
            raise

    def camera_names(self) -> list[str]:
        """Return camera names from the bridge's persistent Blink connection."""
        future = asyncio.run_coroutine_threadsafe(
            self._camera_names_async(),
            self._loop,
        )
        return future.result(timeout=45)

    async def _camera_names_async(self) -> list[str]:
        await self._ensure_blink_async()

        if self._blink is None:
            raise RuntimeError("Blink connection was not created.")

        return sorted(
            (str(name) for name in self._blink.cameras),
            key=str.casefold,
        )

    def start(self, camera_name: str, request_started_at=None) -> dict:
        """Start live view and wait until FFmpeg produces its first JPEG frame."""
        if not camera_name or not camera_name.strip():
            raise ValueError("A camera name is required.")

        started_at = time.monotonic() if request_started_at is None else request_started_at
        with self._control_lock:
            future = asyncio.run_coroutine_threadsafe(
                self._start_async(camera_name.strip(), started_at),
                self._loop,
            )
            try:
                return future.result(timeout=FIRST_FRAME_TIMEOUT_SECONDS + 20)
            except Exception:
                if self._startup is not None:
                    self._startup.log("failed")
                raise

    def _find_camera(self, camera_name: str):
        """Find a camera by dictionary key or camera.name, ignoring case/spaces."""
        if self._blink is None:
            return None

        requested = camera_name.strip().casefold()

        for key, camera in self._blink.cameras.items():
            if str(key).strip().casefold() == requested:
                return camera

            object_name = getattr(camera, "name", None)
            if (
                object_name is not None
                and str(object_name).strip().casefold() == requested
            ):
                return camera

        return None

    async def _start_async(self, camera_name: str, started_at=None) -> dict:
        # Stop only the previous stream. Keep the Blink connection alive.
        await self._stop_async()
        self._startup = StartupDiagnostics(started_at)
        self._startup.mark("previous_stream_stopped")
        await self._ensure_blink_async()
        self._startup.mark("blink_connection_ready")

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError(
                "FFmpeg was not found. Restart Visual Studio so its PATH "
                "includes the FFmpeg installation."
            )

        try:
            camera = self._find_camera(camera_name)

            if camera is None:
                if self._blink is None:
                    available = "(Blink connection unavailable)"
                else:
                    available = ", ".join(
                        repr(str(name)) for name in self._blink.cameras
                    ) or "(none)"

                raise KeyError(
                    f"Camera not found: {camera_name}. "
                    f"Discovered cameras: {available}"
                )

            self._startup.mark("blink_command_requested")
            self._stream = await camera.init_livestream()
            self._startup.mark("blink_command_accepted")

            # 2026-08-14 - Dan/Sage:
            # Keep BlinkPy's normal feed(), but replace its recv() implementation
            # with our TCP-safe IMMI receiver.
            self._stream.recv = self._recv_blink_stream
            original_auth = self._stream.auth
            diagnostics = self._startup

            async def traced_auth():
                diagnostics.mark("stream_connection_requested")
                await original_auth()
                diagnostics.mark("stream_auth_sent")

            self._stream.auth = traced_auth

            await self._stream.start()
            self._startup.mark("local_listener_ready")

            with self._frame_condition:
                self._camera_name = camera_name
                self._active = True
                self._latest_frame = None
                self._frame_number = 0
                self._frame_history.clear()
                self._last_error = None
                self._ffmpeg_messages.clear()
                self._frame_condition.notify_all()

            with self._audio_condition:
                self._audio_chunks.clear()
                self._audio_condition.notify_all()

            self._first_frame_event = asyncio.Event()

            creationflags = getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )

            audio_read_fd, audio_write_fd = os.pipe()

            os.set_blocking(
                audio_read_fd,
                False,
            )

            self._audio_read_fd = audio_read_fd

            try:
                self._ffmpeg = await asyncio.create_subprocess_exec(
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "info",
                    "-nostats",
                    # Bound stream discovery for live input; retain normal
                    # packet buffering so initial video/audio is not discarded.
                    "-analyzeduration",
                    "1000000",
                    "-probesize",
                    "1048576",
                    "-f",
                    "mpegts",
                    "-i",
                    self._stream.url,

                    # Output 1: existing Live View video.
                    "-map",
                    "0:v:0",
                    "-an",
                    "-vf",
                    f"fps={MJPEG_FRAME_RATE},format=yuvj420p",
                    "-c:v",
                    "mjpeg",
                    "-strict",
                    "unofficial",
                    "-q:v",
                    "5",
                    "-f",
                    "mjpeg",
                    "pipe:1",

                    # Output 2: live camera microphone audio.
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "64k",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-af",
                    (
                        "highpass=f=250,"
                        "lowpass=f=3000,"
                        "volume=35dB"
                    ),
                    "-write_xing",
                    "0",
                    "-id3v2_version",
                    "0",
                    "-flush_packets",
                    "1",
                    "-f",
                    "mp3",
                    f"pipe:{audio_write_fd}",

                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                    pass_fds=(audio_write_fd,),
                )

            finally:
                os.close(audio_write_fd)

            self._startup.mark("ffmpeg_started")
            self._frame_task = asyncio.create_task(
                self._read_ffmpeg_frames(),
                name="ReadLiveViewFrames",
            )

            self._audio_task = asyncio.create_task(
                self._read_ffmpeg_audio(),
                name="ReadLiveViewAudio",
            )

            self._stderr_task = asyncio.create_task(
                self._read_ffmpeg_stderr(),
                name="ReadLiveViewFfmpegStderr",
            )

            # FFmpeg connects to the local TCP listener above. feed() then
            # authenticates with Blink and relays MPEG-TS packets to FFmpeg.
            self._feed_task = asyncio.create_task(
                self._stream.feed(),
                name="FeedBlinkLiveView",
            )

            await asyncio.wait_for(
                self._first_frame_event.wait(),
                timeout=FIRST_FRAME_TIMEOUT_SECONDS,
            )

            self._startup.mark("start_response_ready")
            self._startup.log("ready")
            return {
                "diagnostics": self._startup.snapshot(),
                "ok": True,
                "camera": camera_name,
                "frame_rate": MJPEG_FRAME_RATE,
            }

        except Exception as exc:
            details = self._format_ffmpeg_messages()
            message = str(exc)
            if isinstance(exc, TimeoutError) and not message:
                marks = self._startup.snapshot()["elapsed_ms"]
                if "first_transport_payload" in marks:
                    message = "Media arrived, but no decoded video frame was ready within 30 seconds."
                elif "stream_auth_sent" in marks:
                    message = "Stream connection opened, but no valid media payload arrived within 30 seconds."
                else:
                    message = "Stream connection/authentication did not complete within 30 seconds."
            self._set_error(f"{type(exc).__name__}: {message}{details}")
            await self._stop_async(preserve_error=True)
            raise RuntimeError(self._last_error) from exc

    async def _recv_blink_stream(self) -> None:
        """
        Relay complete Blink IMMI packets to the local stream clients.

        2026-08-14 - Dan/Sage:
        BlinkPy's BlinkLiveStream.recv() uses StreamReader.read(n) and
        treats a short TCP read as a broken IMMI packet. TCP may legally
        return fewer than n bytes even when more data is coming.

        Use readexactly() for the fixed 9-byte IMMI header and the declared
        payload length so normal TCP fragmentation does not terminate
        Live View prematurely.
        """
        stream = self._stream

        if stream is None:
            raise RuntimeError("Blink Live View stream is unavailable.")

        try:
            while not stream.target_reader.at_eof():
                try:
                    header = await stream.target_reader.readexactly(9)
                except asyncio.IncompleteReadError:
                    break

                self._startup.mark("first_protocol_header")
                msgtype = header[0]
                payload_length = int.from_bytes(
                    header[5:9],
                    byteorder="big",
                )

                if payload_length <= 0:
                    continue

                try:
                    data = await stream.target_reader.readexactly(
                        payload_length
                    )
                except asyncio.IncompleteReadError:
                    break

                if msgtype != 0x00:
                    continue

                if not data or data[0] != 0x47:
                    continue

                self._startup.mark("first_transport_payload")
                self._startup.pulse("camera_payload", len(data))
                for writer in stream.clients:
                    if not writer.is_closing():
                        writer.write(data)
                        await writer.drain()
                self._startup.pulse("transport_forwarded", len(data))

                await asyncio.sleep(0)

        except ssl.SSLError as exc:
            # BlinkPy treats this specific TLS close notification as normal.
            # Let feed() finish send/poll cleanup instead of exiting gather early.
            if exc.reason != "APPLICATION_DATA_AFTER_CLOSE_NOTIFY":
                raise

        finally:
            if (
                stream.target_writer is not None
                and not stream.target_writer.is_closing()
            ):
                stream.target_writer.close()

    async def _read_ffmpeg_audio(self) -> None:
        """Read encoded MP3 audio from FFmpeg's separate audio pipe."""

        if self._audio_read_fd is None:
            return

        try:
            while True:

                try:
                    chunk = os.read(
                        self._audio_read_fd,
                        16 * 1024,
                    )

                except BlockingIOError:
                    await asyncio.sleep(0.02)
                    continue

                if not chunk:
                    break

                with self._audio_condition:
                    self._audio_chunks.append(chunk)
                    self._startup.pulse("decoded_audio", len(chunk))
                    self._audio_condition.notify_all()

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            self._set_error(
                f"FFmpeg audio reader failed: {exc}"
            )

    async def _read_ffmpeg_frames(self) -> None:
        """Split FFmpeg's MJPEG byte stream into individual JPEG images."""
        if self._ffmpeg is None or self._ffmpeg.stdout is None:
            raise RuntimeError("FFmpeg stdout is unavailable.")

        buffer = bytearray()

        try:
            while True:
                chunk = await self._ffmpeg.stdout.read(64 * 1024)
                if not chunk:
                    break

                buffer.extend(chunk)

                while True:
                    start = buffer.find(JPEG_START)
                    if start < 0:
                        if len(buffer) > MAX_PARSE_BUFFER_BYTES:
                            del buffer[:-2]
                        break

                    end = buffer.find(JPEG_END, start + len(JPEG_START))
                    if end < 0:
                        if start > 0:
                            del buffer[:start]
                        break

                    end += len(JPEG_END)
                    frame = bytes(buffer[start:end])
                    del buffer[:end]
                    self._publish_frame(frame)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_error(f"FFmpeg frame reader failed: {exc}")
        finally:
            with self._frame_condition:
                self._active = False
                self._frame_condition.notify_all()

    async def _read_ffmpeg_stderr(self) -> None:
        """Drain FFmpeg stderr so its pipe cannot fill and stall conversion."""
        if self._ffmpeg is None or self._ffmpeg.stderr is None:
            return

        try:
            while True:
                line = await self._ffmpeg.stderr.readline()
                if not line:
                    break

                message = line.decode("utf-8", errors="replace").strip()
                if message:
                    self._ffmpeg_messages.append(message)
        except asyncio.CancelledError:
            raise

    def _publish_frame(self, frame: bytes) -> None:
        if self._startup is not None:
            self._startup.mark("first_decoded_jpeg")
            self._startup.pulse("decoded_video", len(frame))
        with self._frame_condition:
            self._latest_frame = frame
            self._frame_number += 1
            self._frame_history.append((self._frame_number, frame))
            self._active = True
            self._frame_condition.notify_all()

        if self._first_frame_event is not None:
            self._first_frame_event.set()

    def iter_mjpeg(self) -> Iterator[bytes]:
        """Yield multipart MJPEG sections suitable for a Flask Response."""
        last_frame_number = 0

        while True:
            with self._frame_condition:
                self._frame_condition.wait_for(
                    lambda: (
                        self._frame_number != last_frame_number
                        or not self._active
                    ),
                    timeout=5,
                )

                if self._frame_number == last_frame_number:
                    if not self._active:
                        return
                    continue

                frame = self._latest_frame
                last_frame_number = self._frame_number

            if frame is None:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                + frame
                + b"\r\n"
            )

    def next_frame(self, after: int, session_id: str, timeout: float = 1):
        """Return the next retained frame; never cross Live View sessions."""
        with self._frame_condition:
            def same_session():
                return self._startup and self._startup.snapshot()["session_id"] == session_id
            self._frame_condition.wait_for(
                lambda: not same_session() or not self._active or self._frame_number > after,
                timeout=timeout,
            )
            if not same_session():
                raise ValueError("Live View session changed; restart Live View.")
            if not self._active:
                raise ValueError("Live View is no longer active.")
            return next((item for item in self._frame_history if item[0] > after), None)

    def latest_frame(self, timeout: float = 5) -> bytes | None:
        """Return the latest JPEG, waiting briefly when no frame exists yet."""
        with self._frame_condition:
            self._frame_condition.wait_for(
                lambda: self._latest_frame is not None or not self._active,
                timeout=timeout,
            )
            return self._latest_frame

    def next_audio_chunk(
        self,
        timeout: float = 1.0,
    ) -> bytes | None:
        """Return the next encoded Live View audio chunk."""

        with self._audio_condition:

            self._audio_condition.wait_for(
                lambda: (
                    bool(self._audio_chunks)
                    or not self._active
                ),
                timeout=timeout,
            )

            if self._audio_chunks:
                return self._audio_chunks.popleft()

            return None

    def clear_audio(self) -> None:
        """Discard queued Live View audio."""

        with self._audio_condition:
            self._audio_chunks.clear()
            self._audio_condition.notify_all()

    def status(self) -> dict:
        """Return thread-safe bridge status and FFmpeg diagnostics."""

        with self._frame_condition:
            return {
                "active": self._active,
                "camera": self._camera_name,
                "frames": self._frame_number,
                "diagnostics": self._startup.snapshot() if self._startup else None,
                "error": self._last_error,
                "ffmpeg_messages": list(self._ffmpeg_messages),
            }

    def stop(self) -> None:
        """Stop the current stream without closing the Blink connection."""
        with self._control_lock:
            future = asyncio.run_coroutine_threadsafe(
                self._stop_async(),
                self._loop,
            )
            future.result(timeout=15)

    async def _stop_async(self, preserve_error: bool = False) -> None:
        """Stop FFmpeg and the active Blink stream, retaining Blink login."""
        if self._active and self._startup is not None:
            self._startup.log("playback_stopping")
        with self._frame_condition:
            self._active = False
            self._frame_condition.notify_all()

        if self._ffmpeg is not None and self._ffmpeg.returncode is None:
            self._ffmpeg.terminate()
            try:
                await asyncio.wait_for(
                    self._ffmpeg.wait(),
                    timeout=FFMPEG_STOP_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self._ffmpeg.kill()
                await self._ffmpeg.wait()

        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()

        # Give Blinkpy time to send its final command-done notification.
        if self._feed_task is not None:
            try:
                await asyncio.wait_for(self._feed_task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                if not self._feed_task.done():
                    self._feed_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await self._feed_task

        for task in (
            self._frame_task,
            self._audio_task,
            self._stderr_task,
        ):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError,
                    Exception,
                ):
                    await task

        if self._audio_read_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._audio_read_fd)

            self._audio_read_fd = None

        self._stream = None
        self._feed_task = None
        self._frame_task = None
        self._audio_task = None
        self._stderr_task = None
        self._ffmpeg = None
        self._audio_read_fd = None
        self._first_frame_event = None

        with self._frame_condition:
            self._camera_name = None
            self._latest_frame = None
            self._frame_number = 0
            self._frame_history.clear()
            if not preserve_error:
                self._last_error = None
            self._frame_condition.notify_all()

        with self._audio_condition:
            self._audio_chunks.clear()
            self._audio_condition.notify_all()

    async def _shutdown_async(self) -> None:
        """Stop the stream and close the persistent Blink HTTP session."""
        await self._stop_async()

        if self._session is not None and not self._session.closed:
            await self._session.close()

        self._session = None
        self._blink = None

    def shutdown(self) -> None:
        """Stop live view, close Blink, and stop the background event loop."""
        if not self._loop.is_running():
            return

        with contextlib.suppress(Exception):
            future = asyncio.run_coroutine_threadsafe(
                self._shutdown_async(),
                self._loop,
            )
            future.result(timeout=20)

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def _read_credentials(self) -> dict:
        if not CREDS_PATH.exists():
            raise FileNotFoundError(
                f"Saved Blink credentials were not found at {CREDS_PATH}"
            )

        return json.loads(CREDS_PATH.read_text(encoding="utf-8"))

    def _set_error(self, message: str) -> None:
        with self._frame_condition:
            self._last_error = message
            self._active = False
            self._frame_condition.notify_all()

    def _format_ffmpeg_messages(self) -> str:
        if not self._ffmpeg_messages:
            return ""
        return " | FFmpeg: " + " | ".join(self._ffmpeg_messages)


def _diagnostic_main() -> None:
    """Run an isolated test and save the first converted frame as a JPEG."""
    bridge = LiveViewBridge()

    try:
        cameras = bridge.camera_names()
        if not cameras:
            print("Blink did not discover any cameras.")
            return

        print("\nAvailable cameras:\n")
        for number, name in enumerate(cameras, start=1):
            print(f"{number:2}. {name}")

        while True:
            selection = input("\nEnter camera number: ").strip()
            try:
                selected_number = int(selection)
            except ValueError:
                print("Please enter one of the displayed camera numbers.")
                continue

            if 1 <= selected_number <= len(cameras):
                break

            print("That camera number is outside the displayed range.")

        camera_name = cameras[selected_number - 1]
        print(f"\nStarting live view for {camera_name}...")
        bridge.start(camera_name)

        frame = bridge.latest_frame(timeout=5)
        if frame is None:
            raise RuntimeError("The bridge started but no JPEG frame was available.")

        output_path = ROOT / "liveview_test.jpg"
        output_path.write_bytes(frame)

        print(f"SUCCESS: Saved the first live frame to {output_path}")
        print("Stopping live view...")

    finally:
        bridge.shutdown()
        print("Live-view bridge test finished.")


if __name__ == "__main__":
    try:
        _diagnostic_main()
    except KeyboardInterrupt:
        print("\nLive-view bridge test interrupted.")
    except Exception as exc:
        print(f"\nERROR: {type(exc).__name__}: {exc}")
