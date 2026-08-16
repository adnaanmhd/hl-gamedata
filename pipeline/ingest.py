"""Phase I — ingestion from Shared Drive I (plan §9).

Scan -> parse/quarantine -> completeness (R4) -> dedupe/supersede ->
batch (FIFO + lagging-game priority) -> download + checksum verify + payload
sniff. Drive I is READ-ONLY forever (R6): nothing is ever written or deleted
there, and no status files land in it.

Path contract (§2, Q1/Q5 as amended 08-15):
`<game>/<operator_NAME>/<player_email>/<session>/` with the game folders
directly at the drive root. Operator folders are FREE-TEXT NAMES (Q5
amendment); player folders stay strict emails and session folders stay the
strict id pattern — the junk guard lives one level down. Bad game token,
non-email player, or malformed session id -> QUARANTINED + report line.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config as C
from .ledger import Ledger

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SESSION_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z_[a-z0-9_]+_c_[0-9a-f]{16}$")
_ZIP_PART_RE = re.compile(r"\.zip([.-]?\d{3})?$|\.z\d{2}$", re.IGNORECASE)

# the missing_json a zip-mid-upload incomplete row carries (written by the
# driver's zip_incomplete handler; scan's resolve deliberately skips rows
# bearing exactly this, so their first_seen — the F8 >48h escalation clock —
# survives the folder "listing complete" while parts are still arriving
ZIP_PARTS_MARKER = "zip parts incomplete"


def run_rclone(args: list[str], *, timeout_s: int = 3600
               ) -> subprocess.CompletedProcess:
    """Single choke point for rclone — tests monkeypatch this.

    A timeout comes back as a failed CompletedProcess (rc 124) so callers'
    normal retry/quarantine paths handle it — an uncaught TimeoutExpired
    would wedge the whole run every 30 minutes (review finding #5)."""
    try:
        return subprocess.run(["rclone", *args], capture_output=True,
                              text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            ["rclone", *args], 124, "", f"timed out after {timeout_s}s")


def _md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DriveSession:
    session_id: str
    game: str
    operator_email: str
    player_email: str
    drive_path: str            # remote-relative dir path
    ctime: str                 # Drive createdTime (--drive-use-created-date)
    files: dict[str, dict]     # name -> {size, md5, ctime}
    payload: str = "files"     # "files" | "zip"
    slug_game: str = ""        # game token from the session-id (Phase II check)


@dataclass
class ScanResult:
    discovered: list[str] = field(default_factory=list)
    incomplete: list[tuple[str, list[str]]] = field(default_factory=list)
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    dup_cross: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    integrity_flags: list[str] = field(default_factory=list)
    out_of_tree: int = 0


def list_drive(cfg: C.Config) -> list[dict]:
    """Full recursive listing of Drive I with md5s and CREATED times.

    --drive-use-created-date swaps ModTime for Drive createdTime, which is
    what the dedupe/FIFO rules key on (F3).
    """
    p = run_rclone(["lsjson", "-R", "--hash", "--drive-use-created-date",
                    cfg.remote_collect])
    if p.returncode != 0:
        raise RuntimeError(f"rclone lsjson failed: {p.stderr.strip()[:300]}")
    return json.loads(p.stdout or "[]")


def parse_listing(entries: list[dict]) -> tuple[list[DriveSession],
                                                list[tuple[str, str]], int]:
    """entries -> (sessions, quarantined [(path, why)], out_of_tree count).

    A session dir is any directory at depth 4 under a scoped game tree that
    contains files. Junk outside kamla/ & outer_wilds/ is ignored (counted) —
    the pre-program cutoff targets exactly that (§9.1).
    """
    files_by_dir: dict[str, list[dict]] = {}
    for e in entries:
        if e.get("IsDir"):
            continue
        parent = str(Path(e["Path"]).parent)
        files_by_dir.setdefault(parent, []).append(e)

    sessions: list[DriveSession] = []
    quarantined: list[tuple[str, str]] = []
    out_of_tree = 0
    seen_dirs: set[str] = set()

    for dirpath, files in sorted(files_by_dir.items()):
        parts = Path(dirpath).parts
        if not parts or parts[0] not in C.GAMES:
            out_of_tree += len(files)
            continue
        if len(parts) != 4:
            quarantined.append((dirpath,
                                f"path depth {len(parts)} != 4 "
                                f"(want game/operator/player/session)"))
            continue
        game, op, player, sess = parts
        why = None
        # operator level: free-text names by ruling (Q5 amended 08-15) —
        # no format check; junk detection relies on the two levels below
        if not _EMAIL_RE.match(player):
            why = f"player folder {player!r} is not an email"
        elif not _SESSION_RE.match(sess):
            why = f"session folder {sess!r} doesn't match the id pattern"
        if why:
            quarantined.append((dirpath, why))
            continue
        if dirpath in seen_dirs:
            continue
        seen_dirs.add(dirpath)
        fmap = {f["Name"]: {"size": f.get("Size", 0),
                            "md5": (f.get("Hashes") or {}).get("md5", ""),
                            "ctime": f.get("ModTime", "")}
                for f in files}
        payload = "files"
        if "video.mp4" not in fmap and any(_ZIP_PART_RE.search(n)
                                           for n in fmap):
            payload = "zip"
        m = re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z_(.+)_c_[0-9a-f]{16}$",
                     sess)
        ctimes = sorted(v["ctime"] for v in fmap.values() if v["ctime"])
        sessions.append(DriveSession(
            session_id=sess, game=game, operator_email=op,
            player_email=player, drive_path=dirpath,
            ctime=ctimes[0] if ctimes else "",
            files=fmap, payload=payload,
            slug_game=m.group(1) if m else ""))
    return sessions, quarantined, out_of_tree


