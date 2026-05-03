#!/bin/bash
# =============================================================================
# Stealth - Python Backend Compiler
# Compiles backend.py into a standalone macOS binary using PyInstaller
# =============================================================================

set -e  # Exit on any error

echo "═══════════════════════════════════════════════════════════════"
echo "🔧 STEALTH - Python Backend Build Script"
echo "═══════════════════════════════════════════════════════════════"

# Get script directory (project root)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo ""
echo "[1/5] Checking Python virtual environment..."

VENV_PATH="$SCRIPT_DIR/venv"
PYTHON_PATH="$VENV_PATH/bin/python3"
PIP_PATH="$VENV_PATH/bin/pip"

if [ ! -f "$PYTHON_PATH" ]; then
    echo -e "${RED}❌ Virtual environment not found at: $VENV_PATH${NC}"
    echo "   Please run ./start.sh first to create the venv."
    exit 1
fi

echo -e "${GREEN}✅ Found venv at: $VENV_PATH${NC}"

echo ""
echo "[2/5] Activating virtual environment..."
source "$VENV_PATH/bin/activate"
echo -e "${GREEN}✅ Virtual environment activated${NC}"

echo ""
echo "[3/5] Installing/Updating PyInstaller..."
"$PIP_PATH" install --upgrade pyinstaller
echo -e "${GREEN}✅ PyInstaller ready${NC}"

echo ""
echo "[4/5] Cleaning previous builds..."
rm -rf "$SCRIPT_DIR/bin/stealth-backend"
rm -rf "$SCRIPT_DIR/build"
rm -rf "$SCRIPT_DIR/stealth-backend.spec"
mkdir -p "$SCRIPT_DIR/bin"
echo -e "${GREEN}✅ Clean complete${NC}"

echo ""
echo "[5/5] Building standalone binary..."
echo "      This may take a few minutes..."

# PyInstaller command with optimizations for macOS
"$PYTHON_PATH" -m PyInstaller \
    --clean \
    --noconfirm \
    --distpath "$SCRIPT_DIR/bin" \
    --workpath "$SCRIPT_DIR/build/pyinstaller" \
    --specpath "$SCRIPT_DIR/build" \
    --name stealth-backend \
    --onedir \
    --console \
    --strip \
    --noupx \
    --collect-all whisper \
    --collect-all sounddevice \
    --hidden-import=tiktoken_ext.openai_public \
    --hidden-import=tiktoken_ext \
    --hidden-import=engineio.async_drivers.aiohttp \
    --hidden-import=socketio \
    --hidden-import=aiohttp \
    --hidden-import=PIL \
    --hidden-import=mss \
    --hidden-import=mss.darwin \
    --hidden-import=google.generativeai \
    --hidden-import=certifi \
    "$SCRIPT_DIR/src/python/backend.py"

# Verify the build
if [ -d "$SCRIPT_DIR/bin/stealth-backend" ] && [ -f "$SCRIPT_DIR/bin/stealth-backend/stealth-backend" ]; then
    echo ""
    echo "    📦 [ONEDIR] Package created successfully"
    echo "    Binary size should be much smaller now at launch!"
    echo ""
    echo "═══════════════════════════════════════════════════════════════"
    echo -e "${GREEN}✅ BUILD SUCCESSFUL!${NC}"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    echo "Binary location: $SCRIPT_DIR/bin/stealth-backend/stealth-backend"
    echo "Folder size: $(du -sh "$SCRIPT_DIR/bin/stealth-backend" | cut -f1)"
    echo ""
    echo "Next steps:"
    echo "  1. Test the binary: ./bin/stealth-backend/stealth-backend"
    echo "  2. Build Electron app: npm run dist"
    echo ""
else
    echo ""
    echo -e "${RED}❌ BUILD FAILED!${NC}"
    echo "   Check the output above for errors."
    exit 1
fi
