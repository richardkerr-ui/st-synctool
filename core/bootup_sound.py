"""Play 'Sim Nights' by Kirk Casey on app startup.

Non-blocking: spawns ``afplay`` (macOS) via ``subprocess.Popen`` and returns
immediately, so the GUI never waits on the song. Playback respects system
volume — we do not force a level. A no-op (returning False) when the toggle is
off, the audio file is absent, or ``afplay`` cannot be found, so it can never
block or break startup.

Pure-ish: no PyQt6. Path/binary lookup is injectable for testing.
"""

from __future__ import annotations

import atexit
import subprocess
from typing import Optional

from core import settings as app_settings
from utils.resources import bootup_music_path, find_binary

# Handle to the running afplay process so we can stop it on quit or when the
# user disables the music. afplay is a detached child; without this it would
# keep playing after the app closes.
_player: Optional[subprocess.Popen] = None


def play_bootup_music(*, path=None) -> bool:
    """Start 'Sim Nights' in the background. Returns True if playback started.

    Returns False (silently) when the toggle is off, the file is missing, or
    ``afplay`` is unavailable. Never raises.
    """
    global _player
    try:
        if not app_settings.bootup_music_enabled(path=path):
            return False
        audio = bootup_music_path()
        if not audio:
            return False
        player = find_binary("afplay")
        if not player:
            return False
        _player = subprocess.Popen(
            [player, audio],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Belt-and-braces: aboutToQuit handles the graceful path, but an uncaught
        # exception elsewhere skips it. atexit still fires on a normal interpreter
        # exit, so the song doesn't outlive a crash.
        atexit.register(stop_bootup_music)
        return True
    except Exception:
        return False


def stop_bootup_music() -> None:
    """Stop playback if it's running. Safe to call any number of times."""
    global _player
    p = _player
    _player = None
    if p is None:
        return
    try:
        if p.poll() is None:        # still playing
            p.terminate()
    except Exception:
        pass
