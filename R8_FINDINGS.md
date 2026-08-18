# r-loop 8 — collected results (run STOPPED by Adnaan, 2026-08-18 ~18:45 IST)

- Reviewed HEAD: `869910d` (the r-loop-7 fix set)
- Run `wf_f6553d64-18b`: 50 of 52 agents finished when stopped; the final aggregation never ran — this file reconstructs it from the journal.
- Finder lanes returned: driver-core, fix-validate, ops-tools, payment-split, regressions-r7, translator
- Lost at the stop: **tests-coverage** (the finder never returned; ~1.1 MB of partial transcript exists). Its findings, whatever they were, are NOT in this file.
- All 44 refuter votes for the 22 returned findings completed — the 2-vote discipline WAS fully applied to everything below.
- **22 raised → 21 CONFIRMED (0-or-1 refuters) · 1 KILLED (2/2 refuted)** — 4 confirmed blockers.

Duplicates to note: fix.py:557 was found independently by two lanes (same defect); recal_refix_reset.py:280/:284 are two views of the same seal problem; the two raw_int/OverflowError minors are the same defect; run.py:575 and continuous.py:880 are the same carve-out defect found in both drivers. Unique confirmed count is ~16.

---
## [BLOCKER] CONFIRMED — `pipeline/continuous.py:880` (lane regressions-r7, refuted 0/2)

**The new host-error carve-out re-runs a PARTIALLY APPLIED fix plan blind — FIX_RETRIM_HEAD cuts real gameplay again on every retry**

**CLAIM:** 869910d added `if out.get("kind") == "host"` in both drivers (continuous.py:880, run.py:575). It refunds the attempt (`led.update(sid, fix_attempts=row["fix_attempts"])`) and parks the row on FIX_QUEUED with `reasons_json` UNTOUCHED. plan_fixes is pure, so the next `_fix_one` builds the IDENTICAL step list and apply_fixes dispatches it from step 0 — including steps that already SUCCEEDED before the failing one. That is exactly what the line it was inserted above forbids: run.py:571 "partially-applied plan: never re-run it blind — go back through validation to re-derive from the current copy" and run.py:509 "re-running RETRIM/CUT on it would trim real gameplay twice (review finding #6)". FIX_RETRIM_HEAD is destructive and non-idempotent: tools/retrim_v2_session.py:36-66 probes the CURRENT video, `plan_cuts(kfs, info.duration_s, head_s=head_s...)`, then `ffmpeg -ss head_cut ... -c copy` and `shutil.move(tmp, out_dir/"video.mp4")` — a second run removes ANOTHER head_s seconds and re-slices frames.csv to match, so video and CSV stay consistent and QA cannot see it. Reachable host classes after the trim: FIX_SESSIONJSON_RECOMPUTE (fix.py:1097 `tmp.write_text(...)` → ENOSPC OSError) and V.probe (translator/video.py:30 `subprocess.check_output(..., timeout=600)` → TimeoutExpired) — the exact "disk-full or wedged-ffmpeg episode" the commit message names. PROVED with pipeline's own Config/Ledger/_fix_one, real plan_fixes+apply_fixes, only _dispatch stubbed (reasons CNT_EDGE_NONGAMEPLAY head cut_at_s=25 + STR_SENTINELS):
PLAN: [('FIX_SENTINELS', {}), ('FIX_RETRIM_HEAD', {'head_s': 25.0}), ('FIX_SESSIONJSON_RECOMPUTE', {})]
--- attempt 1 (OSError 28 on the LAST step) --- state= FIX_QUEUED fix_attempts= 0  dispatched: ['FIX_SENTINELS','FIX_RETRIM_HEAD','FIX_SESSIONJSON_RECOMPUTE']  video now: 275.0 s
--- attempt 2 --- dispatched: [...,'FIX_RETRIM_HEAD',...]  video now: 250.0 s
--- attempt 3 --- video now: 225.0 s
--- attempt 4 --- video now: 200.0 s
--- attempt 5 --- state= REVALIDATING fix_attempts= 1  video now: 175.0 s
Starting clip 300 s, head_cut 25 s: 125 s removed, 100 s of it REAL gameplay, and fix_attempts never exceeded 1. FIX_LAGSHIFT_CSV (plan_fixes:143, params carry the measured lag) doubles the same way. Before 869910d every apply_fixes error went to REVALIDATING, which re-derives the reasons from the CURRENT copy — the trimmed head is gone, CNT_EDGE_NONGAMEPLAY does not recur, no second trim.

**SCENARIO:** Kamla session, 300 s clip, VLM finds a 25 s menu intro → CNT_EDGE_NONGAMEPLAY(head, cut_at_s=25) + STR_SENTINELS → plan [FIX_SENTINELS, FIX_RETRIM_HEAD, FIX_SESSIONJSON_RECOMPUTE]. A parallel runner fills the disk (or the host thrashes and ffprobe blows its 600 s timeout) in the seconds between the retrim finishing and session.json being written. OSError/TimeoutExpired → kind=host → attempt refunded, row back on FIX_QUEUED, cooldown CONT_RUNNER_CRASH_RETRY_MIN=5 min (config.py:186). The V dispatcher re-picks FIX_QUEUED at priority 1 (continuous.py:595) every 5 min for as long as the host condition lasts, and each pass trims another 25 s off the SAME video. One hour of disk pressure = 12 passes = 300 s gone: the clip is delivered with 5 minutes of paid gameplay silently deleted, or drops under MIN_CLIP_S=70 and is REJECTED CNT_SHORT (blocking, unfixable) — a wrongful reject and an unpaid player, with nothing in frames.csv/video ever inconsistent for QA to catch.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Park on FIX_QUEUED only when NOTHING was applied. In both drivers: `if out.get("kind") == "host" and not any(a["ok"] for a in out["applied"]):` → refund + FIX_QUEUED + cooldown; otherwise refund the attempt but route to REVALIDATING so the next plan is re-derived from the half-fixed copy (the pre-869910d invariant), e.g. `led.update(sid, fix_attempts=row["fix_attempts"]); led.set_state(sid, "REVALIDATING", f"host-level fix failure after {n} applied step(s) — re-deriving")`.

- refuter (exec): refuted=False confidence=high
  - evidence: LINE NUMBERS EXACT — `grep -n 'out.get("kind") == "host"'`:
  pipeline/continuous.py:880
  pipeline/run.py:575
Both added by HEAD 869910d (`git show 869910d -- pipeline/run.py` → hunk `@@ -572,6 +572,19 @@` adding `+ if out.get("kind") == "host":`). The comment it was inserted under is real: run.py:570-571 "partially-applied plan: never re-run it blind — go back through validation to re-derive from the current copy", and run.py:509-511 "re-running RETRIM/CUT on it would trim real gameplay twice (review finding #6)".

