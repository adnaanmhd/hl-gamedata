"""Overlap-driver tests (plan §18.8): overlap proof, F7 under overlap,
3-writer SQLite contention, REAL spawn ProcessPool (threads+pool, no
monkeypatch), R23 rung round-trip across pool generations, resume
partition, and U-owned batch completion with cross-run hand-back.

The legacy suite runs with PIPELINE_OVERLAP=False (conftest autouse) as
the lockstep regression; these tests opt in to the driver explicitly.
"""
import concurrent.futures
import json
import multiprocessing
import threading
import time

from pipeline import config as C
from pipeline import deliver, ingest, run as runmod
from pipeline.ledger import Ledger

SIDS = [f"2026-08-14T1{i}-00-00Z_kamla_c_{i:016x}" for i in range(6)]


def _seed_discovered(ledger, sids):
    for i, sid in enumerate(sids):
        ledger.insert_session(
            session_id=sid, game="kamla", operator_email="Op Name",
            player_email="p@x.com", drive_path=f"kamla/Op Name/p@x.com/{sid}",
            drive_ctime=f"2026-08-14T10:0{i}:00.000Z", md5_video=f"m{i}",
            bytes_=1, state="DISCOVERED")


def _driver_fakes(monkeypatch, tl, dl_s=0.05, val_s=0.1, up_s=0.25,
                  deliver_mode="deliver"):
    """Stage fakes with sleeps that record a timeline of (event, sid, t)."""
    t0 = time.monotonic()

    def mark(event, sid):
        tl.append((event, sid, time.monotonic() - t0))

    def fake_download(cfg, ledger, sids, alerts):
        for sid in sids:
            mark("dl_start", sid)
            time.sleep(dl_s)
            ledger.set_state(sid, "INGESTED")
            mark("dl_end", sid)

    def fake_validate(cfg, ledger, sids, alerts, workers):
        for sid in sids:
            if ledger.get(sid)["state"] not in ("INGESTED", "VALIDATING",
                                                "HOLD_VLM", "REVALIDATING"):
                continue
            time.sleep(val_s)
            ledger.set_state(sid, "READY")

    def fake_fix(cfg, ledger, sids, alerts, workers):
        for sid in sids:
            if ledger.get(sid)["state"] == "FIX_QUEUED":
                ledger.set_state(sid, "READY", "test fix")
        return []

    def fake_deliver(cfg, ledger, sids, alerts, dest_prefix=C.VENDOR):
        n = 0
        for sid in sids:
            if ledger.get(sid)["state"] != "READY":
                continue
            mark("up_start", sid)
            time.sleep(up_s)
            if deliver_mode == "gate_fail":
                ledger.set_state(sid, "FIX_QUEUED", "final gate: test")
            else:
                ledger.set_state(sid, "PACKAGED")
                ledger.set_state(sid, "UPLOADED")
                ledger.update(sid, duration_delivered_s=360.0,
                              delivered_at="2026-08-15T12:00:00+00:00")
                ledger.set_state(sid, "DELIVERED")
                n += 1
            mark("up_end", sid)
        return {"delivered": n, "hours": n * 0.1, "upload_failures": 0}

    monkeypatch.setattr(runmod, "_download_phase", fake_download)
    monkeypatch.setattr(runmod, "_validate_phase",
                        lambda cfg, ledger, sids, alerts, workers:
                        fake_validate(cfg, ledger, sids, alerts, workers))
    monkeypatch.setattr(runmod, "_fix_phase",
                        lambda cfg, ledger, sids, alerts, workers:
                        fake_fix(cfg, ledger, sids, alerts, workers))
    monkeypatch.setattr(runmod, "_deliver_phase", fake_deliver)
    monkeypatch.setattr(runmod.ingest, "scan",
                        lambda cfg, ledger: ingest.ScanResult())


def _t(tl, event, sid):
    return next(t for e, s, t in tl if e == event and s == sid)


