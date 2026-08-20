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


# ------- r20 #3 + #7 (N4, payment-surface): '' means unknowable
# ------- regardless of the stored md5, and a real md5 over a ''-slot
# ------- adjudicates against the breadcrumb


def test_second_blank_supersede_still_preserves(cfg, ledger):
    """r20 #7: M4's guard required a REAL stored md5
    (zip_unknowable = not new_md5 AND bool(row md5)), so the very
    stamps+''-md5 row the first '' supersede creates failed the guard
    on the SECOND '' supersede (bad-archive quarantine, then the
    coached corrected re-zip — the branch's own invited flow) and took
    the full clear with zero byte evidence, silently re-opening the
    r19 #5 double-pay across two sent sheets. Unknowable is now
    unknowable regardless of the stored md5; the breadcrumb is written
    only over a real prior md5, so the deferral still sees the counted
    bytes."""
    from pipeline.tests.test_payment_split_r6 import (UNFIXABLE, W1,
                                                      _put, _sheet)
    sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000f4"
    _put(ledger, sid, state="REJECTED", raw=1800.0, reasons=UNFIXABLE,
         player="n4@x.com")
    ledger.update(sid, md5_video="a1" * 16)
    _sheet(ledger, W1)
    assert ledger.get(sid)["uploaded_reported_at"]
    ledger.supersede(sid, new_md5="", new_bytes=22,
                     new_ctime="2026-08-15T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["duration_raw_s"] == 1800.0
    ledger.set_state(sid, "QUARANTINED", "bad archive")
    ledger.supersede(sid, new_md5="", new_bytes=23,
                     new_ctime="2026-08-16T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] and row["duration_raw_s"] == 1800.0, \
        "unknowable is unknowable — the second '' supersede preserves too"
    evs = ledger.db.execute(
        "SELECT detail FROM events WHERE session_id=? AND detail LIKE"
        " '%prev_md5=%'", (sid,)).fetchall()
    assert len(evs) == 1, "no breadcrumb is written over a ''-md5 row"
    assert ledger.latest_prev_md5(sid) == "a1" * 16, \
        "the deferral still sees the bytes the sheet counted"


def test_real_md5_over_blank_adjudicates_breadcrumb(cfg, ledger):
    """r20 #3: any real-md5 write over a stored-'' slot read
    'unknowable' as 'changed' and full-cleared the M4-preserved stamps
    even when the breadcrumb PROVED the bytes identical (the coached
    plain-file re-upload of the original footage after a corrupt zip)
    — and with a real md5 restored, the download deferral is skipped
    entirely, so nothing self-healed: the same hours re-entered a
    second sent sheet. The supersede now adjudicates against the
    newest prev_md5 breadcrumb; a DIFFERENT real md5 keeps the full
    clear (proceed-side control, §2 rule 4)."""
    from pipeline.tests.test_payment_split_r6 import (UNFIXABLE, W1,
                                                      _put, _sheet)
    sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000f5"
    _put(ledger, sid, state="REJECTED", raw=1800.0, reasons=UNFIXABLE,
         player="n4b@x.com")
    ledger.update(sid, md5_video="a1" * 16)
    _sheet(ledger, W1)
    ledger.supersede(sid, new_md5="", new_bytes=22,
                     new_ctime="2026-08-15T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    ledger.set_state(sid, "QUARANTINED", "bad archive")
    ledger.supersede(sid, new_md5="a1" * 16, new_bytes=24,
                     new_ctime="2026-08-16T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    row = ledger.get(sid)
    assert row["md5_video"] == "a1" * 16
    assert row["uploaded_reported_at"] and row["duration_raw_s"] == 1800.0, \
        "breadcrumb-equal bytes are provably identical — preserve"
    # control: a DIFFERENT real md5 over '' is known-new bytes
    sid2 = "2026-08-14T09-00-00Z_kamla_c_00000000000000f6"
    _put(ledger, sid2, state="REJECTED", raw=1800.0, reasons=UNFIXABLE,
         player="n4c@x.com")
    ledger.update(sid2, md5_video="b2" * 16)
    _sheet(ledger, W1)
    ledger.supersede(sid2, new_md5="", new_bytes=22,
                     new_ctime="2026-08-15T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    ledger.set_state(sid2, "QUARANTINED", "bad archive")
    ledger.supersede(sid2, new_md5="c3" * 16, new_bytes=24,
                     new_ctime="2026-08-16T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    row2 = ledger.get(sid2)
    assert row2["uploaded_reported_at"] is None \
        and row2["duration_raw_s"] is None, \
        "a different real md5 over '' is known-new bytes — full clear"


def test_heal_real_md5_over_blank_adjudicates_breadcrumb(cfg, ledger):
    """r20 #3 heal sibling: the heal's clears fired on
    vmd5 != stored-'' — an operator typo-fix rename (or payload-switch
    re-upload at a new path) whose listing md5 EQUALS the breadcrumb
    cleared the preserved stamps for provably identical bytes. The
    heal now runs the same breadcrumb adjudication; a different real
    md5 still clears (known-new bytes)."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    sid = "2026-08-14T10-00-00Z_kamla_c_00000000000000f7"
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=sid, md5="d4" * 16))
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000zz", md5="zz"))
    assert ledger.get(sid)["state"] == "QUARANTINED"
    # a zip-class '' writer left the sentinel + breadcrumb + preserved
    # money marks (the r13 #2 seeding idiom)
    ledger.update(sid, md5_video="", duration_raw_s=3600.0,
                  uploaded_reported_at="2026-08-15T00:00:00+00:00")
    ledger.db.execute(
        "INSERT INTO events(session_id, ts, from_state, to_state,"
        " detail) VALUES(?,?,?,?,?)",
        (sid, "2026-08-15T00:00:00+00:00", "QUARANTINED", "DISCOVERED",
         f"superseded: new md5 ; prev_md5={'d4' * 16}"))
    ledger.db.commit()
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=sid, op="op2@x.com", md5="d4" * 16,
        ctime="2026-08-14T11:00:00.000Z"))
    assert any("quarantined path healed" in f for f in res.integrity_flags)
    row = ledger.get(sid)
    assert row["duration_raw_s"] == 3600.0 and row["uploaded_reported_at"], \
        "breadcrumb-equal bytes: the heal preserves the money marks"
    # control: a different real md5 at yet another path clears
    ledger.set_state(sid, "QUARANTINED", "re-quarantined for the control")
    ledger.update(sid, md5_video="")
    res2 = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=sid, op="op3@x.com", md5="e5" * 16,
        ctime="2026-08-14T12:00:00.000Z"))
    assert any("quarantined path healed" in f
               for f in res2.integrity_flags)
    row = ledger.get(sid)
    assert row["uploaded_reported_at"] is None \
        and row["duration_raw_s"] is None, \
        "a different real md5 over '' is known-new bytes — full clear"


# ------- r20 #8 (N5, tests-only): _v1_sidecar_started's degrade
# ------- envelope gets failing-side pins — the finders' exact
# ------- mutants killed


@needs_ffmpeg
def test_v1_sidecar_probe_tolerates_non_utf8_metadata(tmp_path):
    """r20 #8 pin (a): the M1 probe's metadata read carries
    errors='replace' — player-typed latin-1 inside a string value must
    not cost the recovery (UnicodeDecodeError is a ValueError but NOT
    JSONDecodeError, so the probe's except tuple cannot catch a bare
    read). The finder's exact drop-errors mutant was FULL-gate-green
    at 821; it fails only this pin."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_with_sidecars(tmp_path, "not-a-date", "n5lat",
                             "2026-08-10T15:33:55Z",
                             {"head_cut_s": 8.0}, at_root=True)
    (work / "metadata.json").write_bytes(
        b'{"recording": {"started_at_utc": "2026-08-10T15:33:55Z"},'
        b' "player": "Jos\xe9"}')
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "recovered" in note
    s = json.loads((work / "session.json").read_text())
    assert s["created_at_utc"] == "2026-08-10T15:34:03.000000Z"


@needs_ffmpeg
def test_v1_sidecar_probe_degrades_on_truncated_metadata(tmp_path):
    """r20 #8 pin (b): a PRESENT-but-truncated metadata.json must read
    as no-sidecars (the probe's OSError/JSONDecodeError arm) so the
    no-sidecar degrade applies — omit the junk stamp and synthesize.
    The finder's exact delete-the-try/except mutant was
    FULL-gate-green at 821; it fails only this pin."""
    from translator.v2 import _TS_RE

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_with_sidecars(tmp_path, "not-a-date", "n5trunc",
                             "2026-08-10T15:33:55Z",
                             {"head_cut_s": 8.0}, at_root=True)
    (work / "metadata.json").write_text('{"recording": {"started_at')
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note and "recovered" not in note
    out = json.loads((work / "session.json").read_text())
    assert _TS_RE.match(out["created_at_utc"]), \
        "unusable stamp + unreadable sidecars: omit-and-synthesize"


# ------- r21 #1 (O1, payment-surface): _stamp's non-CAS arms never
# ------- re-land the labels mark on a reset generation


def test_midwindow_blank_supersede_does_not_reland_labels_mark(
        cfg, ledger, capsys):
    """r21 #1 (O1): N3 clears the LABELS-only accepted mark at the ''
    writers, but reports._stamp re-landed the same mark when the ''
    supersede fired BETWEEN the sheet's row read and the accepted
    stamp — the CAS missed (row now ''), and the CAS-miss-'' arm (or
    the unconditional/recorded-'' arms) stamped the fresh DISCOVERED
    generation. The identical-bytes re-run that then DELIVERED carried
    a mark no sheet ever counted hours under: footage shipped, hours
    reached no sheet, forever, silently. _stamp now skips the ACCEPTED
    column LOUDLY when the row's state is no longer
    DELIVERED/REJECTED; uploaded keeps every arm unchanged."""
    from datetime import datetime

    import pipeline.config as C
    from pipeline import reports
    from pipeline.tests.test_payment_split_r6 import (UNFIXABLE, W1, W2,
                                                      W3, _put, _sheet)
    sid = "2026-08-14T09-00-00Z_kamla_c_00000000000000o1"
    _put(ledger, sid, state="REJECTED", raw=1800.0, reasons=UNFIXABLE,
         player="o1@x.com")
    ledger.update(sid, md5_video="a1" * 16)
    counted, accepted, md5s = [], [], {}
    reports.build_sheet_rows(ledger, datetime.now(C.IST), bounds=W1,
                             counted_out=counted, accepted_out=accepted,
                             md5_out=md5s)
    assert sid in counted and sid in accepted
    ledger.supersede(sid, new_md5="", new_bytes=22,
                     new_ctime="2026-08-15T00:00:00.000Z",
                     dossier_root=cfg.dossiers)
    reports.mark_uploads_reported(ledger, *W1, sids=counted, md5s=md5s)
    capsys.readouterr()
    reports.mark_accepted_reported(ledger, accepted, md5s=md5s)
    row = ledger.get(sid)
    assert row["uploaded_reported_at"], \
        "uploaded keeps today's behavior on every arm"
    assert row["accepted_reported_at"] is None, \
        "the labels mark must not land on the reset generation"
    assert "SKIPPED" in capsys.readouterr().err, "…and must say so"
    ledger.update(sid, duration_delivered_s=1700.0,
                  delivered_at="2026-08-15T10:00:00+00:00")
    ledger.set_state(sid, "DELIVERED")
    rows2 = _sheet(ledger, W2)
    mine = [r for r in rows2 if r["player_email"] == "o1@x.com"]
    assert mine and mine[0]["kamla_accepted_hrs"] == 0.47, \
        "the delivered re-run's hours reach the next sheet"
    rows3 = _sheet(ledger, W3)
    assert not [r for r in rows3 if r["player_email"] == "o1@x.com"], \
        "…and exactly once"


# ------- r21 #3 + #4≡#6 (O3): the head-cut gate completed — negative
# ------- junk refused, and the overflow disambiguation stops blaming
# ------- a good stamp for absurd-but-representable junk heads


@needs_ffmpeg
def test_v1_negative_head_cut_refuses_on_live_route(tmp_path):
    """r21 #3 (O3): a trim length is >= 0 by construction, but the
    isfinite-only usability test admitted negative finite junk — with
    a usable stamp the conversion shipped created BEFORE the recording
    started (verify falsely condemns the correct CSV, the retranslate
    re-bins at the clamped head 0 and disagrees with the verify
    forever → terminal reject), and with an unusable stamp the
    recovery emitted started − 30s under a false ground-truth
    attestation. Negative junk now takes the committed junk
    dispositions: typed canonical.trim refusal on the live route."""
    import pytest

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    for i, (created, name) in enumerate(
            (("2026-08-10T15:34:03Z", "o3neg0"),
             ("not-a-date", "o3neg1"))):
        work = _v1_with_sidecars(tmp_path, created, name,
                                 "2026-08-10T15:33:55Z",
                                 {"head_cut_s": -30.0}, at_root=True)
        with pytest.raises(fixmod.FixFailed) as e:
            fixmod.fix_v1_to_v2(work, "kamla")
        assert "canonical.trim" in str(e.value), created


@needs_ffmpeg
def test_v1_negative_head_cut_no_sidecar_keeps_stamp(tmp_path):
    """O3 no-sidecar arm (r19 #10 semantics): negative junk degrades
    like every other junk head VALUE — the parseable stamp ships at
    head 0.0, never shifted backwards."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", "o3negns")
    s = json.loads((work / "session.json").read_text())
    s["canonical"]["trim"] = {"head_cut_s": -30.0}
    (work / "session.json").write_text(json.dumps(s))
    note = fixmod.fix_v1_to_v2(work, "kamla")
    assert "converted v1 -> v2" in note
    out = json.loads((work / "session.json").read_text())
    assert out["created_at_utc"] == "2026-08-10T15:34:03.000000Z", \
        "a negative junk trim must not shift the valid stamp backwards"


@needs_ffmpeg
def test_v1_absurd_finite_head_keeps_stamp_no_sidecar(tmp_path):
    """r21 #4≡#6 (O3): the overflow disambiguation blamed the STAMP
    whenever timedelta(head_cut) constructed — but timedelta's range
    is ~340x wider than any present-day stamp can absorb, so an
    absurd-but-representable head (an epoch-milliseconds value ~1e12
    in the seconds field) discarded a GOOD stamp and the delivery
    shipped created_at = processing wall-clock, silently. The head is
    now the junk side unless it is also physically plausible: the
    good stamp survives at head 0.0 (r19 #10)."""
    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop15 import _v1_work
    for i, junk in enumerate((1e12, 3e11, 1.7e12)):
        work = _v1_work(tmp_path, "2026-08-10T15:34:03Z", f"o3band{i}")
        s = json.loads((work / "session.json").read_text())
        s["canonical"]["trim"] = {"head_cut_s": junk}
        (work / "session.json").write_text(json.dumps(s))
        note = fixmod.fix_v1_to_v2(work, "kamla")
        assert "converted v1 -> v2" in note, repr(junk)
        out = json.loads((work / "session.json").read_text())
        assert out["created_at_utc"] == "2026-08-10T15:34:03.000000Z", \
            f"an absurd junk head must not cost the valid stamp ({junk})"


@needs_ffmpeg
def test_v1_absurd_finite_head_refuses_on_live_route(tmp_path):
    """O3 live-route twin: the same absurd-band head beside a good
    stamp and usable sidecars takes the TRUTHFUL canonical.trim
    refusal — pre-O3 it misdirected to the 'cannot recover …
    raw/metadata.json' diagnosis (blaming a file nothing can repair
    for a defect that lives in session.json's trim)."""
    import pytest

    from pipeline import fix as fixmod
    from pipeline.tests.test_r_loop19 import _v1_with_sidecars
    work = _v1_with_sidecars(tmp_path, "2026-08-10T15:34:03Z", "o3bandl",
                             "2026-08-10T15:33:55Z",
                             {"head_cut_s": 1e12}, at_root=True)
    with pytest.raises(fixmod.FixFailed) as e:
        fixmod.fix_v1_to_v2(work, "kamla")
    assert "canonical.trim" in str(e.value)
