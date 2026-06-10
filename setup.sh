#!/bin/bash
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

# 2. Install Python and rclone
echo ""
echo "Installing Python and rclone..."
brew install python rclone

# 3. Create virtual environment
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d .venv ]]; then
  echo ""
  echo "Creating virtual environment..."
  python3 -m venv .venv
else
  echo "✓ Virtual environment already exists"
fi

# 4. Install Python dependencies
echo ""
echo "Installing Python dependencies..."
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt

echo ""
echo "=== Setup complete ==="
echo ""
echo "To run ST SyncTool:"
echo "  ./run.sh"
echo ""
echo "First-time rclone setup (required for Google Drive):"
echo "  rclone config"
