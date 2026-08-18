"""Tests added by adversarial-review round 2: poison-zip/garbage quarantine
kinds, dedupe-keeper survival, scan cross-dup guards, zip supersede,
mid-split disk recovery, child dedupe, sweeps, ceiling alert, redaction,
rrd pinning, semaphore balance under crashes."""
import json
import threading
import zipfile
from datetime import datetime, timezone

import pytest

from pipeline import config as C
from pipeline import deliver, ingest, run as runmod
from pipeline.ledger import Ledger


@pytest.fixture(autouse=True)
def _arm_the_batch_driver(monkeypatch):
    """run() declines when PIPELINE_CONTINUOUS is True (r-loop 5). This
    module exercises the batch driver, so arm it — without this the
    semaphore-leak regression below passed VACUOUSLY: run() returned
    before acquire_lock and the balance it asserts was never disturbed
    (r-loop 6)."""
    monkeypatch.setattr(C, "PIPELINE_CONTINUOUS", False)


SID = "2026-08-14T10-00-00Z_kamla_c_00000000000000bb"


def _seed(ledger, sid=SID, state="DISCOVERED", player="p@x.com",
          md5="", ctime="2026-08-14T10:00:00.000Z", **extra):
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email=player, drive_path=f"kamla/Op/{player}/{sid}",
        drive_ctime=ctime, md5_video=md5, bytes_=1, state=state)
    for k, v in extra.items():
        ledger.update(sid, **{k: v})


# ------------------------------------------------- poison zip / kinds (#0)

def test_unzip_deflate64_raises_quarantine_kind(tmp_path, monkeypatch):
    """NotImplementedError from z.open (Deflate64) must surface as a
    DownloadError with kind=quarantine, not escape and kill the D thread."""
    (tmp_path / "sess.zip").write_bytes(b"PK\x03\x04 not really")

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def namelist(self): return ["video.mp4"]
        def open(self, n):
            raise NotImplementedError("compression type 9 (deflate64)")
    monkeypatch.setattr(ingest.zipfile, "ZipFile", Boom)
    with pytest.raises(ingest.DownloadError) as ei:
        ingest._unzip_payload(tmp_path)
    assert ei.value.kind == "quarantine"


def test_unzip_badzip_stays_retryable_kind(tmp_path):
    (tmp_path / "sess.zip").write_bytes(b"this is not a zip at all")
    with pytest.raises(ingest.DownloadError) as ei:
        ingest._unzip_payload(tmp_path)
    assert ei.value.kind == "zip_incomplete"


def test_download_phase_quarantines_on_quarantine_kind(cfg, ledger,
                                                       monkeypatch):
    """Garbage payload / unusable archive: QUARANTINED, never the infinite
    DISCOVERED retry loop (#10)."""
    _seed(ledger)

    def garbage(cfg_, ledger_, sid):
        ledger_.set_state(sid, "DOWNLOADING")
        raise ingest.DownloadError("unrecognizable payload",
                                   kind="quarantine")
    monkeypatch.setattr(ingest, "download", garbage)
    runmod._download_phase(cfg, ledger, [SID], [])
    assert ledger.get(SID)["state"] == "QUARANTINED"


def test_download_phase_survives_raw_exception(cfg, ledger, monkeypatch):
    """A non-DownloadError escape quarantines the session instead of
    killing the whole download phase (#0 second half)."""
    _seed(ledger)
    other = SID.replace("bb", "cc")
    _seed(ledger, other, ctime="2026-08-14T11:00:00.000Z")
    calls = []

    def crash_once(cfg_, ledger_, sid):
        calls.append(sid)
        ledger_.set_state(sid, "DOWNLOADING")
        if len(calls) == 1:
            raise NotImplementedError("deflate64 escaped")
        ledger_.set_state(sid, "INGESTED")
        return "v2"
    monkeypatch.setattr(ingest, "download", crash_once)
    runmod._download_phase(cfg, ledger, [SID, other], [])
    assert ledger.get(SID)["state"] == "QUARANTINED"
    assert ledger.get(other)["state"] == "INGESTED"   # phase kept going


# ------------------------------------------- dedupe keeper survives (#2)

