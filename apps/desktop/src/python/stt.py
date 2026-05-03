"""Local STT via faster-whisper (CTranslate2 backend, no PyTorch).

Same `tiny.en` quality as openai-whisper but ~5× faster and ~450 MB smaller
in a PyInstaller bundle. Runs on CPU with int8 quantization.
"""

from __future__ import annotations

import threading


class WhisperSTT:
    def __init__(self, model_size: str = "tiny.en"):
        self.model_size = model_size
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        """Eagerly load the model; safe to call multiple times."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
            )

    def transcribe(self, audio_float32) -> str:
        """Transcribe a numpy float32 mono 16kHz array. Returns the joined text."""
        if self._model is None:
            self.load()
        segments, _info = self._model.transcribe(
            audio_float32,
            language="en",
            beam_size=1,           # fastest; tiny.en has tiny beam-search benefit
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return "".join(seg.text for seg in segments).strip()