PLAN IS IDENTICAL EVERY RETRY (read, not assumed):
- continuous.py:830 `reaso
- refuter (harm): refuted=False confidence=high
  - evidence: All cited lines verified verbatim: continuous.py:880 `if out.get("kind") == "host":`; run.py:575 same; run.py:571 "…never re-run it blind — go back through validation to re-derive from the current copy"; run.py:509 "…re-running RETRIM/CUT on it would trim real gameplay twice (review finding #6)"; fix.py:1097 `tmp = work / "session.json.tmp"`; translator/video.py:30 `subprocess.check_output(cmd, text=True, timeout=600)`; continuous.py:595 FIX_QUEUED is priority 1 in `_pick_v`; config.py:186 CONT_RUNNER_CRASH_RETRY_MIN = 5.0; config.py:31 MIN_CLIP_S = 70.0.

CONTROL-FLOW TRACE (Lens B), every li

---
## [BLOCKER] CONFIRMED — `pipeline/fix.py:730` (lane regressions-r7, refuted 0/2)

**retranslate's new "implausible head offset" guard makes FIX_RETRANSLATE impossible for every split child that starts later into the parent than its own length**

**CLAIM:** 869910d added to retranslate_from_sidecars: `if head_s > info.duration_s: raise FixFailed("implausible head offset ...")` (fix.py:730). head_s is `(session.json created_at_utc − raw metadata started_at_utc)`, i.e. the clip's OFFSET INTO THE RAW RECORDING — it has no relation to the clip's own duration. For a split child that offset is by construction the segment's start: cutter.py:191 `seg_created = created + timedelta(microseconds=src_pts[i0])`, and cutter.py:209-211 copies the parent's whole raw/ dir into every child precisely so "a segment can still take the RETRANSLATE path — its created_at encodes the source offset". So `head_s = 5 s trim + segment_t0`, and any second-or-later segment whose start offset exceeds its own duration now raises. The downstream math was always correct for that case: translator/trim.py:108 rebase_events(events, head_cut_s, new_duration_s) keeps `head_us <= t < head_us+end_us` and rebases. PROVED (fix.V.probe/frame_pts stubbed, everything else real):
  head_s the code derives = 605.0 s   clip duration = 120.0 s
  retranslate -> FixFailed: implausible head offset 605.0s for a 120.0s clip ...
  rebase_events(head=605, dur=120) keeps: [{'t': 10000000, 'type': 'key', 'key': 'w', 'action': 'down'}]  <-- correct, rebased to t=10 s
FixFailed is not in the host tuple (fix.py:398) so it is classified "session" and CHARGES the attempt. End-to-end through the real driver, real ledger, real plan_fixes/apply_fixes, child `<parent>-p2` (keep [720,1200], head 725 s, duration 480 s), reason SYN_TS_NOT_PTS:
  has_raw_sidecars(child) = True ; plan: ['FIX_RETRANSLATE','FIX_SESSIONJSON_RECOMPUTE']
  attempt 1 -> REVALIDATING | attempts 1 | detail: fix failed: FIX_RETRANSLATE: FixFailed: implausible head offset 725.0s for a 480.0s clip
  attempt 2 -> REVALIDATING | attempts 2 | same
  attempt 3 -> REJECTED | detail: fix retries exhausted (R2)
Before 869910d there was no such check and the retranslate ran normally.

**SCENARIO:** A 20-minute Kamla recording with a 2-minute AFK block at 10:00–12:00 is split into p1=[0,600] and p2=[720,1200]. p2 is a 480 s segment with head_s=725. p2 validates and raises any retranslate-class code — SYN_TS_NOT_PTS, SYN_DRIFT, SYN_LAG_CONST, STR_HEADER_BAD, STR_SENTINELS, STR_ROWS_MISMATCH, STR_TS_NONMONO, QA_FAIL_UNMAPPED, INP_KEYS_NO_ACTION or INP_FANOUT — all of which route to FIX_RETRANSLATE with no CSV fallback once has_raw is true (plan_fixes:135-146,214-220). Both attempts die on the guard and the child is REJECTED "fix retries exhausted (R2)", after which _finalize_reject → deliver.finalize_rejected wipes the media. Under r-loop 4's own measurement (cutter.py:186 "70% of children FAILing at 1kHz mouse polling") the sync family alone hits children constantly, so this destroys good split segments systematically — 8 minutes of deliverable, already-cut footage per occurrence, unpaid, surfaced to ops only as the bare fix-failed marker because every stored reason is fixable.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Bound head_s against the RAW recording, not the clip: keep the guard only when the offset exceeds what the sidecar can possibly cover, e.g. `last_t = max((e["t"] for e in load_events(raw/"inputs.jsonl")), default=0)/1e6` and fail only if `head_s > last_t` (or if rebase_events returns zero events for a session whose raw file is non-empty). A split child legitimately has head_s >> duration_s, so the clip duration must not appear in this test at all.

- refuter (exec): refuted=False confidence=high
  - evidence: REPRODUCED with real code (no stubs of V.probe/frame_pts — a real 200 s H.264 file made with ffmpeg, the real pipeline.cutter, the real fix.plan_fixes/apply_fixes/retranslate_from_sidecars). Probe dir: /private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/probe

1) The guard exists at HEAD and is new in 869910d.
/Users/adnaan/Documents/hl-projects/hl-gamedata/pipeline/fix.py:730-733
    if head_s > info.duration_s:
        raise FixFailed(
            f"implausible head offset {head_s:.1f}s for a "
            f"{info.duration_s
- refuter (harm): refuted=False confidence=high
  - evidence: REPRO 1 — real cutter + real ffmpeg + real fix code, NO stubs (probe dir: /private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad).
Built a real 300 s parent v2 session (raw started_at_utc 10:00:00Z, created_at_utc 10:00:05Z = the implicit 5 s trim, raw/inputs.jsonl + raw/metadata.json), then ran pipeline.cutter.cut_segments(parent, keep=[(0,100),(150,300)]):
  ...-p1 created_at_utc= 2026-08-18T10:00:05.000000Z duration_s= 100.0 raw dir? True
  ...-p2 created_at_utc= 2026-08-18T10:02:35.000000Z duration_s= 150.0 raw dir? True
i.e.

---
## [BLOCKER] CONFIRMED — `pipeline/run.py:902` (lane payment-split, refuted 0/2)

**Daily send REGENERATES the sheet after the stamps land — one partial stamp or one interruption empties the payment sheet of record, permanently**

**CLAIM:** run.py:877 builds the sheet, 902 `mark_uploads_reported(..., sids=counted)` and 908 `mark_accepted_reported(ledger, accepted)` stamp it, 911 writes the anchor, 913 touches `reports/<day>/.sent`, 915 sends the CSV. There is NO durable record of what the sheet counted, so any interruption between 902 and 913 leaves the marker absent, the next tick calls write_payment_sheet again, and build_sheet_rows now EXCLUDES every root it already stamped (reports.py:483/492/494) — the regenerated file overwrites payment-<day>.csv (reports.py:639 `csv_path.open("w")`) and THAT is what gets sent. The interruption does not need to be a kill: continuous.py:1432-1435 `_duty()` catches `Exception`, alerts, and re-arms `self._next_daily = now + C.CONT_DAILY_RETRY_S` (600 s), so a single `sqlite3.OperationalError: database is locked` inside the per-row `ledger.update` loop (ledger.py:161, three writer threads share the file behind a 10 s busy_timeout) or an OSError from `anchor.write_text` on a full disk produces exactly this. RAN IT (real `run.send_daily_report_if_due`, telegram stubbed, scratch ledger in /tmp/plane7). (a) lock error on the 3rd stamp: first attempt wrote the correct sheet `alice 1.0/0.94, bob 1.0/0.94, carol 1.0/0.94, dave 1.0/0.94`, stamped S0+S1 only; the 5-min retry SENT `alice 0.0/0.94, bob 0.0/0.94, carol 1.0/0.94, dave 1.0/0.94` — 2.0 uploaded hours silently gone. (b) kill right after the accepted stamp: first attempt's sheet was `alice 1.0/0.94, bob 1.0/0.92, carol 1.0/0.0`; the resend SENT a HEADER-ONLY CSV (`telegram: ('doc', ...)` on the empty file) and the 2026-08-19 and 2026-08-20 sheets were empty too — only carol's later-delivered 0.89 h ever came back, via accepted_due. recal_regen_sheets.py:14-17 already solves precisely this (`write .regen-v2-counted.json ... BEFORE any side effect; a re-run after a crash reuses it verbatim — the sheet can never be regenerated post-stamp (the empty-sheet blocker)`); the production daily path never got that record.

**SCENARIO:** 14:00 IST daily send on the VM. `mark_uploads_reported` commits once per root (ledger.py:164) while the D/V/U writer threads are mid-batch; one commit exceeds the 10 s busy_timeout and raises `database is locked` after 40 of 120 roots are stamped. `_duty` logs `housekeeping duty 'daily payment report' failed` and continues. Ten minutes later the retry regenerates: those 40 roots are stamped so they contribute 0 uploaded hours, the good CSV on disk is overwritten by the short one, the short one is sent as the day's payment sheet, `.sent` is written and the anchor advances. Those roots can never be counted again — `in_window` and `late` both test `not root["uploaded_reported_at"]`, and `accepted_due` only ever returns accepted hours. The operators paid from that sheet are short those uploaded hours with nothing in the record saying so.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Give the daily send the same durable resume record the flip tool has: after build_sheet_rows and BEFORE any stamp, write `reports/<day>/.daily-counted.json` = {counted, accepted} atomically; at the top of send_daily_report_if_due, if that file exists for today, skip regeneration entirely, re-send the CSV already on disk and stamp from the record. That keeps the r-loop-5 #39 ordering (stamps before anchor) while making a resend byte-identical instead of post-stamp-empty.

- refuter (exec): refuted=False confidence=high
  - evidence: EVERY line the claim cites is real, and BOTH quoted runs reproduce exactly.

CODE VERIFIED (HEAD 869910d)
- /Users/adnaan/Documents/hl-projects/hl-gamedata/pipeline/run.py:877 `csv_path, _md = reports.write_payment_sheet(...)`; :902 `stamped = reports.mark_uploads_reported(ledger, lo, hi, sids=counted)`; :908 `acc_stamped = reports.mark_accepted_reported(ledger, accepted)`; :911 `anchor.write_text(hi)`; :913 `marker.touch()`; :915 `telegram.send_document(cfg, csv_path, caption="payment sheet")`. Nothing between 877 and 915 persists `counted`/`accepted` — grep for a counted record in pipeline/ 
- refuter (harm): refuted=False confidence=high
  - evidence: CODE (real line numbers, current HEAD 869910d):

pipeline/run.py — the whole unprotected sequence:
  877 csv_path, _md = reports.write_payment_sheet(cfg, ledger, now_ist,
  879                                              counted_out=counted,
  890     telegram.send_message(cfg, msg)          # message goes out FIRST
  902 stamped = reports.mark_uploads_reported(ledger, lo, hi, sids=counted)
  908 acc_stamped = reports.mark_accepted_reported(ledger, accepted)
  911 anchor.write_text(hi)          # next report's window starts here
  913 marker.touch()
  915     telegram.send_document(cfg, csv_p

---
## [BLOCKER] CONFIRMED — `pipeline/run.py:575` (lane fix-validate, refuted 0/2)

**r-loop-7 host carve-out re-applies the STALE plan to a half-fixed copy — FIX_RETRIM_HEAD trims the head twice, destroying delivered gameplay and paid seconds**

**CLAIM:** Before r-loop 7 every `out["error"]` went to REVALIDATING, guarded by the comment still sitting at run.py:570-573: "partially-applied plan: never re-run it blind — go back through validation to re-derive from the current copy… re-running RETRIM/CUT on it would trim real gameplay twice (review finding #6)" (also run.py:509-514). The new branch (run.py:575-587, continuous.py:880-899) does the opposite for kind=="host": it refunds `fix_attempts`, sets the row back to FIX_QUEUED and touches NOTHING else — `reasons_json` is unchanged, so the next pick re-runs `plan_fixes` on the identical reasons and re-dispatches the identical steps onto the already-mutated working copy. FIX_RETRIM_HEAD (fix.py:292/350/471 -> tools/retrim_v2_session.retrim) is destructive and NOT idempotent, and the only step after it is FIX_SESSIONJSON_RECOMPUTE, whose `V.probe` (subprocess, timeout=600) and `tmp.write_text`/`tmp.replace` raise exactly the OSError/TimeoutExpired that fix.py:399-401 classifies as "host". retrim() itself has the same window after `shutil.move(tmp, video.mp4)` (its frames.csv/session.json writes). Nothing restores the work dir: `_discard_split_artifacts` (run.py:361) only removes rowless segment dirs and the manifest. PROVEN on a real 20s/600-frame clip, plan `['FIX_RETRIM_HEAD','FIX_SESSIONJSON_RECOMPUTE']` with head_s=3.0, fix_sessionjson_recompute monkeypatched to raise OSError(28) — exactly the ENOSPC the r-loop-7 commit says it is defending against:
  before:          20.0s frames 600 created 2026-08-12T08:33:31.000000Z
  attempt1 kind = host  error = FIX_SESSIONJSON_RECOMPUTE: OSError: [Errno 28] No space left
   after attempt1: 17.0s frames 510 created 2026-08-12T08:33:34.000000Z rows 510
  re-planned identically: True
  attempt2 error = None
   after attempt2: 14.0s frames 420 created 2026-08-12T08:33:37.000000Z rows 420
Attempt 2 returns error None, so the driver moves the row to REVALIDATING and the now-clean clip validates bin 1 and ships. Because the attempt is refunded every time, this repeats without bound while the host condition persists.

**SCENARIO:** The 08-18 rebuild dump (rebuild-sessions-2026-08-18.csv, 1396 rows) carries 180 CNT_EDGE_NONGAMEPLAY reasons; a head-edge one plans FIX_RETRIM_HEAD with cut_at_s = end-of-intro + 0.5s (a menu/loading intro, tens of seconds). The disk fills while the retrim is writing its video-sized stream copy — the canonical event the r-loop-7 blocker cites ("one disk-full or wedged-ffmpeg episode"). ffmpeg finishes, the video is replaced, and the small session.json write (or the ffprobe inside fix_sessionjson_recompute) hits ENOSPC -> OSError -> kind "host". The attempt is refunded and the row is parked FIX_QUEUED. CONT_RUNNER_CRASH_RETRY_MIN later the V dispatcher re-picks it (FIX_QUEUED is priority 1), re-plans the identical FIX_RETRIM_HEAD, and cuts another cut_at_s seconds of GENUINE gameplay off the front. The second pass succeeds, revalidation sees a clean head, and the session is DELIVERED short by that much footage, with created_at_utc advanced twice. ledger.py:5 states `duration_delivered_s` summed per player/game is the paid number, and deliver.py:208 writes it from the shipped clip — the player is paid for the seconds the pipeline deleted, and the client receives a truncated recording. If the host condition recurs across picks, each iteration eats another cut_at_s until the clip falls under MIN_CLIP_S and the session is rejected CNT_SHORT (blocking, unfixable) — the pipeline destroys the footage and then blames the player for a short clip.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** On the host path, do not leave the row pointing at a plan that has already been partially applied. Either (a) send it to REVALIDATING like every other error while separately refunding the attempt (`ledger.update(sid, fix_attempts=row['fix_attempts'])` then `set_state(sid,'REVALIDATING',...)`), which keeps review-finding-#6's invariant and still costs no fix budget, or (b) keep FIX_QUEUED only when NOTHING in the plan ran (`not any(e['ok'] for e in out['applied'])`) and fall through to REVALIDATING otherwise. Same edit in pipeline/run.py:575 and pipeline/continuous.py:880.

- refuter (exec): refuted=False confidence=high
  - evidence: CODE (real line numbers, HEAD = 869910d "r-loop 7"):

pipeline/run.py:569-590 — the new branch sits directly UNDER the comment it contradicts:
  569  if out["error"]:
  570      # partially-applied plan: never re-run it blind — go back
  571      # through validation to re-derive from the current copy.
  572      # Any cut artifacts from THIS plan are rescinded with it
  573      # (review-r4 #5/#19)
  574      _discard_split_artifacts(cfg, ledger, sid)
  575      if out.get("kind") == "host":
  581          ledger.update(sid, fix_attempts=row["fix_attempts"])
  582          ledger.set_state(s
- refuter (harm): refuted=False confidence=high
  - evidence: CODE (verified line-by-line at HEAD 869910d "r-loop 7"):
- pipeline/run.py:569-590 — `if out["error"]:` still carries the guard comment at 570-573 ("partially-applied plan: never re-run it blind — go back through validation to re-derive from the current copy"), and 575-587 then does the opposite for host: `ledger.update(sid, fix_attempts=row["fix_attempts"])` + `ledger.set_state(sid, "FIX_QUEUED", …)` + `continue`. `reasons_json` untouched.
- pipeline/continuous.py:880-897 — identical carve-out plus `self.cool.set(sid, C.CONT_RUNNER_CRASH_RETRY_MIN * 60)`.
- pipeline/fix.py:399-401 — `kind = "

---
## [MAJOR] CONFIRMED — `pipeline/continuous.py:1175` (lane driver-core, refuted 0/2)

**The digest's stuck list is structurally blind to the two host-error retry loops (V lane and r-loop 7's new fix-lane park)**

**CLAIM:** `_stuck_lines` finds ordinary stuck sessions with `SELECT session_id, state, updated_at FROM sessions WHERE state NOT IN (...) AND updated_at<?` (continuous.py:1174-1178). Two infinite retry loops re-stamp `updated_at` on every retry, so that predicate can never fire:

- V lane (continuous.py:762-772): `_validate_one` does `led.set_state(sid, "VALIDATING")` at line 745 on EVERY attempt, then on `kind == "host"` sets a 5-min cooldown and returns None with the row left VALIDATING. `_pick_v` priority 2 re-claims VALIDATING (line 892-ish of the pick list), so it loops forever at CONT_RUNNER_CRASH_RETRY_MIN.
- Fix lane (continuous.py:880-899, NEW in 869910d): the host carve-out does `led.update(sid, fix_attempts=...)` + `led.set_state(sid, "FIX_QUEUED", ...)` every 5 minutes, on top of the `set_state(FIXING)` at line 838. `Ledger.set_state`/`Ledger.update` both write `updated_at=_now()` (ledger.py:141, 163).

Probe A (real code, `cont._POOL_DISABLED=True`, worker returns `{"kind":"host"}`):
```
attempt 1: returned=None state=VALIDATING updated_at ... -> ...
attempt 2: returned=None state=VALIDATING
attempt 3: returned=None state=VALIDATING
events written: 4
```
Probe B (a 20 h aged ledger: 239 VALIDATING retries and 239 FIXING->FIX_QUEUED parks, last one 5 min ago, plus three controls):
```
stuck lines : ['v-hung (VALIDATING 20.0h)', 'upl (PACKAGED 20.0h)', 'hold (HOLD_VLM 19.5h)']
stuck total : 3
```
The HUNG validation, the failing delivery and the held session are all caught — the two host-retry loops are not. That is exactly the defect the two events-stint subqueries in this same function (continuous.py:1179-1206) were written for: r-loop 1 for HOLD_VLM, r-loop 4 for READY/PACKAGED/UPLOADED. Neither covers a self-refreshing VALIDATING or FIX_QUEUED row.

