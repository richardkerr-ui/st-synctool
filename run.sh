#!/bin/bash
set -e

# Resolve symlinks so this works when called via /opt/homebrew/bin/st-synctool
_SCRIPT="$0"
while [[ -L "$_SCRIPT" ]]; do _SCRIPT="$(readlink "$_SCRIPT")"; done
SCRIPT_DIR="$(cd "$(dirname "$_SCRIPT")" && pwd)"
cd "$SCRIPT_DIR"

# Auto-update: fast-forward this clone to the latest main before launching, so
# `st-synctool` always boots the current version. Skippable with
# ST_SYNC_NO_UPDATE=1; non-fatal and silent on failure so offline launches still
# work. No-op for a non-git install (e.g. a frozen .app).
if [[ -z "$ST_SYNC_NO_UPDATE" ]] && command -v git &>/dev/null \
   && git rev-parse --is-inside-work-tree &>/dev/null; then
    # Pick a timeout wrapper if available; stock macOS has neither, so we also
    # set git's low-speed abort so a stalled connection can't hang startup.
    _TIMEOUT=""
    command -v timeout  &>/dev/null && _TIMEOUT="timeout 15"
    command -v gtimeout &>/dev/null && _TIMEOUT="gtimeout 15"
    _before="$(git rev-parse --short HEAD 2>/dev/null)"
    if GIT_TERMINAL_PROMPT=0 GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=10 \
       $_TIMEOUT git pull --ff-only --quiet 2>/dev/null; then
        _after="$(git rev-parse --short HEAD 2>/dev/null)"
        [[ "$_before" != "$_after" ]] && echo "ST SyncTool updated to $_after"
    fi
fi

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