def _completeness(ds: DriveSession) -> list[str]:
    """Missing required files ([] = complete). Zips defer the check to
    post-unzip (§9.2); Drive shows files only once fully uploaded (R4), so
    presence is a sound completeness signal."""
    if ds.payload == "zip":
        return []
    return [f for f in C.REQUIRED_FILES if f not in ds.files]


def scan(cfg: C.Config, ledger: Ledger,
         entries: list[dict] | None = None) -> ScanResult:
    """One scan pass: discover new sessions, track incompletes, dedupe,
    supersede, quarantine. Ledger is updated; ScanResult feeds the report."""
    if entries is None:
        entries = list_drive(cfg)
    sessions, quarantined, out_of_tree = parse_listing(entries)
    res = ScanResult(out_of_tree=out_of_tree)

    # every directory visible in THIS listing, ancestors included — the
    # move-heal below keys on a registered path being GONE from the drive
    # (a move removes the old path; a copy leaves both) (review-r4 #6)
    listed_dirs: set[str] = set()
    for e in entries:
        d = Path(e["Path"]) if e.get("IsDir") else Path(e["Path"]).parent
        while str(d) not in (".", "/"):
            listed_dirs.add(str(d))
            d = d.parent

    for path, why in quarantined:
        base = Path(path).name
        # bare-basename ids collide: two players' junk subfolders both
        # named "out" collapsed onto one PK row and the second misupload
        # vanished from the chase list (review-r5 #37b). Non-session-
        # shaped names get a path-derived suffix; real session ids stay
        # bare so the quarantine HEAL (same sid at a clean path) works.
        sid = base if _SESSION_RE.match(base) else \
            f"{base[:40]}~{hashlib.md5(path.encode()).hexdigest()[:8]}"
        legacy = ledger.get(base)
        if ledger.get(sid) is None and not (
                legacy is not None and legacy["drive_path"] == path):
            ledger.insert_session(
                session_id=sid, game="", operator_email="", player_email="",
                drive_path=path, drive_ctime="", md5_video="", bytes_=0,
                state="QUARANTINED", detail=why)
            ledger.set_reasons(sid, [{"code": "INT_PATH", "blocking": True,
                                      "fixable": False, "params": {},
                                      "evidence": why}], 3)
        res.quarantined.append((path, why))

    for ds in sessions:
        missing = _completeness(ds)
        if missing:
            ledger.incomplete_seen(ds.drive_path, missing)
            res.incomplete.append((ds.drive_path, missing))
            continue
        # a zip upload "lists complete" while its parts are still arriving;
        # only a successful download proves it (download resolves the row).
        # Deleting here reset first_seen every tick, so the F8 >48h
        # escalation never fired for stuck zips (Adnaan ruling 08-15)
        if ledger.incomplete_missing(ds.drive_path) != [ZIP_PARTS_MARKER]:
            ledger.incomplete_resolved(ds.drive_path)

        vmd5 = ds.files.get("video.mp4", {}).get("md5", "")
        total_bytes = sum(v["size"] for v in ds.files.values())
        existing = ledger.get(ds.session_id)

        if existing is not None:
            # the supersede rule (§9.1) is for the SAME upload slot — a
            # different Drive path claiming a known session id is an
            # identity collision, never a supersede (cross-identity
            # copies must go through INT_DUP_CROSS, not slip past it)
            if existing["drive_path"] and \
                    existing["drive_path"] != ds.drive_path:
                if existing["state"] == "QUARANTINED":
                    # the id was first seen at a MALFORMED path (bad depth/
                    # player folder) and quarantined; the operator has now
                    # fixed the tree and the same session parses clean at
                    # its proper path. Blocking it forever punishes the
                    # correction (review-r3 #7) — re-register. Quarantines
                    # struck AFTER download (validation/delivery crash)
                    # may have seeded the sid's shift record, and the
                    # healed re-upload's bytes can differ — drop it
                    # (review-r4 #7)
                    from .validate import _locked_report_remove
                    _locked_report_remove(
                        cfg.work / "translation_report.json",
                        ds.session_id)
                    # a heal is a FRESH-upload event: reset the slot like
                    # supersede does. The old row's burned fix_attempts
                    # auto-rejected the corrected upload with zero new
                    # attempts (review-r5 #23), stale durations counted
                    # the OLD bytes on payment sheets, and an inherited
                    # uploaded_reported_at blocked the new hours from the
                    # late-arrival guard forever (review-r5 #7)
                    ledger.update(ds.session_id,
                                  drive_path=ds.drive_path,
                                  drive_ctime=ds.ctime, md5_video=vmd5,
                                  bytes=total_bytes,
                                  operator_email=ds.operator_email,
                                  player_email=ds.player_email,
                                  game=ds.game, fix_attempts=0,
                                  duration_raw_s=None,
                                  duration_delivered_s=None,
                                  rrd_sampled=0, delivered_at=None,
                                  uploaded_reported_at=None)
                    # the INT_PATH reasons died with the bad path — left
                    # in place, a LATER re-quarantine (download/validation
                    # crash) put this session on the folder-issues bad_path
                    # list with evidence about a name that is already
                    # fixed; supersede resets reasons the same way
                    # (folder-issues review #3)
                    ledger.set_reasons(ds.session_id, [], None)
                    ledger.set_state(
                        ds.session_id, "DISCOVERED",
                        f"re-registered: quarantined path healed to "
                        f"{ds.drive_path}")
                    # post-download quarantines (download/validation crash,
                    # md5 mismatch, garbage payload) keep a populated work
                    # dir; the fresh download would merge stale payload
                    # files — old raw/ sidecars feeding FIX_RETRANSLATE —
                    # into the new upload. Same wipe both supersede sites
                    # perform (review-r3 #6, completed by review-r4 #21)
                    shutil.rmtree(cfg.work / ds.session_id,
                                  ignore_errors=True)
                    shutil.rmtree(cfg.work / f"{ds.session_id}-analysis",
                                  ignore_errors=True)
                    res.integrity_flags.append(
                        f"{ds.session_id}: quarantined path healed — "
                        f"re-registered at {ds.drive_path}")
                    res.discovered.append(ds.session_id)
                    continue
                if existing["state"] in ("DISCOVERED", "INCOMPLETE") \
                        and existing["drive_path"] not in listed_dirs \
                        and ((vmd5 == existing["md5_video"])
                             if (vmd5 and existing["md5_video"])
                             else existing["player_email"]
                             == ds.player_email):
                    # pre-download MOVE heal (review-r4 #6): the old path
                    # is gone from this listing — an operator folder
                    # rename (free-text names, so typo fixes are routine)
                    # moved the whole subtree. Without this the row kept
                    # the dead path and every download retried it forever.
                    # Gated three ways: pre-download states only (anything
                    # further along works from its local copy and keeps
                    # its row), old path ABSENT (a copy leaves both paths
                    # listed — that stays a collision below), and md5
                    # match when both sides know it. When EITHER side
                    # lacks an md5 (zip payloads), the PLAYER segment must
                    # be unchanged — with no byte identity anywhere, a
                    # same-id folder in another player's tree would
                    # otherwise flip payment attribution and deliver
                    # unverifiable bytes (review-r5 #41); an operator
                    # typo-rename keeps the player, so the motivating
                    # case still heals.
                    ledger.update(ds.session_id,
                                  drive_path=ds.drive_path,
                                  drive_ctime=ds.ctime,
                                  md5_video=vmd5 or existing["md5_video"],
                                  bytes=total_bytes,
                                  operator_email=ds.operator_email,
                                  player_email=ds.player_email,
                                  game=ds.game)
                    # same-state set_state = audit event: drive_path and
                    # attribution changed on a payment-bearing row
                    ledger.set_state(
                        ds.session_id, existing["state"],
                        f"drive folder moved — re-pointed from "
                        f"{existing['drive_path']} to {ds.drive_path}")
                    res.integrity_flags.append(
                        f"{ds.session_id}: drive folder moved — "
                        f"re-pointed to {ds.drive_path}")
                    continue
                res.integrity_flags.append(
                    f"session-id collision: {ds.drive_path} reuses "
                    f"{ds.session_id} already registered at "
                    f"{existing['drive_path']} — ignored + flagged"
                    + (" (identical video md5 — cross-identity copy)"
                       if vmd5 and vmd5 == existing["md5_video"] else ""))
                continue
            if vmd5 and existing["md5_video"] and vmd5 != existing["md5_video"]:
                if existing["state"] in ("REJECTED", "QUARANTINED"):
                    # the replacement video must pass the SAME dedupe bar as
                    # a fresh upload — else a rejected slot becomes a side
                    # door for someone else's already-delivered bytes
                    # (review-2 finding #1)
                    other = [r for r in ledger.by_md5(vmd5)
                             if r["session_id"] != ds.session_id
                             and r["state"] not in ("QUARANTINED",)]
                    if other:
                        res.integrity_flags.append(
                            f"{ds.session_id}: rejected-slot re-upload "
                            f"carries the same video md5 as "
                            f"{other[0]['session_id']} — not superseding "
                            f"(INT_DUP_CROSS)")
                        continue
                    ledger.supersede(ds.session_id, new_md5=vmd5,
                                     new_bytes=total_bytes,
                                     new_ctime=ds.ctime,
                                     dossier_root=cfg.dossiers)
                    # a stale work dir from an unfinalized reject would
                    # merge old payload files into the fresh download
                    # (review-r3 #6)
                    shutil.rmtree(cfg.work / ds.session_id,
                                  ignore_errors=True)
                    shutil.rmtree(cfg.work / f"{ds.session_id}-analysis",
                                  ignore_errors=True)
                    # ... and the sid's entry in the SHARED shift record:
                    # left behind, _seed_shift_record early-returns on it
                    # and qa validates the REPLACEMENT bytes against the
                    # OLD upload's shift — spurious SYN_TS_NOT_PTS, a
                    # burned fix attempt and a paid VLM sweep
                    # (review-r4 #7)
                    from .validate import _locked_report_remove
                    _locked_report_remove(
                        cfg.work / "translation_report.json",
                        ds.session_id)
                    res.superseded.append(ds.session_id)
                    res.discovered.append(ds.session_id)
                else:
                    res.integrity_flags.append(
                        f"{ds.session_id}: re-upload with different md5 while "
                        f"state={existing['state']} — not superseding "
                        f"(supersede applies after a reject/quarantine only)")
            elif ds.payload == "zip" \
                    and existing["state"] in ("REJECTED", "QUARANTINED") \
                    and (total_bytes != (existing["bytes"] or 0)
                         or (ds.ctime or "") >
                         (existing["drive_ctime"] or "")):
                # zip payloads carry no Drive-side video md5, which made
                # the md5-based supersede unreachable — a corrected
                # re-upload after a reject was silently ignored forever
                # (review-r2 #9; QUARANTINED slots included, review-r3
                # #17 — a bad-archive quarantine is exactly what a
                # re-upload corrects). Changed bytes or a newer
                # createdTime is the re-upload signal; the download-time
                # dedupe re-checks the fresh md5 against everyone else,
                # so the review-2 #1 side-door stays closed.
                ledger.supersede(ds.session_id, new_md5="",
                                 new_bytes=total_bytes,
                                 new_ctime=ds.ctime,
                                 dossier_root=cfg.dossiers)
                shutil.rmtree(cfg.work / ds.session_id, ignore_errors=True)
                shutil.rmtree(cfg.work / f"{ds.session_id}-analysis",
                              ignore_errors=True)
                # stale shift record goes with the stale work dir
                # (review-r4 #7)
                from .validate import _locked_report_remove
                _locked_report_remove(
                    cfg.work / "translation_report.json", ds.session_id)
                res.superseded.append(ds.session_id)
                res.discovered.append(ds.session_id)
            continue

        # cross/same-player duplicate detection on the Drive-side video md5
        if vmd5:
            dupes = [r for r in ledger.by_md5(vmd5)
                     if r["session_id"] != ds.session_id
                     and r["state"] not in ("QUARANTINED",)]
            if dupes:
                same_player = any(r["player_email"] == ds.player_email
                                  for r in dupes)
                if same_player:
                    ledger.insert_session(
                        session_id=ds.session_id, game=ds.game,
                        operator_email=ds.operator_email,
                        player_email=ds.player_email,
                        drive_path=ds.drive_path, drive_ctime=ds.ctime,
                        md5_video=vmd5, bytes_=total_bytes,
                        state="DUPLICATE",
                        detail=f"same-player duplicate of "
                               f"{dupes[0]['session_id']}")
                    res.duplicates.append(ds.session_id)
                    continue
                # adjudicated losers (REJECTED/DUPLICATE) neither block a
                # new copy nor get re-rejected — mirrors the download-time
                # dedupe filter (review-r2 #2; review-r4 #37)
                dupes = [r for r in dupes
                         if r["state"] not in ("REJECTED", "DUPLICATE")]
            if dupes:
                # cross-identity: earliest Drive createdTime wins (F3) —
                # unless the other copy already shipped (can't undeliver);
                # the ledger detail must state which case actually happened
                # (payment-dispute evidence, review finding #13)
                earliest = min(dupes, key=lambda r: r["drive_ctime"] or "9")
                is_earlier = (earliest["drive_ctime"] or "9") <= \
                    (ds.ctime or "9")
                # shipped across ALL dupes, and SPLIT counts: a split
                # parent's segments are already delivered — clobbering it
                # and re-queuing the same bytes double-delivers
                # (review-r2 #5)
                shipped = any(r["state"] in ("DELIVERED", "UPLOADED",
                                             "PACKAGED", "SPLIT")
                              for r in dupes)
                # existing copies may only be un-picked while still
                # pre-download; ALL of them must qualify — checking only
                # the earliest let a third, mid-pipeline copy deliver
                # while the ledger evidence named this copy as the keeper
                # (review-r4 #37)
                clobberable = all(r["state"] in ("DISCOVERED", "INCOMPLETE")
                                  for r in dupes)
                if is_earlier or shipped or not clobberable:
                    if is_earlier:
                        why = f"kept earlier upload {earliest['session_id']}"
                    elif shipped:
                        # name the copy that actually shipped — with
                        # several dupes it need not be the earliest
                        # (review-r4 #37)
                        winner = next(r for r in dupes
                                      if r["state"] in ("DELIVERED",
                                                        "UPLOADED",
                                                        "PACKAGED", "SPLIT"))
                        why = (f"kept already-shipped later upload "
                               f"{winner['session_id']} — this copy has "
                               f"the earlier createdTime but the other "
                               f"already shipped")
                    else:
                        # name the copy actually in flight — the earliest
                        # dupe may itself be clobberable (review-r4 #37)
                        blocker = next(r for r in dupes
                                       if r["state"] not in ("DISCOVERED",
                                                             "INCOMPLETE"))
                        why = (f"kept in-flight later upload "
                               f"{blocker['session_id']} (state "
                               f"{blocker['state']}) — F3 deviation: this "
                               f"copy has the earlier createdTime")
                        res.integrity_flags.append(
                            f"F3 deviation: {ds.session_id} is earlier but "
                            f"{blocker['session_id']} already in flight — "
                            f"kept the in-flight copy")
                    ledger.insert_session(
                        session_id=ds.session_id, game=ds.game,
                        operator_email=ds.operator_email,
                        player_email=ds.player_email,
                        drive_path=ds.drive_path, drive_ctime=ds.ctime,
                        md5_video=vmd5, bytes_=total_bytes,
                        state="REJECTED",
                        detail=f"cross-identity duplicate ({why})")
                    ledger.set_reasons(
                        ds.session_id,
                        [{"code": "INT_DUP_CROSS", "blocking": True,
                          "fixable": False, "params": {},
                          "evidence": f"video md5 identical to "
                                      f"{earliest['session_id']}; {why}"}], 3)
                    res.dup_cross.append(ds.session_id)
                    res.integrity_flags.append(
                        f"cross-player duplicate: {ds.session_id} rejected "
                        f"({why})")
                    continue
                # the NEW copy is the earlier one: reject EVERY later
                # existing copy — all are pre-download here; leaving one
                # behind would record a keeper that never delivers
                # (review-r4 #37)
                for loser in dupes:
                    ledger.set_state(
                        loser["session_id"], "REJECTED",
                        "cross-identity duplicate — later upload; "
                        f"earlier copy {ds.session_id} accepted")
                    ledger.set_reasons(
                        loser["session_id"],
                        [{"code": "INT_DUP_CROSS", "blocking": True,
                          "fixable": False, "params": {},
                          "evidence": f"video md5 identical to "
                                      f"{ds.session_id} which has earlier "
                                      f"createdTime"}], 3)
                    res.dup_cross.append(loser["session_id"])
                    res.integrity_flags.append(
                        f"cross-player duplicate: {loser['session_id']} "
                        f"rejected (kept earlier upload {ds.session_id})")

        ledger.insert_session(
            session_id=ds.session_id, game=ds.game,
            operator_email=ds.operator_email, player_email=ds.player_email,
            drive_path=ds.drive_path, drive_ctime=ds.ctime,
            md5_video=vmd5, bytes_=total_bytes, state="DISCOVERED",
            detail=f"payload={ds.payload}")
        res.discovered.append(ds.session_id)

    # ghost incomplete rows (Adnaan via d3, 08-15): the ONLY deletion path
    # was incomplete_resolved on the SAME path rescanning complete, so a
    # renamed/deleted folder lingered forever — and inflated the
    # "N incomplete" counters already shown on the batch/daily/status
    # surfaces. Guard (do not weaken): prune only off a listing that
    # succeeded, and only inside a game tree that is itself non-empty in
    # THIS listing — an erroring/empty/partial listing must never
    # mass-delete live rows and silently erase real outstanding problems.
    # Every prune logs the path + age: a mass prune must be VISIBLE after
    # the fact, never silent. "Tree non-empty" means the tree yielded
    # PARSED CONTENT (a session dir — complete, incomplete, or a
    # quarantined path) — the bare game-dir entry or a stray junk file
    # satisfied a listed_dirs test and made the guard near-vacuous
    # (folder-issues review #4)
    games_present = {ds.game for ds in sessions}
    # depth >= 2 only: a stray file AT the game root quarantines the root
    # itself ("kamla", depth 1) — that is not evidence the tree listed
    games_present |= {Path(p).parts[0] for p, _ in quarantined
                      if len(Path(p).parts) >= 2}
    now = datetime.now(timezone.utc)
    for r in ledger.incomplete_list():
        path = r["drive_path"]
        game = path.split("/", 1)[0]
        if game in games_present and path not in listed_dirs:
            try:
                age_h = (now - datetime.fromisoformat(r["first_seen"])
                         ).total_seconds() / 3600.0
            except ValueError:
                age_h = 0.0
            ledger.incomplete_resolved(path)
            print(f"[incomplete-pruned] {path} — absent from listing "
                  f"after {age_h:.0f} h tracked", file=sys.stderr)

    # bad-path chase rows whose folder VANISHED (renamed/deleted): the
    # normal operator fix produces a NEW session id, so the old
    # QUARANTINED row never heals and the folder-issues report re-listed
    # the already-fixed folder forever (review-r5 #1). The ledger row and
    # its events stay (audit); only the INT_PATH reasons are cleared,
    # which is what the report keys on. Same healthy-tree guard as the
    # incomplete prune; every clear is logged.
    for r in ledger.by_state("QUARANTINED"):
        rp = r["drive_path"] or ""
        parts = Path(rp).parts if rp else ()
        if len(parts) < 2 or parts[0] not in games_present \
                or rp in listed_dirs:
            continue
        try:
            reasons = json.loads(r["reasons_json"] or "[]")
        except json.JSONDecodeError:
            continue
        if not any(x.get("code") == "INT_PATH" for x in reasons):
            continue
        ledger.set_reasons(r["session_id"], [], None)
        ledger.set_state(r["session_id"], "QUARANTINED",
                         "bad-path folder no longer on Drive — dropped "
                         "from the chase list")
        print(f"[bad-path-resolved] {rp} — folder gone from listing",
              file=sys.stderr)
    return res