Probe C (12 wedged sessions, real `_send_digest` with telegram captured):
```
📡 digest 17:42 · last 3.0h
window: 0 delivered (+0.0 h) · 0 rejected
backlog: 0 undownloaded · 6 in-flight · 6 fix · 0 hold · 0 incomplete
pool: 0/8 active
stuck_lines(): ([], 0)
_local_count : 12 of cap 40
_pick_v picks: fh0 -> state FIX_QUEUED
```

**SCENARIO:** At the flip, one host-level condition (disk full on the work volume, ENOMEM, or an ffmpeg that hangs rather than exits — `subprocess.TimeoutExpired` is classified host in fix.py:399-401 and cutter/retrim use `timeout=1800`) hits sessions as they pass through V and the fix lane. Before r-loop 7 the fix lane burned its 2 attempts and terminated in minutes; now every affected session ping-pongs FIX_QUEUED<->FIXING (or re-enters VALIDATING) every 5 minutes forever, each one holding its media and one of the 40 `CONT_MEDIA_CAP_SESSIONS` slots, and each `TimeoutExpired` case holding a gate slot for a full 30 minutes per attempt. The 3-h digest — which the code itself calls "the ONLY ops surface there is" (continuous.py:1195, 1292) — reports them as healthy `6 in-flight · 6 fix` with `stuck: (nothing)` and `stuck_total 0`, and the pool line reads `0/8 active`, i.e. idle. Intake silently throttles and eventually stops (cap reached), and the one surface built to name a wedged session says nothing about any of them.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Age these two loops from the immutable events audit, exactly as the HOLD_VLM and READY/PACKAGED/UPLOADED stints already are: add a stint query for `state IN ('VALIDATING','FIX_QUEUED','FIXING','REVALIDATING')` anchored at `MIN(e.ts)` over those to_states since the last event outside the set, and merge it into `stuck` before the sort (the merged list is already re-sorted at continuous.py:1247). Cheap alternative: have the two host carve-outs record the stint start (e.g. keep the first host-failure timestamp per sid on the driver and surface it) — but the events-based version survives restarts, which the in-memory one does not.

- refuter (exec): refuted=False confidence=high
  - evidence: All quoted code and all three probes reproduce verbatim on HEAD (869910d), file /Users/adnaan/Documents/hl-projects/hl-gamedata/pipeline/continuous.py.

CODE (real line numbers, cited ones all correct):
- continuous.py:1174-1178 — `rows = ... "SELECT session_id, state, updated_at FROM sessions WHERE state NOT IN ('DELIVERED','REJECTED','SPLIT','DUPLICATE','QUARANTINED','DISCOVERED','HOLD_VLM') AND updated_at<? ORDER BY updated_at"`. VALIDATING / FIX_QUEUED / FIXING are inside the net, so they are caught ONLY if updated_at is stale.
- continuous.py:762-772 — V host branch: cooldown + alert + `r
- refuter (harm): refuted=False confidence=high
  - evidence: CODE (HEAD 869910d, /Users/adnaan/Documents/hl-projects/hl-gamedata):

1. The stuck predicate — pipeline/continuous.py:1174-1178 (in `_stuck_lines`, def at :1157):
   "SELECT session_id, state, updated_at FROM sessions WHERE state NOT IN ('DELIVERED','REJECTED','SPLIT','DUPLICATE','QUARANTINED','DISCOVERED','HOLD_VLM') AND updated_at<?"
   VALIDATING and FIX_QUEUED are inside that population, so they are caught ONLY via `updated_at < now-CONT_STUCK_H` (config.py:183 → 6.0 h).

2. Both retry loops re-stamp `updated_at` every CONT_RUNNER_CRASH_RETRY_MIN = 5.0 min (config.py:186):
   - V lane: `l

---
## [MAJOR] KILLED — `pipeline/continuous.py:369` (lane driver-core, refuted 2/2)

**The media cap still scores an EMPTY QUARANTINED work dir as media — the r-loop-7 "an empty dir is not media" rule was applied only to the DISCOVERED half**

**CLAIM:** r-loop 7 introduced `_held_discovered()` (continuous.py:389-424) whose docstring says it is the "ONE definition of 'this row holds media', shared by the cap count and the cap carve-out", because "An EMPTY dir is not media". Three lines above it, the QUARANTINED branch of the same function still uses a bare existence test:
```python
for r in led.by_state("QUARANTINED"):
    sid = r["session_id"]
    if (self.cfg.work / sid).exists() or \
            (self.cfg.work / f"{sid}-analysis").exists():
        n += 1
```
(continuous.py:367-371; `_cap_pressure_alert` repeats the same `.exists()` test at continuous.py:1381-1384.)

An empty QUARANTINED work dir is not hypothetical — `ingest.download` produces one deterministically. On the third md5 verify failure it wipes and RE-CREATES the dir before raising the quarantine error (ingest.py:779-786):
```python
shutil.rmtree(dst, ignore_errors=True)
dst.mkdir(parents=True, exist_ok=True)
if attempt == 2:
    raise DownloadError("video md5 mismatch after 3 attempts", kind="quarantine")
```
Probe (real `_download_one` with `ingest.run_rclone` faked to deliver a file whose md5 differs from the one recorded at scan time):
```
state           : QUARANTINED
work dir exists : True
work dir entries: []
_local_count    : 1  (this row holds 0 bytes)
total bytes under work/: 0
[cap-alert] media cap reached: 1/1 local sessions, so NEW downloads are paused (mostly QUARANTINED: 1)
```
The only exit is `_sweep_terminal_work`'s 48-h reclaim (run.py:1522-1531, `CONT_QUARANTINE_RECLAIM_H = 48`).

**SCENARIO:** An operator re-uploads a corrected video into an existing session folder (the folder-issues chase list actively asks them to fix folders, and run.py:1556-1560 records that a session "routinely waits >12h between discovery and its first attempt"). The Drive-side md5 recorded at scan time no longer matches what rclone fetches, so ingest.download retries the full multi-GB transfer three times, wipes the dir and quarantines. The row now occupies one of the 40 `CONT_MEDIA_CAP_SESSIONS` intake slots for the next 48 hours while holding zero bytes, and `_cap_pressure_alert` blames "mostly QUARANTINED: N" with the disk empty — the same "internally contradictory, pointing at the wrong cause" shape the r-loop-4 comment at continuous.py:1370-1376 says that tally exists to prevent. On a program that must move ~1400 sessions through a 40-slot cap by Aug 24, every such row is a permanent 2.5% cut to intake concurrency for two days; enough of them (the same class of garbage/crash quarantines also lands here via ingest.py:794-797 and continuous.py:530-534) stops intake outright with a bare disk, which is the exact blocker r-loop 3/4/6/7 each fixed one instance of.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Give QUARANTINED the same predicate `_held_discovered` uses. Factor the "does this sid hold bytes" test out of `_held_discovered` into a helper that takes a sid (checking `any(p.iterdir())` on `work/<sid>` and `work/<sid>-analysis`) and call it from continuous.py:367-371 and from the `_cap_pressure_alert` tally at continuous.py:1381-1384, so all three sites share one definition rather than two. (Separately: ingest.py:779-786 should not leave a re-created empty dir behind on the terminal attempt.)

- refuter (exec): refuted=True confidence=medium
  - evidence: PROBE 1 — the claim's quoted output DOES reproduce (real `ContinuousDriver._download_one` + real `ingest.download`; only `ingest.run_rclone` and `telegram.send_message` faked; nothing in the repo modified, probe under /tmp/qprobe):

  rclone attempts : 3
  state           : QUARANTINED
  work dir exists : True
  work dir entries: []
  bytes under work/: 0
  _local_count    : 1
  _held_discovered: set()
  _pick_download  : None
  [telegram] media cap reached: 1/1 local sessions, so NEW downloads are paused (mostly QUARANTINED: 1)

So the asymmetry is real: pipeline/continuous.py:367-371 counts 
- refuter (harm): refuted=True confidence=high
  - evidence: All cited line numbers are accurate. `_local_count`'s QUARANTINED branch really is a bare `.exists()` test (pipeline/continuous.py:367-371), three lines above `_held_discovered` (389-424) whose docstring says "An EMPTY dir is not media", and `_cap_pressure_alert` repeats it (1378-1381). `ingest.download` really does `shutil.rmtree(dst); dst.mkdir(...)` before raising the quarantine (pipeline/ingest.py:779-786). I reproduced the claim's probe exactly — real `ingest.download`, real `ingest.scan`, real `ContinuousDriver._local_count`, fake `run_rclone` delivering a video whose md5 differs from th

---
## [MAJOR] CONFIRMED — `pipeline/fix.py:557` (lane regressions-r7, refuted 0/2)

**Gate-record propagation filters per fixlog ENTRY, not per window — with two frozen windows both segments still inherit the whole destroyed inventory**

**CLAIM:** 869910d's fix filters `mine = [e for e in gate_entries if _gate_entry_touches(e, seg["t0"], seg["t1"])]` (fix.py:557) and _gate_entry_touches (fix.py:567) returns True if ANY window in that entry overlaps the segment. But plan_fixes emits ONE FIX_GATE_WINDOW step carrying ALL windows (`steps.append(("FIX_GATE_WINDOW", {"windows": sorted(gate_windows)}))`, fix.py:266 and 349), and gate.gate_windows returns ONE AGGREGATE inventory for all of them (`"destroyed": {"actions": sorted(destroyed_actions), "key_frames": destroyed_key_frames}`, gate.py:88-90). So whenever the plan holds two frozen windows that land in different segments, every one of those segments still inherits the FULL cross-segment inventory — the precise mis-credit the commit says it removed. PROVED with real plan_fixes + real _propagate_gate_record + real validate._gate_destroyed, production-shaped note:
  plan steps: [('FIX_GATE_WINDOW', {'windows': [(40.0,42.0),(300.0,302.0)]}), ('FIX_CUT_SEGMENTS', {'cut': [(100.0,200.0)]})]
  P-p1 (t0=0,t1=100) _gate_destroyed -> {'actions': ['interact','sprint'], 'key_frames': 65}
  P-p2 (t0=200,t1=400) _gate_destroyed -> {'actions': ['interact','sprint'], 'key_frames': 65}
validate.py:609 needs only a truthy count — `gated_keys = int((aux.get("gate_destroyed") or {}).get("key_frames") or 0); if gated_keys: advisories.append(...)` — so ANY inherited key_frames suppresses INP_KEYS_MISSING outright; validate.py:581-590 does the same for CNT_ACTIONS_FEW via the restored-actions union. (The r-loop-7 unit test hand-builds `"note": {"actions": [...], "key_frames": 65}`, which is not the shape gate.py produces — _gate_destroyed reads note["destroyed"] — so the multi-window case was never exercised.)

**SCENARIO:** A 12-minute Outer Wilds session raises two INP_FROZEN_ACTIONS (a 2 s Observatory-terminal freeze at 40 s and another at 300 s) plus CNT_AFK with cut [100,200]. One FIX_GATE_WINDOW blanks both, destroying 65 key frames and 'interact'/'sprint' — all of them inside p1's rows. The cut yields p1=[0,100] and p2=[200,400]. p2's own capture genuinely lost the keyboard hook (zero key frames) and has 2 distinct actions. p2 inherits the parent's aggregate record, so validate downgrades BOTH INP_KEYS_MISSING and CNT_ACTIONS_FEW to advisories that state as fact that this pipeline gated 65 key frames out of p2 — it gated none — and p2 ships to Odyssey with an empty input_keys column, violating the locked "no missing input modality" and MIN_DISTINCT_ACTIONS delivery bars, with the operator advisory actively pointing away from the real capture failure.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Filter per WINDOW and re-derive the inventory per segment instead of per entry: have gate.gate_windows record `destroyed` per window (it already computes the blanked row set per window) and, in _propagate_gate_record, hand each segment a synthetic entry containing only the windows that overlap it and only their share of actions/key_frames. Failing that, propagate the entry only when EVERY window in it overlaps the segment, and log the dropped remainder rather than crediting it twice.

- refuter (exec): refuted=False confidence=high
  - evidence: REPRODUCED END-TO-END with real code at HEAD 869910d (repo untouched; scripts in /private/tmp/claude-501/.../scratchpad/repro/r.py and r2.py).

STEP 1 — real pipeline.fix.plan_fixes(2x INP_FROZEN_ACTIONS + CNT_AFK cut, game="outer_wilds", has_raw=False):
  PLAN STEPS: [('FIX_GATE_WINDOW', {'windows': [(40.0, 42.0), (300.0, 302.0)]}), ('FIX_CUT_SEGMENTS', {'cut': [(100.0, 200.0)]})]
Confirms ONE gate step carrying BOTH windows. Source: fix.py:161-162 `elif code == "INP_FROZEN_ACTIONS": gate_windows.append((p["t0"], p["t1"]))` accumulates into one list; fix.py:267-268 (cut exit) and fix.py:347-3
- refuter (harm): refuted=False confidence=high
  - evidence: All probing done in /tmp; no repo file touched.

