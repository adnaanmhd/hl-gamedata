"""r-loop 20 fixes (N-set, R8_IMPLEMENTATION_PLAN §0) — pipeline side.

Each test cites the iteration-20 finding it pins (r20 #N, findings of
record in R20_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 5524563 (session scratchpad); pin-only tests use
the mutation-proof pattern with the finders' EXACT mutants, per plan
§0/§1. Iteration 21 reviews this set (the 2026-08-20 NEW RULING).
"""
from __future__ import annotations

import json

from pipeline.tests.test_r_loop8 import needs_ffmpeg  # noqa: F401

# ------- r20 #1 / #5 / #10 / #11 (N1): fix_v1_to_v2's stamp/trim
# ------- resolution completed — falsiness is not absence, destroyed
# ------- evidence never reads as head 0, the emit is resolved
# ------- pre-write, and an overflow blames the junk side


@needs_ffmpeg
def test_v1_sidecar_route_refuses_falsy_junk_trim(tmp_path):
    """r20 #1: float(x or 0.0) short-circuited every falsy junk
    head_cut_s ("", null, false, [], {}) into a fabricated head 0.0 —
    and bool True into a fabricated 1.0s cut — bypassing the refusal
    gate M1 added for exactly this evidence, so the r19 #1 blocker
    chain (created == started shipped beside a >=5s-binned CSV,
    checker-green delivered desync) survived for the falsy family
    while 'abc' correctly refused. PRESENT-but-junk now refuses typed
    on the live-sidecar route, before any write."""
    import csv as _csv

    import pytest

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    for i, junk in enumerate(("", None, False, [], {}, True)):
        work = _v1_with_sidecars(tmp_path, "not-a-date", f"n1falsy{i}",
                                 "2026-08-10T15:33:55Z",
                                 {"head_cut_s": junk}, at_root=True)
        with pytest.raises(fixmod.FixFailed) as e:
            fixmod.fix_v1_to_v2(work, "kamla")
        assert "canonical.trim" in str(e.value), repr(junk)
        with (work / "frames.csv").open(newline="") as f:
            header = next(_csv.reader(f))
        assert len(header) == 7, \
            f"the refusal precedes every write ({junk!r})"


@needs_ffmpeg
def test_v1_good_stamp_falsy_trim_refuses_on_live_route(tmp_path):
    """r20 #1 (the silent half): a GOOD stamp beside a falsy-junk
    head_cut_s converted SILENTLY at head 0.0 on the live-contract
    route — no recovery note, no refusal — shipping created_at
    verbatim while the CSV was binned at the real head cut. Junk trim
    evidence beside usable sidecars refuses regardless of the stamp."""
    import pytest

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_with_sidecars(tmp_path, "2026-08-10T15:34:03Z", "n1gs",
                             "2026-08-10T15:33:55Z",
                             {"head_cut_s": ""}, at_root=True)
    with pytest.raises(fixmod.FixFailed) as e:
        fixmod.fix_v1_to_v2(work, "kamla")
    assert "canonical.trim" in str(e.value)


