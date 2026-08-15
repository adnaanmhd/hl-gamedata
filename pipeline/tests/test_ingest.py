from pipeline import config as C
from pipeline import ingest
from pipeline.tests.conftest import make_session_entries

SID1 = "2026-08-14T10-00-00Z_kamla_c_0123456789abcdef"
SID2 = "2026-08-14T11-00-00Z_kamla_c_fedcba9876543210"
SID_OW = "2026-08-14T12-00-00Z_outer_wilds_c_00aa11bb22cc33dd"


def test_parse_valid_session():
    sessions, quarantined, oot = ingest.parse_listing(make_session_entries())
    assert not quarantined and oot == 0
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == SID1
    assert s.game == "kamla" and s.slug_game == "kamla"
    assert s.operator_email == "op@x.com"
    assert set(C.REQUIRED_FILES) <= set(s.files)


def test_parse_operator_names_ingest_since_q5_amendment():
    """Q5 amended 08-15: operator folders are free-text NAMES — the 08-14
    name-folders (`kamla/Rukaiya+Tanzeela`, …) are valid by design."""
    sessions, quarantined, _ = ingest.parse_listing(
        make_session_entries(op="Bisrambha+Samik"))
    assert not quarantined
    assert len(sessions) == 1
    assert sessions[0].operator_email == "Bisrambha+Samik"


def test_parse_quarantines_non_email_player_and_bad_depth():
    """The junk guard lives one level down: player folders stay strict
    emails, and short paths still quarantine."""
    entries = (make_session_entries(player="Rukaiya Tanzeela")
               + [{"Path": f"kamla/x@y.co/{SID2}/video.mp4", "Name": "video.mp4",
                   "IsDir": False, "Size": 1, "ModTime": "", "Hashes": {}}])
    sessions, quarantined, _ = ingest.parse_listing(entries)
    assert sessions == []
    whys = " | ".join(w for _, w in quarantined)
    assert "player folder" in whys and "not an email" in whys
    assert "depth" in whys


def test_parse_ignores_out_of_tree():
    entries = [{"Path": "junk/test.mp4", "Name": "test.mp4", "IsDir": False,
                "Size": 5, "ModTime": "", "Hashes": {}}]
    sessions, quarantined, oot = ingest.parse_listing(entries)
    assert sessions == [] and quarantined == [] and oot == 1


def test_parse_zip_payload():
    entries = make_session_entries(files=["bundle.zip"])
    sessions, _, _ = ingest.parse_listing(entries)
    assert sessions[0].payload == "zip"
    assert ingest._completeness(sessions[0]) == []


def test_scan_incomplete_then_complete(cfg, ledger):
    part = make_session_entries(files=["video.mp4", "frames.csv",
                                       "session.json"])
    res = ingest.scan(cfg, ledger, entries=part)
    assert res.discovered == []
    assert res.incomplete and sorted(res.incomplete[0][1]) == [
        "inputs.jsonl", "metadata.json"]
    assert len(ledger.incomplete_list()) == 1

    res2 = ingest.scan(cfg, ledger, entries=make_session_entries())
    assert res2.discovered == [SID1]
    assert ledger.incomplete_list() == []
    assert ledger.get(SID1)["state"] == "DISCOVERED"


def test_scan_same_player_duplicate_skips_silently(cfg, ledger):
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="samemd5"))
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID2, md5="samemd5", ctime="2026-08-14T11:00:00.000Z"))
    assert res.duplicates == [SID2]
    assert res.integrity_flags == []
    assert ledger.get(SID2)["state"] == "DUPLICATE"


def test_scan_cross_identity_duplicate_keeps_earliest(cfg, ledger):
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="X"))
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID2, player="p2@x.com", md5="X",
        ctime="2026-08-14T11:00:00.000Z"))
    assert res.dup_cross == [SID2]
    assert ledger.get(SID2)["state"] == "REJECTED"
    assert "INT_DUP_CROSS" in ledger.get(SID2)["reasons_json"]
    assert any("cross-player" in f for f in res.integrity_flags)


def test_scan_cross_identity_new_copy_earlier_wins(cfg, ledger):
    ingest.scan(cfg, ledger, entries=make_session_entries(
        md5="X", ctime="2026-08-14T12:00:00.000Z"))
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID2, player="p2@x.com", md5="X",
        ctime="2026-08-14T09:00:00.000Z"))
    assert SID2 in res.discovered
    assert ledger.get(SID1)["state"] == "REJECTED"
    assert ledger.get(SID2)["state"] == "DISCOVERED"


