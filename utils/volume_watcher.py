"""
Volume watcher — macOS mount/unmount notifications for the Offload tab.

Wraps NSWorkspace didMountNotification / didUnmountNotification (pyobjc AppKit).
Falls back gracefully if pyobjc is unavailable: the watcher simply never fires.

Only surfaces volumes that are BOTH externally removable AND contain a
recognisable media-card marker near the root.  Internal drives, Time Machine
volumes, and network mounts are silently ignored.

Manual test procedure
---------------------
1. Mount a small exFAT disk image with a DCIM/ folder at its root:
     hdiutil create -size 64m -fs ExFAT -volname TestCard /tmp/testcard.dmg
     mkdir -p /tmp/testcard_mount/DCIM
     hdiutil attach /tmp/testcard.dmg
   (or use a real camera card / SD card in a reader)
2. Run the app and switch to the Offload tab.
3. Confirm a banner appears: "New volume 'TestCard' detected … — Add as source?"
4. Click [Add] and confirm a source row is populated with the label "TestCard"
   and the correct mount path.
5. Click the banner's [Dismiss] instead — confirm it disappears and no row is added.
6. Eject and remount the image — confirm the banner reappears (dismiss is per-mount).
7. Mount a plain external drive with no DCIM/PRIVATE/CLIP/etc. — confirm NO banner.
8. Mount a Time Machine volume — confirm NO banner.

Edge cases to verify on real hardware
--------------------------------------
- Multi-slot card reader: each slot mounts as a separate volume.  Each should
  get its own banner independently.  Removing one card should withdraw only
  that card's banner.
- Disk image via hdiutil: flagged as ejectable=True, removable=True by macOS,
  so it qualifies for the marker check.  This is intentional (test convenience).
- Generic volume name ("NO NAME", "UNTITLED", "Untitled"): label falls back to
  "Card_<MMDD>" so the user always gets an editable label rather than an empty one.
# OVERNIGHT-FIX: docstring corrected — scan_existing() now detects pre-mounted cards
- Volume mounted before the app starts: live mount events are caught by the
  NSWorkspace observer, while cards already mounted at launch are surfaced by
  scan_existing() (called once on the Offload tab's first show).  Users can
  also still add any volume manually.
"""

from __future__ import annotations

import os
import re
import subprocess
import plistlib
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

# ---------------------------------------------------------------------------
# Media-card marker detection
# ---------------------------------------------------------------------------

_MEDIA_MARKER_DIRS  = {"DCIM", "PRIVATE", "CLIP", "MEDIA", "AUDIO", "SOUND"}
_MEDIA_MARKER_EXTS  = {".rdm", ".rdc"}   # RED camera structures


def _looks_like_media_card(mount_path: str) -> tuple[bool, str]:
    """
    Shallow check for recognised media-card structures at or near the volume root.
    Returns (True, marker_description) or (False, "").
    Only reads directory entries at depth 0 and 1 — never writes.
    """
    root = Path(mount_path)
    try:
        top_level = list(root.iterdir())
    except PermissionError:
        return False, ""

    for item in top_level:
        name_upper = item.name.upper()
        if name_upper in _MEDIA_MARKER_DIRS and item.is_dir():
            return True, item.name
        if item.suffix.lower() in _MEDIA_MARKER_EXTS:
            return True, item.name
        # Canon / Nikon / Sony put DCIM one level in (e.g. /Volumes/EOS_DIGITAL/DCIM)
        if item.is_dir():
            try:
                for sub in item.iterdir():
                    if sub.name.upper() in _MEDIA_MARKER_DIRS and sub.is_dir():
                        return True, f"{item.name}/{sub.name}"
            except PermissionError:
                continue

    return False, ""


# ---------------------------------------------------------------------------
# Volume metadata via diskutil
# ---------------------------------------------------------------------------

