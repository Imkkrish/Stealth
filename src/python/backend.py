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

flush_log("Attempting to import dotenv...")
from dotenv import load_dotenv
flush_log("dotenv imported.")

# Determine if running as PyInstaller bundle or as script
if getattr(sys, 'frozen', False):
    # Running as compiled binary (PyInstaller)
    # The executable is in Resources/bin/stealth-backend/stealth-backend
    # .env is in Resources/
    exe_dir = os.path.dirname(sys.executable)
    # In onedir, the exe is in bin/stealth-backend/stealth-backend
    # We need to go up from bin/stealth-backend/ to Resources/
    project_root = os.path.dirname(os.path.dirname(exe_dir))
    env_path = os.path.join(project_root, '.env')
    print(f"[PROD] Running as frozen binary")
    print(f"[PROD] Executable: {sys.executable}")
    print(f"[PROD] Resources dir: {project_root}")
else:
    # Running as script (development)
    # Script is in src/python/, so root is ../../
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(current_dir, '../../'))
    env_path = os.path.join(project_root, '.env')
    print(f"[DEV] Running as script")

flush_log(f"Loading environment from: {env_path}")
load_dotenv(env_path)
flush_log("Environment loaded.")

# Verify API Key
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    flush_log("⚠️  CRITICAL WARNING: GEMINI_API_KEY not found in environment!")
else:
    flush_log("✅ GEMINI_API_KEY found.")

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
        result = subprocess.run(
            f"lsof -ti:{port} | xargs kill -9",
            shell=True,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"      ✅ Killed zombie process on port {port}")
            time.sleep(0.5)  # Give OS time to release port
            return True
        return False
    except Exception as e:
        print(f"      ⚠️  Could not kill process: {e}")
        return False

def ensure_port_available(port: int) -> bool:
    """Ensure port is available, attempting cleanup if needed."""
    if not is_port_in_use(port):
        return True
    
    print(f"\n⚠️  Port {port} is already in use!")
    print(f"   Attempting to kill zombie process...")
    
    if kill_process_on_port(port):
        if not is_port_in_use(port):
            return True
    
    print(f"\n" + "=" * 60)
    print(f"❌ CRITICAL: Port {port} is still occupied!")
    print(f"=" * 60)
    print(f"Run this command manually:")
    print(f"   lsof -ti:{port} | xargs kill -9")
    print(f"=" * 60 + "\n")
    return False

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

# Gemini AI Configuration
generation_config = {
    "temperature": 0.3,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 4096,
}

def get_best_model():
    """Returns the fastest/most suitable model for current tasks by scanning availability."""
    try:
        flush_log("      🔍 Scanning for available Gemini models...")
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        # Priority list (Flash is preferred for speed)
        priority = ["models/gemini-1.5-flash", "models/gemini-2.0-flash-exp", "models/gemini-1.5-pro", "models/gemini-pro"]
        
        for model in priority:
            if model in available_models:
                flush_log(f"      ✨ Selected preferred model: {model}")
                return model
        
        if available_models:
            flush_log(f"      💡 Using first available model: {available_models[0]}")
            return available_models[0]
            
        flush_log("      ⚠️  No suitable models found from list_models, using default.")
        return "models/gemini-1.5-flash"
    except Exception as e:
        flush_log(f"      ⚠️  Model scan failed: {e}. Using default.")
        return "models/gemini-1.5-flash"

# Global model variables (initialized in startup)
whisper_model = None
gemini_model = None
gemini_text_model = None
gemini_vision_model = None
chat_session = None
main_loop = None  # Global reference to main event loop for thread-safe emitting

