# ST SyncTool — Beta Manual Checklist

The single sit-down list of everything that needs a human on a real Mac before
calling the app beta-ready. Anything covered by the automated suite (1810+
tests, full pytest-qt under M7.2 CI) is deliberately **not** here — this is only
what CI cannot do: real rclone against real Drive, real removable hardware, an
Apple account, and a "does it feel right" eyeball.

Tick each box as you go. Suggested order is top to bottom.

---

## 0. Blocker — must happen first (only Richard can)

- [ ] **Apple Developer account** ($99/yr). Gates code-signing, notarization and
      therefore beta-tester recruitment. Everything else can be checked on an
      unsigned local build.

---

## 1. Data-safety verifications (the trust gate)

These protect footage custody. If any fail, the beta is not safe to ship.

- [ ] **M12.1 — Free-space block.** Point an offload at a destination drive with
      less free space than the source. Expect: it refuses to start *before any
      copy*, with a clear shortfall message ("need X, only Y free, short by Z").
      Then free up space / pick a bigger drive and confirm it proceeds.
- [ ] **M12.2 — Duplicate-card warning.** Offload a card to a destination. Then
      re-insert / re-select the *same* card to the *same* destination and start
      again. Expect: a "Possible duplicate card" warn-and-confirm listing the
      prior offload; declining aborts, confirming proceeds. Then offload a
      *different* card (new content) and confirm it does **not** warn.
- [ ] **M12.3 — Source untouched.** After any offload, confirm the source card's
      files, sizes and modified-dates are unchanged (the per-source `🔒 read-only`
      badge is the in-app promise; spot-check a couple of files in Finder).
- [ ] **M10.1 / M12.4 — Clearance + banner.** Run an offload to **two** clean
      destinations → expect the green **SAFE TO FORMAT** banner. Run one with a
      single destination or an induced failure → expect the red **DO NOT EJECT**
      banner. Confirm the completion sound plays (and that the Settings toggle
      silences it).

## 2. Field-reality checks (real hardware)

- [ ] **M12.5 — Awake indicator + lid reality.** Start a long offload. Confirm
      the gold "Keeping Mac awake — don't close the lid" indicator shows while it
      runs and clears when done. (Optional truth-test: on a bare laptop, closing
      the lid *will* pause it — that is expected and documented; clamshell mode
      with an external display keeps it running.)
- [ ] **M12.6 — Throughput + ETA.** During an offload, confirm the status line
      shows a live "source → dest · rate · ETA" that updates and reads sensibly
      (not wildly swinging).

## 3. Feature end-to-end (real Drive / third-party tools)

- [ ] **M5.1 — Deep-verify e2e.** Tick "Deep verify (downloads files)" against a
      junk Drive folder; confirm files stream-download, hash and report.
- [ ] **M10.3 — ASC MHL round-trip.** Tick "Export ASC MHL" on a transfer/offload,
      then import the generated `.mhl` into Silverstack or YoYotta and confirm it
      verifies the files. (XSD schema validation is already automated; this is
      the third-party-tool confirmation.)
- [ ] **M9.1 — Org log shipping (in-app).** Do a real offload and confirm the
      activity log lands under `{shared Drive base}/{workstation}/{user}/`.
      (rclone-level write/list already confirmed 2026-06-13.)
- [ ] **M3 — Drive→Drive.** Already confirmed 2026-06-12 — no action.

## 4. Blocked until M7.1 packaging lands

- [ ] **M7.1 — Sign + notarize + fresh-Mac launch.** Code-sign/notarize/staple
      per `docs/release.md`, then on a clean Mac: download the DMG, drag to
      Applications, launch with no Gatekeeper warning, and run one Drive op off
      the bundled rclone.
- [ ] **M5.3 — Scheduled-verify e2e.** Enable the schedule, confirm the launchd
      agent loads, force a failing archive, confirm the next-launch banner
      appears and dismisses. (Needs a stable installed `.app` path.)

## 5. Optional "does it feel right" visual passes (test-covered, not obligations)

- [ ] M7.3 — Report a Problem: save dialog + Finder reveal of the feedback zip.
- [ ] M5.1 / M5.2 / M5.4 — Drive deep-verify checkbox, "Verify All Projects"
      button, persisted verify-report file on disk.
- [ ] Merge — diff-table state pills show their colour fills (gold push / gray
      incoming / coral conflict / muted neutral) and nothing is clipped.

---

## Ship gate

Beta is ready to recruit testers when **Section 0**, **Section 1**, and the
**M7.1** item in Section 4 are all ticked. Sections 2, 3 and 5 are strongly
recommended but not strict blockers.
