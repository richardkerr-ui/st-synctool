HOW TO CHECK THE MERGE FIXES IN THE APP
=======================================

Open the app, go to the Merge tab, and for each scenario paste these three paths
into the three inputs, then click "Scan & Compare".

--------------------------------------------------------------------
SCENARIO A — "Unknown" state (cross-algorithm fix)
--------------------------------------------------------------------
Base Manifest (.json):  /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/A_indeterminate/base_manifest.json
Local Folder (Yours):   /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/A_indeterminate/local
Server (Theirs):        /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/A_indeterminate/server

EXPECT: scene_01.mov and scene_02.mov show the state "Unknown" (glyph warn),
not "Server Changed". The files are byte-identical on both sides; the rows are
flagged only because the base manifest shares no checksum algorithm with the
freshly scanned SHA-256, so equality is unprovable. Before the fix these showed
a false "Server Changed".

--------------------------------------------------------------------
SCENARIO B — duplicate rename target (phantom-deletion fix)
--------------------------------------------------------------------
Base Manifest (.json):  /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/B_rename_collision/base_manifest.json
Local Folder (Yours):   /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/B_rename_collision/local
Server (Theirs):        /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/B_rename_collision/server

EXPECT: new.mov, old_a.mov and old_b.mov all show "Conflict" (flagged for
review). Before the fix, one of old_a/old_b would silently show "Deleted
Locally" (a phantom deletion) while the other collapsed into a rename.

--------------------------------------------------------------------
SCENARIO C — genuine conflict (both modtimes populated)
--------------------------------------------------------------------
Base Manifest (.json):  /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/C_true_conflict/base_manifest.json
Local Folder (Yours):   /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/C_true_conflict/local
Server (Theirs):        /Users/richard.kerr/Claude/Projects/ST SyncTool/demo_merge_fixes/app_fixtures/C_true_conflict/server

EXPECT: edit.prproj shows "Both Changed". Click the row: the conflict panel
fills BOTH the LOCAL and SERVER columns, including two different modtimes (local
2025-06-11 newer, server 2024-06-01), and the suggested action is Push (local is
newer). This is the control case proving the server column populates when the
file genuinely exists on both sides. In Scenario B the flagged files exist on
only one side, which is why one column was blank there.

--------------------------------------------------------------------
NOTE
--------------------------------------------------------------------
The cross-algorithm case cannot be produced with two plain local folders,
because the app always hashes both sides with SHA-256. The md5-only base
manifest stands in for a prior Drive-based state, which is where this actually
happens in production.
