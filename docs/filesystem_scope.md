# Filesystem scope: what "verified" covers (M15.1)

This documents exactly what ST SyncTool's hash verification does and does not
cover, what each copy path preserves, and how filenames are normalised. DITs and
post houses should read this to know what they are and are not getting.

## What "verified" means

**Plain "verified" = the data fork (file byte contents) only.** That is what the
xxh128 (and md5 on Drive) digests cover. Extended attributes, resource forks and
Finder metadata are **not** part of the hash and are **not** verified. This is
stated in the offload chain-of-custody log (`SCOPE:` line), the persisted verify
report (`scope` field) and the Verify tab tooltip. The single source of truth for
the wording is `core.verify.VERIFY_SCOPE_NOTE`.

If a delivery requires xattrs / resource forks / FinderInfo to be carried and
verified, that is out of current scope and must be called out separately — do not
read a plain "verified" verdict as covering it.

## Metadata preservation per copy path

> **Status: the `shutil.copy2` premise must be confirmed empirically (M15.1
> manual audit), not assumed.** Python's `shutil.copy2` calls `copystat`, which on
> macOS *does* copy extended attributes (including `com.apple.ResourceFork` and
> `com.apple.FinderInfo`). The earlier flat claim that "xattrs/resource forks do
> not survive `shutil.copy2`" may be wrong for the local shutil path. The real
> metadata loss is more likely on rclone-managed transfers. The table below
> records expected behaviour; the **M15.1 copy-path metadata audit** in the
> ROADMAP manual-checks table fills in the measured truth before launch.

| Copy path | Data fork | xattrs / resource fork | Notes |
| --- | --- | --- | --- |
| `shutil.copy2` local (APFS→APFS) | ✅ verified (xxh128) | likely preserved (`copystat` copies xattrs on macOS) | confirm in M15.1 audit |
| rclone local→Drive | ✅ (md5 transport + xxh128 stored pre-upload) | not preserved (Drive has no xattr model) | metadata is the expected loss here |
| rclone local→NAS (SMB/NFS) | ✅ | backend-dependent; SMB often drops resource forks | confirm per server in M15.1 audit |
| rclone Drive→Drive | ✅ (md5 only; server-side) | n/a (no local bytes) | md5-only by design |
| APFS→exFAT (card/shuttle) | ✅ | exFAT has no native fork support; macOS uses AppleDouble `._` sidecars | confirm in M15.1 audit |

The verification hash is identical across all paths for the data fork, so a
"verified" file is byte-identical regardless of path. Two "verified" copies may
still differ in metadata a post-house tool cares about — hence the explicit scope.

## Filename normalisation — two representations, kept separate

There are deliberately two representations of a path, and they must never be
confused (pinned in code comments in both modules):

- **Internal fingerprint key** — `core/merkle.py::normalise_path`: forward
  slashes + Unicode **NFC** + **lowercase**. Used only to compute the folder-root
  corruption fingerprint so it agrees across case-insensitive volumes (APFS
  default, exFAT cards, NTFS). It may mangle the original name freely; it is never
  written anywhere a human or post house reads.

- **External contract name** — the manifest `files` keys, the MHL `<path>` text,
  and all comparison/verify keys: the **true on-disk name**, preserved exactly
  (case and Unicode form). Post houses match files by the real on-disk name, so
  this is never normalised. `core/asc_mhl.py` writes `rel` verbatim.

A round-trip test (`tests/test_asc_mhl.py`) asserts that a mixed-case, NFD-named
file (`Café_Shot_01A.mov`) survives MHL export with its name unchanged even though
its folder-root key is the different lowercase/NFC form.

**Path comparison consistency:** ST SyncTool does not normalise manifest /
comparison / verify paths at all — every layer uses the exact on-disk name, so
within a platform comparisons are consistent and an identical file never reads as
a false MISMATCH. macOS stores filenames in NFD; Drive (via rclone) preserves the
bytes rclone is given. If a future cross-normalisation mismatch is observed (an
NFD local name vs an NFC counterpart), the fix is to NFC-normalise *before
comparison only*, never in the stored external name.

## rclone `--checksum`, backend hash capability, and version pinning (M15.2)

**Invocation audit (the real gate).** Every rclone call ST SyncTool treats as a
verification step passes `--checksum`, so rclone compares content hashes rather
than size+modtime: `sync`/`copy` (`rclone_bridge.sync`), `copyto` and
`copyto_result` (Merge). `lsjson --hash` returns hashes for the metadata compare,
and `cat` (deep verify) downloads and re-hashes. Backend capability is a
necessary but not sufficient condition — the flag is the real gate, and it is
present on every verification path.

**Backend hash capability.** `--checksum` only hash-compares when both endpoints
expose a common content hash:

| Backend | Hash under `--checksum` | Counts as integrity-verified |
| --- | --- | --- |
| Google Drive | md5 (native) | ✅ |
| Local APFS / HFS+ | rclone-computed (both sides) | ✅ |
| exFAT card/shuttle (local mount) | rclone-computed | ✅ (local backend) |
| NAS via SMB/NFS (as an rclone remote) | backend-dependent | ⚠️ confirm in M15.2 backend audit |

`rclone_bridge.backend_supports_checksum()` returns True only for Drive and local
paths. A transfer touching an unconfirmed backend logs a loud
`CHECKSUM FALLBACK` custody-log error and sets `checksum_context.integrity_verified
= False`, so the M14.1 clearance gate does not count it. A size+modtime pass is
never silently treated as integrity-verified.

**Version pinning.** `rclone_bridge.RCLONE_REQUIRED_VERSION` pins the rclone
version. Preflight (`preflight.check_rclone_pinned_version`) refuses to proceed if
the running rclone is older than the pin (flag/hash semantics may differ).
`build.sh` refuses to build unless the bundled rclone matches the pin exactly, so
the shipped binary is deterministic — the pin is bumped deliberately, never picked
up silently from a build machine. The actual rclone version is recorded per
transfer in `checksum_context.rclone_version`, so a future dispute over a specific
job traces to the exact binary that ran it.
