"""
Stealth Backend - Final Working Build
Audio Capture + Transcription + AI + Vision Support
macOS Compatible with sounddevice
"""

# =============================================================================
# ENVIRONMENT SETUP (CRITICAL: Load before anything else)
# =============================================================================
import os
import sys

# Ensure all output is flushed immediately
def flush_log(msg):
    # Use triple-arrow for easy scanning
    print(f">>> {msg}", flush=True)

# Force line buffering if possible (Python 3.7+)
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

flush_log("Python backend process started")
flush_log(f"Python version: {sys.version}")
flush_log(f"Executable: {sys.executable}")

# Make sibling modules importable in both dev and PyInstaller modes
_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

flush_log("Loading user config from ~/.stealth/config.json ...")
import config_store

# Ensure baked-in server_url + license are written on first run (or top-up if
# they were missing for any reason). User only ever has to supply provider+key.
user_config = config_store.bootstrap_config()
server_url = user_config.get("server_url") or config_store.BAKED_IN_SERVER_URL
license_key = user_config.get("license") or config_store.BAKED_IN_LICENSE
api_key = user_config.get("api_key", "")
provider_name = user_config.get("provider", "")
model_name = user_config.get("model", "")
# DSA model — falls back to provider default if missing in stored config (back-compat).
model_dsa_name = user_config.get(
    "model_dsa",
    config_store.DEFAULT_DSA_MODELS.get(provider_name, "") if provider_name else "",
)
language_pref = user_config.get("language", config_store.DEFAULT_LANGUAGE)
interview_context_pref = user_config.get("interview_context", "")
resume_text = config_store.get_resume_text()

if config_store.is_configured():
    flush_log(f"✅ Config loaded: provider={provider_name}, model={model_name}")
    if resume_text:
        flush_log(f"✅ Resume loaded ({len(resume_text)} chars)")
else:
    flush_log("⚠️  No API key yet — running in QUEUED mode. User can add a key any time via Settings.")

flush_log("Importing system modules (ssl, certifi, socket, signal)...")
import ssl
import certifi
import socket
import signal
flush_log("System modules imported.")

# Fix SSL certificate issues on macOS
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
ssl._create_default_https_context = ssl._create_unverified_context

flush_log("Importing async modules (asyncio, base64, io, threading, queue, time)...")
import asyncio
import base64
import io
import threading
import queue
import time
import tempfile
import subprocess
flush_log("Async and system utility modules imported.")

# =============================================================================
# PORT MANAGEMENT
# =============================================================================
SERVER_PORT = 5051

def is_port_in_use(port: int) -> bool:
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            return False
        except OSError:
            return True

def kill_process_on_port(port: int) -> bool:
    """Attempt to kill process occupying a port (macOS/Linux)."""
    try:
        import subprocess
        # First find PIDs
        result = subprocess.run(
            f"lsof -ti:{port}",
            shell=True,
            capture_output=True,
            text=True
        )
        pids = result.stdout.strip()
        if not pids:
            return True  # nothing to kill
        print(f"      Found PIDs on port {port}: {pids}")
        # Kill each PID
        for pid in pids.split('\n'):
            pid = pid.strip()
            if pid and pid != str(os.getpid()):  # don't kill ourselves
                subprocess.run(f"kill -9 {pid}", shell=True, capture_output=True)
                print(f"      Killed PID {pid}")
        return True
    except Exception as e:
        print(f"      ⚠️  Could not kill process: {e}")
        return False

def ensure_port_available(port: int, max_retries: int = 5) -> bool:
    """Ensure port is available, attempting cleanup if needed.
    Retries with increasing backoff to wait for OS to release the port."""
    if not is_port_in_use(port):
        return True
    
    print(f"\n⚠️  Port {port} is already in use!")
    print(f"   Attempting to kill zombie process...")
    
    kill_process_on_port(port)
    
    # Retry with backoff -- macOS can take a moment to release
    for attempt in range(max_retries):
        wait = 0.5 * (attempt + 1)
        time.sleep(wait)
        if not is_port_in_use(port):
            print(f"      ✅ Port {port} freed after {attempt + 1} retries")
            return True
        print(f"      Retry {attempt + 1}/{max_retries} -- port still busy, waiting {wait:.1f}s...")
        # Try killing again on later retries
        if attempt >= 1:
            kill_process_on_port(port)
    
    # Last resort: since we use reuse_address + reuse_port, try anyway
    print(f"      ⚠️  Port {port} may still be in TIME_WAIT -- proceeding with SO_REUSEADDR")
    return True

flush_log("Importing server modules (socketio, aiohttp)...")
import socketio
from aiohttp import web
flush_log("Server modules imported.")

flush_log("Minimalist startup: Deferring heavy imports for later...")
# Note: Heavy imports (numpy, PIL, mss, Quartz, torch, whisper, etc.) 
# are now deferred to ensure instant Socket.IO startup.

# Global state for lazy-loaded modules
QUARTZ_AVAILABLE = False
torch = None
whisper = None
genai = None
np = None
Image = None
mss = None

# =============================================================================
# CONFIGURATION
# =============================================================================
SAMPLE_RATE = 16000
CHUNK_DURATION = 2  # seconds per audio chunk
SILENCE_THRESHOLD = 0.01
VISION_SIZE = (1024, 1024)  # Resize for speed

# Vision trigger keywords (for voice commands)
VISION_TRIGGERS = [
    "look at this", "read code", "what is this",
    "screen", "screenshot", "read screen",
    "see this", "analyze this", "look at the screen"
]

# Window Watcher: Target app keywords for auto-triggering vision
TARGET_WINDOW_KEYWORDS = [
    # Coding platforms (in window title)
    "leetcode", "github", "codechef", "hackerrank", "codeforces",
    "codesignal", "hackerearth", "topcoder", "codility", "algoexpert",
    "neetcode", "interviewbit", "pramp", "interviewing.io",
    # IDEs and editors
    "vs code", "visual studio", "code -", "pycharm", "intellij", "webstorm",
    "sublime", "atom", "vim", "neovim", "emacs", "xcode",
    # Notebooks
    "jupyter", "notebook", "colab", "kaggle",
    # Problem indicators  
    "problem", "solution", "challenge", "exercise", "coding test", "deductive reasoning", "switchchallenge",
    "math", "calculus", "geometry", "equation", "statistics", "integral", "derivative", "trigonometry",
    # Common problem names (LeetCode titles)
    "two sum", "valid parentheses", "merge", "binary", "linked list",
    "array", "string", "tree", "graph", "dynamic", "add two numbers"
]

# Apps that are always considered "target" (browsers likely on coding sites)
# Since Chrome/Safari don't expose page titles, we auto-trigger on browser focus
TARGET_APPS = [
    "google chrome", "safari", "firefox", "arc", "brave", "edge", "opera"
]

