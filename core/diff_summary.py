"""Typed summary of a merge diff for the Merge tab header line (M2).

Computes "3 conflicts need review · 44 files will sync automatically ·
2 deletions held for you" from a list of DiffResult rows plus the user's
per-row action selections. The GUI only renders the string.

Also owns ACTION_OPTIONS_BY_STATE, the single source of truth for which
actions each DiffState offers and which is the default (first item).
gui/diff_table.py imports it from here.
"""

from dataclasses import dataclass

from core.comparison import DiffState, conflict_suggested_action
from core.merge_ops import (
    ACT_PUSH, ACT_PULL, ACT_DELETE_LOCAL, ACT_DELETE_SERVER, ACT_SKIP,
)

# For each state, the list of selectable actions. First item is the default
# selection. BOTH_CHANGED uses a smart per-row default instead (the mtime-based
# conflict_suggested_action); its list defines the available options only.
ACTION_OPTIONS_BY_STATE = {
    "LOCAL_ONLY":     [ACT_PUSH, ACT_DELETE_LOCAL,  ACT_SKIP],
    "SERVER_ONLY":    [ACT_PULL, ACT_DELETE_SERVER, ACT_SKIP],
    "LOCAL_CHANGED":  [ACT_PUSH, ACT_PULL,          ACT_SKIP],
    "SERVER_CHANGED": [ACT_PULL, ACT_PUSH,          ACT_SKIP],
    "BOTH_CHANGED":   [ACT_SKIP, ACT_PUSH,          ACT_PULL],
    # Indeterminate (no shared checksum): we cannot prove same-or-different, so
    # default to Skip and offer the same manual choices as a conflict.
    "INDETERMINATE":  [ACT_SKIP, ACT_PUSH,          ACT_PULL],
    "DELETED_LOCAL":  [ACT_SKIP, ACT_DELETE_SERVER, ACT_PULL],
    "DELETED_SERVER": [ACT_SKIP, ACT_DELETE_LOCAL,  ACT_PUSH],
    "DELETED_BOTH":   [ACT_SKIP],
    "RENAMED":        [ACT_SKIP, ACT_PUSH,          ACT_PULL],
}

# Deletion states where a default Skip means "we detected a deletion but are
# not propagating it until you decide". DELETED_BOTH is excluded: the file is
# already gone on both sides, there is nothing to hold.
_HELD_DELETION_STATES = {"DELETED_LOCAL", "DELETED_SERVER"}


@dataclass(frozen=True)
class DiffSummary:
    total: int                  # all rows, including unchanged
    unchanged: int
    conflicts_total: int        # BOTH_CHANGED rows
    conflicts_unresolved: int   # BOTH_CHANGED rows whose action is Skip
    syncing: int                # rows whose action is Push or Pull
    deletions_held: int         # deletion rows still on Skip
    deletions_to_apply: int     # rows whose action is Delete Local/Server
    skipped: int                # everything else left on Skip

    def to_text(self) -> str:
        """Render the header line. Zero segments are omitted."""
        parts = []
        if self.conflicts_unresolved:
            n = self.conflicts_unresolved
            parts.append(f"{n} conflict{'s' if n != 1 else ''} need{'s' if n == 1 else ''} review")
        if self.syncing:
            n = self.syncing
            parts.append(f"{n} file{'s' if n != 1 else ''} will sync automatically")
        if self.deletions_to_apply:
            n = self.deletions_to_apply
            parts.append(f"{n} deletion{'s' if n != 1 else ''} will be applied")
        if self.deletions_held:
            n = self.deletions_held
            parts.append(f"{n} deletion{'s' if n != 1 else ''} held for you")
        if self.skipped:
            n = self.skipped
            parts.append(f"{n} file{'s' if n != 1 else ''} skipped")
        if not parts:
            if self.unchanged:
                return f"Everything in sync, {self.unchanged} file{'s' if self.unchanged != 1 else ''} unchanged"
            return "Nothing to compare"
        return " · ".join(parts)


def default_action(result) -> str:
    """The action a freshly populated row starts on, mirroring the GUI."""
    state_name = result.state.name
    if state_name == "BOTH_CHANGED":
        suggested = conflict_suggested_action(result)
        if suggested in ACTION_OPTIONS_BY_STATE["BOTH_CHANGED"]:
            return suggested
        return ACT_SKIP
    options = ACTION_OPTIONS_BY_STATE.get(state_name, [ACT_SKIP])
    return options[0]


def summarize_diff(results, actions=None) -> DiffSummary:
    """Build a DiffSummary from DiffResult rows and per-row action choices.

    results: list of DiffResult (UNCHANGED rows allowed, counted separately).
    actions: optional {path: action_text} from the GUI's action combos.
             Rows absent from the dict fall back to their default action.
    """
    actions = actions or {}
    total = len(results)
    unchanged = 0
    conflicts_total = 0
    conflicts_unresolved = 0
    syncing = 0
    deletions_held = 0
    deletions_to_apply = 0
    skipped = 0

    for r in results:
        state_name = r.state.name
        if state_name == "UNCHANGED":
            unchanged += 1
            continue

        action = actions.get(r.path) or default_action(r)

        # INDETERMINATE rows need a human decision just like a conflict, so they
        # are counted into the "needs review" rollup rather than vanishing into
        # the skipped bucket. The per-row pill still reads "Unknown", not
        # "Conflict", so the distinction survives where the user actually looks.
        if state_name in ("BOTH_CHANGED", "INDETERMINATE"):
            conflicts_total += 1
            if action == ACT_SKIP:
                conflicts_unresolved += 1
                continue

        if action in (ACT_PUSH, ACT_PULL):
            syncing += 1
        elif action in (ACT_DELETE_LOCAL, ACT_DELETE_SERVER):
            deletions_to_apply += 1
        elif state_name in _HELD_DELETION_STATES:
            deletions_held += 1
        else:
            skipped += 1

    return DiffSummary(
        total=total,
        unchanged=unchanged,
        conflicts_total=conflicts_total,
        conflicts_unresolved=conflicts_unresolved,
        syncing=syncing,
        deletions_held=deletions_held,
        deletions_to_apply=deletions_to_apply,
        skipped=skipped,
    )