def test_download_dedupe_ignores_adjudicated_losers(cfg, ledger,
                                                    monkeypatch):
    """The scan already picked this copy as WINNER; its beaten duplicate
    (DUPLICATE/REJECTED) must not kill the keeper at download time."""
    loser = SID.replace("bb", "dd")
    _seed(ledger, loser, state="DUPLICATE", md5="samemd5")
    _seed(ledger, SID, state="DISCOVERED", md5="")
    work = cfg.work / SID
    work.mkdir(parents=True)
    (work / "video.mp4").write_bytes(b"videobytes")
    (work / "frames.csv").write_text("x")
    (work / "session.json").write_text("{}")
    (work / "inputs.jsonl").write_text("")
    (work / "metadata.json").write_text("{}")
    monkeypatch.setattr(ingest, "run_rclone",
                        lambda args, timeout_s=None:
                        type("P", (), {"returncode": 0, "stderr": ""})())
    monkeypatch.setattr(ingest, "_md5_file", lambda p: "samemd5")
    kind = ingest.download(cfg, ledger, SID)
    assert kind == "v2"
    assert ledger.get(SID)["state"] == "INGESTED"     # keeper survives


# --------------------------------------- scan cross-dup guards (#5)

def _entries_for(sid, player="q@y.com", md5="m-x", ctime=None):
    base = f"kamla/OpB/{player}/{sid}"
    return [{"Path": f"{base}/{n}", "Name": n, "IsDir": False, "Size": 5,
             "ModTime": ctime or "2026-08-14T12:00:00.000Z",
             "Hashes": {"md5": md5}}
            for n in C.REQUIRED_FILES]


def test_scan_never_clobbers_split_parent(cfg, ledger):
    """A SPLIT parent (segments delivered) counts as shipped: an
    earlier-ctime copy of the same bytes rejects, and the parent's state
    survives."""
    _seed(ledger, SID, state="INGESTED", md5="m-x",
          ctime="2026-08-14T12:00:00.000Z")
    ledger.set_state(SID, "SPLIT", "2 segments")
    new_sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000ee"
    res = ingest.scan(cfg, ledger,
                      entries=_entries_for(new_sid,
                                           ctime="2026-08-14T09:00:00.000Z"))
    assert ledger.get(SID)["state"] == "SPLIT"        # untouched
    assert ledger.get(new_sid)["state"] == "REJECTED"
    assert new_sid in res.dup_cross


def test_scan_earlier_copy_wins_only_over_predownload_states(cfg, ledger):
    """An earlier copy un-picks a DISCOVERED later copy (F3), but a
    VALIDATING one stays and the F3 deviation is recorded."""
    _seed(ledger, SID, state="VALIDATING", md5="m-x",
          ctime="2026-08-14T12:00:00.000Z")
    new_sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000ff"
    res = ingest.scan(cfg, ledger,
                      entries=_entries_for(new_sid,
                                           ctime="2026-08-14T09:00:00.000Z"))
    assert ledger.get(SID)["state"] == "VALIDATING"   # not clobbered
    assert ledger.get(new_sid)["state"] == "REJECTED"
    assert any("F3 deviation" in f for f in res.integrity_flags)


# ------------------------------------------------ zip supersede (#9)

def test_zip_reupload_after_reject_supersedes(cfg, ledger):
    zsid = "2026-08-14T10-00-00Z_kamla_c_0000000000000f00"
    _seed(ledger, zsid, state="REJECTED", md5="", bytes=10)
    base = f"kamla/Op/p@x.com/{zsid}"
    entries = [{"Path": f"{base}/session-001.zip", "Name": "session-001.zip",
                "IsDir": False, "Size": 999,
                "ModTime": "2026-08-15T09:00:00.000Z", "Hashes": {}}]
    # path must match the ledger row's drive_path for the same-slot rule
    ledger.update(zsid, drive_path=base)
    res = ingest.scan(cfg, ledger, entries=entries)
    assert zsid in res.superseded
    assert ledger.get(zsid)["state"] == "DISCOVERED"


# ------------------------------- mid-split crash recovery from disk (#7)

def test_mid_split_crash_recovers_manifest_complete_split(cfg, ledger):
    """Kill between child inserts AFTER a complete cut (manifest present,
    all segments on disk/rowed): recovery adopts the split, inserting the
    rows the kill orphaned."""
    _seed(ledger, SID, state="FIX_QUEUED")
    ledger.set_state(SID, "FIXING", "attempt 1")
    (cfg.work / SID).mkdir(parents=True)
    # child 1 got its row; child 2 only its dir; manifest lists both
    ledger.insert_session(
        session_id=f"{SID}-p1", game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path="kamla/Op/p@x.com/x",
        drive_ctime="2026", md5_video="", bytes_=0, state="INGESTED",
        parent_id=SID)
    p2 = cfg.work / f"{SID}-p2"
    p2.mkdir(parents=True)
    (p2 / "session.json").write_text(json.dumps({"duration_seconds": 88.0}))
    (cfg.work / f"{SID}.split-manifest.json").write_text(json.dumps(
        {"parent": SID, "segments": [f"{SID}-p1", f"{SID}-p2"],
         "dropped": 0}))
    sink = set()
    kids = runmod._fix_phase(cfg, ledger, [SID], [], workers=1,
                             children_sink=sink)
    assert ledger.get(SID)["state"] == "SPLIT"
    assert ledger.get(f"{SID}-p2") is not None        # recovered
    assert ledger.get(f"{SID}-p2")["parent_id"] == SID
    assert set(kids) >= {f"{SID}-p1", f"{SID}-p2"}
    assert sink >= {f"{SID}-p1", f"{SID}-p2"}
    assert len(kids) == len(set(kids))                # no double-register
    assert not (cfg.work / f"{SID}.split-manifest.json").exists()