def _diskutil_info(mount_path: str) -> dict:
    """
    Run `diskutil info -plist <path>` and return the parsed dict.
    Returns {} on any error.
    """
    try:
        result = subprocess.run(
            ["diskutil", "info", "-plist", mount_path],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            return {}
        return plistlib.loads(result.stdout)
    except Exception:
        return {}


def _sanitise_label(name: str) -> str:
    """
    Turn a volume name into a safe destination subfolder label.
    Falls back to Card_MMDD for generic names.
    """
    generic = {"NO NAME", "UNTITLED", "UNTITLED SD", "UNTITLED 1", "UNTITLED 2", "EOS_DIGITAL"}
    if not name or name.strip().upper() in generic or name.strip().upper().startswith("UNTITLED"):
        return "Card_" + datetime.now().strftime("%m%d")
    # Replace filesystem-unsafe characters with underscores
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.strip())
    return safe[:64]


def _human_size(bytes_val: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


def _classify_volume(mount_path: str) -> Optional[dict]:
    """
    Inspect a newly-mounted volume and return a dict if it qualifies as a
    media-card source, or None if it should be ignored.

    Returned dict keys:
        mount_path      str
        volume_name     str   (raw name from OS)
        label           str   (sanitised, safe for use as a subfolder name)
        total_size      int   (bytes)
        total_size_str  str   (human-readable)
        filesystem      str
        removable       bool
        ejectable       bool
        looks_like_media_card  bool
        marker          str   (folder/file that triggered the match, e.g. "DCIM")
    """
    info = _diskutil_info(mount_path)
    if not info:
        return None

    removable = bool(info.get("Removable") or info.get("RemovableMedia") or info.get("RemovableMediaOrExternalDevice"))
    ejectable = bool(info.get("Ejectable"))

    if not (removable and ejectable):
        return None

    looks_like, marker = _looks_like_media_card(mount_path)
    if not looks_like:
        return None

    volume_name = info.get("VolumeName") or Path(mount_path).name
    return {
        "mount_path":           mount_path,
        "volume_name":          volume_name,
        "label":                _sanitise_label(volume_name),
        "total_size":           int(info.get("TotalSize") or 0),
        "total_size_str":       _human_size(int(info.get("TotalSize") or 0)),
        "filesystem":           info.get("FilesystemType") or info.get("FilesystemUserVisibleType") or "unknown",
        "removable":            removable,
        "ejectable":            ejectable,
        "looks_like_media_card": True,
        "marker":               marker,
    }


# ---------------------------------------------------------------------------
# Qt-friendly watcher
# ---------------------------------------------------------------------------

class VolumeWatcher(QObject):
    """
    Emits Qt signals on macOS volume mount/unmount events.

    volume_mounted(dict)   — qualified media-card volume appeared
    volume_unmounted(str)  — volume at this path was ejected (any volume)

    If pyobjc AppKit is unavailable, the watcher is a no-op.
    """

    volume_mounted   = pyqtSignal(dict)
    volume_unmounted = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._available = False
        self._observer = None
        self._setup()

    def _setup(self):
        try:
            from AppKit import NSWorkspace, NSNotificationCenter
            from Foundation import NSObject
        except ImportError:
            return

        watcher = self

        class _Observer(NSObject):
            def volumeDidMount_(self, notification):
                path = notification.userInfo().get("NSDevicePath")
                if path:
                    info = _classify_volume(str(path))
                    if info:
                        watcher.volume_mounted.emit(info)

            def volumeDidUnmount_(self, notification):
                path = notification.userInfo().get("NSDevicePath")
                if path:
                    watcher.volume_unmounted.emit(str(path))

        self._observer = _Observer.alloc().init()
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        nc.addObserver_selector_name_object_(
            self._observer, "volumeDidMount:", "NSWorkspaceDidMountNotification", None
        )
        nc.addObserver_selector_name_object_(
            self._observer, "volumeDidUnmount:", "NSWorkspaceDidUnmountNotification", None
        )
        self._available = True

    def scan_existing(self) -> list[dict]:
        """
        Classify all currently-mounted volumes under /Volumes and return those
        that qualify as media cards.  Used to surface cards that were plugged in
        before the app started.
        """
        results = []
        try:
            for entry in Path("/Volumes").iterdir():
                if not entry.is_dir():
                    continue
                info = _classify_volume(str(entry))
                if info:
                    results.append(info)
        except Exception:
            pass
        return results

    @property
    def available(self) -> bool:
        return self._available

    def stop(self):
        if self._observer and self._available:
            try:
                from AppKit import NSWorkspace
                NSWorkspace.sharedWorkspace().notificationCenter() \
                    .removeObserver_(self._observer)
            except Exception:
                pass
