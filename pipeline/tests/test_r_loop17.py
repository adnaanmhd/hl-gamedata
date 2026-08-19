"""r-loop 17 fixes (K-set, R8_IMPLEMENTATION_PLAN §0) — pipeline side.

Each test cites the iteration-17 finding it pins (r17 #N, findings of
record in R17_FINDINGS.md). Fail-first proofs run in a scratch copy of
the pre-fix tree at 7ad7b71 (session scratchpad); pin-only tests use
the mutation-proof pattern with the finders' EXACT mutants, per plan
§0/§1.
"""
from __future__ import annotations


# ------- r17 #1 (K1): the quarantined-path heal refuses cross-player
# ------- identity claims (the review-r5 #41 guard, restored)


_K1_SID = "2026-08-14T10-00-00Z_kamla_c_00000000000000c1"
_PLAYER_A = "playera@x.com"
_PLAYER_B = "playerb@x.com"


def _k1_takeover_scans(cfg, ledger, *, md5_a="", md5_b="", files=None):
    """Drive the r17 #1 two-scan takeover shape: scan 1 registers
    _K1_SID under player A; scans 2/3 list the SAME sid only at player
    B's path (the original folder gone). Scan 2 refuses the move-heal
    (collision flag) AND vanish-quarantines the dead-path row; scan 3
    is where the unguarded quarantined-path heal fired pre-K1.
    Returns scan 3's ScanResult."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=_K1_SID, player=_PLAYER_A, md5=md5_a, files=files))
    assert ledger.get(_K1_SID)["state"] == "DISCOVERED"
    assert ledger.get(_K1_SID)["player_email"] == _PLAYER_A
    entries_b = make_session_entries(
        sid=_K1_SID, player=_PLAYER_B, md5=md5_b, files=files,
        ctime="2026-08-14T11:00:00.000Z")
    res2 = ingest.scan(cfg, ledger, entries=entries_b)
    assert any("session-id collision" in f for f in res2.integrity_flags)
    assert ledger.get(_K1_SID)["state"] == "QUARANTINED", \
        "the vanished arm quarantines the dead-path row on the same scan"
    return ingest.scan(cfg, ledger, entries=entries_b)


def test_zip_class_cross_player_heal_stays_refused(cfg, ledger):
    """r17 #1 (K1) refuse side, zip class — and the hostile-mutant kill
    (§2 rule 4): a zip payload has NO video md5 on either side, the
    exact population review-r5 #41 refused to re-attribute without
    byte identity. Pre-K1 the vanished arm converted the refused
    DISCOVERED row into a QUARANTINED row the guard-less heal accepted
    one scan later — player B captured player A's registered sid in
    two scans. The most damaging bypass shape (comparing '' == '' as
    byte identity, i.e. dropping the both-known qualifier) heals this
    exact case and must fail here."""
    res3 = _k1_takeover_scans(cfg, ledger, files=["bundle.zip"])
    row = ledger.get(_K1_SID)
    assert row["state"] == "QUARANTINED", \
        "cross-player claim without byte identity must stay refused"
    assert row["player_email"] == _PLAYER_A, \
        "payment attribution must never flip"
    assert _PLAYER_B not in row["drive_path"]
    assert any("heal REFUSED" in f and "identity mismatch" in f
               for f in res3.integrity_flags), res3.integrity_flags
    assert _K1_SID not in res3.discovered


def test_files_class_different_md5_heal_refused_keeps_stamps(cfg, ledger):
    """r17 #1 (K1) refuse side, files class (§2 rule 3: player differs
    AND md5 differs): arbitrary new bytes claiming a registered sid
    from another player's tree are refused — pre-K1 the heal accepted
    them and its supersede-style clear wiped the old registration's
    payment stamps as 'genuinely new hours' (the stamp-clearing
    capture)."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=_K1_SID, player=_PLAYER_A, md5="a1" * 16))
    ledger.update(_K1_SID, duration_raw_s=3600.0,
                  uploaded_reported_at="2026-08-15T00:00:00+00:00",
                  accepted_reported_at="2026-08-15T00:00:00+00:00")
    entries_b = make_session_entries(
        sid=_K1_SID, player=_PLAYER_B, md5="b2" * 16,
        ctime="2026-08-14T11:00:00.000Z")
    res2 = ingest.scan(cfg, ledger, entries=entries_b)
    assert any("session-id collision" in f for f in res2.integrity_flags)
    assert ledger.get(_K1_SID)["state"] == "QUARANTINED"
    res3 = ingest.scan(cfg, ledger, entries=entries_b)
    row = ledger.get(_K1_SID)
    assert row["state"] == "QUARANTINED"
    assert row["player_email"] == _PLAYER_A
    assert row["md5_video"] == "a1" * 16, "registered bytes untouched"
    assert row["duration_raw_s"] == 3600.0 and \
        row["uploaded_reported_at"] and row["accepted_reported_at"], \
        "the refused heal must not clear the old registration's stamps"
    assert any("heal REFUSED" in f for f in res3.integrity_flags)


def test_byte_identical_cross_player_heal_still_proceeds(cfg, ledger):
    """K1 control (§2 rules 3/4, the proceed side — DELIBERATE,
    matching the move-heal's identity test): a byte-identical copy of
    the registered bytes at another player's path IS the sid's
    footage — the heal re-registers it exactly as the pre-download
    move-heal would have (player differs, md5 matches: the
    discriminators split the other way from the refuse cases)."""
    from pipeline import ingest
    from pipeline.tests.conftest import make_session_entries
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=_K1_SID, player=_PLAYER_A, md5="c3" * 16))
    # the folder vanishes from an otherwise-healthy kamla listing
    ingest.scan(cfg, ledger, entries=make_session_entries(
        sid="2026-08-14T11-00-00Z_kamla_c_00000000000000b6", md5="h6-md5"))
    assert ledger.get(_K1_SID)["state"] == "QUARANTINED"
    res = ingest.scan(cfg, ledger, entries=make_session_entries(
        sid=_K1_SID, player=_PLAYER_B, md5="c3" * 16,
        ctime="2026-08-14T11:00:00.000Z"))
    row = ledger.get(_K1_SID)
    assert row["state"] == "DISCOVERED", \
        "byte identity proves the footage — the heal must proceed"
    assert row["player_email"] == _PLAYER_B
    assert _PLAYER_B in row["drive_path"]
    assert any("quarantined path healed" in f for f in res.integrity_flags)
    assert _K1_SID in res.discovered
