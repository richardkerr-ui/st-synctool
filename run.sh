#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate Homebrew in this session so rclone is on PATH
[[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
[[ -f /usr/local/bin/brew    ]] && eval "$(/usr/local/bin/brew shellenv)"

if ! command -v rclone &>/dev/null; then
    echo "WARNING: rclone not found on PATH. Google Drive features will not work."
    echo "Run the installer: bash <(curl -fsSL https://raw.githubusercontent.com/richardkerr-ui/st-synctool/main/install.sh)"
fi

if [[ ! -d .venv ]]; then
  echo "Virtual environment not found. Run setup.sh first."
  exit 1
fi

source .venv/bin/activate
python main.py