async def load_models():
    """Heavy model loading and hardware init moved to async task to allow server to start immediately."""
    global whisper_model, gemini_model, gemini_text_model, gemini_vision_model, chat_session, genai, sd
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

    flush_log("[3/5] Lazy-loading AI Engine (Whisper, Gemini, Torch)...")
    try:
        import whisper as whisper_module
        import google.generativeai as genai_module
        import sounddevice as sd_module
        # torch is often imported by whisper, but let's be explicit
        import torch as torch_module
        
        genai = genai_module
        sd = sd_module
        whisper = whisper_module
        global torch
        torch = torch_module
        flush_log("      ✅ AI Engines imported.")
    except Exception as e:
        flush_log(f"      ❌ Failed to import AI modules: {e}")
        return

    flush_log("[4/5] Loading Whisper Model (tiny.en)...")
    try:
        loop = asyncio.get_running_loop()
        whisper_model = await loop.run_in_executor(None, lambda: whisper.load_model("tiny.en"))
        flush_log("      ✅ Whisper loaded.")
    except Exception as e:
        flush_log(f"      ❌ Failed to load Whisper: {e}")

    flush_log("[5/5] Configuring Gemini...")
    if not api_key:
        flush_log("      ⚠️  WARNING: GEMINI_API_KEY not found!")
    else:
        try:
            genai.configure(api_key=api_key)
            selected_model_name = await loop.run_in_executor(None, get_best_model)
            
            # Use modern system_instruction for better chat reliability
            gemini_model = genai.GenerativeModel(
                model_name=selected_model_name,
                generation_config=generation_config,
                system_instruction=SYSTEM_PROMPT
            )
            gemini_text_model = gemini_model
            gemini_vision_model = gemini_model
            chat_session = gemini_text_model.start_chat(history=[])
            flush_log(f"      ✅ Gemini ready with system instructions: {selected_model_name}")
        except Exception as e:
            flush_log(f"      ❌ Failed to configure Gemini: {e}")
    
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
    threading.Thread(target=start_global_gesture_tap, daemon=True).start()
    
    print("      ✅ Hardware loops and Stealth Gestures active.")

# System prompt - Optimized for Universal Interview Excellence (Copilot Architecture)
SYSTEM_PROMPT = """You are a Universal Technical Expert and Advanced AI Assistant. Your knowledge spans all technical and professional domains: Computer Science, Mathematics, Physics, Logic, Quantitative Reasoning, and more. Your goal is to provide the absolute most accurate, professional-grade assistance for ANY task, assessment, or technical interview.

## 🎯 OPERATION MODES (Mandatory)

1. **Coding Problems (All Languages/DSA)**:
   - Use ONLY C++17 unless specify by context.
   - NO SIGNATURE CHANGES. Adhere exactly to provided function names.
   - STRICT POINTER DISCIPLINE: Use correct object models (ListNode*, etc.).
   - PASTE-READY: Include all necessary `<headers>`, use `std::` prefixing, and wrap in class `Solution` if required.
   - OUTPUT: Only the code block. Minimal explanation unless requested.

2. **Advanced Mathematics & Statistics**:
   - Provide precise solutions for Calculus, Linear Algebra, Discrete Math, and Probability.
   - For complex derivations, show key logical steps.
   - Ensure numerical accuracy is perfect.

3. **Logic, Patterns & Rule-Switching (e.g., switchChallenge)**:
   - **CRITICAL**: Detect "Rule Switches" mid-sequence. Do not apply old rules to new steps if a switch signal is present.
   - **Symbol/Shape Rules**: Map symbols (▲, ●, ■) to mathematical or spatial actions precisely.
   - **Number Patterns**: Identify operations (+, -, *, /) and notice when the operation changes mid-sequence.
   - **Arrow & Directional Logic**: Handle rotations (CW/ACW), flips, and axis inversions.
   - **Letter Transformations**: Handle alphabetical shifts and positional reordering.
   - **Conditional Logic**: Apply IF-THEN rules (e.g., "IF Black THEN +1, IF White THEN -2").
   - **Output**: Break down the transformation step-by-step to avoid errors.

4. **System Design & Architecture**:
   - Provide production-grade architecture with clear components (Load Balancer, DB, Cache, CDN).
   - Mention scalability, CAP theorem trade-offs, and specific tools (Redis, Kafka, etc.).

5. **SQL, Data Engineering & Analytics**:
   - Provide optimized SQL queries or Data Processing scripts.
   - Use clear formatting and optimized joins.

6. **MCQs & Theoretical Concepts**:
   - Return format: [Question Number]. [Correct Option] (e.g., 1. B).
   - Provide a 1-sentence expert justification for why it's correct.

Be expert, concise, and proactive. You are an all-capable partner. Provide the most helpful assistance possible regardless of the subject matter.
"""


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