1) MECHANISM (read, real line numbers)
- pipeline/fix.py:268 / 297 / 348 — every emission is ONE step carrying ALL windows: `steps.append(("FIX_GATE_WINDOW", {"windows": sorted(gate_windows)}))`.
- pipeline/gate.py:91-92 — one AGGREGATE payload for all of them: `"destroyed": {"actions": sorted(destroyed_actions), "key_frames": destroyed_key_frames}`.
- pipeline/fix.py:557 — the r-loop-7 filter is per ENTRY: `mine = [e for e in gate_entries if _gate_entry_touches(e, seg.get("t0"), seg.get("t1"))]`; fix.py:567-583 `_gate_entry_touches` returns Tru

---
## [MAJOR] CONFIRMED — `pipeline/fix.py:557` (lane fix-validate, refuted 0/2)

**Gate-record propagation to split children is per-ENTRY, not per-WINDOW — every touched segment inherits the sibling's destroyed inventory and ships under the delivery bars**

**CLAIM:** plan_fixes emits every gate window as ONE step: `steps.append(("FIX_GATE_WINDOW", {"windows": sorted(gate_windows)}))` (fix.py:268 and 348), so apply_fixes records ONE fixlog entry whose `note["destroyed"]` is the union over all windows (gate.py:60-71 accumulates `destroyed_actions`/`destroyed_key_frames` across the whole `blank` set). `_propagate_gate_record` (fix.py:557) filters at ENTRY granularity — `mine = [e for e in gate_entries if _gate_entry_touches(e, seg['t0'], seg['t1'])]` — and `_gate_entry_touches` (fix.py:567) returns True if ANY window in the entry overlaps the segment. So every segment holding at least one window inherits the FULL union. Proven end to end on a synthetic 900-row parent with windows (3.0,4.0) and (24.0,25.0):
  gate note destroyed = {'actions': ['interact', 'jump', 'move_forward'], 'key_frames': 62}
  P-p1 gate_destroyed -> {'actions': ['interact','jump','move_forward'], 'key_frames': 62}
  P-p2 gate_destroyed -> {'actions': ['interact','jump','move_forward'], 'key_frames': 62}
('interact' was blanked only inside P-p1's rows, 'jump' only inside P-p2's.) Feeding that record to map_reasons for a segment whose OWN inventory is distinct_actions=2 / key_frames=0:
  bin = 1  hold = False   blocking reasons = []
  ADVISORY: only 2 distinct actions in the delivered rows, but 4 before this pipeline gated ['interact','jump','move_forward'] out of a confirmed frozen window — not a player deficit, not a reject
  ADVISORY: zero key frames in the delivered rows, but this pipeline gated 62 key frame(s) out of a confirmed frozen window — not a capture failure
  control (no inherited record): bin = 3 blocking = ['CNT_ACTIONS_FEW', 'INP_KEYS_MISSING']
The INP_KEYS_MISSING branch (validate.py:609-619) is weaker still: ANY non-zero inherited `key_frames` downgrades a total absence of keyboard input, with no magnitude test at all. The r-loop-7 test (pipeline/tests/test_r_loop7.py:229) only exercises a single-window entry, which is the one shape the filter handles.

**SCENARIO:** Replaying the reason sets of the 08-18 rebuild dump through pipeline.cutter.complement_windows (MIN_CLIP_S=70) finds 54 real sessions carrying >=1 INP_FROZEN_ACTIONS gate window AND >=1 cut window, and 6 whose gate windows land in DIFFERENT keep segments, e.g. 2026-08-15T14-10-14Z_kamla_c_3179fb3e74fb8862 (gates 247.3-249.1s and 275.4-277.0s; keep segments (0.0,249.1) and (257.6,981.5)) and 2026-08-15T20-51-50Z_kamla_c_07f30392275d6e15 (5 gates across segments 0,2,2,3,4). Every child of those splits is written the same aggregated record. A short segment whose only frozen window destroyed one action then presents as if 4 actions and 62 key frames had been erased from it, so its genuine CNT_ACTIONS_FEW (<3 distinct actions, R14, locked per split segment) and INP_KEYS_MISSING (locked 'no missing input modality' bar) are both downgraded to advisories, bin flips 3 -> 1, and the segment is DELIVERED to Odyssey violating two locked delivery bars — under two operator advisories that are false statements about that segment. That is exactly the harm r-loop 7 wrote this filter to stop; it only closed the single-window case.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Filter at window granularity, not entry granularity: when propagating, rebuild each inherited entry so it carries only the windows (and a `destroyed` recomputed for only those windows) that overlap the segment. gate_windows already returns per-span data — record `destroyed` per requested window (or per contiguous blanked span) instead of one union — and have `_propagate_gate_record` write the per-segment subset. Also compare against the ACTUALLY-blanked spans in `note['windows']` (which include GATE_PAD_FRAMES) rather than `params['windows']`, so pad rows that cross a segment boundary are not orphaned. Separately, give the INP_KEYS_MISSING downgrade a magnitude test like CNT_ACTIONS_FEW's `len(restored) >= MIN_DISTINCT_ACTIONS`.

- refuter (exec): refuted=False confidence=high
  - evidence: SURVIVES. Every quoted line reproduced verbatim against HEAD (869910d).

=== 1. The code reads exactly as claimed (real line numbers) ===

pipeline/fix.py:161-162 — every INP_FROZEN_ACTIONS reason appends its own window to ONE list:
    elif code == "INP_FROZEN_ACTIONS":
        gate_windows.append((p["t0"], p["t1"]))

pipeline/fix.py:267-269 (cut-bearing exit), :296-298 (tail-cut exit), :347-348 (normal exit) — all three emit ONE step carrying ALL windows:
    steps.append(("FIX_GATE_WINDOW", {"windows": sorted(gate_windows)}))

pipeline/gate.py:44-71 — `blank` is a single set unioned over AL
- refuter (harm): refuted=False confidence=high
  - evidence: MECHANISM — verified by reading the real source:

- pipeline/fix.py:161 accumulates one window per INP_FROZEN_ACTIONS reason into `gate_windows`; fix.py:268 (cut path), :297 (tail path) and :348 (no-cut path) each emit ONE step `("FIX_GATE_WINDOW", {"windows": sorted(gate_windows)})`. (The claim cites 268 and 348; there are in fact three sites — immaterial.)
- pipeline/gate.py:60-71 accumulates `destroyed_actions` / `destroyed_key_frames` over the WHOLE `blank` set, i.e. the union across every window in that one step.
- pipeline/fix.py:557 `mine = [e for e in gate_entries if _gate_entry_touche

---
## [MAJOR] CONFIRMED — `pipeline/reports.py:527` (lane payment-split, refuted 0/2)

**Late-arrival deferral survived the stamp split — a late root with one never-settling node reaches no sheet at all, while the identical in-window root is paid incrementally**

**CLAIM:** build_sheet_rows:512-532: when a root qualifies as a LATE arrival it is dropped wholesale (`continue`) if any node in its tree is not in ('DELIVERED','REJECTED','SPLIT','DUPLICATE','QUARANTINED'). The stated justification (lines 513-517) is `a late root counted the moment it was probed would freeze accepted_hrs at 0 and the stamp would lock that in forever` — the RULED split killed that premise: an already-stamped root now returns through `accepted_due` (507-509) carrying the hours that land later. The deferral was never removed, so it is now pure loss. RAN IT: two byte-identical trees (root SPLIT raw=3600 s, p1 DELIVERED 1700 s, p2 HOLD_VLM). The in-window one is counted at once — `day1 [('bob@x.com', 1.0, 0.47)]` — and when p2 later delivers it returns, `day3 [('bob@x.com', 0.0, 0.44)]`, exactly the split's intent. The late one prints `[sheet] LATE ARRIVAL DEFERRED: LATE is countable but its tree is still in flight` on day 1..5 and produces NO row at all: uploaded_reported_at stays None, p1's accepted_reported_at stays None, and 1.0 h uploaded + 0.47 h already delivered to the client are on no sheet. It only unblocks when the last node settles (day 6 gave `[('alice@x.com', 1.0, 0.47, 'black-frozen')]`). continuous.py:1405 states of the blocking state: `HOLD_VLM, which never exits on its own (check the VLM key/quota)`; a FIX_QUEUED row parked by r-loop 7's new host carve-out (run.py:572-586) likewise retries forever with a cooldown.

**SCENARIO:** CONT_DAILY_REPORTS is False through the payment endgame and the stored anchor is 2026-08-16T05:32:50Z. When dailies resume more than 48 h later, run.py:813-824 clamps the window to a trailing 24 h (`window_clamped`) and every root uploaded before it enters through the LATE guard — the code says so itself: `the SHEET attached to it still counts the older roots through the late-arrival guard`. Any of those backlog roots holding one HOLD_VLM segment (Gemini quota exhausted, the exact condition the endgame was fought over) is dropped in full, day after day, taking its already-DELIVERED siblings' hours with it. The only signal is a stderr line; no alert, no row, nothing on the payment document.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Delete the deferral: let a late root be counted like an in-window one (uploaded hours now, accepted hours as they land through accepted_due). If the loud settle warning is still wanted, keep the print but drop the `continue`. If the deferral must stay, bound it — after N sheets count the root anyway — and raise an operator alert instead of a stderr line, because the deferred hours are invisible on every surface.

- refuter (exec): refuted=False confidence=high
  - evidence: CODE (unmodified HEAD 869910d; `git status --porcelain pipeline/` empty), /Users/adnaan/Documents/hl-projects/hl-gamedata/pipeline/reports.py:

  506  sealed = bool(root["accepted_reported_at"])
  507  accepted_due = (up < hi_dt and not sealed
  508                  and bool(root["uploaded_reported_at"])
  509                  and _tree_has_uncounted_accepted(root, children))
  510  if not in_window and not late and not accepted_due:
  511      continue
  512  if late:
  513-517  # ...a late root counted the moment it was probed would freeze
  514       # accepted_hrs at 0 and the stamp would 
- refuter (harm): refuted=False confidence=high
  - evidence: CODE (real line numbers, HEAD 869910d):
- pipeline/reports.py:492-494 `late`; :507-509 `accepted_due` requires `bool(root["uploaded_reported_at"])`; :512-532 the deferral (`continue` at 532 when any tree node is outside DELIVERED/REJECTED/SPLIT/DUPLICATE/QUARANTINED); :537-544 late roots ARE stamped when counted, so counting one immediately would let it re-enter through accepted_due — the deferral's stated premise (513-517) is dead.

REPRO 1 (/tmp copy of pipeline/, /tmp DB; repo untouched — `git status --porcelain pipeline tools` shows only the pre-existing untracked tools/bench_local_vlm.py)

---
## [MAJOR] CONFIRMED — `pipeline/reports.py:506` (lane payment-split, refuted 0/2)

**A root's own per-node accepted mark is read as the whole-tree seal, so a root that is itself DELIVERED/REJECTED locks its live children out of every future sheet**

**CLAIM:** reports.py:503-505 documents the invariant: `the root-level accepted mark is a whole-tree SEAL, applied only by recal_refix_reset when it tears down an already-reported tree`. The ordinary daily send violates it. The tree walk starts at the root (`stack = [root]`, line 551) and appends any DELIVERED/REJECTED node — the root included — to `accepted_out` (565/576); run.py:908 then writes `accepted_reported_at` on it via mark_accepted_reported (reports.py:362-363). Line 506 `sealed = bool(root["accepted_reported_at"])` immediately re-reads that as the tree seal, and 507 `accepted_due = (... and not sealed ...)` plus 560/567 shut the whole subtree out forever. This is reachable because a terminal root can legitimately still own live child rows: run.py:366 `_discard_split_artifacts` states `Rowed children are live work and are kept`, and `_recover_split` hands the parent back to REVALIDATING with rowed children after a mid-split crash, from where run.py:517-519 / 546-556 can drive it to REJECTED and the normal path to DELIVERED. RAN IT: root REJECTED (raw 3600 s) with one live VALIDATING child. `day1 [('alice@x.com', 1.0, 0.0, 'black-frozen')] | accepted: ['R']` and `root accepted_reported_at (=whole-tree SEAL): 2026-08-18T12:10:21+00:00`. The child then delivered 1750 s: `day2 [] | accepted: []`, `day3 [] | accepted: []`, and `R-p1 accepted_reported_at: None duration_delivered_s: 1750.0` — hours delivered to the client, never counted, and structurally unable to be counted.

**SCENARIO:** A kill during a cut leaves rowed segments behind; `_recover_split` returns the parent to REVALIDATING because the cut is incomplete, the parent then exhausts its two fix attempts and is REJECTED with those segment rows still live. The next daily sheet prints the parent's reject label and, as a side effect, stamps the parent's own accepted_reported_at. The segments finish validating and deliver to the client normally; from that moment `accepted_due` is permanently False for the tree and their hours appear on no sheet ever. The same happens if the recovered parent validates clean and DELIVERS instead of rejecting.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Stop overloading one column with two meanings. Either give the seal its own column (e.g. `tree_sealed_at`, written only by recal_refix_reset) and make `sealed` read that, or make `sealed` mean the root-node mark only for the root node — i.e. drop `sealed or` from lines 560/567 and drop `not sealed` from 507, letting each node's own mark decide, with recal_refix_reset writing the seal to the new column.

- refuter (exec): refuted=False confidence=high
  - evidence: REPRODUCED VERBATIM with the real functions (pipeline/reports.py build_sheet_rows + mark_uploads_reported + mark_accepted_reported, wired exactly as pipeline/run.py:875-908 wires them; ledger built with the real pipeline.ledger.Ledger; scratchpad only, repo untouched).

Run 1 — root REJECTED (raw 3600 s, unfixable CNT_BLACK_FROZEN) with one live VALIDATING child R-p1:
  [sheet] PENDING COHORT: alice@x.com/alice@x.com has 0.50h still in flight at generation - accepted hours understated on this sheet
  day1 [('alice@x.com', 1.0, 0.0, 'black-frozen')] | accepted: ['R']
  root uploaded_reported_at
- refuter (harm): refuted=False confidence=high
  - evidence: REPRO A (isolated, /tmp/.../scratchpad/repro.py, real reports.py + real Ledger, wired exactly as run.py:876-908):
  [sheet] PENDING COHORT: alice@x.com/p@x.com has 0.50h still in flight at generation — accepted hours understated on this sheet
  day1 [('alice@x.com', 1.0, 0.0, 'black-frozen')] | counted: ['R'] | accepted: ['R']
    root  accepted_reported_at = 2026-08-18T12:17:02+00:00
  day2 [] | counted: [] | accepted: []
  day3 [] | counted: [] | accepted: []
    child accepted_reported_at = None | duration_delivered_s = 1750.0 | state = DELIVERED
    ledger delivered_hours(kamla) = 0.486111

---
## [MAJOR] CONFIRMED — `tools/recal_refix_reset.py:284` (lane payment-split, refuted 0/2)

**The refix seal is all-or-nothing per root while the mark it reads is per node — a partly-paid tree loses its unpaid delivered hours, and the reconcile JSON hides them**

**CLAIM:** recal_refix_reset.py:280-291 collects `paid_nodes` = subtree nodes that are DELIVERED **and** carry `accepted_reported_at`, then stamps the ROOT with `accepted_reported_at = now if paid_nodes else None`. reports.py:506 reads that root column as `sealed`, and 560/567 skip EVERY DELIVERED/REJECTED node in the tree when it is set, while 507 blocks `accepted_due`. So one paid node seals hours that were never paid. RAN IT with the real `refix._locked_main` against a scratch ledger: root SPLIT; p1 DELIVERED 1200 s and counted on day 1 (`day1 [('alice@x.com', 1.0, 0.33, '')] | accepted: ['R-p1']`); p2 DELIVERED 1100 s AFTER that sheet, so `p2.acc= None`; p3 fix-failed REJECTED, which is what selects the tree. The tool output was `"sealed_roots": [{"root": "R", "paid_nodes": ["R-p1"]}]` and it set root accepted_reported_at. Subsequent sheets: `day2 [] | accepted: []`, `day3 [] | accepted: []`. p2's 0.31 h shipped to the client and reached no sheet before the reset and can reach none after it. The JSON handed to the human names only R-p1 — the node that WAS paid — and says nothing about R-p2, so the reconcile reads as "this tree was paid".

**SCENARIO:** Operator runs the fix-failed recovery for the 08-16 false-positive rejects (`--allow-reported`, required because the root is stamped). Any tree in the 43.7% shape the split was written for — a child counted on one sheet, a sibling delivering after it — is sealed on the strength of the counted child. The unpaid sibling's hours are lost, the re-run re-delivers the whole recording, and `sealed_roots` tells the reconciler only about the child that was already paid, so the shortfall is never found by hand either.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Compute the tree's already-counted delivered seconds (sum of duration_delivered_s over DELIVERED nodes that carry accepted_reported_at) and the total delivered seconds, and either (a) seal only when they are equal, or (b) always report BOTH lists in `sealed_roots` — `paid_nodes` and `unpaid_delivered_nodes` with their hours — so the operator is told exactly what the seal is about to swallow. Reporting only paid_nodes is worse than reporting nothing.

- refuter (exec): refuted=False confidence=high
  - evidence: REPRO (real code, scratch ledger under HL_PIPELINE_HOME=/tmp; nothing in the repo touched). Driver: /private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/repro/run.py (and run2.py for the UPLOADED-event variant). It loads the REAL tools/recal_refix_reset.py via importlib and calls refix._locked_main(cfg, Namespace(yes=True, allow_reported=True)), and the REAL pipeline.reports.build_sheet_rows / mark_uploads_reported / mark_accepted_reported.

Tree: R (SPLIT root, kamla, duration_raw_s 3600, drive_ctime 2026-08-14T12:00Z), childr
- refuter (harm): refuted=False confidence=medium
  - evidence: MECHANISM — read in full, real line numbers:

/Users/adnaan/Documents/hl-projects/hl-gamedata/tools/recal_refix_reset.py
  280-283  paid_nodes = [s for s in [root] + kids
                         if (n := ledger.get(s)) is not None
                         and n["state"] == "DELIVERED"
                         and n["accepted_reported_at"]]
  284-285  if paid_nodes: sealed_roots.append({"root": root, "paid_nodes": paid_nodes})
  286-291  UPDATE sessions SET ... accepted_reported_at=?, ... WHERE session_id=?   (now if paid_nodes else None, now, root)
The only gate before it is line 183 `if stam