# ---------------------------------------------------------------- batching

def lagging_game(ledger: Ledger) -> str | None:
    """F4: when one game's delivered hours lag the other by >10%, its
    sessions get batch priority."""
    hours = {g: ledger.delivered_hours(g) for g in C.GAMES}
    a, b = C.GAMES
    if hours[a] < hours[b] * (1 - C.LAGGING_GAME_PRIORITY_GAP):
        return a
    if hours[b] < hours[a] * (1 - C.LAGGING_GAME_PRIORITY_GAP):
        return b
    return None


def next_batch(ledger: Ledger, size: int | None = None,
               exclude: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """FIFO by Drive createdTime, lagging game first (§9.4). `exclude`
    (the run's attempted set) is filtered BEFORE the slice: slicing first
    let a head-of-queue clique of persistent download failures starve the
    entire intake — every run re-batched only them, emptied the list at
    the caller's filter, and never reached newer sessions (review-r1 #7).
    `size` binds at call time, not import time."""
    if size is None:
        size = C.BATCH_SIZE
    rows = [r for r in ledger.by_state("DISCOVERED")
            if r["session_id"] not in exclude]
    prio = lagging_game(ledger)
    rows.sort(key=lambda r: ((0 if r["game"] == prio else 1),
                             r["drive_ctime"] or "9", r["session_id"]))
    return [r["session_id"] for r in rows[:size]]


# ---------------------------------------------------------------- download

class DownloadError(Exception):
    """kind routes the caller's response (review-r2 #0/#10):
    - "transient": network/rclone — re-queue as DISCOVERED, retry next run
    - "zip_incomplete": multi-part zip mid-upload — retryable (R4)
    - "quarantine": permanent (bad checksum, unusable archive, garbage
      payload) — retrying can never succeed; QUARANTINED + alert, Drive I
      original untouched for a human."""

    def __init__(self, msg: str, *, kind: str = "transient"):
        super().__init__(msg)
        self.kind = kind


def _unzip_payload(work_dir: Path) -> None:
    """Reassemble multi-part zips (-001/-002 concatenation) and extract
    (playbook §0), then drop the archives from the working copy."""
    parts = sorted(p for p in work_dir.iterdir()
                   if _ZIP_PART_RE.search(p.name))
    if not parts:
        return
    whole = parts[0]
    if len(parts) > 1:
        whole = work_dir / "_reassembled.zip"
        with whole.open("wb") as out:
            for p in parts:
                out.write(p.read_bytes())
    try:
        with zipfile.ZipFile(whole) as z:
            names = [n for n in z.namelist()
                     if not n.startswith("__MACOSX")]
            for n in names:
                base = Path(n).name
                if not base or n.endswith("/"):
                    continue
                with z.open(n) as src, (work_dir / base).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as e:
        # possibly a multi-part upload still missing parts — retryable
        raise DownloadError(f"unreadable zip payload: {e}",
                            kind="zip_incomplete") from e
    except OSError as e:
        # host-level errors are TRANSIENT (disk full, I/O hiccup) — a
        # quarantine here would make a full disk permanently kill good
        # sessions (review-r3 #44)
        raise DownloadError(
            f"zip extract failed ({type(e).__name__}: {e}) — host-level, "
            f"retrying", kind="transient") from e
    except (NotImplementedError, RuntimeError) as e:
        # Deflate64 (Windows Explorer >2 GB zips raise NotImplementedError)
        # and encrypted members (RuntimeError): retrying can never succeed,
        # and before this carried a kind it escaped the caller's except
        # entirely and killed the D thread every run (review-r2 #0)
        raise DownloadError(
            f"unusable zip archive ({type(e).__name__}): {e}",
            kind="quarantine") from e
    for p in parts:
        p.unlink(missing_ok=True)
    (work_dir / "_reassembled.zip").unlink(missing_ok=True)


def sniff_payload(work_dir: Path) -> str:
    """v2 | v1 | raw | garbage — playbook §0 format detection."""
    has = lambda n: (work_dir / n).exists()
    if has("frames.csv") and has("session.json"):
        if has("key_binding.json"):
            return "v1"
        try:
            s = json.loads((work_dir / "session.json").read_text())
        except (json.JSONDecodeError, OSError):
            return "garbage"
        return "v1" if "canonical" in s else "v2"
    if has("video.mp4") and has("inputs.jsonl") and has("metadata.json"):
        return "raw"
    return "garbage"


def download(cfg: C.Config, ledger: Ledger, session_id: str) -> str:
    """Download one session to work/<sid>/, checksum-verify, sniff payload.

    Returns the sniffed payload kind; raises DownloadError after 2 failed
    verify retries (caller quarantines). rrd files are excluded (R17) — a
    stub session.rrd + real rrd_creation.py are written locally so qa-v2's
    file-presence gate can run; deliver.py stages explicit file lists, so
    the stub can never ship.
    """
    row = ledger.get(session_id)
    assert row is not None
    ledger.set_state(session_id, "DOWNLOADING")
    dst = cfg.work / session_id
    dst.mkdir(parents=True, exist_ok=True)

    for attempt in range(3):
        p = run_rclone(["copy", "--checksum", "--transfers", "4",
                        "--exclude", "*.rrd",
                        f"{cfg.remote_collect}{row['drive_path']}",
                        str(dst)], timeout_s=21600)   # big sessions, slow lines
        if p.returncode != 0:
            if attempt == 2:
                raise DownloadError(
                    f"rclone copy failed x3: {p.stderr.strip()[:300]}")
            continue
        _unzip_payload(dst)
        video = dst / "video.mp4"
        if row["md5_video"] and video.exists():
            if _md5_file(video) == row["md5_video"]:
                break
            # corrupt download: wipe and retry (§13)
            shutil.rmtree(dst, ignore_errors=True)
            dst.mkdir(parents=True, exist_ok=True)
            if attempt == 2:
                raise DownloadError("video md5 mismatch after 3 attempts",
                                    kind="quarantine")
        else:
            break

    missing = [f for f in C.REQUIRED_FILES if not (dst / f).exists()]
    kind = sniff_payload(dst)
    if kind == "garbage":
        # a complete, checksum-verified download that still sniffs as
        # garbage can never improve by retrying — before this carried a
        # kind it re-entered DISCOVERED forever (review-r2 #10, R4/R2)
        raise DownloadError(f"unrecognizable payload (missing: {missing})",
                            kind="quarantine")
    if missing and kind == "v2":
        # sidecars may be absent post-unzip; completeness rule still applies
        ledger.incomplete_seen(row["drive_path"], missing)
    else:
        # a verified, fully-unpacked USABLE download (v2-complete, v1, raw)
        # settles any incomplete record — including the ZIP_PARTS_MARKER
        # rows scan deliberately leaves alone; raw/v1 payloads legitimately
        # lack v2 files (missing != unusable), so keying this on `missing`
        # would nag forever on zip-wrapped raw bundles
        ledger.incomplete_resolved(row["drive_path"])

    if kind == "v2":
        # Sidecars move to raw/ so the analysis engine sees a clean v2
        # delivery at the session root (its format sniffer reads a root
        # inputs.jsonl as "raw bundle"). raw/ is what FIX_RETRANSLATE and
        # the qa-v2 off-by-one recheck consume.
        raw = dst / "raw"
        raw.mkdir(exist_ok=True)
        for name in ("inputs.jsonl", "metadata.json", "keybind.json"):
            if (dst / name).exists():
                shutil.move(str(dst / name), raw / name)
        from translator import rrd as rrdmod
        if not (dst / "rrd_creation.py").exists():
            rrdmod.write_script(dst)
        stub = dst / "session.rrd"
        if not stub.exists():
            stub.touch()          # placeholder only — never staged (§12)

    # zip payloads arrive with no Drive-side video md5 — backfill it and
    # run the dedupe rules here. Re-entrant BY DESIGN: the md5 update and
    # the dup verdict used to be separable by a kill, after which the
    # `not md5` guard skipped the check forever (review-r1 #3) — now the
    # check runs on every completion until the session leaves DOWNLOADING.
    if (dst / "video.mp4").exists():
        local_md5 = row["md5_video"] or _md5_file(dst / "video.mp4")
        if not row["md5_video"]:
            ledger.update(session_id, md5_video=local_md5)
        # adjudicated LOSERS (REJECTED/DUPLICATE) are excluded: when the
        # scan already picked this copy as the winner, seeing its beaten
        # duplicate here must not kill the keeper — that ended with BOTH
        # copies terminal and the footage never delivered (review-r2 #2);
        # exclusion also makes crash-mid-dedupe resume converge (#22)
        dupes = [r for r in ledger.by_md5(local_md5)
                 if r["session_id"] != session_id
                 and r["state"] not in ("QUARANTINED", "REJECTED",
                                        "DUPLICATE")]
        if dupes:
            same_player = any(r["player_email"] == row["player_email"]
                              for r in dupes)
            if same_player:
                ledger.set_state(session_id, "DUPLICATE",
                                 f"same-player duplicate of "
                                 f"{dupes[0]['session_id']} (zip payload)")
                shutil.rmtree(dst, ignore_errors=True)
                return "duplicate"
            # F3: earliest Drive createdTime wins (review-r1 #9) — with
            # the shipped-copy exception, and never stomping a session
            # another driver thread is actively working (§6 ownership):
            # a later copy is only rejected while still pre-download.
            shipped = any(r["state"] in ("PACKAGED", "UPLOADED",
                                         "DELIVERED") for r in dupes)
            rejectable_losers = [
                r for r in dupes
                if r["state"] in ("DISCOVERED", "INCOMPLETE")
                and (r["drive_ctime"] or "9") > (row["drive_ctime"] or "9")]
            from . import deliver as _deliver     # lazy: no import cycle
            if not shipped and len(rejectable_losers) == len(dupes):
                for r in rejectable_losers:
                    ledger.set_reasons(r["session_id"], [
                        {"code": "INT_DUP_CROSS", "blocking": True,
                         "fixable": False, "params": {},
                         "evidence": f"video md5 identical to "
                                     f"{session_id} which has earlier "
                                     f"createdTime (zip payload)"}], 3)
                    ledger.set_state(
                        r["session_id"], "REJECTED",
                        f"cross-identity duplicate — later upload; "
                        f"earlier copy {session_id} accepted")
                    # dossier + coaching like every other reject
                    # (review-r3 #29)
                    _deliver.finalize_rejected(cfg, ledger,
                                               r["session_id"])
                # this copy is the keeper: fall through to INGESTED
            else:
                keeper = dupes[0]["session_id"]
                f3_dev = (" — F3 deviation: this copy has the earlier "
                          "createdTime but the other is already in flight"
                          if (row["drive_ctime"] or "9") <
                          (dupes[0]["drive_ctime"] or "9") else "")
                ledger.set_reasons(session_id, [
                    {"code": "INT_DUP_CROSS", "blocking": True,
                     "fixable": False, "params": {},
                     "evidence": f"video md5 identical to {keeper} "
                                 f"(zip payload){f3_dev}"}], 3)
                ledger.set_state(session_id, "REJECTED",
                                 f"cross-identity duplicate (zip payload)"
                                 f"{f3_dev}")
                shutil.rmtree(dst, ignore_errors=True)
                _deliver.finalize_rejected(cfg, ledger, session_id)
                return "duplicate"

    dur = _probe_duration(dst / "video.mp4")
    if dur:
        ledger.update(session_id, duration_raw_s=dur)
    ledger.set_state(session_id, "INGESTED", f"payload={kind}")
    return kind


def _probe_duration(video: Path) -> float | None:
    if not video.exists():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=120)
        return float(out.stdout.strip()) if out.returncode == 0 else None
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