# Universal Expert Vision Prompt - Optimized for All Technical Screenshots
GOD_MODE_VISION_PROMPT = """You are an All-Seeing Technical Expert. Analyze this screenshot and provide the most accurate support for ANY problem visible.

- **Coding**: Extract exact signatures, requirements, and provide a production-grade, optimized solution.
- **Mathematics**: Solve equations, identify geometry patterns, and perform calculus or algebraic derivations precisely.
- **Logic & Rule-Switching (switchChallenge)**: 
  1. Identify the CURRENT Legend/Rule Table.
  2. Detect any "Switch Signals" or "Rule Changes" mid-sequence. 
  3. Reset logic when a switch is detected—do NOT use old rules. 
  4. Handle Symbol-Action, Number-Step, Arrow-Rotation, Letter-Shift, and IF-THEN conditions.
  5. Provide the FINAL logical state after all transformations.

- **MCQs/Conceptual/Theory**: Identify the correct option and provide a 1-sentence expert justification.
- **Diagnostics/Infrastructure**: Analyze logs, system diagrams, or emails and provide a technical summary.

Format your output with clear headers and Markdown blocks. If multiple problems are visible, address them all.
"""



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



async def analyze_screen_with_vision(custom_prompt: str = None, target_app: str = None) -> str:
    """Captures screen and sends to Gemini Vision model (multimodal)."""
    
    # Check if models are loaded yet (since they are lazy-loaded)
    if not gemini_vision_model:
        flush_log("      ⚠️  Vision requested but models are still loading...")
        return "## 🧠 AI Models Still Loading\n\nI'm still initializing the AI engines in the background. Please wait a few seconds and try again. You can see the progress in the debug console (`⌘⇧T`)."

    print("\n📸 Capturing screen for vision analysis...")
    
    try:
        img = capture_screen(target_app=target_app)
        
        # Check if we got a black screen (permission issue)
        if is_image_mostly_black(img):
            permission_error = """## ⚠️ Screen Recording Permission Required

**The captured image is completely black.** This means macOS is blocking screen capture.

### How to Fix:

1. Open **System Settings** (or System Preferences)
2. Go to **Privacy & Security** -> **Screen Recording**
3. Find **Stealth.app** and enable it
4. **Restart the app** for changes to take effect

> The orange dot you see is the microphone indicator, which is working correctly.
"""
            return permission_error
        
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG', optimize=True)
        img_bytes = img_buffer.getvalue()
        img_b64 = base64.b64encode(img_bytes).decode('utf-8')
        
        image_part = {
            "mime_type": "image/png",
            "data": img_b64
        }
        
        # Use God Mode prompt by default
        prompt = custom_prompt or GOD_MODE_VISION_PROMPT

        # Use the advanced reasoning chain for vision
        print("   🧠 Using multi-pass reasoning for vision analysis...")
        return await reasoning_chain(prompt, is_vision=True, image_part=image_part)
        
    except Exception as e:
        error_msg = f"Vision error: {str(e)}"
        print(f"      ❌ {error_msg}")
        return error_msg


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
                            
                            answer = await analyze_screen_with_vision(target_app=current_title.split(" - ")[0])
                            await sio.emit('new_answer', {'text': answer})
                            
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
        
        # Send to Gemini
        if api_key:
            try:
                full_query = SYSTEM_PROMPT + "Solve this problem:\n\n" + text
                response = await loop.run_in_executor(
                    None,
                    lambda: chat_session.send_message(full_query)
                )
                answer = response.text
                await sio.emit('new_answer', {'text': answer})
            except Exception as e:
                await sio.emit('new_answer', {'text': f"AI Error: {str(e)}"})
        else:
            await sio.emit('new_answer', {'text': "Error: API Key missing."})
            
    else:
        print("   ❌ No text found or permission denied")
        error_msg = "## Accessibility Text Read Failed\n\nCould not read text.\n\n1. Enable Stealth.app in System Settings > Accessibility\n2. Click on the text area in Chrome\n3. Some apps are not supported"
        await sio.emit('new_answer', {'text': error_msg})