---
## [MAJOR] CONFIRMED — `tools/recal_refix_reset.py:280` (lane ops-tools, refuted 0/2)

**The refix accepted-seal is applied to the ROOT, so it also permanently blocks the unpaid hours the re-run exists to recover**

**CLAIM:** `paid_nodes` (lines 280-283) is computed per NODE — a DELIVERED descendant whose `accepted_reported_at` is set — but the seal is written to the ROOT row (line 291, `accepted_reported_at=?` on `WHERE session_id=?` with `root`). `pipeline/reports.py:506` reads that root column as `sealed = bool(root["accepted_reported_at"])` and uses it as a WHOLE-TREE gate: it kills `accepted_due` (line 507) and short-circuits the tree walk for every DELIVERED and REJECTED node (lines 560, 567). Because `uploaded_reported_at` is deliberately preserved (line 254 comment), `in_window` and `late` are also closed. So one already-paid child seals every future accepted hour of the whole re-delivered tree, forever.

Proved end-to-end in a scratch tree (synthetic ledger, scratch HL_PIPELINE_HOME, nothing in the repo touched). Tree R7 = SPLIT root with R7-p1 (DELIVERED 1750 s, counted on the 08-15 sheet) and R7-p2 (REJECTED, all-fixable = the fix-failed target). `recal_refix_reset --yes --allow-reported` printed:

  "sealed_roots": [ { "root": "R7", "paid_nodes": [ "R7-p1" ] } ]
  --- stamps after refix:  R7  DISCOVERED  up=Y acc=Y

The re-run then re-delivered R7-p1 (1750 s) and R7-p2 (1700 s). Sheets for 2026-08-19/20/21 (`run.send_daily_report_if_due`):

  2026-08-19: uploaded=1.0 delivered=1.83   <- R5 0.8889 + R6 0.9444 only
  --- per DELIVERED node, was it ever on a sheet?
     R7-p1    0.4861 h  accepted_stamped=NO
     R7-p2    0.4722 h  accepted_stamped=NO
  SUM of accepted hours across ALL sheets = 5.6200 h
  SUM of duration_delivered_s in ledger    = 6.0972 h
  --- R7 root seal: 2026-08-18T12:02:59+00:00

R7 contributes 0.0000 h to every later sheet. Withholding R7-p1's 0.4861 h is the seal working as intended (that footage was already paid). Withholding R7-p2's 0.4722 h is not: those hours were never on any sheet, they are the reject this tool was run to recover, and they are now unreachable by any sheet the pipeline can ever generate.

**SCENARIO:** Post-endgame (FLIP_RUNBOOK step 7.3 turns dailies back on, so every root carries an uploaded stamp and every DELIVERED node an accepted stamp), an operator runs `tools/recal_refix_reset.py --yes --allow-reported` to recover fix-failed rejects — the documented purpose of the tool and the recovery path for the 08-16 recalibration's false-positive rejects. Any selected root that was SPLIT and had at least one sibling segment already delivered and paid gets its root sealed. The re-run re-delivers the whole tree to Drive II; the player is paid nothing for the newly-recovered segment. The result JSON reports only `paid_nodes: ["R7-p1"]`, so the operator reconciling it credits one segment's hours and has no signal that the sibling's genuinely-new hours were also sealed away — a silent, permanent underpayment on exactly the path that exists to correct an underpayment.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Seal only what was actually paid, not the whole tree. Either (a) abort on a mixed tree — when the subtree contains DELIVERED nodes both with and without `accepted_reported_at`, refuse and print the split so a human decides; or (b) keep the seal node-scoped: do not delete the paid DELIVERED child rows (or re-insert them stamped under their deterministic `<sid>-p<n>` ids after the re-split), leaving the root's `accepted_reported_at` NULL so `accepted_due` still lets the unpaid siblings re-enter. At minimum, `sealed_roots` must also list the DELIVERED nodes whose hours were NOT counted and are being sealed anyway, so the reconcile is possible at all.

- refuter (exec): refuted=False confidence=high
  - evidence: CODE READ AT HEAD (869910d), every cited line verified verbatim:

tools/recal_refix_reset.py
  280-283  paid_nodes = [s for s in [root] + kids
                         if (n := ledger.get(s)) is not None
                         and n["state"] == "DELIVERED"
                         and n["accepted_reported_at"]]     <- per NODE
  286-291  UPDATE sessions SET ... accepted_reported_at=?, updated_at=?
             WHERE session_id=?
           (now if paid_nodes else None, now, root)          <- written to the ROOT
  254      "# uploaded_reported_at is DELIBERATELY preserved (r-loop 4 blocker)."
- refuter (harm): refuted=False confidence=high
  - evidence: MECHANISM (real line numbers, verified by reading the files):
- tools/recal_refix_reset.py:280-283 computes `paid_nodes` PER NODE (`n["state"]=="DELIVERED" and n["accepted_reported_at"]`), lines 286-291 write the result to the ROOT row: `UPDATE sessions SET ... accepted_reported_at=? ... WHERE session_id=?` with `(now if paid_nodes else None, now, root)`.
- pipeline/reports.py:506 `sealed = bool(root["accepted_reported_at"])`; :507 kills `accepted_due`; :560 and :567 `if sealed or n["accepted_reported_at"]: continue` for every DELIVERED and every REJECTED node in the tree walk. So the root col

---
## [MAJOR] CONFIRMED — `tools/run_suite.sh:23` (lane ops-tools, refuted 0/2)

**The flip's arming gate goes RED on the exact config FLIP_RUNBOOK 6c mandates and vm_setup's interlock enforces**

**CLAIM:** 11 tests read the production constant `C.CONT_DAILY_REPORTS` at call time with no monkeypatch (`pipeline/run.py:783` returns False when it is False). FLIP_RUNBOOK.md:114 (step 6c) requires `CONT_DAILY_REPORTS = False` to be committed and rsynced to ~/hl-gamedata before arming, and tools/vm_setup.sh:195-206 hard-FATALs at step 6e unless it is False. So the tree that is actually armed cannot pass its own arming gate.

Proved in a scratch copy with ONLY that one line changed (`CONT_DAILY_REPORTS = True` -> `False`, config.py:192), same command FLIP_RUNBOOK.md:83 gives:

  11 failed, 508 passed in 60.90s
  FATAL: pytest exited 1
  FAILED pipeline/tests/test_payment_split_r6.py::test_accepted_mark_is_written_by_the_daily_send_before_the_anchor
  FAILED pipeline/tests/test_review_r5_driver.py::test_daily_send_order_stamps_then_anchor_then_marker
  FAILED pipeline/tests/test_review_r5_driver.py::test_daily_send_stamps_exactly_the_counted_roots
  FAILED pipeline/tests/test_review_r5_driver.py::test_daily_resend_after_kill_before_marker_no_double_count
  FAILED pipeline/tests/test_run.py::test_daily_report_gating
  (+ test_folder_issues x3, test_reports_pace, test_review_r1, test_review_r3)

The unmodified tree gives `ARMING GATE OK: 519 passed (floor 440)`. Note 508 still clears the floor, so only the `rc != 0` arm fires. FLIP_RUNBOOK 5.1 sets the same knob False in the canary side checkout, so a suite run from there is red too — confirmed: 11 failed, 508 passed.

**SCENARIO:** Operator completes step 6c (sets CONT_DAILY_REPORTS=False, commits, rsyncs to ~/hl-gamedata) and, before arming at 6e, re-runs the gate on the VM tree — the natural verification, and the only defined meaning of "green" (`run_suite.sh` line 2: "The flip's ARMING GATE. Use this, never a bare pytest"). It prints `FATAL: pytest exited 1` with 11 failures, three of them in the daily-send stamping-order module and one in the payment-split regression test. Same thing on any re-run after the 6b E2->C2D fallback branch, after a redeploy, or by the next operator. A careful operator obeys the script's own instruction ("Do NOT arm") and stalls the flip; a less careful one makes the suite green the only way that works — restoring CONT_DAILY_REPORTS=True — which is precisely the state the interlock exists to prevent: the 14:00 IST send stamps the entire unstamped rebuild cohort into one sheet and deadlocks recal_regen_sheets.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Make the 11 tests independent of the deployed knob — an autouse fixture (or per-test monkeypatch) that forces `runmod.C.CONT_DAILY_REPORTS = True` for the tests that exercise the send path, plus one test that asserts the suppression when it is False. Until then, run_suite.sh should force the flag on for the gate run (e.g. export an override the tests honour) and FLIP_RUNBOOK 6b must say in words that the gate is only valid with the flag True, so the red suite between 6c and 7.3 is never mistaken for a broken deploy.