# =============================================================================
# WINDOW WATCHER STATE
# =============================================================================
auto_watch_enabled = True
stealth_mode_active = False  # NEW: Deep Stealth (Stops all hardware capture)
last_window_title = ""
last_vision_trigger_time = 0
VISION_COOLDOWN = 20  # Increased to 20s to avoid free-tier rate limits
CONTEXT_DEBOUNCE = 3  # Seconds to wait after window change before triggering

# =============================================================================
# SOCKET.IO SERVER SETUP
# =============================================================================
sio = socketio.AsyncServer(async_mode='aiohttp', cors_allowed_origins='*')
app = web.Application()
sio.attach(app)

# =============================================================================
# GLOBAL STATE
# =============================================================================
audio_queue = queue.Queue()
text_queue = queue.Queue()
blackhole_found = False
hardware_initialized = False  # Flag to prevent early "Driver Missing" warnings
selected_device_index = None

# Transcription buffer for debouncing (accumulate text, wait for pause)
transcript_buffer = []
transcript_buffer_lock = threading.Lock()
last_transcript_time = 0
DEBOUNCE_SECONDS = 2.0  # Wait 2 seconds of silence before sending to LLM

# =============================================================================
# AI MODEL INITIALIZATION
# =============================================================================
print("=" * 60)
print("🚀 STEALTH BACKEND - Final Working Build")
print("=" * 60)

# Global state (initialized in startup)
whisper_stt = None         # stt.WhisperSTT instance (faster-whisper)
server_conn = None         # server_client.ServerClient — talks to GCP server
main_loop = None           # main asyncio loop ref for thread-safe emit


async def _emit_to_overlay(event: str, payload: dict):
    """Emit a Socket.IO event to the connected Electron overlay."""
    try:
        await sio.emit(event, payload)
    except Exception as e:
        flush_log(f"emit {event} failed: {e}")


async def _on_server_chunk(token: str):
    """Forward a streamed token from the GCP server to the overlay."""
    await _emit_to_overlay('answer_chunk', {'token': token})


async def _on_server_done(full_text: str):
    """Stream finished — emit both new (answer_done) and legacy (new_answer) events."""
    await _emit_to_overlay('answer_done', {'full_text': full_text})
    # Backward-compat: existing overlay JS still listens for new_answer.
    await _emit_to_overlay('new_answer', {'text': full_text})


async def _on_server_error(message: str):
    flush_log(f"❌ Server error: {message}")
    await _emit_to_overlay('error', {'message': message})
    await _emit_to_overlay('new_answer', {'text': f"⚠️ {message}"})


async def connect_server() -> tuple[bool, str]:
    """(Re)connect to the GCP server using the current config and send hello.

    Re-reads the resume from disk on every connect so that a freshly-uploaded
    or externally-edited ~/.stealth/resume.txt always wins — there is no stale
    in-memory copy that can drift behind the file.
    """
    global server_conn, resume_text

    if not config_store.is_configured():
        return (False, "client not configured")

    # Always pick up the latest resume on the way out the door.
    resume_text = config_store.get_resume_text()

    # Tear down any existing connection first.
    if server_conn is not None:
        try:
            await server_conn.close()
        except Exception:
            pass
        server_conn = None

    import server_client
    sc = server_client.ServerClient(
        server_url=server_url,
        license_key=license_key,
        on_chunk=_on_server_chunk,
        on_done=_on_server_done,
        on_error=_on_server_error,
    )
    ok, err = await sc.connect_and_hello(
        provider=provider_name,
        api_key=api_key,
        model=model_name,
        model_dsa=model_dsa_name,
        resume_text=resume_text,
        language=language_pref,
        interview_context=interview_context_pref,
    )
    if not ok:
        await sc.close()
        return (False, err)
    server_conn = sc
    return (True, "")


async def load_models():
    """Heavy model loading and hardware init moved to async task to allow server to start immediately."""
    global whisper_stt, sd
    global np, Image, mss, QUARTZ_AVAILABLE
    
    flush_log("[1/5] Lazy-loading Core Imaging Modules (numpy, PIL, mss)...")
    try:
        import numpy as np_module
        from PIL import Image as Image_module
        import mss as mss_module
        np = np_module
        Image = Image_module
        mss = mss_module
        flush_log("      ✅ Imaging modules imported.")
    except Exception as e:
        flush_log(f"      ❌ Failed to import imaging modules: {e}")

    flush_log("[2/5] Lazy-loading Quartz (macOS Screen Capture)...")
    try:
        from Quartz import (
            CGWindowListCreateImage,
            CGWindowListCopyWindowInfo,
            CGRectNull,
            kCGWindowListOptionIncludingWindow,
            kCGWindowListExcludeDesktopElements,
            kCGWindowListOptionOnScreenOnly,
            kCGWindowImageDefault,
            kCGNullWindowID,
            kCGWindowOwnerName,
            kCGWindowNumber,
            kCGWindowLayer,
            CGImageGetWidth,
            CGImageGetHeight,
            CGImageGetBytesPerRow,
            CGImageGetDataProvider,
            CGDataProviderCopyData,
            CGEventCreateScrollWheelEvent,
            CGEventPost,
            kCGHIDEventTap,
            kCGScrollEventUnitPixel,
            CGEventTapCreate,
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventLeftMouseDown,
            kCGEventScrollWheel,
            kCGEventFlagMaskAlternate,
            kCGEventFlagMaskCommand,
            CFRunLoopGetCurrent,
            CFRunLoopAddSource,
            kCFRunLoopCommonModes,
            CGEventTapEnable,
            CFRunLoopRun,
            CGEventGetFlags,
            CGEventGetType,
            CGEventGetIntegerValueField
        )
        
        # Safe import for kCGScrollWheelEventDeltaY (missing in some versions)
        try:
            from Quartz import kCGScrollWheelEventDeltaY
        except ImportError:
            kCGScrollWheelEventDeltaY = 114
            flush_log("      ⚠️  kCGScrollWheelEventDeltaY using fallback (114).")

        # Make Quartz symbols global for Event Taps
        globals().update(locals())
        QUARTZ_AVAILABLE = True
        flush_log("      ✅ Quartz framework loaded.")
    except Exception as e:
        QUARTZ_AVAILABLE = False
        flush_log(f"      ⚠️  Quartz not available (Falling back to MSS): {e}")

    flush_log("[3/5] Lazy-loading STT (faster-whisper) + audio I/O...")
    try:
        import sounddevice as sd_module
        import stt as stt_module
        sd = sd_module
        whisper_stt = stt_module.WhisperSTT("tiny.en")
        flush_log("      ✅ faster-whisper + sounddevice imported.")
    except Exception as e:
        flush_log(f"      ❌ Failed to import audio/STT modules: {e}")
        return

    flush_log("[4/5] Loading Whisper Model (tiny.en, int8)...")
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, whisper_stt.load)
        flush_log("      ✅ Whisper loaded.")
    except Exception as e:
        flush_log(f"      ❌ Failed to load Whisper: {e}")

    flush_log(f"[5/5] Connecting to Stealth server ({server_url or 'unconfigured'})...")
    if not config_store.is_configured():
        flush_log("      ⚠️  Not configured. Frontend should prompt user.")
    else:
        ok, err = await connect_server()
        if ok:
            flush_log(f"      ✅ Connected to server, hello acked ({provider_name}/{model_name})")
        else:
            flush_log(f"      ❌ Server connect failed: {err}")
    
    # Finally, start audio capture once hardware is initialized
    flush_log("🎤 Starting Hardware Initialization...")
    device_index = await loop.run_in_executor(None, find_blackhole_device)
    
    global hardware_initialized
    hardware_initialized = True
    
    # NEW: Only emit error if we've actually finished checking and it's missing
    if not blackhole_found:
        flush_log("      ⚠️  BlackHole not found after scan. Notifying frontend.")
        await emit_driver_error()
    else:
        flush_log("      ✅ BlackHole verified and active.")
    
    audio_thread = threading.Thread(target=audio_loop, args=(device_index,), daemon=True)
    transcribe_thread = threading.Thread(target=transcription_loop, daemon=True)
    
    audio_thread.start()
    transcribe_thread.start()
    
    # Start Global Gestures once environment is ready
    # NOTE: Disabled — CFRunLoopRun() in a daemon thread segfaults on
    # Python 3.14 + pyobjc Quartz. The overlay's in-window gestures
    # (Option+Click, Cmd+Scroll) still work via the renderer JS.
    # threading.Thread(target=start_global_gesture_tap, daemon=True).start()
    
    print("      ✅ Hardware loops active (gestures handled by Electron).")

