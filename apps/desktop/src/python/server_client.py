"""Async Socket.IO client to the Stealth GCP server.

Owns one persistent WebSocket. Methods yield streamed token chunks back to
the caller as they arrive — caller forwards them to the Electron overlay.

Lifecycle:
    sc = ServerClient(url, license, on_chunk, on_done, on_error)
    await sc.connect_and_hello(provider, api_key, model, resume_text, language, interview_context)
    await sc.ask(text)            # streams via callbacks
    await sc.ask_vision(prompt, image_b64)
    await sc.test()
    await sc.close()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import socketio

log = logging.getLogger("stealth.server_client")

ChunkCB = Callable[[str], Awaitable[None]]
DoneCB = Callable[[str], Awaitable[None]]
ErrorCB = Callable[[str], Awaitable[None]]


class ServerClient:
    def __init__(
        self,
        server_url: str,
        license_key: str,
        *,
        on_chunk: ChunkCB,
        on_done: DoneCB,
        on_error: ErrorCB,
    ):
        self.server_url = server_url.rstrip("/")
        self.license_key = license_key
        self._on_chunk = on_chunk
        self._on_done = on_done
        self._on_error = on_error
        self._sio = socketio.AsyncClient(reconnection=True, reconnection_attempts=5)
        self._hello_acked = asyncio.Event()
        self._test_result: Optional[dict] = None
        self._test_event = asyncio.Event()
        self._wire()

    def _wire(self):
        @self._sio.event
        async def connect():
            log.info("Server WS connected: %s", self.server_url)

        @self._sio.event
        async def connect_error(data):
            log.warning("Server WS connect_error: %s", data)
            self._hello_acked.set()  # unblock caller; they'll see _hello_error

        @self._sio.event
        async def disconnect():
            log.info("Server WS disconnected")

        @self._sio.on("hello_ack")
        async def on_hello_ack(data):
            self._hello_ack_payload = data or {}
            self._hello_acked.set()

        @self._sio.on("answer_chunk")
        async def on_chunk(data):
            tok = (data or {}).get("token", "")
            if tok:
                await self._on_chunk(tok)

        @self._sio.on("answer_done")
        async def on_done(data):
            await self._on_done((data or {}).get("full_text", ""))

        @self._sio.on("error")
        async def on_error(data):
            await self._on_error((data or {}).get("message", "unknown error"))

        @self._sio.on("test_result")
        async def on_test_result(data):
            self._test_result = data or {}
            self._test_event.set()

    async def connect_and_hello(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        resume_text: str = "",
        language: str = "Python",
        interview_context: str = "",
        timeout: float = 15.0,
    ) -> tuple[bool, str]:
        try:
            await self._sio.connect(
                self.server_url,
                auth={"license": self.license_key},
                transports=["websocket"],
            )
        except Exception as e:
            return (False, f"connect failed: {e}")

        self._hello_acked.clear()
        self._hello_ack_payload = {}
        await self._sio.emit("hello", {
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "resume_text": resume_text,
            "language": language,
            "interview_context": interview_context,
        })
        try:
            await asyncio.wait_for(self._hello_acked.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return (False, "hello_ack timeout")

        ack = self._hello_ack_payload
        if not ack.get("ok"):
            return (False, ack.get("error") or "hello rejected")
        return (True, "")

    async def ask(self, text: str):
        if not self._sio.connected:
            await self._on_error("not connected to server")
            return
        await self._sio.emit("ask", {"text": text})

    async def ask_vision(self, prompt: str, image_b64: str, mime_type: str = "image/png"):
        if not self._sio.connected:
            await self._on_error("not connected to server")
            return
        await self._sio.emit("ask_vision", {
            "prompt": prompt,
            "image_b64": image_b64,
            "mime_type": mime_type,
        })

    async def test(self, timeout: float = 15.0) -> tuple[bool, str]:
        if not self._sio.connected:
            return (False, "not connected to server")
        self._test_event.clear()
        self._test_result = None
        await self._sio.emit("test", {})
        try:
            await asyncio.wait_for(self._test_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return (False, "test timeout")
        res = self._test_result or {}
        return (bool(res.get("success")), res.get("error") or "")

    async def close(self):
        if self._sio.connected:
            try:
                await self._sio.disconnect()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        return self._sio.connected