- refuter (exec): refuted=False confidence=high
  - evidence: REPRO (scratch copy at /private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/tree — rsync of pipeline/ translator/ tools/, nothing in the real repo touched).

1) Baseline, unmodified, exact FLIP_RUNBOOK.md:82-86 command
   `SUITE_FLOOR=440 bash tools/run_suite.sh --with numpy==2.4.6 --with opencv-python-headless==5.0.0.93 --with rerun-sdk==0.36.0`
   -> `519 passed in 63.78s (0:01:03)` / `ARMING GATE OK: 519 passed (floor 440)` (exit 0).

2) ONE line changed, verified by `git diff --no-index` against the real repo (single hunk):
- refuter (harm): refuted=False confidence=high
  - evidence: MECHANISM REPRODUCED EXACTLY (isolated copy of pipeline/ translator/ tools/ at /private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/rep; the real repo at /Users/adnaan/Documents/hl-projects/hl-gamedata was NOT modified — `git status --porcelain pipeline tools translator` shows only the pre-existing untracked tools/bench_local_vlm.py, and pipeline/config.py:192 still reads `CONT_DAILY_REPORTS = True`).

1) BASELINE, unmodified, command exactly as FLIP_RUNBOOK.md:83 gives it
   `SUITE_FLOOR=440 bash tools/run_suite.sh --with nump

---
## [MAJOR] CONFIRMED — `translator/trim.py:137` (lane translator, refuted 0/2)

**rebase_events uses the untrusted `key`/`button` value as a dict key — the container key codes r-loop 7 just taught bin_session to tolerate crash it first**

**CLAIM:** r-loop 7 hardened `keys.py:71` (`normalize_event_key`) so a container key code from a hand-edited/foreign-tool sidecar degrades to None instead of raising, and pinned it with `pipeline/tests/test_r_loop7.py:306` `@pytest.mark.parametrize("bad", [65, ["w"], {"k": "w"}, None])`. But `rebase_events` runs BEFORE `bin_session` in every production path (`v2.py:263-266`, `fix.py:747`), and at trim.py:137/142 it does `held_keys[e["key"]] = e` / `held_btns[e["button"]] = e` on the raw value. Proved on HEAD: `rebase_events([{"t":1_000_000,"type":"key","action":"down","key":["a"]}], 6.0, 100.0)` -> `RAISE TypeError: cannot use 'list' as a dict key (unhashable type: 'list') @ trim.py:137`; the dict form raises at the same line; a container `button` raises at trim.py:142. The very same value through the guarded half returns cleanly: `K.normalize_event_key(["a"]) -> None`, and `bin_session([...key=["a"]...])` returns rows with `keys_seen=set()`. So r-loop 7's fix is unreachable for any such event that falls in the head-trim window.

**SCENARIO:** A player's inputs.jsonl encodes one key (say a modifier) as a container — the exact class r-loop 7's own parametrize list declares supported. Over a 10-30 min session that key is pressed in the first ~5-8 s with near-certainty, i.e. inside the head-trim window `t < head_us`. Ran through the real fix lane in a scratch copy: FIX_TRANSLATE_RAW on a raw bundle with one such event at t=1s -> `fix.apply_fixes` returns `error='FIX_TRANSLATE_RAW: TypeError: cannot use 'list' as a dict key (unhashable type: 'list')', kind='session'`; FIX_RETRANSLATE on the same session as a v2 upload with raw sidecars -> `error='FIX_RETRANSLATE: TypeError: ...', kind='session'`. kind='session' means the attempt is NOT refunded (fix.py:400-403), so run.py:588 / continuous.py:901 set REVALIDATING, the identical plan re-runs, attempt 2 dies identically, and the session is terminally REJECTED. Because every stored reason is still `fixable`, `reports._reject_labels` emits the bare FIX_FAILED_MARKER — the player is told 'fix-failed' with no reason, the media is wiped, and the hours are unpaid. FIX_RETRANSLATE is 'the universal strong fix', so this kills the repair path for every defect class at once.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Normalize the identity before using it as a dict key, matching the guard one call away: in `rebase_events` replace `e["key"]` / `e["button"]` with a hashable coercion (e.g. `k = e.get("key"); if not isinstance(k, str): continue` for the held-state bookkeeping, and the same `isinstance(btn, str)` test binner.py:155 already uses for buttons). Skipping a non-str identity is exactly the answer `normalize_event_key` gives it two steps later, so nothing is lost. Add a parametrize over the same `[65, ["w"], {"k": "w"}]` values driving `rebase_events` with the event placed at `t < head_us`.

- refuter (exec): refuted=False confidence=high
  - evidence: All claimed output reproduced verbatim on HEAD (869910d), probing only in /tmp; repo untouched.

1) Direct crash, exact traceback (real line numbers):
   $ PYTHONPATH=. python3 -c "from translator.trim import rebase_events; rebase_events([{'t':1000000,'type':'key','action':'down','key':['a']}], 6.0, 100.0)"
     File "/Users/adnaan/.../translator/trim.py", line 137, in rebase_events
       held_keys[e["key"]] = e
   TypeError: cannot use 'list' as a dict key (unhashable type: 'list')
   dict form {'k':'a'} -> same line 137, "cannot use 'dict' as a dict key".
   button=['left'] -> line 142  hel
- refuter (harm): refuted=False confidence=high
  - evidence: MECHANISM (reproduced on HEAD 869910d, repo imported read-only, all work in /tmp scratchpad):

$ PYTHONPATH=<repo> python3 -c "... rebase_events([{'t':1_000_000,'type':'key','action':'down','key':bad}], 6.0, 100.0) ..."
key ['a'] -> TypeError cannot use 'list' as a dict key (unhashable type: 'list') @ trim.py:137
key {'k': 'a'} -> TypeError cannot use 'dict' as a dict key (unhashable type: 'dict') @ trim.py:137
key 65 -> OK [{'t': 0, ...}]
key None -> OK []
btn ['left'] -> TypeError cannot use 'list' as a dict key (unhashable type: 'list') @ trim.py:142
btn {'b': 'left'} -> TypeError ... @ tri

---
## [MINOR] CONFIRMED — `pipeline/continuous.py:195` (lane driver-core, refuted 0/2)

**AlertBook records an alert as sent BEFORE it is sent, so any failed send silences that alert for the full 60-minute TTL**

**CLAIM:** `AlertBook.alert` stamps the dedup slot and only then attempts delivery (continuous.py:186-200):
```python
self._sent[text] = now
try:
    telegram.send_message(self.cfg, f"⚠️ {text}")
except telegram.TelegramError as e:
    print(f"[alert-undelivered] {text} ({e})", file=sys.stderr)
```
The failure path never removes the entry, so a send that raised still consumes the whole `CONT_ALERT_DEDUP_MIN * 60` window. Probe with an injected clock and a `send_message` that raises a Telegram 429 for the first two calls:
```
t=0     send attempted: 1 delivered: 0
t= 1min attempts so far: 1
t= 5min attempts so far: 1
t=30min attempts so far: 1
t=59min attempts so far: 1
t=61min attempts so far: 2
```
One undelivered attempt, then 60 minutes of silence. The class docstring states the opposite contract: "a forever-process must RE-raise persisting conditions".

**SCENARIO:** Telegram's per-chat limit is roughly 20 messages/minute. During exactly the incident that produces many alerts — a disk-full episode alerting per session from `_download_one` (continuous.py:517-533), `_validate_one` (770-772) and `_fix_one` (896-898) within seconds, amplified by the digest retry storm below — some sends come back `sendMessage rejected: 429` (telegram.py:38-41 raises TelegramError). Every alert that 429s is nonetheless stamped delivered and suppressed for the next hour. The F7 disk-paused alert (continuous.py:459-461), which is the operator's only notice that intake has stopped, is one of them; one-shot alerts such as `download quarantined <sid>: <why>` are lost permanently, leaving only the digest's `N quarantined` count with no sid and no reason.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Stamp `self._sent[text]` only after `telegram.send_message` returns without raising (take the lock again to record, or record and pop it back out on TelegramError). A failed send should leave the condition eligible for the next tick, not consume the TTL.

- refuter (exec): refuted=False confidence=high
  - evidence: SOURCE (real file, /Users/adnaan/Documents/hl-projects/hl-gamedata/pipeline/continuous.py:186-199, HEAD 869910d) — the quote in the claim is verbatim and line 195 is exactly `self._sent[text] = now`:

    186	    def alert(self, text: str) -> None:
    187	        with self._lock:
    188	            now = self._mono()
    189	            for k in [k for k, t in self._sent.items()
    190	                      if now - t > self._ttl]:
    191	                del self._sent[k]
    192	            last = self._sent.get(text)
    193	            if last is not None and now - last < self._ttl:
   
- refuter (harm): refuted=False confidence=high
  - evidence: MECHANISM — verified by reading pipeline/continuous.py:186-200 (unmodified):

    def alert(self, text: str) -> None:
        with self._lock:
            now = self._mono()
            for k in [k for k, t in self._sent.items()
                      if now - t > self._ttl]:
                del self._sent[k]
            last = self._sent.get(text)
            if last is not None and now - last < self._ttl:
                return
            self._sent[text] = now          # <-- line 195, stamped
        try:
            telegram.send_message(self.cfg, f"⚠️ {text}")
        except telegram.Tele

---
## [MINOR] CONFIRMED — `pipeline/continuous.py:1444` (lane driver-core, refuted 0/2)

**The 3-h digest has no retry cadence stamp, so a Telegram outage turns it into a ~180/hour rebuild-and-resend loop — the exact defect CONT_DAILY_RETRY_S was added to fix, 25 lines below**

**CLAIM:** In `_housekeeping_thread.body` the digest runs unconditionally on every ~20 s tick (continuous.py:1443-1444):
```python
if self.send_telegram:
    _duty("digest", lambda: self._send_digest(led))
```
whereas the daily/folder-issues duties immediately below get their own stamp (continuous.py:1453, 1469): `if C.CONT_DAILY_REPORTS and now >= self._next_daily: self._next_daily = now + C.CONT_DAILY_RETRY_S`, added in r-loop 5 with the comment that a Telegram outage otherwise means "~180 full sheet generations an hour". `_send_digest` returns without setting `_digest_anchor_mem` or writing the anchor when the send raises (continuous.py:1334-1337), so `_digest_window` keeps returning a window on every subsequent tick.

Probe (anchor 4 h old so a digest is due; `telegram.send_message` always raises; ten housekeeping ticks):
```
housekeeping ticks       : 10
full digests BUILT       : 10
telegram sends attempted : 10
anchor file after        : 2026-08-18T08:15:31+00:00 (unchanged)
_digest_anchor_mem       : None

contrast: CONT_DAILY_RETRY_S = 600.0 s -> 6 attempts/hour
          digest has no cadence stamp  -> ~180 attempts/hour