# Note: The system prompt now lives on the server (server/prompts.py — slot
# composer, per-provider tuned). The client just streams transcripts and
# screenshots up; the server assembles the prompt per-request.


# =============================================================================
# AUDIO DEVICE DETECTION (BlackHole using sounddevice)
# =============================================================================
def find_blackhole_device():
    """
    Scans all input devices for BlackHole using sounddevice.
    Returns the device index if found, else None.
    """
    global blackhole_found, selected_device_index
    print("\n[3/3] Scanning for BlackHole 2ch...")
    
    try:
        devices = sd.query_devices()
        print(f"      Found {len(devices)} audio device(s):")
        
        for i, device in enumerate(devices):
            name = device['name']
            max_input = device['max_input_channels']
            
            if max_input > 0:
                print(f"        [{i}] {name} (inputs: {max_input})")
                
                if "blackhole" in name.lower():
                    selected_device_index = i
                    blackhole_found = True
                    print(f"      ✅ Selected: {name}")
                    return i
        
        # Not found
        blackhole_found = False
        print("\n" + "=" * 60)
        print("⚠️  BlackHole 2ch NOT FOUND!")
        print("=" * 60)
        print("Install: brew install blackhole-2ch")
        print("=" * 60)
        
        # Fallback to default mic
        default = sd.query_devices(kind='input')
        print(f"      ⚠️  Fallback: {default['name']}")
        return None
        
    except Exception as e:
        print(f"❌ Audio device scan error: {e}")
        blackhole_found = False
        return None


async def emit_driver_error():
    """Emit critical error to frontend about missing BlackHole."""
    await sio.emit('critical_error', {
        'type': 'audio_driver',
        'title': '⚠️ BlackHole Driver Missing!',
        'message': 'Cannot capture system audio. Using microphone instead.',
        'instructions': 'brew install blackhole-2ch'
    })


# =============================================================================
# SMART VISION & CAPTURE
# =============================================================================

# Vision prompts now live on the server (server/prompts.py — VISION_RULES slot).
# The client only sends a short user-supplied prompt ("Analyze this screenshot.")
# alongside the image bytes; the server prepends the full system prompt.
DEFAULT_VISION_USER_PROMPT = "Analyze this screenshot under the system rules."



def is_image_mostly_black(img, threshold: float = 0.95) -> bool:
    """Check if an image is mostly black (indicates permission issue).
    Returns True if more than threshold% of pixels are black/very dark."""
    try:
        # Convert to grayscale for easier analysis
        gray = img.convert('L')
        pixels = list(gray.getdata())
        
        # Count very dark pixels (value < 10 out of 255)
        dark_pixels = sum(1 for p in pixels if p < 10)
        total_pixels = len(pixels)
        
        if total_pixels == 0:
            return True
            
        dark_ratio = dark_pixels / total_pixels
        return dark_ratio > threshold
        
    except Exception as e:
        print(f"      ⚠️ Black check error: {e}")
        return False