def test_mid_split_crash_without_manifest_wipes_partials_and_revalidates(
        cfg, ledger):
    """Kill MID-CUT (no manifest): a partial segment set must never
    complete as a SPLIT subset — rowless partial dirs are wiped and the
    parent re-derives via REVALIDATING (review-r3 #1/#5)."""
    _seed(ledger, SID, state="FIX_QUEUED")
    ledger.set_state(SID, "FIXING", "attempt 1")
    (cfg.work / SID).mkdir(parents=True)
    p1 = cfg.work / f"{SID}-p1"                       # half-written cut
    p1.mkdir(parents=True)
    (p1 / "video.mp4").write_bytes(b"partial")
    kids = runmod._fix_phase(cfg, ledger, [SID], [], workers=1,
                             children_sink=set())
    assert ledger.get(SID)["state"] in ("REVALIDATING", "REJECTED",
                                        "FIX_QUEUED")
    assert ledger.get(SID)["state"] != "SPLIT"        # never a bogus split
    assert not p1.exists()                            # partial wiped
    assert f"{SID}-p1" not in kids


# ------------------------------------------------- sweeps (#8, #22)

def test_finalize_orphan_rejects_sweep(cfg, ledger, monkeypatch):
    _seed(ledger, SID, state="REJECTED")
    (cfg.work / SID).mkdir(parents=True)
    done = []
    monkeypatch.setattr(runmod.deliver, "finalize_rejected",
                        lambda cfg_, ledger_, sid: done.append(sid))
    runmod._finalize_orphan_rejects(cfg, ledger)
    assert done == [SID]


def test_sweep_terminal_work_reclaims_delivered_dirs(cfg, ledger):
    _seed(ledger, SID, state="INGESTED")
    for st in ("VALIDATING", "READY", "PACKAGED", "UPLOADED", "DELIVERED"):
        ledger.set_state(SID, st)
    (cfg.work / SID).mkdir(parents=True)
    (cfg.work / f"{SID}-analysis").mkdir(parents=True)
    live = SID.replace("bb", "aa")
    _seed(ledger, live, state="INGESTED")
    (cfg.work / live).mkdir(parents=True)
    runmod._sweep_terminal_work(cfg, ledger)
    assert not (cfg.work / SID).exists()
    assert not (cfg.work / f"{SID}-analysis").exists()
    assert (cfg.work / live).exists()                 # mid-pipeline kept


# ------------------------------------------- ceiling alert (#31, #36)

def test_upload_ceiling_alert_counts_split_children(cfg, ledger,
                                                    monkeypatch):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, (bytes_, dur) in enumerate([(0, 3600.0 * 100), (0, 3600.0 * 100)]):
        sid = f"2026-08-15T0{i}-00-00Z_kamla_c_{i + 32:016x}-p1"
        ledger.insert_session(
            session_id=sid, game="kamla", operator_email="o",
            player_email="p@x.com", drive_path="kamla/o/p/x",
            drive_ctime="2026", md5_video="", bytes_=bytes_,
            state="DISCOVERED", parent_id="parent")
        ledger.update(sid, duration_delivered_s=dur,
                      delivered_at=now)
        ledger.set_state(sid, "DELIVERED")
    alerts = []
    monkeypatch.setattr(runmod, "_alert",
                        lambda cfg_, text, sent: alerts.append(text))
    runmod._upload_ceiling_alert(cfg, ledger, [])
    # 200 delivered fh * 3.13 GB = 626 GB > 600 — children counted
    assert any("upload ceiling" in a for a in alerts)


# ------------------------------------------------- redaction (#28, #35)

