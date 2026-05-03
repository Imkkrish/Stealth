"""Stealth server — FastAPI + Socket.IO endpoint.

Stateless across connections. Per-connection in-memory session holds:
  - session["provider"]:  llm_router.LLMProvider for this user
  - session["history"]:   list[{role, content}] rolling chat history (capped)
  - session["slots"]:     dict of prompt slots (language, resume, interview_context)

Wire format:
  client → "hello":      {license, provider, api_key, model, resume_text, language, interview_context}
                         server replies "hello_ack" {ok, error?}
  client → "ask":        {text}
                         server streams "answer_chunk" {token} ... then "answer_done" {full_text}
  client → "ask_vision": {prompt, image_b64, mime_type}
                         same streaming response shape, history NOT updated
  client → "test":       {} — runs provider.test_connection(), replies "test_result" {success, error?}

Auth: shared secret in `auth` payload of the Socket.IO connect call.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import socketio
from fastapi import FastAPI

import auth
import classifier
import llm_router
import prompts


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stealth.server")

# CORS is permissive — clients connect from Electron (file:// origin), not a browser.
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


# --------------------------------------------------------------------------
# FastAPI host
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    secret_set = bool(auth.expected_secret())
    log.info("Stealth server starting. Auth configured: %s", secret_set)
    if not secret_set:
        log.warning("STEALTH_SHARED_SECRET is unset — every connection will be rejected.")
    yield
    log.info("Stealth server shutting down.")


fastapi_app = FastAPI(lifespan=lifespan, title="Stealth Server")


@fastapi_app.get("/health")
async def health() -> dict:
    return {"ok": True, "auth_configured": bool(auth.expected_secret())}


# Compose the ASGI app: Socket.IO under /socket.io, FastAPI for HTTP routes.
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
HISTORY_TURNS_CAP = 24  # last N user/assistant turns kept; older ones evicted
MAX_INPUT_CHARS = 16_000


# --------------------------------------------------------------------------
# Connection lifecycle
# --------------------------------------------------------------------------
@sio.event
async def connect(sid, environ, auth_payload):
    """Reject connections without a valid shared secret."""
    provided = None
    if isinstance(auth_payload, dict):
        provided = auth_payload.get("license") or auth_payload.get("token")
    if not auth.check(provided):
        log.warning("Rejected connection from sid=%s (bad/missing license)", sid)
        # Returning False from an async connect handler causes the server to
        # disconnect the client with ConnectionRefusedError on the client side.
        raise ConnectionRefusedError("Invalid or missing license")

    log.info("Accepted connection sid=%s", sid)
    async with sio.session(sid) as sess:
        sess["provider"] = None
        sess["history"] = []
        sess["slots"] = {}
        # Single-flight guard for ask / ask_vision. While streaming==True, any
        # second request is rejected with a visible 'answer already streaming'
        # error rather than interleaving tokens into the live answer card.
        sess["streaming"] = False


@sio.event
async def disconnect(sid):
    log.info("Disconnect sid=%s", sid)


# --------------------------------------------------------------------------
# Hello / setup — build the per-session LLM provider
# --------------------------------------------------------------------------
@sio.event
async def hello(sid, data: dict):
    """Receive user config, build LLM providers (default + DSA) for this session.

    Two providers are built — one for fast/general routing, one for DSA. They
    share the same API key and system prompt, only the model name differs.
    If `model_dsa` is missing or equal to `model`, only one provider is built
    and reused for both routes (back-compat with old clients).
    """
    try:
        provider_name = (data or {}).get("provider", "").strip()
        api_key = (data or {}).get("api_key", "").strip()
        model = (data or {}).get("model", "").strip()
        model_dsa = ((data or {}).get("model_dsa") or "").strip() or model
        slots = {
            "language": (data or {}).get("language", "").strip() or "Python",
            "resume": (data or {}).get("resume_text", "") or "",
            "interview_context": (data or {}).get("interview_context", "") or "",
        }

        if not provider_name or not api_key or not model:
            await sio.emit("hello_ack", {"ok": False, "error": "provider, api_key, model required"}, to=sid)
            return

        # Build text-mode system prompt now; vision uses a per-call recompose.
        slots_text = dict(slots, mode="text")
        sys_prompt = prompts.compose_for(provider_name, slots_text)

        try:
            provider = llm_router.build(
                provider=provider_name, api_key=api_key, model=model, system_prompt=sys_prompt,
            )
            if model_dsa != model:
                provider_dsa = llm_router.build(
                    provider=provider_name, api_key=api_key, model=model_dsa, system_prompt=sys_prompt,
                )
            else:
                # Same model on both routes — share the instance to save a build.
                provider_dsa = provider
        except Exception as e:
            await sio.emit("hello_ack", {"ok": False, "error": f"build failed: {e}"}, to=sid)
            return

        async with sio.session(sid) as sess:
            sess["provider"] = provider
            sess["provider_dsa"] = provider_dsa
            sess["history"] = []
            sess["slots"] = slots
            sess["provider_name"] = provider_name
            sess["model"] = model
            sess["model_dsa"] = model_dsa

        log.info("hello sid=%s provider=%s model=%s model_dsa=%s resume=%dchars",
                 sid, provider_name, model, model_dsa, len(slots["resume"]))
        await sio.emit("hello_ack", {
            "ok": True, "provider": provider_name, "model": model, "model_dsa": model_dsa,
        }, to=sid)
    except Exception as e:
        log.exception("hello error sid=%s: %s", sid, e)
        await sio.emit("hello_ack", {"ok": False, "error": str(e)}, to=sid)


# --------------------------------------------------------------------------
# Streaming text chat
# --------------------------------------------------------------------------
async def _try_acquire_stream(sid) -> bool:
    """Reserve the single-flight slot for this session. Returns False if busy."""
    async with sio.session(sid) as sess:
        if sess.get("streaming"):
            return False
        sess["streaming"] = True
        return True


async def _release_stream(sid):
    async with sio.session(sid) as sess:
        sess["streaming"] = False


@sio.event
async def ask(sid, data: dict):
    """Stream a chat answer back to the client as answer_chunk events.

    Routes per-request: DSA-shaped prompts go to the strong model
    (`provider_dsa` / `model_dsa`); everything else uses the fast default.
    """
    text = (data or {}).get("text", "").strip()
    if not text:
        await sio.emit("error", {"message": "empty text"}, to=sid)
        return
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]

    sess = await sio.get_session(sid)
    provider_default = sess.get("provider")
    provider_dsa = sess.get("provider_dsa") or provider_default
    history = sess.get("history") or []
    if provider_default is None:
        await sio.emit("error", {"message": "session not initialized — send hello first"}, to=sid)
        return

    is_dsa = classifier.is_coding_problem(text)
    provider = provider_dsa if is_dsa else provider_default
    model_used = sess.get("model_dsa") if is_dsa else sess.get("model")
    log.info("ask sid=%s routed=%s model=%s len=%d", sid, "dsa" if is_dsa else "default", model_used, len(text))

    if not await _try_acquire_stream(sid):
        await sio.emit("error", {"message": "answer already streaming — wait for it to finish"}, to=sid)
        return

    full = []
    try:
        async for token in provider.chat_stream(text, history):
            full.append(token)
            await sio.emit("answer_chunk", {"token": token}, to=sid)
        full_text = "".join(full)
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": full_text})
        # Cap history to the last N turns to keep prompts cheap.
        if len(history) > HISTORY_TURNS_CAP * 2:
            history[:] = history[-HISTORY_TURNS_CAP * 2:]
        async with sio.session(sid) as s:
            s["history"] = history
        await sio.emit("answer_done", {"full_text": full_text, "model_used": model_used, "route": "dsa" if is_dsa else "default"}, to=sid)
    except Exception as e:
        log.exception("ask error sid=%s: %s", sid, e)
        await sio.emit("error", {"message": str(e)}, to=sid)
    finally:
        await _release_stream(sid)


# --------------------------------------------------------------------------
# Streaming vision (one-shot — does not update history)
# --------------------------------------------------------------------------
@sio.event
async def ask_vision(sid, data: dict):
    user_prompt = (data or {}).get("prompt", "") or ""
    image_b64 = (data or {}).get("image_b64", "") or ""
    mime = (data or {}).get("mime_type", "image/png")
    if not image_b64:
        await sio.emit("error", {"message": "image_b64 required"}, to=sid)
        return

    sess = await sio.get_session(sid)
    provider = sess.get("provider")
    if provider is None:
        await sio.emit("error", {"message": "session not initialized — send hello first"}, to=sid)
        return

    if not await _try_acquire_stream(sid):
        await sio.emit("error", {"message": "answer already streaming — wait for it to finish"}, to=sid)
        return

    # For vision we recompose with mode="vision" and rebuild a one-shot provider
    # so the resume/interview context is truncated appropriately. The model is
    # always the DSA / strong model — the 95% case is a coding screenshot, and
    # vision answers are full-card replies where quality > latency. The one-shot
    # provider is used once and discarded.
    slots = sess.get("slots") or {}
    provider_name = sess.get("provider_name", "")
    model_for_vision = sess.get("model_dsa") or sess.get("model", "")
    api_key = provider.api_key  # type: ignore[attr-defined]

    vision_slots = dict(slots, mode="vision")
    vision_system = prompts.compose_for(provider_name, vision_slots)
    try:
        vision_provider = llm_router.build(
            provider=provider_name, api_key=api_key, model=model_for_vision, system_prompt=vision_system,
        )
    except Exception as e:
        await sio.emit("error", {"message": f"vision build failed: {e}"}, to=sid)
        await _release_stream(sid)
        return

    log.info("ask_vision sid=%s routed=dsa model=%s img_b64_len=%d", sid, model_for_vision, len(image_b64))

    full = []
    try:
        async for token in vision_provider.vision_stream(user_prompt, image_b64, mime):
            full.append(token)
            await sio.emit("answer_chunk", {"token": token}, to=sid)
        await sio.emit("answer_done", {"full_text": "".join(full), "model_used": model_for_vision, "route": "dsa"}, to=sid)
    except Exception as e:
        log.exception("ask_vision error sid=%s: %s", sid, e)
        await sio.emit("error", {"message": str(e)}, to=sid)
    finally:
        await _release_stream(sid)


# --------------------------------------------------------------------------
# Connection probe (used by the client's "Test connection" button)
# --------------------------------------------------------------------------
@sio.event
async def test(sid, data: dict):
    sess = await sio.get_session(sid)
    provider = sess.get("provider")
    if provider is None:
        await sio.emit("test_result", {"success": False, "error": "session not initialized"}, to=sid)
        return

    import asyncio
    loop = asyncio.get_running_loop()
    try:
        ok, err = await loop.run_in_executor(None, provider.test_connection)
        await sio.emit("test_result", {"success": bool(ok), "error": err}, to=sid)
    except Exception as e:
        await sio.emit("test_result", {"success": False, "error": str(e)}, to=sid)


# --------------------------------------------------------------------------
# Local dev entry: `uvicorn server.app:app --reload`
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