def capture_screen(target_app: str = None):
    """Captures the relevant screen/window for analysis.
    Uses Quartz for direct window capture without switching apps if possible."""

    print(f"      📷 Starting Smart Capture for: {target_app}...")
    
    # Debug: Save captured images to desktop
    debug_path = os.path.expanduser("~/Desktop/stealth_capture_debug.png")
    
    # 1. STEALTH WINDOW CAPTURE (Quartz)
    if QUARTZ_AVAILABLE:
        try:
            # Identify frontmost app if not provided
            if not target_app:
                try:
                    from AppKit import NSWorkspace
                    front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
                    if front_app:
                        target_app = front_app.localizedName()
                except:
                    pass

            # Common target apps search (expanded)
            search_apps = [target_app] if target_app else ['Google Chrome', 'Code', 'Arc', 'Safari', 'Terminal', 'Cursor', 'Brave Browser', 'Vivaldi', 'Opera']
            
            options = kCGWindowListExcludeDesktopElements | kCGWindowListOptionOnScreenOnly
            window_list = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
            
            target_window_id = None
            
            # 1a. Try to find the EXACT match for target_app first
            if target_app:
                for window in window_list:
                    owner = window.get(kCGWindowOwnerName, '')
                    if target_app.lower() in owner.lower() and window.get(kCGWindowLayer, 0) == 0:
                        # Skip if it's our own app or system elements
                        if any(ign in owner.lower() for ign in ["stealth", "electron", "antigravity", "system preferences", "system settings"]):
                            continue
                        target_window_id = window.get(kCGWindowNumber)
                        print(f"      🎯 Found Exact Target: {owner} (ID: {target_window_id})")
                        break

            # 1b. Fallback to searching through search_apps
            if not target_window_id:
                for window in window_list:
                    owner = window.get(kCGWindowOwnerName, '')
                    # Skip overlay itself
                    if any(ign in owner.lower() for ign in ["system", "stealth", "electron", "antigravity"]):
                        continue
                    
                    # Layer 0 is the normal application layer
                    if any(app.lower() in owner.lower() for app in search_apps) and window.get(kCGWindowLayer, 0) == 0:
                        target_window_id = window.get(kCGWindowNumber)
                        print(f"      🎯 Found Search-List Target: {owner} (ID: {target_window_id})")
                        break
            
            # 1c. Last resort: top-most non-ignored window (if not yet found)
            if not target_window_id:
                for window in window_list:
                    owner = window.get(kCGWindowOwnerName, '')
                    if any(ign in owner.lower() for ign in ["system", "stealth", "electron", "antigravity"]):
                        continue
                    if window.get(kCGWindowLayer, 0) == 0:
                        target_window_id = window.get(kCGWindowNumber)
                        print(f"      🎯 Found Top-Most Target: {owner} (ID: {target_window_id})")
                        break
            
            if target_window_id:
                # Retry loop for black frames
                for attempt in range(2):
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        tmp_path = tmp.name
                    
                    # Capture specific window via screencapture -l
                    subprocess.run(['screencapture', '-x', f'-l{target_window_id}', tmp_path], capture_output=True, timeout=5)
                    
                    if os.path.exists(tmp_path):
                        img = Image.open(tmp_path)
                        img = img.convert("RGB")
                        os.unlink(tmp_path)
                        
                        if not is_image_mostly_black(img):
                            img.thumbnail(VISION_SIZE, Image.Resampling.LANCZOS)
                            print(f"      ✅ Stealth window capture successful (Attempt {attempt+1}): {img.size}")
                            try:
                                img.save(debug_path)
                            except: pass
                            return img
                    
                    if attempt == 0:
                        time.sleep(0.2) # Small wait before retry
        except Exception as e:
            print(f"      ⚠️ Stealth Quartz capture failed: {e}")

    # 2. FALLBACK: Full Screen (MSS)
    print("      ⚠️ Using Full Screen fallback...")
    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor
            screenshot = sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            
            if is_image_mostly_black(img):
                error_img = Image.new('RGB', (800, 100), color=(40, 40, 50))
                return error_img
            
            img.thumbnail(VISION_SIZE, Image.Resampling.LANCZOS)
            print(f"      ✅ MSS capture successful: {img.size}")
            # DEBUG: Save to desktop
            try:
                img.save(debug_path)
                print(f"      🔍 Debug image saved to {debug_path}")
            except Exception as save_err:
                print(f"      ⚠️ Could not save debug: {save_err}")
            return img
    except Exception as e:
        print(f"      ❌ MSS failed: {e}")
        return Image.new('RGB', (800, 600), color=(30, 30, 40))



async def analyze_screen_with_vision(custom_prompt: str = None, target_app: str = None) -> None:
    """Capture screen and stream a vision answer back to the overlay via the GCP server.

    Fire-and-forget: tokens arrive asynchronously through the server_client
    callbacks and get emitted to the overlay as 'answer_chunk' / 'answer_done'.
    """
    if server_conn is None or not server_conn.connected:
        await _emit_to_overlay('new_answer', {'text': "## 🧠 Not Connected\n\nThe client isn't connected to the Stealth server. Open settings to configure server URL + license + provider key."})
        return

    print("\n📸 Capturing screen for vision analysis...")
    try:
        img = capture_screen(target_app=target_app)

        if is_image_mostly_black(img):
            await _emit_to_overlay('new_answer', {'text': """## ⚠️ Screen Recording Permission Required

**The captured image is completely black.** macOS is blocking screen capture.

1. Open **System Settings** → **Privacy & Security** → **Screen Recording**
2. Enable **Stealth.app**
3. Restart the app
"""})
            return

        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG', optimize=True)
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')

        prompt = custom_prompt or DEFAULT_VISION_USER_PROMPT
        print("   🧠 Streaming vision answer from server…")
        await server_conn.ask_vision(prompt, img_b64, "image/png")
    except Exception as e:
        await _on_server_error(f"Vision error: {e}")


# =============================================================================
# WINDOW WATCHER (Auto-Context Detection)
# =============================================================================
def get_active_window_title() -> str:
    """Get the title of the currently active window on macOS.
    Returns combined 'AppName - WindowTitle' for target detection."""
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
            kCGWindowListExcludeDesktopElements
        )
        from AppKit import NSWorkspace
        
        # Apps to skip (Ignore the overlay itself)
        skip_apps = ['stealth', 'electron', 'window server', 'dock', 'control center', 
                     'systemuiserver', 'spotlight', 'notification center', 'antigravity', 'systemsettings']
        
        # Get frontmost app name
        workspace = NSWorkspace.sharedWorkspace()
        front_app = workspace.frontmostApplication()
        front_app_name = front_app.localizedName() if front_app else ""
        
        # Skip if our app is frontmost
        if front_app_name.lower() in skip_apps:
            return ""
        
        # Get window list
        options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        window_list = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
        
        # Find the largest window from the frontmost app (handles multi-window apps)
        best_title = ""
        best_area = 0
        
        for window in window_list:
            if window.get('kCGWindowLayer', 999) != 0:
                continue
                
            owner = window.get('kCGWindowOwnerName', '')
            name = window.get('kCGWindowName', '')
            bounds = window.get('kCGWindowBounds', {})
            
            # Skip system UI
            if owner.lower() in skip_apps:
                continue
            
            # Must match frontmost app
            if not (front_app_name and front_app_name.lower() in owner.lower()):
                continue
            
            # Calculate area to find main window
            width = int(bounds.get('Width', 0))
            height = int(bounds.get('Height', 0))
            area = width * height
            
            # Prefer windows with actual titles
            if area > best_area or (name and not best_title):
                best_area = area
                if name:
                    best_title = f"{owner} - {name}"
                else:
                    best_title = owner
        
        return best_title
        
    except ImportError as ie:
        print(f"      ⚠️ Quartz/AppKit import error: {ie}")
        return ""
    except Exception as e:
        print(f"      ⚠️ Window detection error: {e}")
        return ""


def is_target_window(title: str) -> bool:
    """Check if window title matches target keywords OR is a browser app."""
    if not title:
        return False
    title_lower = title.lower()
    
    # Check for target keywords in title
    if any(keyword in title_lower for keyword in TARGET_WINDOW_KEYWORDS):
        return True
    
    
    # Check if it's a browser (which might be on a coding site)
    # Browsers don't expose page titles, so we assume any browser is potentially a target
    if any(app in title_lower for app in TARGET_APPS):
        return True
    
    return False


