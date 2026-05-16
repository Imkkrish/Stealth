"""User config + resume storage in ~/.stealth/.

Schema for ~/.stealth/config.json:
{
  "server_url":         "https://stealth-server-xxxxx.run.app",
  "license":            "<shared secret pasted by user>",
  "provider":           "gemini" | "openai" | "anthropic",
  "api_key":            "...",
  "model":              "<fast/general model — used for chit-chat, MCQ, conceptual>",
  "model_dsa":          "<slower/stronger model — used when server classifies the request as a coding/DSA problem>",
  "language":           "Python" (default),
  "interview_context":  "Senior backend SWE at Stripe, 60-min coding round" (optional)
}

Resume text is stored in ~/.stealth/resume.txt as plain UTF-8.
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".stealth"
CONFIG_PATH = CONFIG_DIR / "config.json"
RESUME_PATH = CONFIG_DIR / "resume.txt"

# ----------------------------------------------------------------------------
# Baked-in defaults — friends should not have to know these. They live with
# the app binary; rotating either requires shipping a new client. The license
# is a low-stakes shared secret (limits server access to people who have the
# app), not a real auth credential.
# ----------------------------------------------------------------------------
BAKED_IN_SERVER_URL = "https://stealth-server-ojhmhbexla-el.a.run.app"
BAKED_IN_LICENSE = "4c57e3a11042f31f46b0a3314ce2612017e5213f06f87f83"

# Default model used for general / conversational requests — fast, cheap, good
# enough for chit-chat / MCQ / conceptual.
DEFAULT_MODELS = {
    "gemini": "gemini-3-flash-preview",
    "openai": "gpt-4o",
    "anthropic": "claude-haiku-4-5-20251001",
}

# Default model used when the server's classifier decides the request is a
# coding / DSA problem — slower but stronger reasoning. User can override
# either field in the setup modal.
DEFAULT_DSA_MODELS = {
    "gemini": "gemini-3.1-pro-preview",
    "openai": "gpt-4o",          # `o1` is opt-in via override
    "anthropic": "claude-sonnet-4-6",
}

VALID_PROVIDERS = set(DEFAULT_MODELS.keys())

# Default coding language if the user doesn't pick one. Python because most
# interview prep / DSA reference code is easiest to read in it.
DEFAULT_LANGUAGE = "Python"


def _ensure_dir():
    """Create ~/.stealth/ if it doesn't exist.

    Gracefully handles macOS TCC permission blocks where stat() fails with
    EPERM but the directory actually exists on disk, causing mkdir(exist_ok)
    to raise FileExistsError instead of silently succeeding.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, PermissionError, OSError):
        # FileExistsError: TCC blocks stat() — dir exists but pathlib can't
        #   confirm it, so exist_ok=True doesn't kick in.
        # PermissionError/OSError: system-level block on the path.
        pass
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def _safe_write(path: Path, data: str, mode: int = 0o600):
    """Write text to a file, ignoring permission errors."""
    try:
        path.write_text(data, encoding="utf-8")
    except (PermissionError, OSError) as e:
        print(f"⚠️  config_store: cannot write {path}: {e}")
        return False
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    return True


def load_config() -> dict:
    try:
        if not CONFIG_PATH.exists():
            return {}
    except (PermissionError, OSError):
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, PermissionError, OSError):
        return {}


def save_config(
    *,
    server_url: str = "",
    license: str = "",
    provider: str,
    api_key: str,
    model: str = "",
    model_dsa: str = "",
    language: str = "",
    interview_context: str = "",
) -> dict:
    """Persist the user's config. server_url and license fall back to the
    baked-in defaults — the user does not need to provide them."""
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Must be one of {sorted(VALID_PROVIDERS)}.")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("api_key must not contain newlines")

    cfg = {
        "server_url": (server_url or BAKED_IN_SERVER_URL).strip().rstrip("/"),
        "license": (license or BAKED_IN_LICENSE).strip(),
        "provider": provider,
        "api_key": api_key.strip(),
        "model": (model or DEFAULT_MODELS[provider]).strip(),
        "model_dsa": (model_dsa or DEFAULT_DSA_MODELS[provider]).strip(),
        "language": (language or DEFAULT_LANGUAGE).strip(),
        "interview_context": (interview_context or "").strip(),
    }
    _ensure_dir()
    _safe_write(CONFIG_PATH, json.dumps(cfg, indent=2))
    return cfg


def bootstrap_config() -> dict:
    """Make sure ~/.stealth/config.json exists and has the baked-in server_url
    + license. Called on every backend start; idempotent.

    Does NOT add a provider/api_key — those are user-supplied. Returns the
    (possibly partially-empty) config dict.
    """
    cfg = load_config()
    changed = False
    if not cfg.get("server_url"):
        cfg["server_url"] = BAKED_IN_SERVER_URL
        changed = True
    if not cfg.get("license"):
        cfg["license"] = BAKED_IN_LICENSE
        changed = True
    if changed:
        _ensure_dir()
        _safe_write(CONFIG_PATH, json.dumps(cfg, indent=2))
    return cfg


def is_configured() -> bool:
    """The app is "configured" once the user has supplied a provider + api_key.
    server_url and license auto-fill from baked-in defaults, so we only check
    the user-supplied fields."""
    cfg = load_config()
    return bool(cfg.get("provider") and cfg.get("api_key"))


def get_resume_text() -> str:
    try:
        if not RESUME_PATH.exists():
            return ""
    except (PermissionError, OSError):
        return ""
    try:
        return RESUME_PATH.read_text(encoding="utf-8").strip()
    except (PermissionError, OSError):
        return ""


def save_resume_text(text: str) -> None:
    _ensure_dir()
    _safe_write(RESUME_PATH, text or "")


def clear_resume() -> None:
    try:
        if RESUME_PATH.exists():
            RESUME_PATH.unlink()
    except (PermissionError, OSError):
        pass
