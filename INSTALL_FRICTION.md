# Install Friction Log

Observed friction points from a real first-time install on a fresh Mac Studio (macOS Tahoe, Apple Silicon). Use this as the brief for improving the onboarding experience.

---

## Friction points observed

### 1. GitHub auth — password no longer works
**What happened:** User ran `git clone` and was prompted for a GitHub username/password. Password was rejected because GitHub deprecated password auth for Git operations.
**Workaround:** Generate a Personal Access Token with `repo` scope and embed it in the clone URL.
**Root cause:** No auth guidance in README or setup instructions.

### 2. PAT token had wrong scope / wrong account
**What happened:** First token attempt returned 403 "Write access to repository not granted" even for a read (clone). Required regenerating the token.
**Root cause:** No guidance on which scope to select when generating the token.

### 3. Homebrew not in PATH after install
**What happened:** `setup.sh` successfully installed Homebrew but the current terminal session didn't pick up the new PATH. `brew` returned "command not found" immediately after setup completed.
**Workaround:** Manually run `eval "$(/opt/homebrew/bin/brew shellenv zsh)"` in the current session.
**Root cause:** `setup.sh` correctly writes to `.zprofile` but the running shell doesn't re-source it. The script needs to `eval` the shellenv itself before calling `brew install`.

### 4. rclone not found when `./run.sh` was called
**What happened:** `run.sh` launched and immediately errored with "rclone not found" because `brew` wasn't in PATH (see #3), so rclone never actually installed.
**Root cause:** Downstream consequence of issue #3.

### 5. Re-cloning on a machine where the repo already exists
**What happened:** On second run, `git clone` failed with "destination path already exists." User had to skip the clone step manually.
**Root cause:** The one-liner clone+setup+run command has no guard for an existing directory.

### 6. rclone OAuth flow didn't complete — empty token
**What happened:** After running `rclone config`, the remote was created but with an empty token. App launched and immediately failed with "empty token found."
**Likely cause:** Browser OAuth window may have been dismissed or not fully completed. rclone silently saved an incomplete config.
**Workaround:** `rclone config reconnect gdrive:`
**Root cause:** No post-config validation in the app or setup flow. The setup wizard should detect an invalid/empty token and prompt the user to reconnect before allowing the app to proceed.

### 7. No single install command
**What happened:** Install required multiple manual steps across multiple sessions: xcode-select, clone, setup.sh, rclone config, run.sh — with PATH issues causing failures mid-flow.
**Root cause:** There is no true one-shot install path.

---

## Ideal end state

A new user on a fresh Mac should be able to run **one command** and end up with a working, authenticated app. Something like:

```bash
curl -fsSL https://raw.githubusercontent.com/richardkerr-ui/st-synctool/main/install.sh | bash
```

That script should:
1. Check for / install Xcode CLT non-interactively (or detect and skip)
2. Check for / install Homebrew, and immediately add it to PATH in the current shell
3. Install Python and rclone via brew
4. Clone the repo to a known location (`~/Applications/st-synctool` or similar)
5. Create the venv and install pip dependencies
6. Run `rclone config` if no valid `gdrive` remote exists
7. Validate the rclone token before exiting
8. Launch the app (or print `./run.sh` with the correct path)

---

## Claude Code prompt to fix this

Paste the following into a Claude Code terminal session opened in the ST SyncTool repo:

---

```
I need you to overhaul the install experience for ST SyncTool so that a new user on a vanilla macOS machine can go from zero to running app with a single terminal command.

Here is the full friction log from a real first-time install: [paste contents of INSTALL_FRICTION.md]

Please make the following changes:

1. **Create `install.sh`** — a single curl-able install script that:
   - Detects whether Xcode Command Line Tools are installed; if not, triggers `xcode-select --install` and waits for it to complete before proceeding
   - Installs Homebrew if missing, and immediately evals the shellenv so brew is usable in the same shell session (do not rely on .zprofile being re-sourced)
   - Installs Python and rclone via brew
   - Clones the repo to `~/Applications/st-synctool` (skips clone if the directory already exists and does a `git pull` instead)
   - Creates the venv and pip installs requirements.txt
   - Calls `rclone config` only if there is no existing remote with `type = drive`, or if the existing one has an empty token
   - After rclone config, validates the token by running `rclone lsd gdrive:` — if it fails, prints a clear error and prompts the user to run `rclone config reconnect gdrive:`
   - Prints a clear success message with the command to launch the app

2. **Update `setup.sh`** — fix the PATH issue: after installing Homebrew, immediately `eval "$(/opt/homebrew/bin/brew shellenv)"` within the script so subsequent brew/rclone commands work without opening a new terminal.

3. **Update the setup wizard in the app** — when the app launches and rclone is configured but the token is empty or expired, show a clear in-app prompt with a button that runs `rclone config reconnect gdrive:` rather than surfacing a raw error message.

4. **Update README.md** — replace the current Installation section with:
   - The one-line curl install command as the primary path
   - The manual fallback (clone + setup.sh) as a secondary option
   - A note that GitHub password auth is not supported; link to GitHub docs on Personal Access Tokens if cloning a private repo

Keep the changes minimal — do not refactor anything unrelated to the install flow. The goal is: one command, working app, no manual steps.
```