def get_focused_element_text() -> str:
    """
    Experimental: Get text from the focused UI element using macOS Accessibility API.
    Recursively searches for text if the focused element is a container.
    """
    try:
        from ApplicationServices import (
            AXUIElementCreateSystemWide,
            AXUIElementCopyAttributeValue,
            kAXFocusedUIElementAttribute,
            kAXValueAttribute,
            kAXTitleAttribute,
            kAXSelectedTextAttribute,
            kAXChildrenAttribute,
            kAXRoleAttribute,
            kAXDescriptionAttribute
        )
        
        def get_ax_text(element, depth=0, max_depth=5):
            if depth > max_depth:
                return ""
                
            # Try efficient attributes first
            for attr in [kAXValueAttribute, kAXSelectedTextAttribute, kAXDescriptionAttribute, kAXTitleAttribute]:
                error, value = AXUIElementCopyAttributeValue(element, attr, None)
                if error == 0 and value and isinstance(value, str) and value.strip():
                    return value.strip()
            
            # If no text, check children
            error, children = AXUIElementCopyAttributeValue(element, kAXChildrenAttribute, None)
            if error == 0 and children:
                # Limit child search to avoid performance hit
                for child in children[:20]:  
                    text = get_ax_text(child, depth + 1, max_depth)
                    if text:
                        return text
            return ""

        system_wide = AXUIElementCreateSystemWide()
        
        # Get focused element
        error, element = AXUIElementCopyAttributeValue(system_wide, kAXFocusedUIElementAttribute, None)
        if error != 0 or not element:
            print(f"      ⚠️ Accessibility: No focused element (Error code: {error})")
            return ""

        # Recursive search
        text = get_ax_text(element)
        
        if text:
            print(f"      ✅ Accessibility: Found {len(text)} chars")
            return text
        else:
            # Debug: what was the role?
            _, role = AXUIElementCopyAttributeValue(element, kAXRoleAttribute, None)
            print(f"      ⚠️ Accessibility: Focused element (Role: {role}) had no text.")
            return ""
            
    except ImportError:
        print("      ❌ Accessibility: ApplicationServices module not found")
        return ""
    except Exception as e:
        print(f"      ❌ Accessibility Error: {e}")
        return ""


async def window_watcher_loop():
    """
    Background loop that monitors the active window.
    Triggers auto-vision when switching to a target app (LeetCode, etc.)
    Also continuously re-analyzes while staying on target window.
    """
    global last_window_title, last_vision_trigger_time, auto_watch_enabled, stealth_mode_active
    
    print("\n👁️ Starting Window Watcher (God Mode)...")
    print("   🔍 Watching for keywords:", TARGET_WINDOW_KEYWORDS[:3], "...")
    print("   🔍 Watching for apps:", TARGET_APPS[:3], "...")
    print("   ⚡ Continuous mode: Will re-analyze every", VISION_COOLDOWN, "seconds")
    
    currently_on_target = False
    
    while True:
        try:
            if not auto_watch_enabled or stealth_mode_active:
                await asyncio.sleep(2)
                continue
            
            current_title = get_active_window_title()
            current_time = time.time()
            
            # Check if window changed
            if current_title and current_title != last_window_title:
                # Debug: Log detected window
                print(f"   🔍 Active window: \"{current_title[:60]}\"")
                
                is_target = is_target_window(current_title)
                currently_on_target = is_target
                
                # Notify frontend about context change
                await sio.emit('context_changed', {
                    'window_title': current_title,
                    'is_target': is_target
                })
                
                if is_target:
                    print(f"   👁️ Target detected: {current_title[:50]}...")
                    
                    # Debounce: wait for page to load
                    await asyncio.sleep(CONTEXT_DEBOUNCE)
                    
                    # Check cooldown
                    if current_time - last_vision_trigger_time >= VISION_COOLDOWN:
                        # Verify window is still the same
                        if get_active_window_title() == current_title:
                            print("   🚀 Auto-triggering vision analysis...")
                            await sio.emit('auto_vision_triggered', {
                                'window': current_title
                            })
                            
                            await analyze_screen_with_vision(target_app=current_title.split(" - ")[0])
                            
                            last_vision_trigger_time = current_time
                
                last_window_title = current_title
            
            # CONTINUOUS MODE: Re-analyze if still on target window after cooldown (Optional feature)
            # elif currently_on_target and current_title:
            #     if current_time - last_vision_trigger_time >= VISION_COOLDOWN:
            #         answer = await analyze_screen_with_vision(target_app=current_title.split(" - ")[0])
            #         await sio.emit('new_answer', {'text': answer})
            #         last_vision_trigger_time = current_time
            
            await asyncio.sleep(2)  # Check every 2 seconds
            
        except Exception as e:
            print(f"   ⚠️ Window watcher error: {e}")
            await asyncio.sleep(5)


# =============================================================================
# AUDIO CAPTURE LOOP (using sounddevice)
# =============================================================================
def audio_loop(device_index):
    """Continuous audio capture using sounddevice."""
    device_name = "default" if device_index is None else sd.query_devices(device_index)['name']
    print(f"\n🎤 Starting Audio Capture: {device_name}")
    
    try:
        frames_per_chunk = int(SAMPLE_RATE * CHUNK_DURATION)
        print("      ✅ Recorder initialized. Listening...")
        
        while True:
            # Check stealth mode - don't start recording if active
            if stealth_mode_active:
                time.sleep(1)
                continue

            audio_data = sd.rec(
                frames_per_chunk,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                device=device_index
            )
            sd.wait()
            
            audio_flat = audio_data.flatten()
            rms = np.sqrt(np.mean(audio_flat ** 2))
            
            if rms > SILENCE_THRESHOLD:
                audio_queue.put(audio_flat)
                
    except Exception as e:
        print(f"❌ Audio Capture Error: {e}")


@sio.event
async def read_focused_text(sid):
    """Handler for the 'Dev: Read Text' button."""
    print("\n🧐 Accessibility: Reading focused element text...")
    await sio.emit('processing', {'status': 'Reading text via Access API...'})
    
    # Run in executor to avoid blocking async loop
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, get_focused_element_text)
    
    if text:
        print(f"   ✅ Got text: \"{text[:50]}...\"")
        await sio.emit('processing', {'status': f'Found {len(text)} chars'})

        if server_conn is not None and server_conn.connected:
            await server_conn.ask("Solve this problem:\n\n" + text)
        else:
            await sio.emit('new_answer', {'text': "Error: server not connected. Open settings."})
            
    else:
        print("   ❌ No text found or permission denied")
        error_msg = "## Accessibility Text Read Failed\n\nCould not read text.\n\n1. Enable Stealth.app in System Settings > Accessibility\n2. Click on the text area in Chrome\n3. Some apps are not supported"
        await sio.emit('new_answer', {'text': error_msg})


# =============================================================================
# TRANSCRIPTION LOOP
# =============================================================================
def transcription_loop():
    """Consumes audio chunks, transcribes locally with faster-whisper, buffers
    until a 2-second pause, then forwards the utterance to the GCP server."""
    global last_transcript_time
    print("\n📝 Starting Transcription Loop (2s debounce)...")

    while True:
        try:
            audio_data = audio_queue.get()
            if audio_data is None:
                break

            max_val = np.max(np.abs(audio_data))
            audio_normalized = audio_data / max_val if max_val > 0 else audio_data

            if whisper_stt is None:
                time.sleep(1)
                continue

            text = whisper_stt.transcribe(audio_normalized)
            
            # Filter out noise/garbage
            if len(text) > 5 and not text.lower().startswith(("thank you", "thanks for", "bye")):
                print(f"   🎤 Buffered: \"{text}\"")
                
                # Add to buffer instead of immediately sending to LLM
                with transcript_buffer_lock:
                    transcript_buffer.append(text)
                    last_transcript_time = time.time()
                
                # Emit transcription to frontend for live display
                if main_loop:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            sio.emit('transcription', {'text': text}),
                            main_loop
                        )
                    except Exception:
                        pass
                
        except Exception as e:
            print(f"❌ Transcription Error: {e}")
                
