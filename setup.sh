#!/bin/bash
# Developer / manual install helper.
# For end-user installs, use install.sh instead:
#   bash <(curl -fsSL https://raw.githubusercontent.com/richardkerr-ui/st-synctool/main/install.sh)
set -e

echo "=== ST SyncTool Setup ==="
echo ""

# 1. Check for Homebrew, install if missing
# Activate brew in this session even if .zprofile has not been re-sourced.
if ! command -v brew &>/dev/null; then
  [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [[ -f /usr/local/bin/brew    ]] && eval "$(/usr/local/bin/brew shellenv)"
fi

if ! command -v brew &>/dev/null; then
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [[ -f /usr/local/bin/brew    ]] && eval "$(/usr/local/bin/brew shellenv)"
else
  echo "✓ Homebrew already installed"
fi

# 2. Install Python 3.11 and rclone
# Pin to 3.11 — CI runs 3.11, build.sh defaults to /opt/homebrew/bin/python3.11.
# Do not use the unversioned 'python' formula: it tracks latest (currently 3.14)
# and PyInstaller/pyobjc wheel availability on 3.14 is unverified as of 2026-06.
echo ""
echo "Installing Python 3.11 and rclone..."
brew install python@3.11 rclone

# 3. Create virtual environment
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d .venv ]]; then
  echo ""
  echo "Creating virtual environment (Python 3.11)..."
  python3.11 -m venv .venv
else
  echo "✓ Virtual environment already exists"
  echo "  (If it was built with a different Python, delete .venv and re-run.)"
fi

# 4. Install Python dependencies
echo ""
echo "Installing Python dependencies..."
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt
pip install -r requirements-dev.txt

echo ""
echo "=== Setup complete ==="
echo ""
echo "To run ST SyncTool:"
echo "  ./run.sh"
echo ""
echo "First-time rclone setup (required for Google Drive):"
echo "  rclone config"