@needs_ffmpeg
def test_v1_no_sidecar_falsy_trim_keeps_stamp(tmp_path):
    """N1 proceed-side control (§2 rule 4, the r19 #10 semantics
    preserved): with NO sidecars nothing downstream consumes
    created − started, so a falsy-junk head cut degrades to 0.0 and
    the parseable stamp ships — same disposition as 'abc'/'5,0'."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    for i, junk in enumerate(("", None, False, [], {}, True)):
        work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", f"n1nsf{i}")
        s = json.loads((work / "session.json").read_text())
        s["canonical"]["trim"] = {"head_cut_s": junk}
        (work / "session.json").write_text(json.dumps(s))
        note = fixmod.fix_v1_to_v2(work, "kamla")
        assert "converted v1 -> v2" in note, repr(junk)
        out = json.loads((work / "session.json").read_text())
        assert out["created_at_utc"] == "2026-08-10T15:34:03.000000Z", \
            repr(junk)


@needs_ffmpeg
def test_v1_destroyed_sessionjson_refuses_on_live_route(tmp_path):
    """r20 #5: a torn/unreadable session.json (or a non-dict canonical)
    degraded to canonical={} whose ABSENT trim read as the v1-optional
    head 0.0 — on the live-sidecar route the conversion then RECOVERED
    created = started + 0, a fabricated head offset for footage whose
    genuine v1 head cut is >=5s by construction (the r19 #1 desync
    chain through the destroyed-evidence door). Destroyed canonical
    evidence now refuses typed on the live route, before any write."""
    import csv as _csv

    import pytest

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    for i, torn in enumerate(('{"canonical": {"created_at',
                              '{"canonical": null}')):
        work = _v1_with_sidecars(tmp_path, "2026-08-10T15:34:03Z",
                                 f"n1torn{i}", "2026-08-10T15:33:55Z",
                                 ..., at_root=True)
        (work / "session.json").write_text(torn)
        with pytest.raises(fixmod.FixFailed) as e:
            fixmod.fix_v1_to_v2(work, "kamla")
        assert "session.json" in str(e.value), torn
        with (work / "frames.csv").open(newline="") as f:
            header = next(_csv.reader(f))
        assert len(header) == 7, torn


@needs_ffmpeg
def test_v1_absent_trim_on_readable_canonical_is_head0(tmp_path):
    """N1 proceed-side control (§2 rule 4): a READABLE well-formed
    canonical with NO trim key is the documented v1-optional shape — a
    payload that never recorded a cut — and head 0.0 is TRUE there,
    sidecars or not. The destroyed-evidence refusal must not fire."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_with_sidecars(tmp_path, "2026-08-10T15:33:55Z", "n1abs",
                             "2026-08-10T15:33:55Z", ..., at_root=True)
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note
    s = json.loads((work / "session.json").read_text())
    assert s["created_at_utc"] == "2026-08-10T15:33:55.000000Z"