# reasoning_chain() removed — the GCP server is now the reasoning engine,
# and it streams a single optimized pass back to us via server_client.
# (Pass-2 self-verification was dropped per the v2 plan; the server-side
# system prompt is strict enough about DSA correctness on the first pass.)


# =============================================================================
# TRANSCRIPT → SERVER LOOP
# =============================================================================
async def llm_loop():
    """Forward complete utterances (transcript buffer) to the GCP server.

    Streaming response chunks land back on the overlay via server_client
    callbacks (_on_server_chunk / _on_server_done) — this loop is fire-and-forget.
    """
    global last_transcript_time
    print("\n🧠 Starting transcript-forward loop (2s pause debounce)...")

    while True:
        try:
            current_time = time.time()
            with transcript_buffer_lock:
                has_text = len(transcript_buffer) > 0
                time_since_last = current_time - last_transcript_time if last_transcript_time > 0 else 0
                if has_text and time_since_last >= DEBOUNCE_SECONDS:
                    text = " ".join(transcript_buffer)
                    transcript_buffer.clear()
                    last_transcript_time = 0
                    print(f"   🗣️  Utterance: \"{text[:80]}…\"" if len(text) > 80 else f"   🗣️  Utterance: \"{text}\"")
                else:
                    text = None

            if not text:
                await asyncio.sleep(0.2)
                continue

            text_lower = text.lower()
            if any(trigger in text_lower for trigger in VISION_TRIGGERS):
                print(f"   📸 Vision trigger in speech: \"{text}\"")
                await sio.emit('processing', {'status': 'Analyzing screen...'})
                try:
                    from AppKit import NSWorkspace
                    front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
                    app_name = front_app.localizedName() if front_app else None
                    if app_name and any(ign in app_name.lower() for ign in ["system", "stealth", "electron", "antigravity"]):
                        app_name = None
                except Exception as e:
                    print(f"   ⚠️ AppKit query failed: {e}")
                    app_name = None
                await analyze_screen_with_vision(target_app=app_name)
            else:
                if server_conn is not None and server_conn.connected:
                    await sio.emit('processing', {'status': 'Thinking…'})
                    await server_conn.ask(text)
                else:
                    await sio.emit('new_answer', {'text': "Error: server not connected. Open settings."})
        except Exception as e:
            print(f"❌ LLM-loop Error: {e}")
            await asyncio.sleep(1)


# =============================================================================
# SOCKET.IO EVENTS
# =============================================================================
@sio.event
async def remote_scroll(sid, data):
    """Simulate a mouse wheel scroll event on macOS."""
    direction = data.get('direction', 'down')
    amount = data.get('amount', 20)  # pixels
    
    try:
        from Quartz import CGEventCreateScrollWheelEvent, CGEventPost, kCGHIDEventTap, kCGScrollEventUnitPixel
        
        # Invert amount for 'up'
        scroll_amount = amount if direction == 'down' else -amount
        
        # Units: 0=pixels (kCGScrollEventUnitPixel), 1=lines (kCGScrollEventUnitLine)
        event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitPixel, 1, scroll_amount)
        CGEventPost(kCGHIDEventTap, event)
    except Exception as e:
        print(f"⚠️ Remote scroll failed: {e}")

@sio.event
async def connect(sid, environ):
    global main_loop
    if not main_loop:
        try:
            main_loop = asyncio.get_running_loop()
        except:
            pass
    print(f"🔌 Client connected: {sid}")
    await sio.emit('connected', {'status': 'Stealth Active', 'auto_watch': auto_watch_enabled}, to=sid)
    
    # If unconfigured (no provider/key), notify the frontend so it can show setup
    if not config_store.is_configured():
        print("🔑 LLM not configured - notifying frontend")
        await sio.emit('api_key_missing', to=sid)
    
    if hardware_initialized and not blackhole_found:
        await emit_driver_error()


@sio.event
async def disconnect(sid):
    print(f"🔌 Client disconnected: {sid}")


@sio.event
async def analyze_image(sid, data):
    """Receive image directly from client (Electron) and analyze it."""
    print(f"📸 Received client-side image from {sid}")
    await sio.emit('processing', {'status': 'Analyzing screen...'}, to=sid)
    
    img_b64 = data.get('image')
    if not img_b64:
        # Fallback to backend capture
        print("      ⚠️ No image data, falling back to backend capture")
        await trigger_vision(sid, data)
        return
        
    try:
        # Remove header if present (data:image/png;base64,...)
        if "base64," in img_b64:
            img_b64 = img_b64.split("base64,")[1]
        
        if server_conn is None or not server_conn.connected:
            await sio.emit('new_answer', {'text': "⚠️ Not connected to server. Open settings."}, to=sid)
            return

        await server_conn.ask_vision(DEFAULT_VISION_USER_PROMPT, img_b64, "image/png")
        print("      📡 Vision request streamed to server.")
            
    except Exception as e:
        print(f"❌ Analysis Error: {e}")
        await sio.emit('new_answer', {'text': f"⚠️ Error: {str(e)}"}, to=sid)


@sio.event
async def trigger_vision(sid, data):
    """Manual vision trigger from frontend."""
    print(f"📸 Vision trigger from {sid}")
    
    # Identify what app we are looking at
    try:
        from AppKit import NSWorkspace
        front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
        app_name = front_app.localizedName() if front_app else None
    except:
        app_name = None

    await sio.emit('processing', {'status': 'Analyzing screen...'}, to=sid)
    
    custom_prompt = data.get('prompt') if isinstance(data, dict) else None
    # analyze_screen_with_vision now streams to overlay via server callbacks.
    await analyze_screen_with_vision(custom_prompt, target_app=app_name)


@sio.event
async def toggle_auto_watch(sid, data):
    """Toggle automatic window watching."""
    global auto_watch_enabled
    if isinstance(data, dict) and 'enabled' in data:
        auto_watch_enabled = data['enabled']
    else:
        auto_watch_enabled = not auto_watch_enabled
    
    status = "enabled" if auto_watch_enabled else "disabled"
    print(f"👁️ Auto-watch {status} by {sid}")
    await sio.emit('auto_watch_status', {'enabled': auto_watch_enabled})