```

**SCENARIO:** Telegram is unreachable or rate-limiting when a 3-h digest falls due. From that moment the housekeeping lane rebuilds the full digest (four `_stuck_lines` queries, `_held_discovered`'s work-dir scan, a dossier read per session in a window that keeps growing because the anchor never advances, `led.incomplete_list()`, two `delivered_hours` sums and `_pace_now`) and fires a `urlopen` at the Telegram API up to 180 times an hour for the whole outage. Each attempt can block the lane for the 60 s urlopen timeout (telegram.py:33), stretching H's own 20 s tick and with it the 60 s autoscale cadence that is the driver's only backpressure response. Worse, when the cause is rate-limiting rather than a hard outage, the storm keeps the chat over Telegram's per-chat limit, so every ⚠️ alert raised in that window 429s — and is then marked delivered and suppressed for an hour by the AlertBook defect above. The driver goes quiet on both surfaces at once, at the flip, when the digest is the only ops surface.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Give the digest its own cadence stamp exactly like the daily: keep `self._next_digest` on the driver, set it to `now + <retry interval>` before calling `_send_digest`, so a failed send is retried on a bounded schedule instead of every tick. `_digest_window`'s 3-h gate already handles the success case; the stamp only needs to bound the failure case.

- refuter (exec): refuted=False confidence=medium
  - evidence: SOURCE (real lines, HEAD 869910d, repo untouched — probes ran in /tmp/digest_probe):

pipeline/continuous.py
  1440	            if now >= self._next_scale:
  1441	                self._next_scale = now + C.CONT_AUTOSCALE_INTERVAL_S
  1442	                _duty("autoscale", self._autoscale_tick)
  1443	            if self.send_telegram:
  1444	                _duty("digest", lambda: self._send_digest(led))     <-- no cadence gate
  ...
  1453                    if C.CONT_DAILY_REPORTS and now >= self._next_daily:
  1469                        self._next_daily = now + C.CONT_DAILY_RETRY_S

  133
- refuter (harm): refuted=False confidence=medium
  - evidence: ALL CITED LINES CHECK OUT VERBATIM (HEAD 869910d, /Users/adnaan/Documents/hl-projects/hl-gamedata/pipeline/continuous.py):
  1443	            if self.send_telegram:
  1444	                _duty("digest", lambda: self._send_digest(led))
  1453	                if C.CONT_DAILY_REPORTS and now >= self._next_daily:
  1465-1468	 comment: "...It also stretched H's own cadence (up to three 60s urlopen timeouts per tick), delaying the 60s autoscale ticks that are the driver's only backpressure response."
  1469	                    self._next_daily = now + C.CONT_DAILY_RETRY_S
  1334-1336	        except

---
## [MINOR] CONFIRMED — `pipeline/validate.py:90` (lane fix-validate, refuted 0/2)

**Five STR_SJ_INVALID FAIL classes map to a fix that provably cannot clear them — FIX_SESSIONJSON_REWRITE reports success while changing nothing**

**CLAIM:** The needle table routes 20+ qa-v2 FAIL strings to STR_SJ_INVALID, and r-loop 7 made that code plan FIX_SESSIONJSON_REWRITE unconditionally (fix.py:179-193, 218-220). But fix_sessionjson_recompute (fix.py:1054) only repairs a field when it is ABSENT or FALSY — `platform=s.get("platform") or "PC"`, `localization=s.get("localization") or LOCALIZATIONS.get(...)`, and `if not isinstance(conv, dict) or "maps_to" not in conv` — while the checker rejects PRESENT-but-invalid values, and created_at_utc is only rewritten when tz-NAIVE. Ran the real round trip (build session -> check_session_v2 -> _map_qa_issues -> plan_fixes -> apply_fixes -> check_session_v2):
  bad_platform      BEFORE ["FAIL: platform not in spec enum: 'Windows'"]        plan ['FIX_SESSIONJSON_REWRITE','FIX_SESSIONJSON_RECOMPUTE'] err None  AFTER ["FAIL: platform not in spec enum: 'Windows'"]
  bad_localization  BEFORE ["FAIL: localization not BCP 47 …: 'english'"]        err None  AFTER identical
  conv_partial      BEFORE ["FAIL: input_mouse_convention missing: ['dx_positive','dx_negative','dy_positive','dy_negative']"]  err None  AFTER identical
  conv_bad_axes     BEFORE ['FAIL: camera mapping: dx_positive/dx_negative must be right|left']  err None  AFTER identical
  conv_bad_mapsto   BEFORE ["FAIL: maps_to not in enum: 'camera_look'", 'FAIL: non-camera mapping: …']  err None  AFTER identical
  space_sep_ts      BEFORE ["FAIL: … created_at_utc not timezone-aware ISO 8601: '2026-08-12 08:33:31+00:00'"]  err None  AFTER identical
  offset_nocolon    BEFORE ["… '2026-08-12T08:33:31+0000'"]  err None  AFTER identical
  (control) naive_ts BEFORE two FAILs -> AFTER []  — the one case the rewrite does fix.
Having raw sidecars does not help: FIX_RETRANSLATE ends in the same fix_sessionjson_recompute (fix.py:789). Each attempt reports ok:True, so the driver moves to REVALIDATING every time.

**SCENARIO:** A v2 upload whose session.json carries a present-but-invalid constant (platform 'Windows', localization 'en_US', an input_mouse_convention with maps_to but no/def wrong axis fields, or a parseable but non-conforming aware created_at_utc such as datetime str() output). qa FAILs -> STR_SJ_INVALID blocking+fixable -> bin 2 -> attempt 1 rewrites nothing and reports success -> REVALIDATING -> identical FAIL (2nd paid Gemini sweep) -> attempt 2 identical -> REVALIDATING (3rd sweep) -> _fix_one sees fix_attempts >= FIX_RETRIES and REJECTs 'fix retries exhausted (R2)', finalize_rejected wipes the media. Because every stored reason is fixable, reports.py:74-81 surfaces only the bare FIX_FAILED_MARKER ('fix-failed'), so the operator and the player get no actionable reason and the hours are unpaid — the exact trap the file's own r-loop-4/r-loop-6 notes at validate.py:114-131 unmap other FAILs to avoid. Bounded: zero STR_SJ_INVALID rows appear in the 1396-row 08-18 rebuild dump (reason counts there are CNT_MID_NONGAMEPLAY 1523, SYN_TS_NOT_PTS 267, CNT_AFK 217, CNT_EDGE_NONGAMEPLAY 180, SYN_LAG_CONST 79, INP_FROZEN_ACTIONS 75, CNT_SHORT 14, INT_PATH 8, CNT_ACTIONS_FEW 6, INP_KEYS_MISSING 6, INP_MOTION_MISSING 4, CNT_BLACK_FROZEN 4), so this is a latent trap rather than an active loss.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Make fix_sessionjson_recompute validate, not just default: overwrite `platform` unless it is in translator.v2._PLATFORMS, overwrite `localization` unless it matches _LOC_RE, replace `input_mouse_convention` unless it is a dict that fully satisfies _check_session_json's rules (all five keys present, maps_to in _MAPS_TO, axis values in the right enums), and re-stamp created_at_utc whenever it fails _TS_RE rather than only when tz-naive. Alternatively, split the needle table so the FAIL classes the rewrite cannot clear are left unmapped (the r-loop-4/6 precedent) so they reject at once with a truthful reason instead of burning the budget.

- refuter (exec): refuted=False confidence=high
  - evidence: REPRODUCED end-to-end with the real modules (script at /private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/repro.py, reusing the repo's own synthetic-session builder from pipeline/tests/test_fix_cut_gate.py:103-158; ffmpeg present; no repo file touched).

Real round trip check_session_v2 -> validate._map_qa_issues -> fix.plan_fixes -> fix.apply_fixes -> check_session_v2, has_raw=False, game=kamla. Every case planned ['FIX_SESSIONJSON_REWRITE','FIX_SESSIONJSON_RECOMPUTE'], both steps ok:True, err None:

bad_platform     BEFORE 
- refuter (harm): refuted=False confidence=high
  - evidence: MECHANISM — reproduced end-to-end with the repo's own code (script at /private/tmp/claude-501/-Users-adnaan-Documents-hl-projects-hl-gamedata/5d20eb3b-6734-4283-b92d-6f369beb2e08/scratchpad/repro.py, sessions built in /private/tmp/hl-verify-sj via pipeline/tests/test_fix_cut_gate.py::_make_session, real ffmpeg video; nothing in the repo was modified). Each case: check_session_v2 -> validate._map_qa_issues -> fix.plan_fixes -> fix.apply_fixes -> check_session_v2. Real output:

--- bad_platform
  BEFORE ["FAIL: platform not in spec enum: 'Windows'"]
  codes  ['STR_SJ_INVALID'] (blocking=True, fi

---
## [MINOR] CONFIRMED — `tools/run_suite.sh:18` (lane ops-tools, refuted 1/2)

**SUITE_FLOOR is 440 against 519 collected tests, so the gate's "tests vanished" arm has 79 tests of slack**

**CLAIM:** `SUITE_FLOOR="${SUITE_FLOOR:-440}"` (line 18) and FLIP_RUNBOOK.md:83 pins the same 440, while the suite now collects 519 (`519 tests collected in 0.05s`). r-loop 7 added 31 tests and did not raise the floor, despite line 13 of this same file saying "Raise the floor when the suite grows". The script names two jobs (line 12: "a summary line must EXIST, and the number of passing tests must be at least SUITE_FLOOR") and line 38 states the second one as "Either the suite was truncated or tests vanished. Do NOT arm."

Proved in a scratch copy: deleting pipeline/tests/test_continuous.py (47 tests — the entire continuous-driver module being armed) and pipeline/tests/test_r_loop7.py (31 tests — every regression test for r-loop 7's two blockers) gives:

  441 passed in 21.79s
  ARMING GATE OK: 441 passed (floor 440)

Exit 0. The gate green-lights arming with 78 tests, including both of those whole modules, absent.

**SCENARIO:** FLIP_RUNBOOK 6c deploys by rsync to ~/hl-gamedata and 6b runs the gate on the VM. If the VM tree is stale or a deploy drops test files (an excluded path, an interrupted rsync, a bad merge), up to 79 tests can be missing and the gate still prints ARMING GATE OK. The operator arms production believing the r-loop-7 blocker regressions and the continuous-driver suite were exercised on the VM when neither ran. The truncation arm still works (the os._exit regression truncates early, measured 140/449), but the vanished-tests arm the script explicitly claims cannot fire.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Raise the default to the current count (`SUITE_FLOOR="${SUITE_FLOOR:-519}"`) and update FLIP_RUNBOOK.md:83 to match; better, assert on collected-vs-passed instead of a hand-maintained constant — run `--collect-only -q` first and require `passed == collected`, which cannot drift as the suite grows.

- refuter (exec): refuted=False confidence=high
  - evidence: I tried to refute this and could not. Every quoted number reproduced in a /tmp copy; the real repo was never modified.

1. THE CODE IS AS CLAIMED. /Users/adnaan/Documents/hl-projects/hl-gamedata/tools/run_suite.sh is 46 lines; I read all of it. Line 18: `SUITE_FLOOR="${SUITE_FLOOR:-440}"`. The only checks are summary-exists (l.29-34), floor (l.36-40), and rc (l.41-44). Its own comments state the contract the claim invokes:
  - l.12-14: "Two things must both hold: a summary line must EXIST, and the number of passing tests must be at least SUITE_FLOOR. Raise the floor when the suite grows; never
- refuter (harm): refuted=True confidence=high
  - evidence: TREE: clean at HEAD 869910d (r-loop 7); `git status --porcelain -- pipeline/tests translator/tests pipeline translator` prints nothing.

(1) THE CLAIMED SUITE SIZE IS WRONG. `PYTHONPATH=. uv run --with pytest pytest pipeline/tests translator/tests --collect-only` →
    ========================= 511 tests collected in 0.05s =========================
and the full gate run → `510 passed, 2 skipped in 54.23s`. Not 519. No test files exist outside those two dirs (`find . -name "test_*.py"` outside them → empty). Slack is 70, not 79.

(2) THE CLAIMED REPRO FIRES THE GATE. Exact scenario reproduced i

---
## [MINOR] CONFIRMED — `translator/binner.py:80` (lane regressions-r7, refuted 1/2)

**raw_int still raises OverflowError on inf / huge-int sidecar values — the crash it was written to stop**

**CLAIM:** 869910d's raw_int (binner.py:66-81) is documented "DEGRADE, never crash" but catches only `(TypeError, ValueError)` around `int(float(v or 0))`. `int(float('inf'))` raises OverflowError and `float(<400-digit int>)` raises OverflowError, neither of which is caught. Both values come straight out of json.loads on ONE inputs.jsonl line, because Python's json accepts the Infinity/NaN literals and unbounded ints. Ran against the repo's own module:
  '1.0' -> 1 ; 'abc' -> 0 ; [1,2] -> 0 ; None -> 0 ; nan -> 0 ; {'a':1} -> 0
  {"type":"mouse_raw","t":1,"dx":Infinity,...} | dx= float -> RAISED OverflowError cannot convert float infinity to integer
  {"type":"mouse_raw","t":1,"dx":1e999,...}    | dx= float -> RAISED OverflowError cannot convert float infinity to integer
  {"type":"mouse_raw","t":1,"dx":111...1,...}  | dx= int   -> RAISED OverflowError int too large to convert to float
raw_int is called unguarded from binner.bin_session:167-168 and from v2._verify_against_raw:915-916, whose call site (v2.py:810) is not wrapped, so the exception leaves check_session_v2. run._validate_worker (run.py:180) classifies OverflowError as kind="crash", not "host".

**SCENARIO:** A player's inputs.jsonl carries one mouse_raw line whose dx was written as `Infinity` (or a corrupted multi-hundred-digit integer). check_session_v2 raises OverflowError → _validate_worker returns kind="crash" → the driver writes QUARANTINED "validation crashed", which is terminal with no automatic re-entry and holds the media 48 h for a manual queue — for a session that would otherwise have PASSed, and the identical outcome the r-loop-7 finding was raised to eliminate. Every FIX_RETRANSLATE on that session raises the same way, so the fix lane cannot recover it either.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Catch the overflow and clamp: `except (TypeError, ValueError, OverflowError): return 0`, and guard non-finite floats explicitly (`if isinstance(v, float) and not math.isfinite(v): return 0`) so inf/-inf/NaN all degrade to 0 motion like every other malformed value.

- refuter (exec): refuted=True confidence=high
  - evidence: MECHANISM — REPRODUCES (the one true half):
translator/binner.py:79-82 is exactly as quoted:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0
Ran the repo's own module (inspect.getsourcefile confirmed /Users/adnaan/Documents/hl-projects/hl-gamedata/translator/binner.py):
  '1.0' -> 1 ; 'abc' -> 0 ; [1, 2] -> 0 ; None -> 0 ; nan -> 0 ; {'a': 1} -> 0
  {"type":"mouse_raw","t":1,"dx":Infinity,...}  | dx float -> RAISED OverflowError cannot convert float infinity to integer
  {"type":"mouse_raw","t":1,"dx":1e999,...}     | dx float -> RAISED OverflowE
- refuter (harm): refuted=False confidence=medium
  - evidence: REPRODUCED (against the repo's own module, PYTHONPATH=/Users/adnaan/Documents/hl-projects/hl-gamedata):

$ python3 -c "... from translator.binner import raw_int ..."
'1.0' -> 1
'abc' -> 0
[1, 2] -> 0
None -> 0
nan -> 0
{'a': 1} -> 0
---
{"type":"mouse_raw","t":1,"dx":Infinity,"dy":0} | dx type float RAISED OverflowError cannot convert float infinity to integer
{"type":"mouse_raw","t":1,"dx":1e999,"dy":0} | dx type float RAISED OverflowError cannot convert float infinity to integer
{"type":"mouse_raw","t":1,"dx":11111111111111111111111111111 | dx type int RAISED OverflowError int too large to c

---
## [MINOR] CONFIRMED — `translator/binner.py:80` (lane translator, refuted 0/2)

**raw_int() promises "DEGRADE, never crash" but int(float(v)) raises OverflowError, still quarantining the session**

**CLAIM:** `raw_int` catches only `(TypeError, ValueError)`. `int(float(v or 0))` raises **OverflowError** — not a subclass of either — for a JSON float infinity or an oversized integer. Measured: `int(float(1e999))` -> `OverflowError: cannot convert float infinity to integer`; `int(float(10**400))` -> `OverflowError: int too large to convert to float`. Note `json.loads` accepts `Infinity`/`-Infinity` literals by default. Driven through the real checker on HEAD with a valid session plus one injected inputs.jsonl line: `{"t": 20000000, "type": "mouse_raw", "dx": 1e999, "dy": 0}` -> `check_session_v2` raises `OverflowError ... @ binner.py:80`; same for a literal `Infinity` and for a 400-digit integer. The r-loop-7 test `test_raw_numeric_fields_degrade_to_zero` parametrizes `["1.0", "abc", [1], {"a": 1}, None, True]` — all of which I confirmed degrade correctly — but no value that overflows.

**SCENARIO:** One such value in a player-supplied inputs.jsonl. The raise escapes `_verify_against_raw` -> `check_session_v2` -> `analyze()` -> `validate.validate_session` -> `run._validate_worker`, which classifies anything that is not OSError/MemoryError/sqlite3.OperationalError as `kind='crash'` (run.py:180), and run.py:331 / continuous.py:775 then write **QUARANTINED "validation crashed"** — terminal, no automatic re-entry, media held for CONT_QUARANTINE_RECLAIM_H, manual queue. That is verbatim the outcome the raw_int docstring says it exists to prevent, and it happens for a session that (with the value degraded to 0) reports WARN and would otherwise ship. It also raises out of `bin_session`, so both retranslate attempts fail too. Input plausibility is lower than the string/container cases r-loop 7 covered — I could not point at a capture-tool path that emits it — so I am scoring it minor, but the guard's stated contract is provably broken.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Widen the except to `(TypeError, ValueError, OverflowError)` in `translator/binner.py:81`, or clamp: `f = float(v or 0); return int(f) if math.isfinite(f) else 0`. Add `1e999`, `float('inf')`, and a 400-digit int to the `test_raw_numeric_fields_degrade_to_zero` parametrize list.

- refuter (exec): refuted=False confidence=high
  - evidence: SOURCE (translator/binner.py:66-82), read in full:

    def raw_int(v) -> int:
        """Coerce ONE untrusted inputs.jsonl numeric field.
           DEGRADE, never crash — ..."""
    79:    try:
    80:        return int(float(v or 0))
    81:    except (TypeError, ValueError):
    82:        return 0

Line 80 is exactly the line cited. OverflowError is caught by neither arm:
  python3 -c "print(issubclass(OverflowError,(OSError,MemoryError)), OverflowError.__mro__)"
  -> False (<class 'OverflowError'>, <class 'ArithmeticError'>, <class 'Exception'>, <class 'BaseException'>, <class 'object'>)
- refuter (harm): refuted=False confidence=high
  - evidence: MECHANISM (primitive, real output):
  python3: json.loads('{"a": 1e999}') -> {'a': inf};  json.loads('{"a": Infinity}') -> {'a': inf}
  int(float(float('inf') or 0)) -> OverflowError: cannot convert float infinity to integer
  int(float((10**400) or 0))    -> OverflowError: int too large to convert to float
translator/binner.py:79-82 catches only (TypeError, ValueError); OverflowError is an ArithmeticError, caught by neither.

END-TO-END THROUGH THE REAL CHECKER (script in scratchpad, session built in /tmp; nothing in the repo touched). Real 60-frame mp4, frames.csv on real PTS, raw/ bundle wi

---
## [MINOR] CONFIRMED — `translator/keys.py:93` (lane translator, refuted 0/2)

**normalize_literal crashes on a non-string modifier/key — r-loop 7's VK-code fallback covers only the flat form**

**CLAIM:** r-loop 7's keybind fix (translate.py:85-104) names "VK codes" as a handled class and its test parametrizes `{"move_up": 87, "interact": 69}`. That works because `_binding_groups` returns [] for a bare int, so `bound_literals` is empty and the built-in fallback fires. But for the documented `{modifier, key}` binding shape, `_binding_groups` (keybind.py:62-72) passes the raw values into `normalize_literal`, which does a bare `raw.strip().lower()` at keys.py:93. Fuzzed `translate_bundle_v2` on HEAD with real keybind.json files: `{"move_up": 87}` -> OK (falls back, prints the warning); `{}`, wrapper object, nulls, a JSON array -> OK (fall back). But `{"move_up": {"modifier": null, "key": 87}}`, `{"move_up": {"modifier": 16, "key": "w"}}` and `{"move_up": [{"modifier": "ctrl", "key": 87}]}` all -> `RAISE AttributeError: 'int' object has no attribute 'strip' @ keys.py:93`. `invert_keybind` (keybind.py:198-204) has the identical unguarded call.

**SCENARIO:** A contributor or a controls-export tool writes keybind.json in the combo shape with VK numbers instead of key names — the same authoring mistake r-loop 7 already observed in the flat shape. `resolve_keybind` is now the FIRST thing to touch it (the r-loop-7 `bound_literals(kb)` pre-check moved the crash earlier), so it raises out of both `translate_bundle_v2` (FIX_TRANSLATE_RAW) and `retranslate_from_sidecars` (FIX_RETRANSLATE) before any keybind fallback can run. `apply_fixes` classifies AttributeError as kind='session', both attempts burn identically, and the session is terminally rejected under the bare fix-failed marker — instead of taking the built-in-keybind fallback that the flat VK-code form gets.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Make `normalize_literal` type-safe at keys.py:93 (`if not isinstance(raw, str): return ""`), which lets `_binding_groups` drop the unusable token and hand `bound_literals` an empty set so the r-loop-7 built-in fallback fires as designed. Extend the `test_unusable_keybind_falls_back_to_the_builtin` parametrize list with `{"move_up": {"modifier": 16, "key": 87}}` and `{"move_up": [{"modifier": "ctrl", "key": 87}]}`.

- refuter (exec): refuted=False confidence=high
  - evidence: All three claimed crashes reproduce on HEAD (869910d), at the exact line named, with the exact error text.

1) The unguarded call is real. /Users/adnaan/Documents/hl-projects/hl-gamedata/translator/keys.py:91-94

    def normalize_literal(raw: str) -> str:
        """Keybind literal ("LCtrl", "Spacebar", "MouseRight") -> canonical token."""
        s = raw.strip().lower()                     # <- line 93
        return _LITERAL_ALIASES.get(s, s)

Its sibling normalize_event_key (keys.py:71-75) WAS hardened by r-loop 7 with an explicit `if not isinstance(raw, str): return None` and a comment na
- refuter (harm): refuted=False confidence=high
  - evidence: MECHANISM — reproduced at HEAD (869910d), real output.

/Users/adnaan/Documents/hl-projects/hl-gamedata/translator/keys.py:91-94 is unguarded:
    def normalize_literal(raw: str) -> str:
        s = raw.strip().lower()
Its sibling normalize_event_key WAS hardened by r-loop 7 (keys.py:71-75): "a numeric or container key code from a hand-edited/foreign-tool sidecar raised AttributeError straight out of bin_session (r-loop 7); None is the same answer an unrecognised key gets". normalize_literal was left bare.

Driving real keybind.json files through the real entry point resolve_keybind (translate

---
## [MINOR] CONFIRMED — `translator/v2.py:211` (lane translator, refuted 1/2)

**translate_bundle_v2 reads metadata.json with zero guards — the raw-only fix path crashes where its r-loop-7-hardened sibling fails attributably**

**CLAIM:** r-loop 7 taught `retranslate_from_sidecars` (fix.py:701-720) that `recording.started_at_utc` is player-supplied and must fail attributably: `_utc(ts)` returns None for a non-str/unparseable stamp and raises a typed `FixFailed("cannot derive the head offset ...")`. The twin read in `translate_bundle_v2` has no guard at all. Fuzzed on HEAD, every one of these raises out of the function: `recording` key absent / `started_at_utc` absent / null / a number -> `AttributeError: 'NoneType'|'int' object has no attribute 'replace' @ v2.py:45` (via v2.py:211); a non-ISO stamp -> `ValueError @ v2.py:45`; `recording` a list -> `AttributeError @ v2.py:210`; `system` a list or `screen_width: "1920x1080"` -> `AttributeError`/`ValueError @ v2.py:227`; `game` a list or the whole file a JSON array -> `AttributeError @ v2.py:248-249`; a truncated file -> `JSONDecodeError @ v2.py:246`. Nothing upstream parses it first: `ingest.sniff_payload` (ingest.py:735-748) returns "raw" purely on file EXISTENCE.

**SCENARIO:** A raw-only upload (a real, expected arrival class — ARR_RAW_ONLY exists for it) whose metadata.json is truncated by a partial Drive upload or carries an odd block. Ran through the real lane: `sniff_payload -> 'raw'`, then `fix.apply_fixes(work, {'steps':[('FIX_TRANSLATE_RAW',{})]})` returns `error="FIX_TRANSLATE_RAW: JSONDecodeError: Unterminated string...", kind='session'` and `error="FIX_TRANSLATE_RAW: AttributeError: 'NoneType' object has no attribute 'replace'", kind='session'`. Two harms. (1) Ops clarity: the operator gets a Python exception name instead of the named field, on the one path where the sibling was fixed to name it. (2) A genuinely deliverable session dies: `screen_width`/`system` feed only the cosmetic `screen_width_px` (which already falls back to `info.width`), yet a malformed `system` block terminally rejects the whole session under the bare fix-failed marker. Amplifier: `fix_translate_raw` only calls `shutil.rmtree(out)` on the success path, so each failed attempt leaves `work/<sid>/_translated/.../video.mp4` — a full trimmed copy of the source video — sitting in the work dir (confirmed present after the crash run), inflating the continuous driver's media accounting until the terminal wipe.

**PROPOSED FIX (unvetted — reviewers' fixes have been wrong before):** Give `translate_bundle_v2` the same guards its sibling got: wrap the `metadata.json` parse in try/except (JSONDecodeError, OSError) and `isinstance(meta, dict)`; use `(meta.get("recording") or {})` / `(meta.get("system") or {})` / `(meta.get("game") or {})` with isinstance checks; coerce `screen_width`/`screen_height` through the same tolerant `_num`-style cast analyze_sample.py already uses; and raise one typed error naming `recording.started_at_utc` when it is missing or unparseable, exactly as fix.py:717-720 does. Separately, wrap the `out` tree in fix_translate_raw in try/finally so the temp copy is removed on failure.

- refuter (exec): refuted=False confidence=high
  - evidence: All work done in the scratchpad; nothing in the repo was modified. HEAD = 869910d.

=== 1. The code, at the exact lines cited (all citations verified exact) ===
translator/v2.py:210-211
    started_raw = meta.get("recording", {}).get("started_at_utc")
    started = _utc_aware(started_raw)
translator/v2.py:45   d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
translator/v2.py:227  "screen_width_px": int(system.get("screen_width") or info.width),
translator/v2.py:246  meta = json.loads((bundle_dir / "metadata.json").read_text(
translator/v2.py:248-249  game_info = meta.get("game", {}) / gam
- refuter (harm): refuted=True confidence=high
  - evidence: All probing done in /tmp/rl8probe; repo untouched (git status identical to the session snapshot).

A. MECHANISM — reproduced exactly as claimed (granted by Lens B anyway). Real bundle (320x240/30fps/20s h264 + inputs.jsonl + variant metadata.json), `translate_bundle_v2(bundle, out, make_rrd=False)`:
```
good           OK   out_dir=/tmp/rl8probe/out_good/humynlabs/08-18-2026/kamla/kamla_2026-08-18T10-00-00Z
no_recording   RAISE AttributeError: 'NoneType' object has no attribute 'replace'  @ v2.py:45
null_started   RAISE AttributeError: 'NoneType' object has no attribute 'replace'  @ v2.py:45
nu