def test_telegram_rejection_redacts_token_before_truncation(cfg,
                                                            monkeypatch):
    from pipeline import telegram
    cfg.secrets["TELEGRAM_BOT_TOKEN"] = "123456:SECRETTOKENVALUE"
    cfg.secrets["TELEGRAM_CHAT_ID"] = "42"

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""
    # pad sized so the token STRADDLES the 200-char truncation boundary:
    # truncate-then-redact keeps a partial token ("123456:SEC…") that the
    # redact can no longer match — the original leak (r2 #28). The prior
    # version of this test buried the token past the boundary and passed
    # against the broken code too (review-r3 #39).
    pad = "x" * 160
    monkeypatch.setattr(telegram.json, "load",
                        lambda r: {"ok": False,
                                   "description": pad +
                                   "123456:SECRETTOKENVALUE"})
    monkeypatch.setattr(telegram.urllib.request, "urlopen",
                        lambda req, timeout=0: FakeResp())
    with pytest.raises(telegram.TelegramError) as ei:
        telegram.send_message(cfg, "hi")
    msg = str(ei.value)
    assert "SECRETTOKENVALUE" not in msg
    assert "123456:" not in msg          # no partial-token fragment either


# ------------------------------------------------- rrd pinning (#12)

def test_rrd_sampled_pinned_on_resume_past_ready(cfg, ledger, monkeypatch):
    """A PACKAGED resume honors the RECORDED sampling decision. Tested in
    the want=0 direction — recorded 0, fresh draw says True — because in
    the want=1 direction the §1.4 floor branch of the UNfixed code also
    regenerates, masking the fix (review-r3 #40)."""
    _seed(ledger, SID, state="INGESTED")
    ledger.set_state(SID, "READY")
    ledger.set_state(SID, "PACKAGED")
    ledger.update(SID, rrd_sampled=0)
    stage = cfg.stage / C.VENDOR / "08-15-2026" / "kamla" / SID
    stage.mkdir(parents=True)
    regen = []
    gate_sampled = []
    monkeypatch.setattr(deliver, "stage_session",
                        lambda cfg_, sid, game, dest_prefix=C.VENDOR:
                        (stage, True))            # fresh draw: sample!
    monkeypatch.setattr(deliver.rrdmod, "write_script",
                        lambda d: regen.append("script"))
    monkeypatch.setattr(deliver.rrdmod, "generate",
                        lambda d, **kw: regen.append("rrd"))
    def gate(stage_dir, sampled):
        gate_sampled.append(sampled)
        return False, ["FAIL: stop"]
    monkeypatch.setattr(deliver, "final_gate", gate)
    out = deliver.deliver_session(cfg, ledger, SID)
    assert out.status == "failed_gate"            # stops after pinning
    assert regen == []                            # record 0 honored: no rrd
    assert gate_sampled == [False]                # gate saw the record
    assert ledger.get(SID)["rrd_sampled"] == 0


# ---------------------------------- semaphore balance under crashes (#44)

def test_flight_semaphore_balanced_after_u_crash(cfg, monkeypatch):
    """Strengthens the r1 crash tests: count acquires/releases through a
    crashing run and assert net zero — the actual leak signal."""
    monkeypatch.setattr(C, "PIPELINE_OVERLAP", True)
    balance = {"n": 0}
    real_sem = threading.Semaphore

    class CountingSem:
        def __init__(self, value):
            self._s = real_sem(value)

        def acquire(self, timeout=None):
            got = self._s.acquire(timeout=timeout) \
                if timeout is not None else self._s.acquire()
            if got:
                balance["n"] += 1
            return got

        def release(self):
            balance["n"] -= 1
            self._s.release()
    monkeypatch.setattr(runmod.threading, "Semaphore", CountingSem)
    led = Ledger(cfg.ledger_path)
    for i in range(2):
        sid = f"2026-08-14T1{i}-00-00Z_kamla_c_{i + 64:016x}"
        led.insert_session(
            session_id=sid, game="kamla", operator_email="o",
            player_email="p@x.com", drive_path=f"kamla/o/p/{sid}",
            drive_ctime=f"2026-08-14T1{i}:00:00.000Z", md5_video=f"z{i}",
            bytes_=1, state="DISCOVERED")
    led.close()

    def fake_download(cfg_, ledger_, sids, alerts):
        for s in sids:
            ledger_.set_state(s, "INGESTED")

    monkeypatch.setattr(runmod, "_download_phase", fake_download)
    monkeypatch.setattr(runmod, "_validate_phase",
                        lambda cfg_, l, sids, a, workers, **kw:
                        [l.set_state(s, "READY") for s in sids])
    monkeypatch.setattr(runmod, "_fix_phase",
                        lambda cfg_, l, sids, a, workers, **kw: [])
    monkeypatch.setattr(
        runmod, "_deliver_phase",
        lambda cfg_, l, sids, a, dest_prefix=C.VENDOR:
        (_ for _ in ()).throw(RuntimeError("upload boom")))
    monkeypatch.setattr(runmod.ingest, "scan",
                        lambda cfg_, ledger_: ingest.ScanResult())
    assert runmod.run(cfg, send_telegram=False) == 0
    assert balance["n"] == 0                          # every slot returned