@sio.event
async def set_stealth_mode(sid, data):
    """Toggle Deep Stealth mode (stops hardware capture)."""
    global stealth_mode_active
    if isinstance(data, dict) and 'enabled' in data:
        stealth_mode_active = data['enabled']
    else:
        stealth_mode_active = not stealth_mode_active
    
    status = "ACTIVE (Capture Paused)" if stealth_mode_active else "INACTIVE (Capture Resumed)"
    print(f"👻 Deep Stealth {status} by {sid}")
    await sio.emit('stealth_mode_status', {'enabled': stealth_mode_active})


@sio.event
async def save_setup(sid, data):
    """First-run setup: persist full client config + parse resume + (re)connect to server.

    data = {
        "server_url": str,
        "license": str,
        "provider": "gemini" | "openai" | "anthropic",
        "api_key": str,
        "model": str,                  # optional, falls back to provider default
        "language": str,               # optional, default Python
        "interview_context": str,      # optional
        "resume_path": str | None,     # optional, absolute path to PDF/DOCX/TXT/MD
    }

    Replies via 'setup_saved' with {success, error?, resume_chars?, provider?, model?}.
    """
    global server_url, license_key, api_key, provider_name, model_name, model_dsa_name
    global language_pref, interview_context_pref, resume_text

    try:
        d = data or {}
        provider = d.get("provider", "").strip()
        key = d.get("api_key", "").strip()
        model = d.get("model", "").strip()
        model_dsa = d.get("model_dsa", "").strip()
        srv_url = d.get("server_url", "").strip()
        lic = d.get("license", "").strip()
        lang = d.get("language", "").strip() or config_store.DEFAULT_LANGUAGE
        ictx = d.get("interview_context", "").strip()
        resume_path = d.get("resume_path") or None

        # Settings-edit case: if api_key was left blank by the user, reuse the
        # saved one. The renderer shows "Saved (paste new to replace)" so they
        # know it's still active.
        if not key:
            existing = config_store.load_config().get("api_key", "")
            if existing:
                key = existing

        if not srv_url:
            srv_url = config_store.BAKED_IN_SERVER_URL
        if not lic:
            lic = config_store.BAKED_IN_LICENSE

        if not provider or not key:
            await sio.emit('setup_saved', {'success': False, 'error': 'provider and api_key are required'}, to=sid)
            return

        # Parse + save resume first (if given)
        resume_chars = 0
        if resume_path:
            try:
                import resume_parser
                parsed = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: resume_parser.parse(resume_path)
                )
                config_store.save_resume_text(parsed)
                resume_text = parsed
                resume_chars = len(parsed)
                print(f"📄 Resume parsed: {resume_chars} chars from {resume_path}")
            except Exception as e:
                await sio.emit('setup_saved', {'success': False, 'error': f'Resume parse failed: {e}'}, to=sid)
                return

        # Persist config
        cfg = config_store.save_config(
            server_url=srv_url, license=lic, provider=provider, api_key=key,
            model=model, model_dsa=model_dsa,
            language=lang, interview_context=ictx,
        )
        server_url = cfg["server_url"]
        license_key = cfg["license"]
        api_key = cfg["api_key"]
        provider_name = cfg["provider"]
        model_name = cfg["model"]
        model_dsa_name = cfg["model_dsa"]
        language_pref = cfg["language"]
        interview_context_pref = cfg["interview_context"]

        # (Re)connect to the GCP server. Surfaces bad license / bad key here.
        ok, err = await connect_server()
        if not ok:
            await sio.emit('setup_saved', {'success': False, 'error': f'Server connect failed: {err}'}, to=sid)
            return

        print(f"✅ Setup saved: server={server_url}, {provider_name}/{model_name} (DSA: {model_dsa_name}) (resume: {resume_chars} chars)")
        await sio.emit('setup_saved', {
            'success': True,
            'provider': provider_name,
            'model': model_name,
            'model_dsa': model_dsa_name,
            'resume_chars': resume_chars,
        }, to=sid)
    except Exception as e:
        print(f"❌ save_setup error: {e}")
        await sio.emit('setup_saved', {'success': False, 'error': str(e)}, to=sid)


@sio.event
async def test_connection(sid, data):
    """Probe by relaying through the GCP server.

    Two cases:
      (a) We're already connected with the saved config → server.test() runs
          on the existing session.
      (b) The user is testing un-saved values → spin up a temporary connection
          to the server with the provided values and run test there.
    """
    try:
        d = data or {}
        srv_url = d.get("server_url", "").strip() or server_url
        lic = d.get("license", "").strip() or license_key
        provider = d.get("provider", "").strip()
        key = d.get("api_key", "").strip()
        model = d.get("model", "").strip() or config_store.DEFAULT_MODELS.get(provider, "")

        if not srv_url or not lic or not provider or not key or not model:
            await sio.emit('test_connection_result', {'success': False, 'error': 'server_url, license, provider, api_key, model required'}, to=sid)
            return

        # Spin up an isolated probe connection — does not disturb the live session.
        import server_client

        async def _noop_chunk(_t): pass
        async def _noop_done(_t): pass
        async def _noop_err(_m): pass

        probe = server_client.ServerClient(
            server_url=srv_url, license_key=lic,
            on_chunk=_noop_chunk, on_done=_noop_done, on_error=_noop_err,
        )
        ok, err = await probe.connect_and_hello(
            provider=provider, api_key=key, model=model,
            resume_text="", language=language_pref or "Python", interview_context="",
        )
        if not ok:
            await probe.close()
            await sio.emit('test_connection_result', {'success': False, 'error': err}, to=sid)
            return
        ok, err = await probe.test()
        await probe.close()
        await sio.emit('test_connection_result', {'success': bool(ok), 'error': err}, to=sid)
    except Exception as e:
        await sio.emit('test_connection_result', {'success': False, 'error': str(e)}, to=sid)


@sio.event
async def reload_api_key(sid, data):
    """Re-read ~/.stealth/config.json and reconnect to the GCP server.

    Triggered after the frontend saves new config.
    """
    global server_url, license_key, api_key, provider_name, model_name, model_dsa_name
    global language_pref, interview_context_pref, resume_text

    cfg = config_store.load_config()
    server_url = cfg.get("server_url", "")
    license_key = cfg.get("license", "")
    api_key = cfg.get("api_key", "")
    provider_name = cfg.get("provider", "")
    model_name = cfg.get("model", "")
    model_dsa_name = cfg.get(
        "model_dsa",
        config_store.DEFAULT_DSA_MODELS.get(provider_name, "") if provider_name else "",
    )
    language_pref = cfg.get("language", config_store.DEFAULT_LANGUAGE)
    interview_context_pref = cfg.get("interview_context", "")
    resume_text = config_store.get_resume_text()

    if not config_store.is_configured():
        await sio.emit('api_key_saved', {'success': False, 'error': 'Config incomplete'}, to=sid)
        return

    ok, err = await connect_server()
    if ok:
        print(f"🔑 Reconnected to server ({provider_name}/{model_name} + DSA {model_dsa_name})")
        await sio.emit('api_key_saved', {'success': True, 'provider': provider_name, 'model': model_name, 'model_dsa': model_dsa_name}, to=sid)
    else:
        await sio.emit('api_key_saved', {'success': False, 'error': err}, to=sid)


