#!/usr/bin/env python3
"""List Gemini models available to the current API key and probe them.

Usage:
  python3 services/stealth-server/check_gemini_models.py
  python3 services/stealth-server/check_gemini_models.py --env-file ../../.env
  python3 services/stealth-server/check_gemini_models.py --models gemini-2.5-flash gemini-2.5-pro
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

INTERVIEW_PROMPT = """
You are evaluating a senior full-stack developer interview assistant.

Task:
1. Generate 3 high-quality full-stack developer interview questions.
2. Cover backend, frontend, and system design.
3. For each question, state what a strong answer should include.
4. Keep the output concise and structured.
""".strip()

TWO_SUM_PROMPT = """
Solve this Python coding interview problem:

Given a list of integers nums and an integer target, return the indices of the two numbers such that they add up to target.
You may assume exactly one solution exists, and you may not use the same element twice.

Example:
nums = [2, 7, 11, 15]
target = 9
Output: [0, 1]

Requirements:
1. Provide only Python code.
2. Use an efficient approach.
3. Include a function signature: def two_sum(nums, target):
""".strip()


def load_env_file(env_file: str | None) -> None:
    if not env_file:
        return

    path = Path(env_file).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60, context=ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120, context=ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ssl_context() -> ssl.SSLContext:
    cafile = os.getenv("SSL_CERT_FILE", "").strip()
    if cafile:
        return ssl.create_default_context(cafile=cafile)

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def list_models(api_key: str) -> list[dict]:
    page_token = None
    models: list[dict] = []
    while True:
        params = {"key": api_key}
        if page_token:
            params["pageToken"] = page_token
        url = f"{MODELS_URL}?{urllib.parse.urlencode(params)}"
        data = http_get_json(url)
        models.extend(data.get("models", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            return models


def model_short_name(name: str) -> str:
    return name.removeprefix("models/")


def is_gemini_family(model: dict) -> bool:
    name = model_short_name(model["name"])
    return name.startswith("gemini")


def is_text_candidate(model: dict) -> bool:
    methods = set(model.get("supportedGenerationMethods", []))
    name = model_short_name(model["name"])
    return "generateContent" in methods and not any(
        token in name
        for token in (
            "tts",
            "image",
            "embedding",
            "robotics",
            "computer-use",
            "deep-research",
        )
    )


def classify_model(model: dict) -> str:
    name = model_short_name(model["name"])
    if "tts" in name:
        return "tts"
    if "image" in name:
        return "image"
    if "embedding" in name:
        return "embedding"
    if "deep-research" in name:
        return "research"
    if "robotics" in name:
        return "robotics"
    if "computer-use" in name:
        return "computer-use"
    if "generateContent" in model.get("supportedGenerationMethods", []):
        return "text"
    return "other"


def probe_model(api_key: str, model_name: str, prompt: str) -> dict:
    url = f"{GENERATE_URL.format(model=model_name)}?{urllib.parse.urlencode({'key': api_key})}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ]
    }

    started = time.perf_counter()
    try:
        data = http_post_json(url, payload)
        elapsed = time.perf_counter() - started
        text = extract_text(data)
        return {
            "ok": True,
            "seconds": round(elapsed, 2),
            "preview": one_line(text, 220),
        }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "seconds": round(time.perf_counter() - started, 2),
            "preview": one_line(detail, 220),
        }
    except Exception as exc:  # pragma: no cover
        return {
            "ok": False,
            "seconds": round(time.perf_counter() - started, 2),
            "preview": one_line(str(exc), 220),
        }


def extract_text(response: dict) -> str:
    candidates = response.get("candidates") or []
    parts: list[str] = []
    for candidate in candidates:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            text = part.get("text")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def one_line(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def print_model_table(models: list[dict]) -> None:
    print("\nAvailable Gemini-family models for this key:")
    for model in models:
        short_name = model_short_name(model["name"])
        category = classify_model(model)
        methods = ",".join(model.get("supportedGenerationMethods", []))
        print(f"- {short_name:40} category={category:13} methods={methods}")


def resolve_probe_models(models: list[dict], requested: list[str] | None) -> list[str]:
    available = {model_short_name(model["name"]): model for model in models}
    if requested:
        missing = [name for name in requested if name not in available]
        if missing:
            raise ValueError(f"Requested models not found for this key: {', '.join(missing)}")
        return requested

    preferred = [
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview-customtools",
        "gemini-3-flash-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-pro-latest",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-2.0-flash",
    ]

    chosen = [name for name in preferred if name in available and is_text_candidate(available[name])]
    if chosen:
        return chosen

    return [
        model_short_name(model["name"])
        for model in models
        if is_text_candidate(model)
    ]


def run_probe_set(api_key: str, models: list[str], title: str, prompt: str) -> None:
    print(f"\n{title}:")
    for model_name in models:
        result = probe_model(api_key, model_name, prompt)
        status = "OK" if result["ok"] else "FAIL"
        print(f"- {model_name:40} {status:4} {result['seconds']:>6}s  {result['preview']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional path to .env file that contains GEMINI_API_KEY",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        help="Optional explicit model names to probe, without the models/ prefix",
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("GEMINI_API_KEY is not set.", file=sys.stderr)
        print("Pass --env-file ../../.env or export GEMINI_API_KEY first.", file=sys.stderr)
        return 1

    try:
        models = list_models(api_key)
    except Exception as exc:
        print(f"Failed to list models: {exc}", file=sys.stderr)
        return 2

    gemini_models = [model for model in models if is_gemini_family(model)]
    print_model_table(gemini_models)

    try:
        probe_targets = resolve_probe_models(gemini_models, args.models)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    run_probe_set(api_key, probe_targets, "Interview-task probe results", INTERVIEW_PROMPT)
    run_probe_set(api_key, probe_targets, "Two-sum Python probe results", TWO_SUM_PROMPT)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
