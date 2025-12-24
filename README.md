# 👻 Stealth - Ghost Overlay

An invisible interview helper that floats over your screen, listens to audio, transcribes it, and provides AI-generated answers in real-time.

**INVISIBLE to screen capture software (Zoom/Teams/Screen Sharing) but VISIBLE to you.**

## 🚀 Quick Start

### Option 1: Double-Click App (Recommended)
1. Double-click **`Stealth.app`** in Finder
2. The overlay will appear in the top-right corner
3. Click ▶ **Start** to begin listening

### Option 2: Terminal
```bash
./Stealth.command
```

## 🎛️ Controls

| Button | Action |
|--------|--------|
| ▶ Start | Begin listening and transcribing |
| ⬛ Stop | Stop the backend |
| 📷 Vision | Capture and analyze screen |
| ✕ Quit | Close the application |

## 🔒 Stealth Features

- **Hidden from Dock**: App doesn't appear in macOS Dock
- **Invisible to Screen Share**: Uses `setContentProtection(true)`
- **Click-through**: Window lets you click through it; hover the bar to interact
- **Always on Top**: Stays visible over all windows

## 🎤 Audio Setup (Important!)

### To capture system audio (what the interviewer says):

1. **Install BlackHole**:
   ```bash
   brew install blackhole-2ch
   ```

2. **Create Multi-Output Device**:
   - Open **Audio MIDI Setup** (Spotlight search)
   - Click **+** → "Create Multi-Output Device"
   - Check both **BlackHole 2ch** AND your speakers
   - Name it "Multi-Output"

3. **Set as Output**:
   - Go to **System Preferences** → **Sound** → **Output**
   - Select "Multi-Output Device"

4. **Restart Stealth**

Without BlackHole, the app will use your microphone instead.

## ⚠️ About Privacy Notifications

macOS will show a microphone indicator when the app is recording. This is a system-level privacy feature that cannot be disabled. However:

- The notification shows "Electron" or your terminal name, not "Stealth"
- The app itself is hidden from the Dock
- The overlay is invisible to screen recording

## 📁 Project Structure

```
Stealth/
├── Stealth.app/           # Double-click to launch
├── Stealth.command        # Alternative launcher
├── src/
│   ├── electron/          # Frontend (overlay)
│   │   ├── main.js
│   │   └── overlay.html
│   └── python/            # Backend (AI & audio)
│       └── backend.py
├── .env                   # Your API key
└── venv/                  # Python environment
```

## 🔧 Troubleshooting

### "Backend not starting"
- Ensure you ran `./start.sh` once to install dependencies
- Check that `venv/` exists

### "No audio detected"
- Check your input device in System Preferences
- Ensure BlackHole is set up for system audio

### "LLM Error 404"
- Your API key may be invalid or have restrictions
- Try regenerating your Gemini API key

## 🛠️ Development

```bash
# Install all dependencies
./start.sh

# Run manually (for debugging)
source venv/bin/activate
python3 src/python/backend.py  # In one terminal
npm run start-electron          # In another terminal
```

---

**Good luck with your interview! 🍀**
