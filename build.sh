#!/usr/bin/env bash
# M7.1 — build ST SyncTool.app and a distributable DMG (macOS).
#
# Produces an UNSIGNED build by default (works for local testing). Pass an
# Apple Developer ID via SIGN_IDENTITY to code-sign; notarization is a separate
# step documented in docs/release.md.
#
# Usage:
#   ./build.sh                       # unsigned .app + .dmg in dist/
#   SIGN_IDENTITY="Developer ID Application: Signal Theory (TEAMID)" ./build.sh
#
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-/opt/homebrew/bin/python3.11}"
APP="dist/ST SyncTool.app"
VERSION="$("$PYTHON" -c 'import core.version as v; print(v.__version__)')"
DMG="dist/ST_SyncTool_${VERSION}.dmg"

echo "==> Building ST SyncTool v${VERSION}"

if ! command -v rclone >/dev/null 2>&1; then
  echo "WARNING: rclone not on PATH — it will NOT be bundled. Install with: brew install rclone" >&2
else
  # M15.2: the bundled rclone must match the pinned version so flag/hash
  # semantics are deterministic. Bump core.rclone_bridge.RCLONE_REQUIRED_VERSION
  # deliberately when you intend to ship a new rclone — never silently.
  PINNED_RCLONE="$("$PYTHON" -c 'import core.rclone_bridge as r; print(r.RCLONE_REQUIRED_VERSION)')"
  BUNDLED_RCLONE="$(rclone version | sed -n 's/^rclone v//p' | head -1)"
  if [ "$BUNDLED_RCLONE" != "$PINNED_RCLONE" ]; then
    echo "ERROR: rclone on PATH is v${BUNDLED_RCLONE} but the pinned version is" \
         "v${PINNED_RCLONE}." >&2
    echo "       Install the pinned version, or update RCLONE_REQUIRED_VERSION in" \
         "core/rclone_bridge.py deliberately." >&2
    exit 1
  fi
  echo "==> rclone pinned version OK: v${PINNED_RCLONE}"
fi

echo "==> PyInstaller"
"$PYTHON" -m PyInstaller --noconfirm --clean STSyncTool.spec

[ -d "$APP" ] || { echo "ERROR: $APP was not produced" >&2; exit 1; }

# Optional code-signing (requires an Apple Developer ID in the keychain).
if [ -n "${SIGN_IDENTITY:-}" ]; then
  echo "==> Code-signing with: $SIGN_IDENTITY"
  codesign --deep --force --options runtime --timestamp \
    --sign "$SIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
else
  echo "==> Skipping code-sign (set SIGN_IDENTITY to sign). Build is UNSIGNED."
fi

echo "==> Building DMG"
rm -f "$DMG"
if command -v create-dmg >/dev/null 2>&1; then
  create-dmg --volname "ST SyncTool ${VERSION}" \
    --app-drop-link 450 180 --icon "ST SyncTool.app" 150 180 \
    "$DMG" "$APP" || true
fi
# Fallback / default: a plain drag-to-Applications DMG via hdiutil.
if [ ! -f "$DMG" ]; then
  STAGE="$(mktemp -d)"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "ST SyncTool ${VERSION}" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG"
  rm -rf "$STAGE"
fi

echo "==> Done: $DMG"
echo "    Unsigned builds show a Gatekeeper warning until signed + notarized."
echo "    See docs/release.md for the signing/notarization steps."