def test_scan_supersede_after_reject(cfg, ledger):
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="old"))
    ledger.set_state(SID1, "REJECTED")
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        md5="new", ctime="2026-08-15T00:00:00.000Z"))
    assert res.superseded == [SID1]
    row = ledger.get(SID1)
    assert row["state"] == "DISCOVERED" and row["md5_video"] == "new"


def test_scan_no_supersede_while_in_flight(cfg, ledger):
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="old"))
    ledger.set_state(SID1, "VALIDATING")
    res = ingest.scan(cfg, ledger, entries=make_session_entries(md5="new"))
    assert res.superseded == []
    assert any("not superseding" in f for f in res.integrity_flags)
    assert ledger.get(SID1)["md5_video"] == "old"


def test_next_batch_fifo_and_priority(cfg, ledger):
    entries = (make_session_entries(md5="m1",
                                    ctime="2026-08-14T10:00:00.000Z")
               + make_session_entries(sid=SID2, md5="m2",
                                      ctime="2026-08-14T09:00:00.000Z")
               + make_session_entries(sid=SID_OW, game="outer_wilds",
                                      md5="m3",
                                      ctime="2026-08-14T12:00:00.000Z"))
    ingest.scan(cfg, ledger, entries=entries)
    # no delivered hours yet -> pure FIFO
    assert ingest.next_batch(ledger) == [SID2, SID1, SID_OW]
    # give kamla a big lead -> outer_wilds becomes the lagging game
    ledger.insert_session(
        session_id="done1", game="kamla", operator_email="o@x.com",
        player_email="p@x.com", drive_path="kamla/o/p/done1",
        drive_ctime="2026-08-13T00:00:00.000Z", md5_video="zz", bytes_=1,
        state="DELIVERED")
    ledger.update("done1", duration_delivered_s=7200.0,
                  delivered_at="2026-08-14T00:00:00+00:00")
    assert ingest.lagging_game(ledger) == "outer_wilds"
    assert ingest.next_batch(ledger)[0] == SID_OW


def test_sniff_payload(tmp_path):
    (tmp_path / "frames.csv").write_text("h")
    (tmp_path / "session.json").write_text('{"game_title": "Kamla"}')
    assert ingest.sniff_payload(tmp_path) == "v2"
    (tmp_path / "key_binding.json").write_text("{}")
    assert ingest.sniff_payload(tmp_path) == "v1"
    (tmp_path / "key_binding.json").unlink()
    (tmp_path / "session.json").write_text('{"canonical": {}}')
    assert ingest.sniff_payload(tmp_path) == "v1"
    for f in tmp_path.iterdir():
        f.unlink()
    (tmp_path / "video.mp4").write_bytes(b"x")
    (tmp_path / "inputs.jsonl").write_text("")
    (tmp_path / "metadata.json").write_text("{}")
    assert ingest.sniff_payload(tmp_path) == "raw"
    (tmp_path / "inputs.jsonl").unlink()
    assert ingest.sniff_payload(tmp_path) == "garbage"


def test_download_verifies_md5_and_stubs_rrd(cfg, ledger, monkeypatch):
    import hashlib
    payload = b"fake-video-bytes"
    md5 = hashlib.md5(payload).hexdigest()
    ingest.scan(cfg, ledger, entries=make_session_entries(md5=md5))

    def fake_rclone(args, **kw):
        import subprocess
        dst = None
        for a in args:
            if str(cfg.work) in str(a):
                dst = a
        assert dst is not None
        d = ingest.Path(dst)
        d.mkdir(parents=True, exist_ok=True)
        (d / "video.mp4").write_bytes(payload)
        (d / "frames.csv").write_text("frame_id\n")
        (d / "session.json").write_text('{"game_title": "Kamla"}')
        (d / "inputs.jsonl").write_text("")
        (d / "metadata.json").write_text("{}")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ingest, "run_rclone", fake_rclone)
    monkeypatch.setattr(ingest, "_probe_duration", lambda v: 123.0)
    kind = ingest.download(cfg, ledger, SID1)
    assert kind == "v2"
    row = ledger.get(SID1)
    assert row["state"] == "INGESTED"
    assert row["duration_raw_s"] == 123.0
    work = cfg.work / SID1
    assert (work / "session.rrd").exists()          # stub for qa-v2 presence
    assert (work / "rrd_creation.py").exists()
    # sidecars tucked into raw/ so the engine sees a clean v2 root
    assert (work / "raw" / "inputs.jsonl").exists()
    assert (work / "raw" / "metadata.json").exists()
    assert not (work / "inputs.jsonl").exists()


