# GCP VM offload — exploration only

Written 2026-08-14 ~23:25 IST. **No GCP resource was created for this
document; no production code changed.** Web claims are marked `[web]` with
the page they came from; arithmetic is script-computed (session scratchpad).

## 1. What it is — very simply

Today all the video flows through the home internet line twice (down from
Drive, up to Drive) and all the checking runs on the Mac. The offload idea:
rent a computer that lives inside Google's own building — the same company
that runs Google Drive. For that rented computer, copying a file from Drive
is like carrying it across a hallway instead of across the city: hundreds
of megabits instead of our 60. The rented computer does the downloading,
checking, and uploading; the Mac keeps only the phone and the clipboard —
it receives the Telegram reports and holds backup copies of the records.

It would take the four biggest risks off the table at once: the slow home
line, the ISP's unknown data cap, the Mac falling asleep, and the Mac
overheating. The price is real but small (roughly ₹11k / ~$135 for the
whole 10 days, on-demand) — because, verified below, Google does **not**
charge the rented computer for talking to Google Drive.

## 2. Pros and cons — honest

**Pros**

- Home line, bufferbloat (880–892 ms loaded), the unknown ISP data cap
  (~6.3 TB total program transfer, `BOTTLENECK_FINDINGS.md` §4), Mac sleep
  and Mac thermals all leave the risk list in one move.
- Transfers stop mattering: even the current **lockstep** flow reaches
  ~121–176 fh/day on a VM at conservative 300–1000 Mbps effective Drive
  throughput (vs 67–78 on the Mac) — the required 100–111 clears **without
  amending R5** and without the streaming driver. The Mac becomes free for
  Adnaan's normal work.
- Runs 24/7 by definition — no launchd-on-wake catch-up, no caffeinate.
- Cheap: ~$129 + ~$7 disk for 10 days on-demand (table below); "tens of
  dollars" on spot.

**Cons — each one real**

- **Migration risk mid-crunch.** Linux box to provision (ffmpeg, uv,
  numpy/opencv-headless/rerun-sdk, rclone, repo, secrets) and the whole
  pipeline re-validated there, days before the deadline. macOS→Linux
  differences are small in this codebase (no caffeinate, paths) but "small"
  is not "zero", and every debugging hour costs ~110 delivered hours of
  pace.
- **A second machine to operate and secure.** The Drive service-account
  key (content manager on BOTH drives) and the Gemini key move onto a
  cloud VM. That VM must be locked down (no public inbound, OS login via
  IAP/SSH keys only) and both keys rotated after the crunch as hygiene.
  A leaked SA key = write access to the delivery drive and read of all
  collection PII.
- **What the Mac still does** must stay honest: Telegram + reports send
  fine from the VM, but the ledger/dossiers then LIVE on the VM. The Mac
  should pull a daily ledger backup + dossier sync (rsync over SSH), or a
  VM disk loss re-derives everything from Drive I + re-validation (possible
  — Drive I is the archive of record — but costs a day).
- **If the VM dies mid-run**: state-driven resume works exactly as on the
  Mac (same ledger states, same idempotent rclone), but nobody's laptop
  chimes — needs a systemd timer (the launchd equivalent) plus the
  existing Telegram alerts, and a human who reacts. Spot instances add
  forced preemptions to this; for ~$90 saved over 10 days, spot is the
  wrong trade during a deadline — **on-demand**.