# =============================================================================
# TRANSCRIPTION LOOP
# =============================================================================
def transcription_loop():
    """Consumes audio chunks and transcribes using openai-whisper.
    Buffers transcriptions and waits for 2-second pause before sending to LLM."""
    global last_transcript_time
    print("\n📝 Starting Transcription Loop (2s debounce)...")
    
    while True:
        try:
            audio_data = audio_queue.get()
            if audio_data is None:
                break
            
            max_val = np.max(np.abs(audio_data))
            if max_val > 0:
                audio_normalized = audio_data / max_val
            else:
                audio_normalized = audio_data
            
            if not whisper_model:
                time.sleep(1)
                continue

            result = whisper_model.transcribe(
                audio_normalized,
                language="en",
                fp16=False
            )
            
            text = result["text"].strip()
            
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
                
async def reasoning_chain(text: str, is_vision: bool = False, image_part: dict = None) -> str:
    """Implement multi-pass reasoning: Generate -> Review -> Refine."""
    if not chat_session:
        return "Error: AI Session not initialized."
    
    loop = asyncio.get_running_loop()
    
    try:
        # PASS 1: Initial Reasoning & Generation
        print(f"   🧠 [Pass 1/2] Generating initial solution...")
        if is_vision:
            # For vision, we combine the GOD_MODE_VISION_PROMPT logic with the query
            prompt = GOD_MODE_VISION_PROMPT + "\n\nUser Query: " + text
            response = await loop.run_in_executor(
                None,
                lambda: gemini_vision_model.generate_content([prompt, image_part])
            )
        else:
            response = await loop.run_in_executor(
                None,
                lambda: chat_session.send_message(text)
            )
        
        initial_answer = response.text if response else "Error: No response."
        
        # PASS 2: Self-Verification (Layer 2)
        print(f"   🔍 [Pass 2/2] Performing Self-Verification audit...")
        verification_prompt = f"""
Audit the following response for a technical assessment.

TYPES OF TASKS:
1. **Coding Problem**: 
   - Ensure ALL headers (e.g. #include <vector>) are present.
   - Use pointers correctly (e.g. `ListNode* next`).
   - If it is code, return ONLY the code block. NO INTRO OR OUTRO.
2. **MCQs / Conceptual**:
   - Verify the chosen answers are 100% correct.
   - For MCQs, return the Question Number and the Correct Option (e.g. 1. B).
   - Keep explanations extremely brief.

Initial Response:
---
{initial_answer}
---

INSTRUCTION: provide the FINAL, HIGHEST-QUALITY version of the answer. If the initial response was perfect, repeat it. If it was missing headers or had incorrect MCQ options, fix them now. Provide the result directly.
"""
        # We use a separate chat session or a direct prompt to avoid context pollution
        # For simplicity and speed, we use the same session but mark it as a review
        verify_response = await loop.run_in_executor(
            None,
            lambda: chat_session.send_message(verification_prompt)
        )
        
        final_answer = verify_response.text if verify_response else initial_answer
        return final_answer

    except Exception as e:
        print(f"      ❌ Reasoning Chain Error: {e}")
        return f"⚠️ Reasoning Error: {str(e)}"

