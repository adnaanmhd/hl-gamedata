# Note for hl-gamedata-d3 (or successor) — from the build session, 2026-08-15

Your last-known socket (`uds:/tmp/cc-socks/55364.sock`) is stale and no successor session
was listed when review iteration 5 landed (commit `662e05b`, deployed to the VM, suite 307
green on both hosts). Three things you need:

## 1. Payment-sheet stamping semantics changed under r5 — adopt in your acceptance protocol

- Stamps (`uploaded_reported_at`) are now written for **EXACTLY the roots the sheet
  counted**: `build_sheet_rows(..., counted_out=...)` → `mark_uploads_reported(..., sids=counted)`.
  No more stamp-time re-derive (it raced the D thread).
- `in_window` additionally requires the root be **unstamped** (anchor loss/rewind can never
  re-count).
- `supersede()` and the quarantine heal now **clear the stamp** (a re-upload restarts
  late-arrival accounting).
- **Late arrivals DEFER until their tree is settled** (logged `LATE ARRIVAL DEFERRED`);
  terminal REJECTED never-downloaded roots now count as late arrivals once.
- Consequence for you: your "moves to tomorrow" figure may shift by one window for
  unsettled cohorts, and your byte-identical diff against `payment-2026-08-15.csv` will
  show these deltas **until you regenerate the reference** from a fresh ledger snapshot.
- Persistence order in `send_daily_report_if_due` is now **stamps → anchor → marker**
  (BLOCKER fix — anchor-first double-counted a window on kill/resend).

## 2. Folder-issues start-date decision — still needed from Adnaan

Today's (08-15) first-ever send is HELD (`~/hl-pipeline/reports/2026-08-15/.issues-sent`
touched manually 19:33 IST; verified it survived the deploy). Nobody unholds without
Adnaan's call: delete the marker for today's to fire, or leave it and tomorrow's fires.
His answer never reached the build session — if you have it, send it to the current
hl-gamedata build session (check ListAgents) or leave a reply note here.

## 3. Also new since your last sync

- `send_folder_issues_if_due` now fires from the overlap end-of-run + `daily-report` CLI
  (idle ticks used to skip it entirely) and requires today's payment `.sent` marker first.
- The folder-issues message degrades to counts-only above 3500 chars.
- A separate documentation session (`hl-gamedata-b0`) wrote `PIPELINE_ARCHITECTURE.md`
  (repo root, untracked) describing the post-r5 semantics — consistent with the above.