def test_download_md5_mismatch_quarantine_path(cfg, ledger, monkeypatch):
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="ff" * 16))

    def fake_rclone(args, **kw):
        import subprocess
        for a in args:
            if str(cfg.work) in str(a):
                d = ingest.Path(a)
                d.mkdir(parents=True, exist_ok=True)
                (d / "video.mp4").write_bytes(b"wrong-content")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ingest, "run_rclone", fake_rclone)
    try:
        ingest.download(cfg, ledger, SID1)
        assert False, "must raise after 3 md5 mismatches"
    except ingest.DownloadError as e:
        assert "md5 mismatch" in str(e)


def test_session_id_collision_across_paths_never_supersedes(cfg, ledger):
    """Review finding: a different Drive path reusing a known session id
    must be flagged and ignored — never merged, deduped-away silently, or
    allowed to supersede a reject."""
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="orig"))
    ledger.set_state(SID1, "REJECTED")
    # same sid, same video, DIFFERENT player path
    collide = make_session_entries(player="p2@x.com", md5="orig")
    res = ingest.scan(cfg, ledger, entries=collide)
    assert res.superseded == [] and res.discovered == []
    assert any("session-id collision" in f for f in res.integrity_flags)
    assert ledger.get(SID1)["state"] == "REJECTED"          # untouched
    # different md5 from the foreign path must not supersede either
    res2 = ingest.scan(cfg, ledger, entries=make_session_entries(
        player="p2@x.com", md5="evil"))
    assert res2.superseded == []
    assert ledger.get(SID1)["md5_video"] == "orig"


def test_supersede_refuses_other_sessions_video(cfg, ledger):
    """Review-2 #1: a rejected slot must not be superseded by bytes that
    already exist under another session (payment side door)."""
    ingest.scan(cfg, ledger, entries=make_session_entries(md5="mineA"))
    ledger.set_state(SID1, "DELIVERED")
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID2, player="p2@x.com", md5="theirsB",
        ctime="2026-08-14T11:00:00.000Z"))
    ledger.set_state(SID2, "REJECTED")
    # p2 re-uploads SID2's slot carrying SID1's already-delivered bytes
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID2, player="p2@x.com", md5="mineA",
        ctime="2026-08-14T12:00:00.000Z"))
    assert res.superseded == []
    assert ledger.get(SID2)["state"] == "REJECTED"
    assert ledger.get(SID2)["md5_video"] == "theirsB"     # untouched
    assert any("not superseding" in f for f in res.integrity_flags)


def test_zip_payload_md5_backfilled_and_deduped(cfg, ledger, monkeypatch):
    """Review-2 #2: zip payloads must land inside the dedupe rules."""
    import hashlib
    payload = b"shared-video-bytes"
    md5 = hashlib.md5(payload).hexdigest()
    # an existing delivered session with the same bytes, other player
    ingest.scan(cfg, ledger, entries=make_session_entries(md5=md5))
    ledger.set_state(SID1, "DELIVERED")
    # zip upload by another player, no Drive-side video md5
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=SID2, player="p2@x.com", files=["bundle.zip"],
        ctime="2026-08-14T12:00:00.000Z"))
    assert ledger.get(SID2)["md5_video"] == ""

    def fake_rclone(args, **kw):
        import io
        import subprocess
        import zipfile
        for a in args:
            if str(cfg.work) in str(a):
                d = ingest.Path(a)
                d.mkdir(parents=True, exist_ok=True)
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    z.writestr("video.mp4", payload)
                    z.writestr("frames.csv", "frame_id\n")
                    z.writestr("session.json", '{"game_title": "Kamla"}')
                    z.writestr("inputs.jsonl", "")
                    z.writestr("metadata.json", "{}")
                (d / "bundle.zip").write_bytes(buf.getvalue())
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(ingest, "run_rclone", fake_rclone)
    kind = ingest.download(cfg, ledger, SID2)
    assert kind == "duplicate"
    row = ledger.get(SID2)
    assert row["state"] == "REJECTED"
    assert row["md5_video"] == md5
    assert "INT_DUP_CROSS" in row["reasons_json"]