def test_overlap_proof(cfg, monkeypatch):
    """Batch N+1's download completes before batch N−1's upload ends, and
    never more than MAX_BATCHES_IN_FLIGHT new batches are local at once."""
    monkeypatch.setattr(C, "PIPELINE_OVERLAP", True)
    led = Ledger(cfg.ledger_path)
    _seed_discovered(led, SIDS[:5])
    led.close()
    tl = []
    _driver_fakes(monkeypatch, tl)
    # one-session batches: next_batch's `size` default binds C.BATCH_SIZE
    # at import time, so patching the constant cannot shrink batches —
    # wrap the real function instead (production always passes no size)
    orig_next = ingest.next_batch
    monkeypatch.setattr(runmod.ingest, "next_batch",
                        lambda led: orig_next(led, size=1))
    assert runmod.run(cfg, send_telegram=False) == 0
    led = Ledger(cfg.ledger_path)
    assert all(led.get(s)["state"] == "DELIVERED" for s in SIDS[:5])
    assert not led.open_batches()          # every batch completed
    led.close()
    # the laundry-room property: batch 3 finished downloading while
    # batch 1 was still uploading
    assert _t(tl, "dl_end", SIDS[2]) < _t(tl, "up_end", SIDS[0])
    # ≤3 in flight at any download start
    for e, s, t in tl:
        if e != "dl_start":
            continue
        in_flight = sum(
            1 for other in SIDS[:5] if other != s
            and _t(tl, "dl_start", other) <= t
            and t < max(x for ev, sd, x in tl
                        if ev == "up_end" and sd == other))
        assert in_flight <= C.MAX_BATCHES_IN_FLIGHT - 1


def test_low_disk_pauses_downloads_while_u_drains(cfg, monkeypatch):
    """F7 under the driver: D pauses below the low-water mark; U still
    delivers what is already local."""
    monkeypatch.setattr(C, "PIPELINE_OVERLAP", True)
    led = Ledger(cfg.ledger_path)
    _seed_discovered(led, [SIDS[0]])
    led.insert_session(
        session_id=SIDS[5], game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path=f"kamla/Op/p@x.com/{SIDS[5]}",
        drive_ctime="2026-08-14T10:09:00.000Z", md5_video="m9", bytes_=1,
        state="READY")
    led.close()
    tl = []
    _driver_fakes(monkeypatch, tl, up_s=0.01)
    monkeypatch.setattr(runmod.deliver, "disk_free_gb", lambda p: 50.0)
    alerts = []
    monkeypatch.setattr(runmod, "_alert",
                        lambda cfg_, text, sent: alerts.append(text))
    assert runmod.run(cfg, send_telegram=False) == 0
    led = Ledger(cfg.ledger_path)
    assert led.get(SIDS[0])["state"] == "DISCOVERED"     # never downloaded
    assert led.get(SIDS[5])["state"] == "DELIVERED"      # U kept draining
    led.close()
    assert any("downloads paused" in a for a in alerts)


