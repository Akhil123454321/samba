#!/bin/bash
set -e

echo "============================================"
echo "  Samba Meeting Assistant — Installer"
echo "============================================"

INSTALL_DIR="$HOME/Applications/Samba"
APP_DEST="/Applications/Samba.app"

# ── 1. Check prerequisites ────────────────────────────────────────────────────

echo ""
echo "Checking prerequisites..."

if ! command -v brew &> /dev/null; then
    echo "  ✗ Homebrew not found. Install it from https://brew.sh then re-run this script."
    exit 1
fi
echo "  ✓ Homebrew"

if ! command -v python3 &> /dev/null; then
    echo "  ✗ Python 3 not found. Install via: brew install python"
    exit 1
fi
echo "  ✓ Python 3 ($(python3 --version))"

if ! command -v ollama &> /dev/null; then
    echo "  Installing Ollama..."
    brew install ollama
fi
echo "  ✓ Ollama"

# Check BlackHole
if ! python3 -c "import sounddevice; devs=[d['name'] for d in sounddevice.query_devices()]; exit(0 if any('BlackHole' in d for d in devs) else 1)" 2>/dev/null; then
    echo ""
    echo "  ✗ BlackHole audio driver not found."
    echo "    Install it with: brew install blackhole-2ch"
    echo "    Then set up a Multi-Output Device in Audio MIDI Setup."
    echo "    Re-run this installer after."
    exit 1
fi
echo "  ✓ BlackHole audio driver"

# ── 2. Install Python dependencies ───────────────────────────────────────────

echo ""
echo "Installing Python dependencies (this may take a few minutes)..."
pip3 install -q flask flask-cors pywebview mlx-whisper sounddevice numpy \
                  requests psutil faster-whisper

echo "  ✓ Python packages installed"

# ── 3. Pull Ollama model ──────────────────────────────────────────────────────

echo ""
echo "Pulling Ollama model (llama3.2)..."
ollama pull llama3.2
echo "  ✓ Ollama model ready"

# ── 4. Copy app to ~/Applications/Samba ──────────────────────────────────────

echo ""
echo "Installing Samba to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -R app.py templates static requirements.txt settings.json 2>/dev/null "$INSTALL_DIR/" || true
cp -R app.py templates static requirements.txt "$INSTALL_DIR/"
echo "  ✓ App files copied"

# ── 5. Install .app launcher ──────────────────────────────────────────────────

echo ""
echo "Installing Samba.app to /Applications..."
cp -R dist/Samba.app "$APP_DEST"
echo "  ✓ Samba.app installed"

# ── 6. First model warm-up ────────────────────────────────────────────────────

echo ""
echo "Pre-downloading Whisper model (one-time, ~1.5GB)..."
python3 -c "
import mlx_whisper, numpy as np
print('  Downloading whisper-large-v3-turbo...')
mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo='mlx-community/whisper-large-v3-turbo')
print('  ✓ Whisper model cached')
"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Installation complete!"
echo ""
echo "  Open Samba from /Applications or Launchpad."
echo ""
echo "  Before your first meeting:"
echo "  • Set System Output to your Multi-Output Device"
echo "    (System Settings → Sound → Output)"
echo "============================================"
