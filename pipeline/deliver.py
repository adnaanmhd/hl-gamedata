"""Phase IV — packaging & delivery to Shared Drive II (plan §12).

Stage → rrd sampling (R17: deterministic 20% per game per day, recorded) →
final qa-v2 gate (rrd-presence waived BY FILENAME for non-sampled) →
rclone upload → checksum verification → only then DELIVERED + local media
deleted. Dossiers and the ledger are never deleted (R6/§8).

Staging copies an EXPLICIT file list — the work copy's stub session.rrd can
never ship.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from translator import rrd as rrdmod
from translator.v2 import check_session_v2

from . import config as C
from .ingest import run_rclone, _md5_file
from .ledger import Ledger

SPEC_FILES = ("video.mp4", "frames.csv", "session.json")
RRD_FILES = ("session.rrd", "rrd_creation.py")


class DeliverError(Exception):
    pass


@dataclass
class DeliveryOutcome:
    session_id: str
    status: str          # delivered | failed_gate | failed_upload
    detail: str = ""
    hours: float = 0.0
    rrd_sampled: bool = False


def rrd_sampled(session_id: str, game: str, date: str,
                frac: float = C.RRD_SAMPLE_FRAC) -> bool:
    """Deterministic per-session draw seeded from (date, game, session) —
    ~20% per game per day, reproducible across resumes (R17)."""
    return random.Random(f"{date}:{game}:{session_id}").random() < frac


def upload_date_utc() -> str:
    return datetime.now(timezone.utc).strftime("%m-%d-%Y")


def stage_session(cfg: C.Config, session_id: str, game: str, *,
                  date: str | None = None,
                  dest_prefix: str = C.VENDOR) -> tuple[Path, bool]:
    """Copy the spec files to the staging tree; regenerate the rrd pair for
    sampled sessions. Returns (stage_dir, sampled)."""
    date = date or upload_date_utc()
    work = cfg.work / session_id
    stage_dir = cfg.stage / dest_prefix / date / game / session_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    for name in SPEC_FILES:
        src = work / name
        if not src.exists():
            raise DeliverError(f"{session_id}: staging missing {name}")
        shutil.copy2(src, stage_dir / name)
    sampled = rrd_sampled(session_id, game, date)
    if sampled:
        rrdmod.write_script(stage_dir)
        rrdmod.generate(stage_dir)
    return stage_dir, sampled


def final_gate(stage_dir: Path, sampled: bool) -> tuple[bool, list[str]]:
    """qa-v2 on the staged copy. For non-sampled sessions the two rrd
    file-presence failures are waived BY FILENAME (§12.3) — the exact
    strings are constructed here from the filenames, never text-matched."""
    r = check_session_v2(stage_dir)
    fails = [i for i in r.issues if i.startswith("FAIL:")]
    if not sampled:
        waivable = {f"FAIL: missing delivery file: {n}" for n in RRD_FILES}
        fails = [f for f in fails if f not in waivable]
        if not fails and r.status == "FAIL":
            # re-run the content checks with a stub pair present so the
            # early-return on missing files doesn't mask real problems
            for n in RRD_FILES:
                (stage_dir / n).touch()
            try:
                r2 = check_session_v2(stage_dir)
                fails = [i for i in r2.issues if i.startswith("FAIL:")]
            finally:
                for n in RRD_FILES:
                    (stage_dir / n).unlink(missing_ok=True)
    return (not fails), fails


def upload_and_verify(cfg: C.Config, stage_dir: Path,
                      remote_dir: str) -> None:
    """rclone copy --checksum, then verify the remote listing's sizes+md5s
    against the staged files. Raises on any mismatch."""
    p = run_rclone(["copy", "--checksum", "--transfers", "4",
                    str(stage_dir), f"{cfg.remote_deliver}{remote_dir}"])
    if p.returncode != 0:
        raise DeliverError(f"upload failed: {p.stderr.strip()[:300]}")
    q = run_rclone(["lsjson", "--hash", f"{cfg.remote_deliver}{remote_dir}"])
    if q.returncode != 0:
        raise DeliverError(f"verify listing failed: {q.stderr.strip()[:300]}")
    remote = {e["Name"]: e for e in json.loads(q.stdout or "[]")
              if not e.get("IsDir")}
    for f in sorted(stage_dir.iterdir()):
        if not f.is_file():
            continue
        r = remote.get(f.name)
        if r is None:
            raise DeliverError(f"verify: {f.name} missing on Drive II")
        if r.get("Size") != f.stat().st_size:
            raise DeliverError(
                f"verify: {f.name} size {r.get('Size')} != "
                f"{f.stat().st_size}")
        rmd5 = (r.get("Hashes") or {}).get("md5")
        if rmd5 and rmd5 != _md5_file(f):
            raise DeliverError(f"verify: {f.name} md5 mismatch")


def deliver_session(cfg: C.Config, ledger: Ledger, session_id: str, *,
                    dest_prefix: str = C.VENDOR) -> DeliveryOutcome:
    """READY -> PACKAGED -> UPLOADED(verified) -> DELIVERED (+ wipe).

    Every step is state-guarded so a mid-batch kill resumes exactly: an
    already-verified upload re-verifies as a no-op; hours are recorded once,
    at the DELIVERED transition."""
    row = ledger.get(session_id)
    assert row is not None
    game = row["game"]
    date = upload_date_utc()
    stage_dir, sampled = stage_session(cfg, session_id, game, date=date,
                                       dest_prefix=dest_prefix)
    ledger.update(session_id, rrd_sampled=int(sampled))
    ok, fails = final_gate(stage_dir, sampled)
    if not ok:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return DeliveryOutcome(session_id, "failed_gate",
                               detail="; ".join(fails)[:400])
    ledger.set_state(session_id, "PACKAGED",
                     f"staged {date} rrd_sampled={sampled}")
    remote_dir = f"{dest_prefix}/{date}/{game}/{session_id}"
    try:
        upload_and_verify(cfg, stage_dir, remote_dir)
    except DeliverError as e:
        return DeliveryOutcome(session_id, "failed_upload", detail=str(e))
    ledger.set_state(session_id, "UPLOADED", f"verified at {remote_dir}")

    s = json.loads((stage_dir / "session.json").read_text())
    hours = float(s.get("duration_seconds") or 0.0)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ledger.update(session_id, duration_delivered_s=hours, delivered_at=now)
    ledger.set_state(session_id, "DELIVERED", f"{hours:.1f}s delivered")
    # deletion only AFTER checksum-verified upload (locked rule)
    shutil.rmtree(cfg.work / session_id, ignore_errors=True)
    shutil.rmtree(stage_dir, ignore_errors=True)
    return DeliveryOutcome(session_id, "delivered", hours=hours / 3600.0,
                           rrd_sampled=sampled)


_COACHING = {
    "CNT_SHORT": "Record at least 70 s of play — target 10-30 minutes.",
    "INP_MOTION_MISSING": "Mouse motion was not captured. Restart the "
                          "capture tool before recording and check the "
                          "session preview.",
    "INP_BUTTONS_MISSING": "Mouse clicks were not captured — restart the "
                           "capture tool; report if it repeats.",
    "INP_KEYS_MISSING": "Keyboard was not captured — restart the capture "
                        "tool before recording.",
    "CNT_NOTIF_MID": "Enable Do Not Disturb and disable Steam/Discord "
                     "overlays before recording.",
    "CNT_WRONG_GAME": "Only Kamla and Outer Wilds count for this program.",
    "CNT_ACTIONS_FEW": "Play actively — sessions need several different "
                       "in-game actions.",
    "CNT_DROPS": "The recording dropped too many frames — close other "
                 "apps, lower the game's settings.",
    "CNT_BLACK_FROZEN": "The capture shows a black/frozen screen — run the "
                        "game in borderless-windowed mode.",
    "CNT_AFK": "Long AFK stretches were cut; stay active while recording.",
    "CNT_CHAT_PII": "Personal text was visible on screen — keep chats and "
                    "personal windows out of recordings.",
    "INT_DUP_CROSS": "This video was already uploaded by another player — "
                     "only the first upload counts.",
}


def finalize_rejected(cfg: C.Config, ledger: Ledger,
                      session_id: str) -> None:
    """Dossier gets the verdict + a coaching note; local media is wiped.
    The Drive I original stays untouched forever (R6)."""
    row = ledger.get(session_id)
    dossier = cfg.dossiers / session_id
    dossier.mkdir(parents=True, exist_ok=True)
    try:
        reasons = json.loads(row["reasons_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        reasons = []
    lines = [f"# Rejection — {session_id}", ""]
    for r in reasons:
        lines.append(f"- **{r.get('code')}**: {r.get('evidence', '')}")
        tip = _COACHING.get(r.get("code"))
        if tip:
            lines.append(f"  - Coaching: {tip}")
    (dossier / "coaching.md").write_text("\n".join(lines) + "\n")
    ledger.update(session_id, dossier_path=str(dossier))
    shutil.rmtree(cfg.work / session_id, ignore_errors=True)


def cleanup_test_folder(cfg: C.Config, prefix: str = "_pipeline_test"
                        ) -> None:
    """Remove the Drive II test folder (build-time uploads only ever go
    under _pipeline_test/ and are deleted afterward)."""
    run_rclone(["purge", f"{cfg.remote_deliver}{prefix}"])


def disk_free_gb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1 << 30)
