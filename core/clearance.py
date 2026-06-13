"""
M10.1 — "Safe to format" clearance.

The scariest moment in a DIT's day is wiping a card. After an offload the app
already holds per-file verification results for every destination. This module
turns those results into a single, explicit per-source verdict:

  • GREEN  — "All N files verified on K destinations. Card X is safe to format."
             Only when at least MIN_CLEAN_DESTS (2) destinations verified clean,
             so a single drive failure can never cost the only copy.
  • AMBER  — "Not cleared: <reason>" in every other case (a failure, an
             unverified destination, or insufficient redundancy).

Pure logic, no GUI and no import of core.offload (we read ``result.state`` by
its ``.value`` string to avoid a circular import), so it is fully headless-
testable and can also be driven by lightweight stand-in objects.
"""

from __future__ import annotations

from dataclasses import dataclass

# At least this many destinations must verify clean before a card is cleared.
# Two means a single destination failure still leaves a good copy.
MIN_CLEAN_DESTS = 2

_FAILED = "failed"
_DONE = "done"


def _state_value(state) -> str:
    """Return the lowercase string form of a CellState enum or a plain string."""
    return str(getattr(state, "value", state)).lower()


def _media_verify_failed(result) -> bool:
    """True if any format-aware media check on this destination hard-failed."""
    for entry in getattr(result, "media_verify_log", None) or []:
        if "MEDIA VERIFY FAILED" in str(entry):
            return True
    return False


@dataclass(frozen=True)
class ClearanceVerdict:
    """Typed clearance outcome for one source/card across all its destinations."""

    source_label: str
    cleared: bool
    clean_dest_count: int
    total_dest_count: int
    reason: str  # empty when cleared

    def to_text(self) -> str:
        if self.cleared:
            plural = "destination" if self.clean_dest_count == 1 else "destinations"
            return (
                f"All files verified on {self.clean_dest_count} {plural}. "
                f"{self.source_label} is safe to format."
            )
        return f"Not cleared: {self.reason}"


def compute_clearance(source_label: str, results: list) -> ClearanceVerdict:
    """
    Compute the safe-to-format verdict for one source from its CellResults.

    ``results`` may contain cells for several sources; only those whose
    ``source_label`` matches are considered. A destination counts as *clean*
    when it committed (state DONE), its per-file hash verification passed
    (``verified is True``) and no media-format check hard-failed.

    Cleared (green) only when at least MIN_CLEAN_DESTS destinations are clean
    and none failed or went unverified. Any failure, any unverified
    destination, or fewer than MIN_CLEAN_DESTS clean destinations yields an
    amber verdict with a human-readable reason.
    """
    cells = [r for r in results if r.source_label == source_label]
    total = len(cells)

    if not cells:
        return ClearanceVerdict(
            source_label, False, 0, 0,
            "no destinations recorded for this source",
        )

    clean = failed = unverified = 0
    for r in cells:
        state = _state_value(r.state)
        media_failed = _media_verify_failed(r)
        if state == _FAILED or r.verified is False or media_failed:
            failed += 1
        elif r.verified is True and state == _DONE:
            clean += 1
        else:
            # verified is None, or the cell never reached DONE (skipped, partial)
            unverified += 1

    # Severity order: a hard failure outranks an unverified destination, which
    # outranks insufficient redundancy.
    if failed:
        reason = (
            f"verification failed on {failed} of {total} destination"
            f"{'s' if total != 1 else ''}"
        )
        return ClearanceVerdict(source_label, False, clean, total, reason)

    if unverified:
        reason = (
            f"{unverified} destination{'s' if unverified != 1 else ''} not verified"
        )
        return ClearanceVerdict(source_label, False, clean, total, reason)

    if clean < MIN_CLEAN_DESTS:
        reason = (
            f"only {clean} destination{'s' if clean != 1 else ''} verified clean; "
            f"at least {MIN_CLEAN_DESTS} required before formatting"
        )
        return ClearanceVerdict(source_label, False, clean, total, reason)

    return ClearanceVerdict(source_label, True, clean, total, "")