def test_sqlite_three_writer_threads_no_locked_errors(cfg):
    """3 threads × 300 set_state on one ledger file (one connection per
    thread, WAL + busy_timeout): zero `database is locked`, consistent
    events table."""
    seed = Ledger(cfg.ledger_path)
    for i in range(3):
        seed.insert_session(
            session_id=f"s{i}", game="kamla", operator_email="o",
            player_email="p@x.com", drive_path=f"kamla/o/p/s{i}",
            drive_ctime="2026", md5_video=f"h{i}", bytes_=1,
            state="DISCOVERED")
    seed.close()
    errors = []

    def writer(sid):
        led = Ledger(cfg.ledger_path)
        try:
            for n in range(300):
                led.set_state(sid, "VALIDATING", f"tick {n}")
        except Exception as e:              # noqa: BLE001 — the assertion
            errors.append(f"{sid}: {type(e).__name__}: {e}")
        finally:
            led.close()

    threads = [threading.Thread(target=writer, args=(f"s{i}",))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    led = Ledger(cfg.ledger_path)
    n = led.db.execute(
        "SELECT COUNT(*) n FROM events WHERE detail LIKE 'tick %'"
    ).fetchone()["n"]
    led.close()
    assert n == 3 * 300


def _seed_ingested_with_work(cfg, ledger, sids):
    for i, sid in enumerate(sids):
        ledger.insert_session(
            session_id=sid, game="kamla", operator_email="o",
            player_email="p@x.com", drive_path=f"kamla/o/p/{sid}",
            drive_ctime=f"2026-08-14T10:0{i}:00.000Z", md5_video=f"w{i}",
            bytes_=1, state="INGESTED")
        (cfg.work / sid).mkdir(parents=True, exist_ok=True)


def test_real_spawn_pool_with_concurrent_ledger_writer(cfg, ledger):
    """REAL threads + REAL spawn ProcessPool, no monkeypatch (§18.8): a
    worker pool validates while another thread writes the ledger. The
    empty work dirs sniff as garbage -> unreadable-video bin 2, so the
    full production worker path runs cheaply."""
    _seed_ingested_with_work(cfg, ledger, SIDS[:2])
    ledger.db.execute(  # unrelated row the side thread hammers
        "INSERT INTO sessions(session_id, state, reasons_json, created_at,"
        " updated_at) VALUES('side','DISCOVERED','[]','x','x')")
    ledger.db.commit()
    errors = []

    def side_writer():
        led = Ledger(cfg.ledger_path)
        try:
            for n in range(200):
                led.set_state("side", "VALIDATING", f"side {n}")
        except Exception as e:              # noqa: BLE001
            errors.append(str(e))
        finally:
            led.close()

    side = threading.Thread(target=side_writer)
    side.start()
    runmod._validate_phase(cfg, ledger, SIDS[:2], [], workers=2)
    side.join()
    assert errors == []
    for sid in SIDS[:2]:
        row = ledger.get(sid)
        assert row["state"] == "FIX_QUEUED", (row["state"],
                                              row["reasons_json"])
        assert "STR_VIDEO_UNREADABLE" in row["reasons_json"]


def test_rung_injection_reaches_spawn_workers(cfg):
    """R23 round-trip, worker half: fresh spawn interpreters can only know
    the run's rung via the parent's injection — module state alone resets
    every pool generation."""
    jobs = [{"sid": f"j{i}", "work_dir": str(cfg.work / f"j{i}"),
             "dossier_dir": str(cfg.dossiers / f"j{i}"), "payload": "v2",
             "expected_game": None, "gemini_key": "", "gemini_model": "m",
             "vlm_rung": 1} for i in range(2)]
    for j in jobs:
        (cfg.work / j["sid"]).mkdir(parents=True, exist_ok=True)
    ctx = multiprocessing.get_context("spawn")
    with concurrent.futures.ProcessPoolExecutor(
            max_workers=2, mp_context=ctx) as ex:
        results = list(ex.map(runmod._validate_worker, jobs))
    assert [r["vlm_rung"] for r in results] == [1, 1]


def test_rung_carries_across_two_pool_generations(cfg, ledger):
    """R23 round-trip, parent half: the run-level rung seeds generation 1's
    REAL spawn pool, survives its teardown, and seeds generation 2
    identically; worker reports feed the parent's max."""
    runmod._VLM_RUN_STATE["rung"] = 2
    _seed_ingested_with_work(cfg, ledger, SIDS[:2])
    runmod._validate_phase(cfg, ledger, SIDS[:2], [], workers=2)   # gen 1
    assert runmod._VLM_RUN_STATE["rung"] == 2
    for sid in SIDS[:2]:
        ledger.set_state(sid, "REVALIDATING", "gen 2")
    runmod._validate_phase(cfg, ledger, SIDS[:2], [], workers=2)   # gen 2
    assert runmod._VLM_RUN_STATE["rung"] == 2
    # parent-side max: a worker that laddered UP raises the run rung
    runmod._VLM_RUN_STATE["rung"] = 0
    for r in [{"sid": "x", "error": "boom", "vlm_rung": 1},
              {"sid": "y", "error": "boom", "vlm_rung": 3}]:
        runmod._VLM_RUN_STATE["rung"] = max(runmod._VLM_RUN_STATE["rung"],
                                            int(r["vlm_rung"]))
    assert runmod._VLM_RUN_STATE["rung"] == 3


def test_partition_resume_groups_and_routes(cfg):
    led = Ledger(cfg.ledger_path)
    rows = [("a1", "DOWNLOADING"), ("a2", "READY"),
            ("b1", "READY"), ("b2", "UPLOADED"),
            ("stray", "VALIDATING")]
    for sid, st in rows:
        led.insert_session(
            session_id=sid, game="kamla", operator_email="o",
            player_email="p@x.com", drive_path=f"kamla/o/p/{sid}",
            drive_ctime="2026", md5_video=sid, bytes_=1, state=st)
    bA = led.start_batch(sessions=["a1", "a2"])
    bB = led.start_batch(sessions=["b1", "b2"])
    # split child rides with its parent's batch
    led.insert_session(
        session_id="b1-p1", game="kamla", operator_email="o",
        player_email="p@x.com", drive_path="kamla/o/p/b1",
        drive_ctime="2026", md5_video="", bytes_=1, state="INGESTED",
        parent_id="b1")
    d_q, v_q, u_q = runmod._partition_resume(led)
    assert [b.no for b in d_q] == [bA]           # DOWNLOADING → D
    assert sorted(d_q[0].sids) == ["a1", "a2"]
    assert len(v_q) == 2                          # B (child INGESTED) + stray
    by_no = {b.no: b for b in v_q}
    assert sorted(by_no[bB].sids) == ["b1", "b1-p1", "b2"]
    stray_batch = next(b for b in v_q if b.no != bB)
    assert stray_batch.sids == ["stray"]
    # the stray's new batch row carries its sid list from START
    srow = led.db.execute("SELECT summary_json FROM batches WHERE batch_no=?",
                          (stray_batch.no,)).fetchone()
    assert json.loads(srow["summary_json"])["sessions"] == ["stray"]
    assert u_q == []
    led.close()


def test_gate_failed_batch_stays_open_and_completes_next_run(cfg,
                                                             monkeypatch):
    """U-owned completion (§6): a final-gate failure hands the session
    back to V's domain on the NEXT run; the batch stays open, regroups via
    the start-written summary_json, and its ONE Telegram message fires on
    the run that finishes it."""
    monkeypatch.setattr(C, "PIPELINE_OVERLAP", True)
    led = Ledger(cfg.ledger_path)
    _seed_discovered(led, [SIDS[0]])
    led.close()
    msgs = []
    monkeypatch.setattr(runmod.telegram, "send_message",
                        lambda cfg_, text: msgs.append(text))
    monkeypatch.setattr(runmod, "send_daily_report_if_due",
                        lambda cfg_, ledger_: False)

    tl = []
    _driver_fakes(monkeypatch, tl, dl_s=0.0, val_s=0.0, up_s=0.0,
                  deliver_mode="gate_fail")
    assert runmod.run(cfg, send_telegram=True) == 0
    led = Ledger(cfg.ledger_path)
    assert led.get(SIDS[0])["state"] == "FIX_QUEUED"     # handed back
    opens = led.open_batches()
    assert len(opens) == 1                               # batch NOT finished
    batch_no = opens[0]["batch_no"]
    assert json.loads(opens[0]["summary_json"])["sessions"] == [SIDS[0]]
    led.close()
    assert msgs == []                                    # no premature message

    _driver_fakes(monkeypatch, tl, dl_s=0.0, val_s=0.0, up_s=0.0)
    assert runmod.run(cfg, send_telegram=True) == 0      # next run
    led = Ledger(cfg.ledger_path)
    assert led.get(SIDS[0])["state"] == "DELIVERED"
    assert led.open_batches() == []
    fin = led.db.execute("SELECT summary_json FROM batches WHERE batch_no=?",
                         (batch_no,)).fetchone()
    assert json.loads(fin["summary_json"])["delivered"] == 1
    led.close()
    assert len(msgs) == 1 and f"batch #{batch_no}" in msgs[0]