@needs_ffmpeg
def test_v1_nearmax_stamp_sane_trim_recovers(tmp_path):
    """r20 #11: created + head_cut overflows for two DIFFERENT junk
    sides, and M1 blamed the head for both — a parseable near-max
    STAMP beside a sane trim skipped the designed recovery arm and
    refused with a false 'repair canonical.trim' diagnosis. The junk
    side is now disambiguated: the head's own timedelta constructs, so
    the stamp is treated unusable and ground-truth recovery proceeds
    (the committed unusable-stamp disposition)."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_with_sidecars(tmp_path, "9999-12-31T23:59:59Z", "n1nm",
                             "2026-08-10T15:33:55Z",
                             {"head_cut_s": 8.0}, at_root=True)
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "recovered" in note
    s = json.loads((work / "session.json").read_text())
    assert s["created_at_utc"] == "2026-08-10T15:34:03.000000Z"


@needs_ffmpeg
def test_v1_huge_head_cut_still_refuses_on_live_route(tmp_path):
    """N1 refuse-side control for the #11 disambiguation (§2 rule 4):
    a sane stamp beside a large-but-finite head cut whose OWN timedelta
    overflows is genuinely head-side junk — the canonical.trim refusal
    stands on the live route."""
    import pytest

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_with_sidecars(tmp_path, "2026-08-10T15:34:03Z", "n1hh",
                             "2026-08-10T15:33:55Z",
                             {"head_cut_s": 1e18}, at_root=True)
    with pytest.raises(fixmod.FixFailed) as e:
        fixmod.fix_v1_to_v2(work, "kamla")
    assert "canonical.trim" in str(e.value)


@needs_ffmpeg
def test_v1_nearmax_aware_stamp_emit_overflow_degrades(tmp_path):
    """r20 #10: the created_at emit (astimezone → strftime) ran at the
    session.json write, AFTER frames.csv was rewritten, with no guard —
    an aware negative-offset stamp near datetime.max survives the
    addition in naive fields yet overflows the UTC conversion, so the
    route crashed mid-write on both attempts (a junk shape that still
    crashed, entry 90's defect clause). The emit is now resolved
    inside the pre-write resolution block: no-sidecar → the stamp is
    unusable, omit-and-synthesize; sidecar → ground-truth recovery."""
    from translator.v2 import _TS_RE

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_work(tmp_path, "9999-12-31T20:00:00-05:00", "n1emit")
    s = json.loads((work / "session.json").read_text())
    s["canonical"]["trim"] = {"head_cut_s": 5.0}
    (work / "session.json").write_text(json.dumps(s))
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note
    out = json.loads((work / "session.json").read_text())
    assert _TS_RE.match(out["created_at_utc"]) and \
        not out["created_at_utc"].startswith("9999"), \
        "the unusable stamp is synthesized, never shipped or crashed"
    work2 = _v1_with_sidecars(tmp_path, "9999-12-31T20:00:00-05:00",
                              "n1emit2", "2026-08-10T15:33:55Z",
                              {"head_cut_s": 8.0}, at_root=True)
    note2 = fixmod.fix_v1_to_v2(work2, "kamla")
    assert "recovered" in note2
    s2 = json.loads((work2 / "session.json").read_text())
    assert s2["created_at_utc"] == "2026-08-10T15:34:03.000000Z"


@needs_ffmpeg
def test_v1_nearmax_started_at_refuses_recovery(tmp_path):
    """r20 #10 (recovery-side twin): when ground truth ITSELF cannot
    produce a representable stamp (a near-max parseable started_at —
    the M2 gate only requires parseability), the recovery refuses
    typed instead of crashing past the frames.csv write."""
    import pytest

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_with_sidecars(tmp_path, "not-a-date", "n1nmst",
                             "9999-12-31T23:59:59Z",
                             {"head_cut_s": 8.0}, at_root=True)
    with pytest.raises(fixmod.FixFailed) as e:
        fixmod.fix_v1_to_v2(work, "kamla")
    assert "cannot recover" in str(e.value)


# ------- r20 #4 / #9 (N2): fix_sessionjson_recompute's overflow
# ------- degrade completed — the except arm cannot re-overflow and
# ------- the ended emit joins the guard


@needs_ffmpeg
def test_recompute_survives_huge_container_duration(tmp_path,
                                                    monkeypatch):
    """r20 #4: M6's except arm reset `created` to now and re-ran the
    IDENTICAL `ended = created + timedelta(duration)` — when the
    DURATION itself is the overflowing side (a crafted/corrupt
    container duration; player-supplied bytes are never bounded), the
    re-run addition raised OverflowError a second time, uncaught,
    inside the degrade arm — re-crashing the repair chain M6 was built
    to save, on every plan (recompute rides every fix chain). The
    inner addition now degrades to ended = created (zero-length; the
    checker's duration compare owns the junk duration, G4)."""
    import dataclasses

    from translator.v2 import _TS_RE

    from pipeline import fix as fixmod
    from pipeline.tests.test_fix_cut_gate import _make_session
    work = _make_session(tmp_path, seconds=80, name="n2dur")
    real = fixmod.V.probe(work / "video.mp4")
    monkeypatch.setattr(
        fixmod.V, "probe",
        lambda p: dataclasses.replace(real, duration_s=1e12))
    note = fixmod.fix_sessionjson_recompute(work, "kamla")
    assert "recomputed" in note
    s = json.loads((work / "session.json").read_text())
    assert _TS_RE.match(s["created_at_utc"]) and \
        _TS_RE.match(s["ended_at_utc"])


@needs_ffmpeg
def test_recompute_nearmax_negoffset_ended_emit_degrades(tmp_path):
    """r20 #9: the ended_at_utc emit astimezone sat OUTSIDE M6's guard
    — a regex-conformant, parseable '9999-12-31T20:00:00-05:00'
    survives the addition in naive fields (20:00 + clip stays inside
    year 9999) and overflows only at the UTC conversion in s.update,
    crashing the rewrite on both attempts with nothing written. The
    emit now lives inside the guard and the shape degrades to the
    designed synthesized stamp."""
    from translator.v2 import _TS_RE

    from pipeline import fix as fixmod
    from pipeline.tests.test_fix_cut_gate import _make_session
    work = _make_session(tmp_path, seconds=80, name="n2emit")
    s = json.loads((work / "session.json").read_text())
    s["created_at_utc"] = "9999-12-31T20:00:00-05:00"
    (work / "session.json").write_text(json.dumps(s))
    note = fixmod.fix_sessionjson_recompute(work, "kamla")
    assert "recomputed" in note
    out = json.loads((work / "session.json").read_text())
    assert not out["created_at_utc"].startswith("9999"), \
        "the unusable stamp is synthesized from now"
    assert _TS_RE.match(out["ended_at_utc"])


# ------- r20 #2≡#6 (N3, payment-surface): the '' preserve arms clear
# ------- the LABELS-only accepted mark — shipped hours stay payable


def test_rejected_counted_rezip_then_delivered_hours_reach_sheet(
        cfg, ledger):
    """r20 #2≡#6 (N3): M4 preserved accepted_reported_at through the
    '' supersede — but on the writer's whole population (REJECTED/
    QUARANTINED slots) that mark means 'reject LABELS counted, zero
    hours paid' (the refix doctrine). When the identical-bytes re-run
    then DELIVERED (fresh fix budget, nondeterministic VLM — the
    routine recovery premise), build_sheet_rows skipped the
    accepted-marked node forever: shipped footage, player never paid,
    zero loud lines. The '' supersede now clears the labels-only mark
    (money marks stay preserved) so the delivered hours reach exactly
    one later sheet — the finder's day-2 assertion inverted."""
    from pipeline.tests.test_payment_split_r6 import (UNFIXABLE, W1, W2,
                                                      W3, _put, _sheet)
    sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000d3"
    _put(ledger, sid, state="REJECTED", raw=1800.0, reasons=UNFIXABLE,
         player="n3@x.com")
    ledger.update(sid, md5_video="a1" * 16)
    _sheet(ledger, W1)
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["accepted_reported_at"], \
        "seed control: the reject was counted (labels mark landed)"
    ledger.supersede(sid, new_md5="", new_bytes=22,
                     new_ctime="2026-08-15T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["duration_raw_s"] == 1800.0, \
        "the money marks stay preserved (M4)"
    assert row["accepted_reported_at"] is None, \
        "the labels-only mark is cleared (N3)"
    ledger.update(sid, duration_delivered_s=1700.0,
                  delivered_at="2026-08-15T10:00:00+00:00")
    ledger.set_state(sid, "DELIVERED")
    rows2 = _sheet(ledger, W2)
    mine = [r for r in rows2 if r["player_email"] == "n3@x.com"]
    assert mine and mine[0]["kamla_accepted_hrs"] == 0.47 \
        and mine[0]["kamla_hrs_uploaded"] == 0.0, \
        "the delivered re-run's hours must reach the next sheet " \
        "(accepted side only — uploaded stays counted-once)"
    rows3 = _sheet(ledger, W3)
    assert not [r for r in rows3 if r["player_email"] == "n3@x.com"], \
        "…and exactly once"


def test_delivered_row_blank_supersede_keeps_hours_mark(cfg, ledger):
    """N3 refuse-side control (§2 rule 4): on a DELIVERED row the
    accepted mark IS an hours mark — the '' preserve arm keeps it (no
    production caller supersedes a DELIVERED row; the guard is
    belt-and-braces and this pins it)."""
    from pipeline.tests.test_payment_split_r6 import W1, _put, _sheet
    sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000d4"
    _put(ledger, sid, state="DELIVERED", raw=1800.0, delivered=1700.0,
         player="n3d@x.com")
    ledger.update(sid, md5_video="b2" * 16)
    _sheet(ledger, W1)
    assert ledger.get(sid)["accepted_reported_at"]
    ledger.supersede(sid, new_md5="", new_bytes=22,
                     new_ctime="2026-08-15T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    row = ledger.get(sid)
    assert row["accepted_reported_at"] and row["uploaded_reported_at"], \
        "an hours mark on a DELIVERED row survives the '' preserve arm"


def test_heal_preserve_arm_clears_labels_mark(cfg, ledger):
    """r20 #2≡#6 (N3) heal sibling: the quarantined-path heal's
    preserve arms (identical md5, or '' vmd5) carried the same
    LABELS-only accepted mark onto the healed slot — same stranding
    when the re-run delivers. The preserve arms now clear it; the
    money marks stay preserved exactly as ruled (entries 25/32)."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    sid = "2026-08-14T10-00-00Z_kamla_c_00000000000000e3"
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=sid, md5="c3" * 16))
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000zz", md5="zz"))
    assert ledger.get(sid)["state"] == "QUARANTINED"
    ledger.update(sid, duration_raw_s=3600.0,
                  uploaded_reported_at="2026-08-15T00:00:00+00:00",
                  accepted_reported_at="2026-08-15T00:00:00+00:00")
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=sid, op="op2@x.com", md5="c3" * 16,
        ctime="2026-08-14T11:00:00.000Z"))
    assert any("quarantined path healed" in f for f in res.integrity_flags)
    row = ledger.get(sid)
    assert row["state"] == "DISCOVERED"
    assert row["duration_raw_s"] == 3600.0 and row["uploaded_reported_at"], \
        "identical bytes: the money marks stay preserved (entry 25)"
    assert row["accepted_reported_at"] is None, \
        "the labels-only mark is cleared (N3)"