- Gemini quota/billing tier question is unchanged (follows the key's
  project, not the caller's location) — the VM doesn't fix 429s, only
  Task 4's failover and the §13 policy do.

## 3. Verified detail

### 3a. The make-or-break: GCE↔Drive traffic is NOT internet egress

From Google's own network pricing page, fetched raw 2026-08-14 ~23:15 IST
`[web: cloud.google.com/vpc/network-pricing]`, General network pricing
table, quoted verbatim:

> "Data transfer to specific Google products such as Gmail, YouTube,
> Google Maps, DoubleClick, and Google Drive, whether from a VM in Google
> Cloud with an external IP address or an internal IP address. — **No
> charge**"

Ingress (Drive→VM downloads) is free as a rule. For scale, the same page's
internet rates (data transfer out TO North America): $0.12/GiB up to
1 TiB/mo, $0.11/GiB to 10 TiB — if Drive uploads *were* billed as
internet, our ~2,910 GiB delivery leg would be ~$330; it is instead $0.
Residual caveat, stated not hidden: the row names the *product* "Google
Drive"; our traffic goes to the Drive **API** (`www.googleapis.com`) via
rclone. This is universally read as the same thing, but the day-1
verification is checking the billing page after the first ~100 GB moves —
cost of being wrong is ~$0.11/GiB from then on, caught within a day.

Also verified, applies to VM and Mac alike: **each user (the service
account counts as one) can upload at most 750 GB to Drive per 24 h**
`[web: knowledge.workspace.google.com/admin/drive/storage-and-upload-limits-for-google-workspace]`.
Peak delivery at 130 fh/day ≈ 407 GB/day up — inside the cap, but a
backlog-flush day cannot exceed ~240 fh of uploads.

### 3b. VM sizing and cost

From the measured M5 numbers (`BOTTLENECK_FINDINGS.md` §2: one full
1080p30→160×90 decode pass = 47.5 CPU-s per 348.2 s of footage), with
[assumption] 3–4 decode-equivalent passes per session + 15% Python/opencv
overhead, and [assumption] a cloud vCPU at 2–3× slower than an M5 P-core
(cloud vCPUs are SMT threads — deliberately conservative):

| Estimate | vCPU-h per footage-h | vCPUs busy @130 fh/day | 16-vCPU ceiling |
|---|---|---|---|
| low | 0.94 | 5.1 | 408 fh/day |
| high | 1.88 | 10.2 | 204 fh/day |

→ **e2-standard-16 (16 vCPU / 64 GB)** covers 130 fh/day with 1.6–3×
headroom even at the pessimistic end. Prices `[web: cloudprice.net +
economize.cloud, 08-14]`: on-demand **$0.5361/h ≈ $129 for 10 days**; spot
quoted $0.3217/h (~$77) by one source and ~$0.16/h (~$38) by historic
norms — **sources disagree; treat spot as "$38–77"** and irrelevant anyway
(on-demand recommended above). 200 GB pd-balanced disk ≈ $7/10 days.
**Total ≈ $135–140 on-demand.** The earlier "tens of dollars" floated
figure was right for spot, slightly optimistic for on-demand.

### 3c. Architecture sketch

**Everything moves; the Mac watches.** (A split — Mac keeps ledger, VM does
transfers — creates two writers to one state and re-imports the home line
into the loop; rejected.)

- VM (e2-standard-16, on-demand, region `asia-south1` Mumbai or
  `us-central1` — Drive traffic is free either way; Mumbai keeps SSH snappy
  from IST): Debian, `apt install ffmpeg rclone`, install `uv`, clone the
  repo, `HL_PIPELINE_HOME=~/hl-pipeline`, run under a **systemd timer**
  every 30 min exactly like launchd (the plist is not loaded on the Mac
  anyway; the run-lock already guarantees single-instance).
- Secrets: `scp` `~/.config/hl-gamedata/{sa.json,secrets.env}` (chmod 600).
  Same SA (R18), same remotes config. **Rotate both keys after Phase 1.**
- VLM from the VM: fine — the Gemini key works from anywhere (quota is
  per key/project), and Task 4's Vertex express failover is
  location-independent.
- Mac's remaining jobs: daily `rsync` pull of `ledger.db` backup +
  `dossiers/` + `reports/`; Adnaan shares reports as today (R12).
  Telegram flows straight from the VM.
- Kill switch: stop the systemd timer, `rsync` state back, relaunch on the
  Mac — the ledger-state design makes the pipeline machine-portable by
  construction.

### 3d. Recommendation

**Do not migrate now. Prepare the trigger.** The Mac + the Task 1B overlap
fix already clears required pace with ~1.6× margin, with zero migration
risk and zero new secrets exposure. The VM is the right *insurance*, not
the right first move — it exists for the failure modes the Mac can't
control (line degradation, ISP cap, thermal/sleep surprises).

Concrete adoption trigger — adopt the VM if ANY of:
1. measured throughput over the first 24 h of real running (pace report's
   trailing line) **< 120 fh/day** while backlog exists (needed 111 from
   Aug 16 + margin); or
2. the ISP cap/throttle shows itself (sudden sustained line-speed drop, or
   a data-cap warning); or
3. cumulative pipeline downtime from sleep/thermals/home-network outages
   exceeds ~6 h in any 24 h window.

Prep now (30 min, no GCP resources until triggered): keep this doc's §3c
as the runbook; pre-write the provisioning script into `tools/`; Adnaan
pre-enables billing on the existing `hl-gamedata-pipeline` project so the
trigger-to-running gap is ~1 h, not a day.
