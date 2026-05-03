"""Provider-agnostic LLM router for Gemini, OpenAI, and Anthropic.

Each provider exposes:
  - chat_stream(message, history)              -> AsyncIterator[str]
  - vision_stream(prompt, image_b64, mime)     -> AsyncIterator[str]
  - test_connection()                          -> (ok: bool, error_message: str)

History is a list of {"role": "user"|"assistant", "content": str}.
The system prompt is held by the provider and passed automatically.

Streaming is the *only* code path that runs during normal use; the non-stream
methods exist solely for `test_connection`.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import AsyncIterator


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, api_key: str, model: str, system_prompt: str = ""):
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt

    @abstractmethod
    def chat_stream(self, message: str, history: list[dict] | None = None) -> AsyncIterator[str]: ...

    @abstractmethod
    def vision_stream(self, prompt: str, image_b64: str, mime_type: str = "image/png") -> AsyncIterator[str]: ...

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]: ...


# ---------------------------------------------------------------------------
# Helper: convert a sync iterator into an async one without blocking the loop
# ---------------------------------------------------------------------------
async def _stream_sync(iter_factory) -> AsyncIterator[str]:
    """Run a synchronous generator factory in a thread, yielding strings."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    SENTINEL = object()

    def producer():
        try:
            for chunk in iter_factory():
                if chunk:
                    asyncio.run_coroutine_threadsafe(queue.put(chunk), loop).result()
        except Exception as e:
            asyncio.run_coroutine_threadsafe(queue.put(f"\n\n⚠️  Stream error: {e}"), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(SENTINEL), loop).result()

    loop.run_in_executor(None, producer)

    while True:
        item = await queue.get()
        if item is SENTINEL:
            return
        yield item


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, system_prompt: str = ""):
        super().__init__(api_key, model, system_prompt)
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._genai = genai
        self._model = genai.GenerativeModel(
            model_name=model,
            system_instruction=system_prompt or None,
        )

    def _gemini_history(self, history: list[dict] | None) -> list[dict]:
        out = []
        for turn in history or []:
            role = "user" if turn["role"] == "user" else "model"
            out.append({"role": role, "parts": [turn["content"]]})
        return out

    def chat_stream(self, message: str, history: list[dict] | None = None) -> AsyncIterator[str]:
        gemini_history = self._gemini_history(history)
        chat = self._model.start_chat(history=gemini_history)

        def factory():
            resp = chat.send_message(message, stream=True)
            for chunk in resp:
                txt = getattr(chunk, "text", None)
                if txt:
                    yield txt

        return _stream_sync(factory)

    def vision_stream(self, prompt: str, image_b64: str, mime_type: str = "image/png") -> AsyncIterator[str]:
        image_part = {"mime_type": mime_type, "data": image_b64}

        def factory():
            resp = self._model.generate_content([prompt, image_part], stream=True)
            for chunk in resp:
                txt = getattr(chunk, "text", None)
                if txt:
                    yield txt

        return _stream_sync(factory)

    def test_connection(self) -> tuple[bool, str]:
        try:
            resp = self._model.generate_content("ping")
            return (bool(getattr(resp, "text", "")), "")
        except Exception as e:
            return (False, str(e))


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, system_prompt: str = ""):
        super().__init__(api_key, model, system_prompt)
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)

    def _messages(self, message: str, history: list[dict] | None = None) -> list[dict]:
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        for turn in history or []:
            msgs.append({"role": turn["role"], "content": turn["content"]})
        msgs.append({"role": "user", "content": message})
        return msgs

    def chat_stream(self, message: str, history: list[dict] | None = None) -> AsyncIterator[str]:
        def factory():
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=self._messages(message, history),
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                token = getattr(delta, "content", None) if delta else None
                if token:
                    yield token

        return _stream_sync(factory)

    def vision_stream(self, prompt: str, image_b64: str, mime_type: str = "image/png") -> AsyncIterator[str]:
        data_url = f"data:{mime_type};base64,{image_b64}"
        msgs = []
        if self.system_prompt:
            msgs.append({"role": "system", "content": self.system_prompt})
        msgs.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        })

        def factory():
            stream = self._client.chat.completions.create(
                model=self.model, messages=msgs, stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                token = getattr(delta, "content", None) if delta else None
                if token:
                    yield token

        return _stream_sync(factory)

    def test_connection(self) -> tuple[bool, str]:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return (bool(resp.choices[0].message.content), "")
        except Exception as e:
            return (False, str(e))


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, system_prompt: str = ""):
        super().__init__(api_key, model, system_prompt)
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)

    def chat_stream(self, message: str, history: list[dict] | None = None) -> AsyncIterator[str]:
        msgs = []
        for turn in history or []:
            msgs.append({"role": turn["role"], "content": turn["content"]})
        msgs.append({"role": "user", "content": message})

        def factory():
            with self._client.messages.stream(
                model=self.model,
                system=self.system_prompt or None,
                max_tokens=4096,
                messages=msgs,
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        yield text

        return _stream_sync(factory)

    def vision_stream(self, prompt: str, image_b64: str, mime_type: str = "image/png") -> AsyncIterator[str]:
        msgs = [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }]

        def factory():
            with self._client.messages.stream(
                model=self.model,
                system=self.system_prompt or None,
                max_tokens=4096,
                messages=msgs,
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        yield text

        return _stream_sync(factory)

    def test_connection(self) -> tuple[bool, str]:
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            ok = any(getattr(b, "type", "") == "text" for b in resp.content)
            return (ok, "")
        except Exception as e:
            return (False, str(e))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
PROVIDERS = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}


def build(provider: str, api_key: str, model: str, system_prompt: str = "") -> LLMProvider:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Must be one of {sorted(PROVIDERS)}.")
    return PROVIDERS[provider](api_key=api_key, model=model, system_prompt=system_prompt)