# =============================================================================
# TRANSCRIPTION LOOP
# =============================================================================
async def llm_loop():
    """Processes transcribed text through Gemini.
    Waits for 2-second pause in speech before generating response."""
    global last_transcript_time
    print("\n🧠 Starting LLM Loop (waits for 2s pause)...")
    
    while True:
        try:
            # Check if we have buffered text and enough time has passed
            current_time = time.time()
            
            with transcript_buffer_lock:
                has_text = len(transcript_buffer) > 0
                time_since_last = current_time - last_transcript_time if last_transcript_time > 0 else 0
                
                # Wait for 2 seconds of silence after last transcription
                if has_text and time_since_last >= DEBOUNCE_SECONDS:
                    # Combine all buffered text into one query
                    text = " ".join(transcript_buffer)
                    transcript_buffer.clear()
                    last_transcript_time = 0
                    print(f"   🗣️  Complete utterance: \"{text[:80]}...\"" if len(text) > 80 else f"   🗣️  Complete utterance: \"{text}\"")
                else:
                    text = None
            
            if not text:
                await asyncio.sleep(0.2)  # Check every 200ms
                continue
            
            text_lower = text.lower()
            
            if any(trigger in text_lower for trigger in VISION_TRIGGERS):
                print(f"   📸 Vision trigger detected in speech: \"{text}\"")
                await sio.emit('processing', {'status': 'Analyzing screen...'})
                # Identify current app for vision precision
                try:
                    from AppKit import NSWorkspace
                    front_app = NSWorkspace.sharedWorkspace().frontmostApplication()
                    app_name = front_app.localizedName() if front_app else None
                    
                    # IGNORE the overlay app itself (Common names: SystemSettings, Electron, Stealth, Antigravity)
                    if app_name and any(ign in app_name.lower() for ign in ["system", "stealth", "electron", "antigravity"]):
                        print(f"      🛡️  Ignoring overlay app targeting: {app_name}")
                        app_name = None # Fallback to search list
                except:
                    app_name = None
                
                # Directly call analyze_screen_with_vision which now uses reasoning_chain
                answer = await analyze_screen_with_vision(target_app=app_name)
            else:
                print(f"   🧠 Sending query to reasoning engine: \"{text[:100]}...\"")
                await sio.emit('processing', {'status': 'Thinking (Multi-pass)...'})
                
                if api_key and chat_session:
                    answer = await reasoning_chain(text)
                else:
                    answer = "Error: GEMINI_API_KEY not set."

            await sio.emit('new_answer', {'text': answer})
            print(f"   💡 Answer: {answer[:80]}...")
                
        except Exception as e:
            print(f"❌ LLM Error: {e}")
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
    
    # Check if API key is missing and notify frontend
    if not api_key:
        print("🔑 API key not configured - notifying frontend")
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
        
        # DEBUG: Save received image to Desktop for inspection
        try:
            debug_path = os.path.expanduser("~/Desktop/stealth_debug_capture.png")
            import base64 as b64_module
            with open(debug_path, "wb") as f:
                f.write(b64_module.b64decode(img_b64))
            print(f"      🔍 DEBUG: Saved capture to {debug_path}")
        except Exception as debug_err:
            print(f"      ⚠️ Debug save failed: {debug_err}")
            
        image_part = {
            "mime_type": "image/png",
            "data": img_b64
        }
        
        if not gemini_vision_model:
            await sio.emit('new_answer', {'text': "⚠️ AI Models are still loading. Please wait a few seconds..."}, to=sid)
            return

        prompt = GOD_MODE_VISION_PROMPT

        loop = asyncio.get_running_loop()
        
        response = await loop.run_in_executor(
            None,
            lambda: gemini_vision_model.generate_content([prompt, image_part])
        )
        
        if response and response.text:
            print("      ✅ Analysis complete.")
            await sio.emit('new_answer', {'text': response.text}, to=sid)
        else:
            await sio.emit('new_answer', {'text': "⚠️ Vision Error: No response."}, to=sid)
            
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
    answer = await analyze_screen_with_vision(custom_prompt, target_app=app_name)
    await sio.emit('new_answer', {'text': answer}, to=sid)


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
async def reload_api_key(sid, data):
    """Reload API key from environment after user saves it."""
    global api_key
    
    # Reload .env file
    load_dotenv(env_path, override=True)
    api_key = os.getenv("GEMINI_API_KEY")
    
    if api_key:
        print(f"🔑 API key reloaded successfully")
        # Reconfigure Gemini with the new key
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            await sio.emit('api_key_saved', {'success': True}, to=sid)
        except Exception as e:
            print(f"❌ Error configuring Gemini: {e}")
            await sio.emit('api_key_saved', {'success': False, 'error': str(e)}, to=sid)
    else:
        print(f"❌ API key still not found after reload")
        await sio.emit('api_key_saved', {'success': False, 'error': 'Key not found'}, to=sid)


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
