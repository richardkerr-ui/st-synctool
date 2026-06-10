#!/bin/bash
# ST SyncTool one-shot installer for macOS.
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/richardkerr-ui/st-synctool/main/install.sh)

set -e

REPO_URL="https://github.com/richardkerr-ui/st-synctool.git"
INSTALL_DIR="$HOME/Applications/st-synctool"

echo ""
echo "=== ST SyncTool Installer ==="
echo ""

# ── 1. Xcode Command Line Tools ───────────────────────────────────────────────
if xcode-select -p &>/dev/null; then
    echo "✓ Xcode Command Line Tools"
else
    echo "Installing Xcode Command Line Tools..."
    echo "  (A dialog will appear — click Install and wait for it to finish)"
    xcode-select --install 2>/dev/null || true
    until xcode-select -p &>/dev/null; do
        sleep 5
    done
    echo "✓ Xcode Command Line Tools installed"
fi

# ── 2. Homebrew ───────────────────────────────────────────────────────────────
# Activate brew in this session even if .zprofile has not been re-sourced.
if ! command -v brew &>/dev/null; then
    [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [[ -f /usr/local/bin/brew    ]] && eval "$(/usr/local/bin/brew shellenv)"
fi

if command -v brew &>/dev/null; then
    echo "✓ Homebrew"
else
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [[ -f /usr/local/bin/brew    ]] && eval "$(/usr/local/bin/brew shellenv)"
    echo "✓ Homebrew installed"
fi

# ── 3. Python and rclone ──────────────────────────────────────────────────────
echo ""
echo "Installing Python and rclone..."
brew install python rclone

# ── 4. Clone or update the repo ──────────────────────────────────────────────
echo ""
if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "Repository already exists — pulling latest..."
    git -C "$INSTALL_DIR" pull
else
    echo "Cloning to $INSTALL_DIR..."
    mkdir -p "$HOME/Applications"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# ── 5. Python venv and dependencies ──────────────────────────────────────────
echo ""
echo "Setting up Python environment..."
cd "$INSTALL_DIR"
[[ ! -d .venv ]] && python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt

# ── 6. rclone config — skip if a valid gdrive remote already exists ───────────
_needs_config=false
if rclone listremotes 2>/dev/null | grep -q "^gdrive:$"; then
    _token=$(rclone config show gdrive 2>/dev/null | grep "^token" | cut -d= -f2- | xargs)
    if [[ -z "$_token" || "$_token" == "{}" ]]; then
        _needs_config=true
        echo ""
        echo "! gdrive remote exists but token is empty — re-running rclone config."
    fi
else
    _needs_config=true
fi

if [[ "$_needs_config" == "true" ]]; then
    echo ""
    echo "Setting up Google Drive (see README.md for step-by-step instructions)..."
    echo ""
    rclone config
fi

# ── 7. Validate the connection ────────────────────────────────────────────────
echo ""
echo "Validating Google Drive connection..."
if rclone lsd gdrive: &>/dev/null; then
    echo "✓ Google Drive connection verified"
else
    echo ""
    echo "  ERROR: Google Drive authentication failed."
    echo "  The OAuth flow may not have completed fully."
    echo ""
    echo "  Run this to reconnect:"
    echo "    rclone config reconnect gdrive:"
    echo ""
    echo "  Then launch the app:"
    echo "    $INSTALL_DIR/run.sh"
    echo ""
    exit 1
fi

# ── 8. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "  ST SyncTool is ready. To launch:"
echo "    $INSTALL_DIR/run.sh"
echo ""
