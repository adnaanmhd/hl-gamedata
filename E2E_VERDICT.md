# Independent E2E Verification — VERDICT (2026-08-20, per E2E_KICKOFF_PROMPT.md)

## VERDICT: GREEN-WITH-FINDINGS

The continuous pipeline at HEAD `cc46210` (code = O-set `a6f5205`; the 4 commits on
top verified docs-only) survived a full Mac-local canary modeled on FLIP_RUNBOOK §5:
5 seed classes end-to-end, a REAL 3-leg kill -9 matrix with clean convergence, real
Gemini validation (bounded), real Drive II `_pipeline_test/` deliveries verified and
purged, and 3 payment-sheet generations proving counted-exactly-once and the O1
reset-generation guard. **No blocker found. The flip is NOT started** — it waits on
Adnaan's explicit go (`FLIP_EXEC_KICKOFF_PROMPT.md`).

Inherited state independently confirmed: Mac arming gate re-run → `850 passed`,
`ARMING GATE OK (floor 846)`; only docs commits sit on top of `a6f5205`.

## The run (what actually executed)

- Fresh `HL_PIPELINE_HOME` in the session scratchpad; real `~/hl-pipeline` (empty
  dir on this Mac) proven byte-identical before/after; repo tree and HEAD unchanged.
- Intake staged as canary uploads: a scratch rclone config aliased `drive-collect:`
  to a local staging tree (real `drive-deliver:` kept). **Real Drive I was never
  listed or read** — intake realism preserved through the same rclone/scan/download
  path, spend bounded by seed count. Ingest code verified read-only toward the
  collect remote (`lsjson`/`copy` only).
- Seeds (all unique video md5s; two genuinely <70s → real rejects):
  S1 clean Kamla 146.4s (v2 zip) → DELIVERED; S2 clean OW 348.2s (v2 zip) →
  DELIVERED (after a fixable fix pass); S3 Kamla 70.8s files-payload with junk-bytes
  `metadata.json`+`inputs.jsonl` (M/N/O surface) → **degrade-and-DELIVERED, reasons
  []**, with the exact warn-note `shift-record seeding failed (UnicodeDecodeError:
  ...)`; S4 v1-format zip 55.8s → v1 fix path → REJECTED `CNT_SHORT` (+ see F2);
  S5 OW zip payload 181MB → DELIVERED.
- Real VLM: gemini-3.7-flash; 33 calls across the 5 final-pass verdicts, ~45±10
  total incl. the 2 killed sweeps and re-validates; one 503 absorbed by backoff and
  logged to `logs/vlm-pressure.jsonl` (channel live).
- Deliveries: remote byte/md5 verify per file; first-per-game force-rrd-sampled as
  designed (real 91–113MB rrds regenerated; no 0-byte stub ever shipped); non-sampled
  sids exactly {video.mp4, frames.csv, session.json}; one remote path per sid.
- Teardown: `deliver.cleanup_test_folder` purged `_pipeline_test/`; verified
  externally (`rclone lsf` rc=3 "directory not found"); client tree untouched.

## Kill -9 matrix (real SIGKILL, each verified from the ledger)