# =============================================================================
# GLOBAL SYSTEM GESTURES (Quartz Event Tap)
# =============================================================================
def global_gesture_callback(proxy, event_type, event, refcon):
    """Callback for global system-wide gestures."""
    if not QUARTZ_AVAILABLE:
        return event

    try:
        flags = CGEventGetFlags(event)
        
        # 1. Option + Left Click = Rapid Vision Capture
        if event_type == kCGEventLeftMouseDown:
            if flags & kCGEventFlagMaskAlternate:
                print("\n🌟 [Global Gesture] Option + Click detected! Triggering Vision...")
                # We need to run the async trigger in the main loop
                if main_loop:
                    asyncio.run_coroutine_threadsafe(
                        analyze_screen_with_vision(),
                        main_loop
                    )
                # Consume the event so it doesn't click through to the app? 
                # Actually, better to let it pass through so user can click and analyze simultaneously.
                return event

        # 2. Cmd + Scroll = Remote Scroll
        elif event_type == kCGEventScrollWheel:
            if flags & kCGEventFlagMaskCommand:
                delta_y = CGEventGetIntegerValueField(event, kCGScrollWheelEventDeltaY)
                direction = "down" if delta_y < 0 else "up"
                # Multiply delta for sensitivity
                amount = abs(delta_y) * 4 
                
                # print(f"🖱️ [Global Gesture] Cmd + Scroll: {direction} ({amount})")
                
                if main_loop:
                    # We reuse the remote_scroll logic
                    asyncio.run_coroutine_threadsafe(
                        sio.emit('remote_scroll', {'direction': direction, 'amount': amount}), # This is for frontend feedback if needed
                        main_loop
                    )
                    # Also perform it directly
                    asyncio.run_coroutine_threadsafe(
                        perform_remote_scroll(direction, amount),
                        main_loop
                    )
                
                # Return None to stop the scroll from affecting the frontmost app if we want purely remote
                # However, user might want to scroll BOTH. For stealth, let's keep it remote-only?
                # No, standard behavior should be "scroll what I'm looking at".
                # But user's request was "remote scroll the target app".
                return None 

    except Exception as e:
        print(f"⚠️ Global Gesture Error: {e}")
    
    return event

def start_global_gesture_tap():
    """Starts the Quartz Event Tap for system-wide gestures."""
    if not QUARTZ_AVAILABLE:
        print("⚠️ Quartz not available, Global Gestures disabled.")
        return

    print("🎹 Starting Global Stealth Gestures (Option+Click, Cmd+Scroll)...")
    
    try:
        # Listen for Mouse Down and Scroll
        mask = (1 << kCGEventLeftMouseDown) | (1 << kCGEventScrollWheel)
        
        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            0, # active tap
            mask,
            global_gesture_callback,
            None
        )
        
        if not tap:
            print("❌ Failed to create Event Tap. (Needs Accessibility Permissions)")
            # Emit warning to frontend
            if main_loop:
                asyncio.run_coroutine_threadsafe(
                    sio.emit('critical_error', {
                        'type': 'accessibility',
                        'title': '⚠️ Accessibility Permission Required',
                        'message': 'Stealth Gestures (Option+Click) require Accessibility permissions to work system-wide.',
                        'instructions': 'System Settings > Privacy > Accessibility > Enable Stealth (or Terminal/VSCode)'
                    }),
                    main_loop
                )
            return

        source = CFRunLoopAddSource(
            CFRunLoopGetCurrent(),
            CGEventTapEnable(tap, True),
            kCFRunLoopCommonModes
        )
        
        # Run the loop (This blocks, so run in thread)
        CFRunLoopRun()
    except Exception as e:
        print(f"⚠️ Global Gesture Tap crashed: {e}")
        print("   Gestures disabled, but core functionality continues.")

async def perform_remote_scroll(direction: str, amount: int):
    """Directly executes the scroll event in the background."""
    try:
        from Quartz import (
            CGEventCreateScrollWheelEvent, CGEventPost, 
            kCGHIDEventTap, kCGScrollEventUnitPixel
        )
        # Inverted for natural scroll feeling? usually positive is up
        delta = amount if direction == "up" else -amount
        
        scroll_event = CGEventCreateScrollWheelEvent(None, kCGScrollEventUnitPixel, 1, delta)
        CGEventPost(kCGHIDEventTap, scroll_event)
    except Exception as e:
        print(f"⚠️ Direct scroll failed: {e}")

# =============================================================================
# MAIN ENTRY POINT
# =============================================================================
async def on_startup(app_instance):
    """Startup tasks when server starts."""
    global main_loop
    try:
        main_loop = asyncio.get_running_loop()
    except:
        main_loop = None
        
    flush_log("Startup tasks initiated (Background loops starting).")
    asyncio.create_task(load_models())  # Load heavy models in background
    asyncio.create_task(llm_loop())
    asyncio.create_task(window_watcher_loop())  # God Mode: Auto-context detection
    
    # Start Global Gestures in a background thread
    # threading.Thread(target=start_global_gesture_tap, daemon=True).start() # MOVED to after models load for safety
    
    # Ensure server is truly listening before signaling Electron
    await asyncio.sleep(0.5)
    flush_log("PY_BACKEND_READY")


async def on_shutdown(app_instance):
    """Cleanup tasks when server shuts down."""
    flush_log("🛑 Shutting down gracefully...")
    # Signal queues to stop
    audio_queue.put(None)
    text_queue.put(None)
    flush_log("   ✅ Cleanup complete.")


def main():
    flush_log("Main function started.")
    # Step 0: Ensure port is available
    if not ensure_port_available(SERVER_PORT):
        flush_log("❌ Port not available, exiting.")
        sys.exit(1)
    
    flush_log("Port verified.")
    
    # Note: Audio and transcription threads are now started in on_startup -> load_models
    
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    flush_log("=" * 60)
    flush_log(f"🚀 Socket.IO Server: http://127.0.0.1:{SERVER_PORT}")
    flush_log("   Starting immediately while models load in background...")
    flush_log("=" * 60)
    
    # Graceful shutdown handler
    def signal_handler(sig, frame):
        print("\n🛑 Received shutdown signal...")
        raise KeyboardInterrupt
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Run with reuse_address=True to prevent "Address already in use" on quick restarts
        web.run_app(
            app,
            host='127.0.0.1',
            port=SERVER_PORT,
            print=None,
            reuse_address=True,
            reuse_port=True,  # macOS specific
            handle_signals=True
        )
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user.")
    except Exception as e:
        print(f"   ❌ Server error: {e}")
    finally:
        print("🧹 Final cleanup complete.")


if __name__ == '__main__':
    main()
