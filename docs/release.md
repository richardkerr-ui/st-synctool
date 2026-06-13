# Release runbook — ST SyncTool (M7.1)

How to build the macOS app and DMG for beta testers. The build is reproducible
and **unsigned by default** (works for local testing); signing and notarization
are added once an Apple Developer account exists.

## Tooling decision: PyInstaller (not Briefcase)

PyInstaller was chosen over Briefcase because:

- It bundles PyQt6 cleanly with no extra plugin configuration (Briefcase needs a
  separate Qt backend setup and is opinionated about project layout).
- Arbitrary helper binaries (rclone, optionally ffmpeg) drop in via the spec's
  `binaries` list with no packaging ceremony.
- The `.app` → DMG → codesign → notarize path is well-trodden and scriptable.

Briefcase is a better fit for greenfield cross-platform apps with its own
lifecycle; for a single existing macOS app that shells out to bundled binaries,
PyInstaller is less friction.

## Dependency handling

| Dependency | How it ships | Why |
|------------|--------------|-----|
| Python + PyQt6 + pure-Python deps | Bundled by PyInstaller into the `.app` | No Python install needed on the tester's Mac |
| **rclone** (required) | **Bundled** — the spec adds it from the build machine's PATH | Drive features work with zero terminal setup |
| **ffmpeg / ffprobe** (optional) | **Not bundled** — resolved from PATH at runtime | Only used for contact-sheet thumbnails and advisory frame-count verify, which degrade gracefully when absent. Bundling ffmpeg adds ~70 MB; deferred until thumbnails are a beta priority |

At runtime, `main.py` calls `utils.resources.prepend_bundle_to_path()`, which
puts the bundle's binary dirs at the front of PATH when frozen, so every
subprocess call (`rclone`, `ffmpeg`, …) finds the bundled copy first and falls
back to a system install otherwise. `utils.resources.find_binary()` does the
same resolution for explicit lookups. Running from source is unaffected.

## Build (unsigned)

```bash
brew install rclone        # so it gets bundled
./build.sh                 # → dist/ST SyncTool.app and dist/ST_SyncTool_<version>.dmg
```

`build.sh` runs PyInstaller against `STSyncTool.spec`, then builds a
drag-to-Applications DMG (via `create-dmg` if installed, else `hdiutil`). The
version comes from `core/version.py` — bump it there for each release.

An unsigned build launches but Gatekeeper shows "unidentified developer"; the
tester must right-click → Open the first time. Signing removes this.

## Sign + notarize (requires Apple Developer account — $99/yr)

Prerequisites once the account exists:
- A **Developer ID Application** certificate in the login keychain.
- An app-specific password (or `notarytool` keychain profile) for notarization.

```bash
# 1. Build and sign in one step:
SIGN_IDENTITY="Developer ID Application: Signal Theory (TEAMID)" ./build.sh

# 2. Notarize the DMG (store creds once with: xcrun notarytool store-credentials):
xcrun notarytool submit "dist/ST_SyncTool_<version>.dmg" \
  --keychain-profile "st-notary" --wait

# 3. Staple the ticket so it verifies offline:
xcrun stapler staple "dist/ST_SyncTool_<version>.dmg"

# 4. Verify:
spctl -a -t open --context context:primary-signature \
  "dist/ST_SyncTool_<version>.dmg"
```

Notes:
- The spec sets `target_arch=None` (host arch). For a release that runs on both
  Apple Silicon and Intel, set `target_arch='universal2'` and build on a Mac
  whose Python is a universal2 build.
- `--options runtime` (hardened runtime) is already passed by `build.sh`; it is
  required for notarization.
- Add `assets/icon.icns` and point the spec's `BUNDLE(icon=...)` at it for a
  branded icon before public release.

## Acceptance (the M7.1 "done when")

On a **fresh Mac** with neither Python nor rclone installed: download the DMG,
drag the app to Applications, launch it with no security warnings and no
terminal use, and confirm a Drive operation works (proving the bundled rclone
is found). This final check requires the signed + notarized build.