| leg | kill point | evidence | convergence |
|---|---|---|---|
| 1 | mid-DOWNLOAD (S3 at 3.8MB partial) | row stayed DOWNLOADING; driver-3 startup states `{DOWNLOADING:1, DISCOVERED:1}` prove no row lost | kill-resume re-claimed first; `--checksum` re-copy; INGESTED |
| 2 | mid-VALIDATE (3s into S3's live sweep) | state stayed VALIDATING, **no verdict.json** in dossier | crash-triage re-validated → READY → DELIVERED |
| 3 | mid-DELIVER (269MB S2 upload in flight) | killed with rclone-to-drive-deliver running; row stayed PACKAGED | same-date re-stage honoring recorded `rrd_sampled`; idempotent re-upload; **exactly one** UPLOADED→DELIVERED |

Oracles, all PASS: exactly-one DELIVERED event per sid; hours written once and only
on DELIVERED rows; every seed terminal after `--until-idle` (self-stopped: `idle —
stopping; scans ok=1`); event-chain continuity (no conflicting transitions);
`incomplete` table empty; `batches` table untouched (0 rows). macOS stale-lock
pid-reclaim worked after every kill.

## Payment sheets (TEST mode, canary ledger)

- 3 generations: gen-1 (fired at driver start — 14:00 IST gate open, counted []),
  gen-2 (all 5 roots via the late-arrival arm), gen-3 (counted [] — disjointness).
- **Counted-exactly-once PROVEN**: gen-2 ∩ gen-3 = ∅ (counted and accepted); anchor
  chain contiguous `05:45:34 → 06:06:11 → 06:06:52` (each lo == previous hi).
- **Reject-label surface matches the ruled unfixable-only rule**: S4's cell is
  exactly `<70s qa-unmapped` — stored-fixable-field filtering, no fixable labels,
  no ×N counts (grep PASS); MD "Reject detail" carries raw codes as designed.
- **O1 verified by this run** (its designated verification, RULED): fail-first send
  wrote `.daily-counted.json` with no stamps; S5 then mutated to a reset generation
  (`state='DISCOVERED', md5_video=''`); resume printed VERBATIM
  `[sheet-stamp] 2026-08-15T10-12-15Z_outer_wilds_c_dbe4ff39049d23b5: row was reset
  mid-window (state=DISCOVERED) — accepted_reported_at SKIPPED; the next
  generation's hours/labels stay countable`, re-stamped 5 uploads / 4 accepted;
  reset row's `accepted_reported_at` NULL; whole-run reset query 0 rows; resume
  re-sent the byte-identical sheet (never regenerated). Uploaded stamp unguarded as
  designed.

## Findings (none blocking)

**F1 — TEST mode has no test-channel routing (config/kickoff premise gap).**
`HL_PIPELINE_TEST_MODE` only prefixes "TEST " (telegram.py:21-22,29,58;
config.py:262,311-315); both modes send to the real `TELEGRAM_CHAT_ID` from
secrets.env — no TEST-channel var exists anywhere in the package. FLIP_RUNBOOK §5.2
("TEST-prefixed Telegram") matches the code, but this brief's "no message may reach
the real channel" is unsatisfiable while actually sending. This run intercepted
`pipeline.telegram.send_message/send_document` at the process boundary (scratchpad
wrapper, zero repo changes): 12 send attempts captured, **all TEST-prefixed, none
delivered to any channel**. Decide before the flip whether canary/TEST traffic in
the production chat is acceptable, or add a TEST-channel var.

**F2 — v1 early-exit skips delivery scaffolding → narrow wrongful-terminal-reject
class.** `fix_v1_to_v2`'s already-v2 arm (`"canonical" not in s and "game_title" in
s` → delete stray `key_binding.json`, return — fix.py ~1656-1663) returns BEFORE
the tail that writes `rrd_creation.py` + touches the `session.rrd` stub
(fix.py:1925-1927); the v1-sniffed download branch creates no scaffolding either
(ingest.py:957-972 is v2-only). Input class: v2-shaped `session.json` + stray
`key_binding.json` + no rrd files in the payload + absent/unusable raw sidecars →
revalidation rejects `QA_FAIL_UNMAPPED` "missing delivery file: session.rrd /
rrd_creation.py" stored blocking+UNFIXABLE (fixable=has_raw=False) → terminal;
hours lost to a scaffolding gap, not content. Observed verbatim on S4 (whose
outcome was independently correct — genuinely <70s — the label pair is the tell);
byte-similar v2-sniffed S3 got scaffolding at download and DELIVERED. With usable
sidecars the same class stays fixable (retranslate), so the terminal window is
narrow. Finding for the record; NOT patched by this session per the brief.

**F3 — autoscale movement NOT observable Mac-local.** 18 CPUs → `CONT_POOL_MAX =
max(8, 18-12) = 8 = CONT_POOL_MIN`; `[autoscale]` prints only on change. The §5.4
"autoscale observed moving" criterion is unverified by this run — verify on the VM
canary (44-worker band).

**F4 — daily headline vs sheet counts (not a defect, don't misread at the flip).**
The 💰 message said "delivered today +0.0 h from 0 sessions" while the same
generation's sheet counted 5 roots / 0.21 h: the headline counts deliveries inside
[lo, hi=now−4h) and all canary deliveries were newer than hi.

**F5 — environment notes.** (a) Committed `CONT_DAILY_REPORTS=True` fired a
payment sheet within seconds of the first driver start (gate open past 14:00 IST) —
live confirmation of why the flip deploys False first (runbook §6c interlock).
(b) Local-alias staging makes canary "downloads" server-side/instant; leg-1 needed
`RCLONE_DISABLE=Copy` + `RCLONE_BWLIMIT` to stream — VM canary against real Drive I
won't have this quirk. (c) S3 carried an advisory `VLM game guess 'xonotic' (12/20
votes) differs from claimed 'kamla'` (below tripwire, correctly non-blocking) — the
08-12 "kamla" sample may be worth a human glance. (d) Both 08-1x seeds carried the
known SYNTHETIC-timeline (uniform_fps) advisory from capture-side frame drops.

## What did NOT run (honest scope)

VM/systemd (nothing deployed, per brief) · batch driver (dormant) · split/cutter
path (no seed triggered a cut) · HOLD_VLM · multipart-zip incomplete
(ZIP_PARTS_MARKER) · dup/supersede/quarantined-path-heal arms (no dup seeds) ·
429 backpressure storm (one 503 only) · folder-issues with content · autoscale (F3).

## §6 payment-surface list — review-status labels

- **O1** — **VERIFIED BY THIS RUN** (loud skip + no stamp on reset generation +
  next-gen countability; see above).
- **M5** (truthful stored fixable + labels) — verified live: S4's stored
  `fixable:false` reasons surfaced as the exact unfixable-only cell.
- **L2-adjacent** (corrupt sidecars degrade, hours reach sheets) — verified live on
  S3 (junk sidecars → DELIVERED → counted+stamped on gen-2).
- **N3/N4** ('' semantics) — indirectly exercised (O1 mutation used the ''-md5
  sentinel; the resume's `_bytes_changed` pre-filter passed it as designed).
- F6, F7 + r12 #1/#2 + G1 + H1, G5 + H3/H9a, C6-era tests, I1+I2, I7,
  fix_sync_from_v1, J5, J6, J2, J1, K1, L1, M4 — reviewed by loops ≤21, ship
  unchanged; **not directly re-verified by this run** (labeled honestly).
- OW satellite-camera: RULED 08-20 — schema approved, implemented AFTER the flip in
  its own session with its own adversarial review (`SATELLITE_KICKOFF_PROMPT.md`);
  pre-mapping OW deliveries recorded by the flip for re-map.

## Evidence

Scratchpad `e2e/` (session-scoped, kept): `audit/` (pre/post snapshots, leg
evidence, sheet generations gen-1..3, remote listing pre-purge, O1 pre-mutation
row), `logs/` (driver-1..4, daily-gen2-fail/resume, gen-3), `tg-capture.jsonl`
(all 12 intercepted sends), the scratch home (ledger.db with the full 60+-event
audit trail), staging tree, and the wrapper `e2e_driver.py`.

---

# Phase 2 (Adnaan 08-20: "do exercise these") — VERDICT UNCHANGED: GREEN-WITH-FINDINGS

Same session, same scratch home; every previously-not-run surface exercised except
the split cutter (below, with why). All prior oracles re-PASS on the extended
ledger (11 sessions: 7 DELIVERED / 1 DUPLICATE / 3 REJECTED; delivered-once,
hours-once, all-terminal, event-chain, batches=0 in home1).

- **VM gate + systemd**: HEAD synced to `~/hl-gamedata-continuous-test`; VM gate
  **`850 passed in 697.64s — ARMING GATE OK (floor 846)`** → BOTH host gates now
  independently re-verified. Read-only systemd inventory: only `hl-backup.*`
  installed (timer enabled/active); `hl-continuous.service` NOT installed;
  `hl-pipeline.timer` disabled; **`hl-recal-rebuild`/`hl-recal-watch` unit files
  no longer exist** → FLIP_RUNBOOK §6a's stop-the-units step is now a no-op
  (runbook drift, flag for the flip session). Balloon net 0.0 GiB. Nothing armed,
  nothing deployed.
- **Batch driver (rollback path)**: run in a fully local sandbox (both rclone
  remotes aliased to local dirs; its fixed `humynlabs/` prefix could only land in
  the sandbox), `PIPELINE_CONTINUOUS=False` set at the process boundary. Batch #1
  started/finished with summary `{"delivered": 1}` (the `batches` table the
  continuous driver never touches), seed DELIVERED (70.8s, force-rrd-sampled,
  real 171MB rrd), batch topline + its own TEST daily sheet + a folder-issues
  message WITH content captured (also covers the folder-issues surface).
- **HOLD_VLM**: broken-key leg → verbatim `VLM sweep unfinished — never pass
  unlooked-at (F5)`; after hold-timer rewind + real key, the expired hold was
  re-picked and the session DELIVERED. Full park→recover cycle proven. The same
  leg proved ladder exhaustion (invalid key, prev-key rung unarmed) never
  silently passes.
- **Multipart-zip**: parts .001/.002 staged → `zip payload incomplete/unreadable —
  retrying` + incomplete row `["zip parts incomplete"]` (ZIP_PARTS_MARKER); .003
  staged → byte-reassembly → INGESTED → DELIVERED; marker resolved.
- **Dup/heal/supersede arms** (all verbatim ledger events):
  same-player dup → `DUPLICATE same-player duplicate of <S1>`; cross-identity dup
  vs a shipped copy → `REJECTED cross-identity duplicate (kept earlier upload …)`;
  depth-3 bait → `QUARANTINED path depth 3 != 4`; clean re-upload →
  `re-registered: quarantined path healed to kamla/e2e_canary_op/canary-h…` →
  processed (genuine CNT_SHORT 50.0s reject). Supersede of REJECTED S4 (zip class,
  '' md5): `superseded: new md5 ; prev_md5=94df6b8a…` → download adjudication
  `zip-backfill: bytes CHANGED (94df… -> 9449…)` → **both payment stamps cleared**
  (accepted was stamped pre-supersede), fix_attempts reset, new generation
  re-judged on its own merits. Live N3/N4/M4 confirmation. Bonus F2 cross-check:
  the v2-sniffed replacement got rrd scaffolding at download → no QA_FAIL_UNMAPPED.
- **Sheets gen-4**: counted-once still holds (∅ counted overlap with gen-2); the
  O1-skipped S5 re-entered via accepted_due and stamped; S4's cleared slot sits in
  the pending cohort until a window whose hi passes its new ctime (F4 mechanics).
  (S5's accepted hours re-listed because this harness manually restored the same
  row; in production a reset slot is a genuinely new generation.)
- **429 storm**: 18 synthetic status-429 lines (tagged `e2e-injected-429-storm`,
  ~4/min ≫ the 1.0/min threshold) injected into the live pressure channel; no
  autoscale/rung reaction possible at the Mac pool floor (F3) and no
  destabilization. Real-quota behavior seen this session: one genuine 503 absorbed
  by backoff. The 429 step-down remains VM-only observable.
- **Split/cutter: NOT live-exercised — with evidence why.** A 45 s zero-input
  window (1351 rows AFK-blanked mid-clip) did NOT cut: the scanner timeline on
  this capture-tool footage is SYNTHETIC (packet-count disagreement), so static/
  AFK spans are deliberately not acted on (`bounds are not real PTS` advisory),
  and cuts key on VLM-detected NON-GAMEPLAY footage — the video itself was genuine
  gameplay. Constructing a real mid-clip pause requires a re-encode that desyncs
  frames.csv↔PTS (instant integrity rejects). The construct instead live-exercised
  FIX_ACTIONS_CONTEXT (1873 frames context-gated), FIX_LAGSHIFT_CSV and
  FIX_GATE_WINDOW, then delivered. Split machinery remains covered by the
  850-green suite (mid-split crash recovery tests) only.

Teardown re-verified: `_pipeline_test` purged (lsf rc=3), real `~/hl-pipeline`
byte-identical, repo tree clean, VM untouched beyond the side checkout.
Phase-2 VLM spend: ~35 additional gemini-3.7-flash calls (bounded).
