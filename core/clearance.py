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
    # Of clean_dest_count, how many distinct destinations came from earlier
    # offload runs (the M12.2 ledger) rather than this run. Lets the UI be
    # transparent that redundancy was accumulated across runs.
    from_earlier_count: int = 0

    def to_text(self) -> str:
        if self.cleared:
            plural = "destination" if self.clean_dest_count == 1 else "destinations"
            base = (
                f"All files verified on {self.clean_dest_count} {plural}. "
                f"{self.source_label} is safe to format."
            )
            if self.from_earlier_count:
                base += (
                    f" ({self.from_earlier_count} from earlier "
                    f"offload{'s' if self.from_earlier_count != 1 else ''}.)"
                )
            return base
        return f"Not cleared: {self.reason}"


def compute_clearance(
    source_label: str, results: list, prior_clean_dests=None
) -> ClearanceVerdict:
    """
    Compute the safe-to-format verdict for one source.

    ``results`` may contain cells for several sources; only those whose
    ``source_label`` matches are considered. A destination counts as *clean*
    when it committed (state DONE), its per-file hash verification passed
    (``verified is True``), no media-format check hard-failed, AND the
    verification was integrity-based — an actual content-hash compare, not a
    size+modtime pass (``integrity_verified is True``).

    M14.1 — integrity_verified decision (option b): the flag defaults to False.
    Absence of a confirmed hash compare is NOT a hash compare. Offload's
    ``verify_staging`` re-hashes every destination file with xxh128 against the
    source ground truth, so offload-local destinations set it True on a clean
    pass. rclone/Drive-managed destinations stay False until M15.2 wires the
    ``--checksum`` result signal — so a Drive destination cannot, on its own,
    reach the 2-destination gate until M15.2 ships. A destination that passed by
    size+modtime only does NOT count toward the gate, but it does NOT block it
    either: two integrity-verified local disks still clear even when a third
    (e.g. Drive, pre-M15.2) is integrity-unconfirmed.

    ``prior_clean_dests`` (M12.2-backed) is an iterable of destination labels
    this same card was verified to in *earlier* offload runs. Redundancy is
    counted as the number of **distinct** destinations across runs — so a card
    offloaded to Dest 1 today and Dest 2 tomorrow clears, without recopying
    Dest 1. A hard failure or unverified destination in the *current* run still
    blocks clearance, so a fresh problem is never hidden by past success.

    Cleared (green) only when at least MIN_CLEAN_DESTS distinct destinations are
    clean and nothing in this run failed or went unverified.
    """
    cells = [r for r in results if r.source_label == source_label]
    total = len(cells)
    prior = {d for d in (prior_clean_dests or [])}

    if not cells and not prior:
        return ClearanceVerdict(
            source_label, False, 0, 0,
            "no destinations recorded for this source",
        )

    current_clean: set = set()
    failed = unverified = integrity_unconfirmed = 0
    for r in cells:
        state = _state_value(r.state)
        media_failed = _media_verify_failed(r)
        integrity = bool(getattr(r, "integrity_verified", False))
        if state == _FAILED or r.verified is False or media_failed:
            failed += 1
        elif r.verified is True and state == _DONE:
            if integrity:
                current_clean.add(r.dest_label)
            else:
                # Committed and "passed", but with no confirmed content-hash
                # compare (e.g. an rclone size+modtime fall-back). Does not count
                # toward the gate; does not block it. M14.1 / M15.2.
                integrity_unconfirmed += 1
        else:
            # verified is None, or the cell never reached DONE (skipped, partial)
            unverified += 1

    all_clean = current_clean | prior
    clean = len(all_clean)
    from_earlier = len(prior - current_clean)

    # Severity order: a hard failure outranks an unverified destination, which
    # outranks insufficient redundancy. Failures/unverified are current-run only.
    if failed:
        reason = (
            f"verification failed on {failed} of {total} destination"
            f"{'s' if total != 1 else ''}"
        )
        return ClearanceVerdict(source_label, False, clean, total, reason, from_earlier)

    if unverified:
        reason = (
            f"{unverified} destination{'s' if unverified != 1 else ''} not verified"
        )
        return ClearanceVerdict(source_label, False, clean, total, reason, from_earlier)

    if clean < MIN_CLEAN_DESTS:
        reason = (
            f"only {clean} destination{'s' if clean != 1 else ''} verified clean; "
            f"at least {MIN_CLEAN_DESTS} required before formatting"
        )
        if integrity_unconfirmed:
            reason += (
                f" ({integrity_unconfirmed} destination"
                f"{'s' if integrity_unconfirmed != 1 else ''} passed without a "
                f"confirmed integrity check and do not count)"
            )
        return ClearanceVerdict(source_label, False, clean, total, reason, from_earlier)

    return ClearanceVerdict(source_label, True, clean, total, "", from_earlier)
