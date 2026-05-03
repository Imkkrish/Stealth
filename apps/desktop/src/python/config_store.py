"""User config + resume storage in ~/.stealth/.

Schema for ~/.stealth/config.json:
{
  "server_url":         "https://stealth-server-xxxxx.run.app",
  "license":            "<shared secret pasted by user>",
  "provider":           "gemini" | "openai" | "anthropic",
  "api_key":            "...",
  "model":              "<provider-specific model id>",
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

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
}

VALID_PROVIDERS = set(DEFAULT_MODELS.keys())

# Default coding language if the user doesn't pick one. Python because most
# interview prep / DSA reference code is easiest to read in it.
DEFAULT_LANGUAGE = "Python"


def _ensure_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(
    *,
    server_url: str,
    license: str,
    provider: str,
    api_key: str,
    model: str = "",
    language: str = "",
    interview_context: str = "",
) -> dict:
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}. Must be one of {sorted(VALID_PROVIDERS)}.")
    for name, val in (("server_url", server_url), ("license", license), ("api_key", api_key)):
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"{name} must be a non-empty string")
        if "\n" in val or "\r" in val:
            raise ValueError(f"{name} must not contain newlines")

    cfg = {
        "server_url": server_url.strip().rstrip("/"),
        "license": license.strip(),
        "provider": provider,
        "api_key": api_key.strip(),
        "model": (model or DEFAULT_MODELS[provider]).strip(),
        "language": (language or DEFAULT_LANGUAGE).strip(),
        "interview_context": (interview_context or "").strip(),
    }
    _ensure_dir()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return cfg


def is_configured() -> bool:
    cfg = load_config()
    return bool(
        cfg.get("server_url")
        and cfg.get("license")
        and cfg.get("provider")
        and cfg.get("api_key")
    )


def get_resume_text() -> str:
    if not RESUME_PATH.exists():
        return ""
    try:
        return RESUME_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_resume_text(text: str) -> None:
    _ensure_dir()
    RESUME_PATH.write_text(text or "", encoding="utf-8")
    try:
        os.chmod(RESUME_PATH, 0o600)
    except OSError:
        pass


def clear_resume() -> None:
    if RESUME_PATH.exists():
        RESUME_PATH.unlink()
