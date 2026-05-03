# Stealth — Desktop Client (Mac)

The Mac client for Stealth: a stealthy Electron overlay that captures audio + screen, transcribes locally with `faster-whisper`, and forwards everything to the Stealth server (`services/stealth-server/`) for streaming LLM answers.

> **Invisible to screen capture (Zoom/Teams/Meet) but visible to you.**

## Quick start (dev)

```bash
cd apps/desktop

# One-time setup
./start.sh                    # creates venv, installs Python + Node deps

# Run
npm run start-electron        # spawns the Python backend automatically
```

On first launch, the overlay's **Setup modal** appears. Fill in:

- **Server URL** — the deployed `*.run.app` (or `http://localhost:8080` for local dev).
- **License key** — the shared secret your server admin gave you.
- **Provider / Model / API Key** — your own Gemini, OpenAI, or Anthropic key (BYOK).
- **Preferred coding language** — Python, C++, Java, …
- **Interview context** *(optional)* — e.g. "Senior backend SWE at Stripe, 60-min coding round."
- **Resume** *(optional)* — PDF / DOCX / TXT.

Click **Test connection** to validate, then **Save & start**. Config is written to `~/.stealth/config.json` and `~/.stealth/resume.txt` — uninstalling the app and deleting `~/.stealth/` removes everything.

## Controls

| Button | Action |
|--------|--------|
| ▶ Start | Begin listening + transcribing |
| ⬛ Stop | Stop the backend |
| 📷 Vision | Capture and analyze screen |
| ✕ Quit | Close the application |
| ⌘⇧L | Toggle Ghost Mode (panic) |
| ⌘⇧T | Toggle debug console |
| ⌘⌥C | Manual screen capture |

## Stealth features

- **Hidden from Dock** — `LSUIElement: true`.
- **Invisible to screen share** — `setContentProtection(true)`.
- **Click-through** — window lets you click through; hover the bar to interact.
- **Always on top** — stays visible over all windows.

## Audio setup (important)

To capture **system audio** (what the interviewer says):

1. Install BlackHole:
   ```bash
   brew install blackhole-2ch
   ```
2. Open **Audio MIDI Setup** → click **+** → "Create Multi-Output Device" → check both **BlackHole 2ch** and your speakers → name it "Multi-Output."
3. **System Settings** → **Sound** → **Output** → select "Multi-Output Device."
4. Restart Stealth.

Without BlackHole the app falls back to your microphone.

## Privacy notification

macOS shows a microphone indicator while recording — system-level, can't be disabled. The notification shows "Electron" or your terminal name, not "Stealth." The app itself stays hidden from the Dock; the overlay is invisible to screen recording.

## Build a packaged `.app`

```bash
cd apps/desktop
npm run build-python          # PyInstaller bundle of the Python backend
npm run dist                  # electron-builder DMG/ZIP in dist/
```

The PyInstaller bundle lands in `apps/desktop/bin/stealth-backend/` and is included as `extraResources` in the packaged app. `bin/` and `dist/` are gitignored — rebuild on every release.

## Layout

```
apps/desktop/
├── package.json
├── start.sh                  # dev: create venv + install deps
├── build_python.sh           # PyInstaller bundle of the backend
├── Stealth.command           # double-click launcher (dev)
├── entitlements.mac.plist
├── build-resources/          # icon, etc.
├── context/                  # legacy placeholder dir
└── src/
    ├── electron/             # main.js, overlay.html
    └── python/
        ├── backend.py        # Electron-spawned local agent
        ├── server_client.py  # Socket.IO client → GCP server
        ├── stt.py            # faster-whisper wrapper
        ├── config_store.py   # ~/.stealth/config.json + resume.txt
        ├── resume_parser.py  # pypdf + python-docx
        └── requirements.txt
```

## Troubleshooting

- **"Backend not starting"** — ensure `./start.sh` ran once. Check `apps/desktop/venv/` exists.
- **"No audio detected"** — verify input device in System Preferences. For system audio, make sure BlackHole + Multi-Output is set as your output.
- **Setup modal says "connect failed"** — wrong server URL or wrong license. The license is the `STEALTH_SHARED_SECRET` your server admin set.
- **"build failed: ..."** in setup — bad provider key. Generate a new one at the provider's console.
