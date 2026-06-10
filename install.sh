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
echo "Installing Python and rclone (this takes 1-3 minutes on a first install)..."
brew install python rclone || { echo ""; echo "  ERROR: 'brew install python rclone' failed."; echo "  Try running it manually, then re-run this installer."; exit 1; }

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
pip install -v -r requirements.txt || { echo ""; echo "  ERROR: pip install failed."; echo "  Try: cd $INSTALL_DIR && source .venv/bin/activate && pip install -r requirements.txt"; exit 1; }

# ── 6. rclone config — skip if a valid gdrive remote already exists ───────────
_needs_config=false
if rclone listremotes 2>/dev/null | grep -q "^gdrive:$"; then
    echo "Checking existing Google Drive connection..."
    if rclone lsd gdrive: --max-depth 1 &>/dev/null; then
        echo "✓ Google Drive already connected"
    else
        _needs_config=true
        echo "! gdrive remote exists but authentication failed — re-running rclone config."
    fi
else
    _needs_config=true
fi

if [[ "$_needs_config" == "true" ]]; then
    echo ""
    echo "Quick rclone setup guide:"
    echo "  1. Type 'n' → new remote"
    echo "  2. Name: gdrive"
    echo "  3. Storage type: look for 'Google Drive', type its number"
    echo "  4. client_id, client_secret: press Enter (use defaults)"
    echo "  5. Scope: type 1 and press Enter (full access)"
    echo "  6. root_folder_id, service_account_file, advanced config: press Enter"
    echo "  7. Auto config: y (browser opens)"
    echo "  8. Sign in with your Signal Theory Google account"
    echo "  9. Shared drive: n"
    echo "  10. Save: y, then q to quit"
    echo ""
    rclone config
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
fi

# ── 8. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "  ST SyncTool is ready. To launch:"
echo "    $INSTALL_DIR/run.sh"
echo ""
