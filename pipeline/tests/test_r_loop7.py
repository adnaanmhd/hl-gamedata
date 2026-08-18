"""r-loop 7 — the confirmed findings outside the payment split.

Two blockers (the regen resume record, in test_payment_split_r6.py; and the
fix lane's missing host carve-out, here), five majors and two minors. The
theme repeats r-loop 4 and r-loop 6: most of the serious ones are
regressions from the PREVIOUS iteration's own fixes.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pipeline import config as C
from pipeline import continuous as cont
from pipeline import fix as fixmod
from pipeline.ledger import Ledger


def _R(code, fixable=True, **params):
    return {"code": code, "blocking": True, "fixable": fixable,
            "params": params, "evidence": "e"}


# ------------------------------------------- BLOCKER: fix-lane host errors

@pytest.mark.parametrize("exc,kind", [
    (OSError(28, "No space left on device"), "host"),
    (MemoryError(), "host"),
    (subprocess.TimeoutExpired("ffmpeg", 1800), "host"),
    (subprocess.CalledProcessError(1, "ffmpeg"), "session"),
    (ValueError("bad csv"), "session"),
])
def test_apply_fixes_classifies_host_vs_session(tmp_path, monkeypatch,
                                                exc, kind):
    """A disk-full or wedged-ffmpeg episode is the MACHINE having a bad
    minute, not the session's bytes being wrong. CalledProcessError stays
    'session': ffmpeg exiting non-zero is usually undecodable footage, and
    calling that a host fault would retry a broken clip forever."""
    def boom(*a, **kw):
        raise exc
    monkeypatch.setattr(fixmod, "_dispatch", boom)
    out = fixmod.apply_fixes(tmp_path, {"steps": [("FIX_SENTINELS", {})]},
                             game="kamla", dossier_dir=tmp_path / "d")
    assert out["error"]
    assert out["kind"] == kind, out


def test_host_error_refunds_the_attempt_and_parks_the_row(cfg, ledger,
                                                          monkeypatch):
    """BLOCKER. Every fix failure burned an attempt, so ONE disk-full
    episode spent both attempts back to back (fix -> revalidate -> fix ->
    REJECTED within minutes) and finalize_rejected wiped the media. The
    stored reasons are all still fixable, so the reject surfaced as the
    bare fix-failed marker: an infrastructure failure reported to the
    player as a fault in their footage."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    sid = "2026-08-14T10-00-00Z_kamla_c_00000000000000h1"
    ledger.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path=f"kamla/Op/p@x.com/{sid}",
        drive_ctime="2026-08-14T10:00:00.000Z", md5_video="m", bytes_=1,
        state="DISCOVERED")
    ledger.set_reasons(sid, [_R("STR_SENTINELS")], 2)
    ledger.set_state(sid, "FIX_QUEUED")
    (cfg.work / sid).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        fixmod, "apply_fixes",
        lambda *a, **kw: {"applied": [], "children": None,
                          "error": "FIX_SENTINELS: OSError: [Errno 28] "
                                   "No space left on device",
                          "kind": "host"})
    assert drv._fix_one(ledger, sid) is False      # slot dropped
    row = ledger.get(sid)
    assert row["state"] == "FIX_QUEUED", row["state"]
    assert row["fix_attempts"] == 0, "the attempt must be refunded"

    # ... and a SESSION-level failure still charges and revalidates
    monkeypatch.setattr(
        fixmod, "apply_fixes",
        lambda *a, **kw: {"applied": [], "children": None,
                          "error": "FIX_SENTINELS: ValueError: bad csv",
                          "kind": "session"})
    assert drv._fix_one(ledger, sid) is True
    row = ledger.get(sid)
    assert row["state"] == "REVALIDATING"
    assert row["fix_attempts"] == 1


# --------------------------------------------- MAJOR: the media-cap rules

def _disc(led, sid):
    led.insert_session(
        session_id=sid, game="kamla", operator_email="Op",
        player_email="p@x.com", drive_path=f"kamla/Op/p@x.com/{sid}",
        drive_ctime=f"2026-08-14T10:00:00.{sid[-3:]}Z", md5_video=sid,
        bytes_=1, state="DISCOVERED")


def test_cap_carve_out_uses_the_same_media_rule_as_the_count(cfg, ledger):
    """An EMPTY work dir is not media — _local_count has said so since
    r-loop 6, but the carve-out tested only .exists(), so it admitted
    exactly the rows the count scores as 0. Each pick then downloaded a
    whole new session BEYOND the cap and the cap stopped bounding bytes
    at all."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    for i in range(C.CONT_MEDIA_CAP_SESSIONS):
        sid = f"2026-08-14T10-00-00Z_kamla_c_{i:016x}"
        _disc(ledger, sid)
        ledger.set_state(sid, "INGESTED")
    empty = "2026-08-14T09-00-00Z_kamla_c_00000000000000e0"
    _disc(ledger, empty)
    (cfg.work / empty).mkdir(parents=True)          # dir, ZERO bytes

    assert drv._local_count(ledger) == C.CONT_MEDIA_CAP_SESSIONS
    assert empty not in drv._held_discovered(ledger)
    assert drv._pick_download(ledger) is None, \
        "an empty dir must not buy a free pass past the cap"

    # a row that really holds bytes IS released
    real = "2026-08-14T08-00-00Z_kamla_c_00000000000000e1"
    _disc(ledger, real)
    (cfg.work / real).mkdir(parents=True)
    (cfg.work / real / "video.mp4").write_bytes(b"x" * 32)
    assert real in drv._held_discovered(ledger)
    assert drv._pick_download(ledger) == real


def test_cap_carve_out_is_not_limited_to_one_batch_window(cfg, ledger):
    """It routed through ingest.next_batch, whose `size=None` silently
    means BATCH_SIZE (10). The moment ten rows sorted ahead, the rows
    actually holding the cap became unreachable and intake stopped
    completely until the 12 h reclaim — the very blocker the carve-out
    exists to close."""
    drv = cont.ContinuousDriver(cfg, send_telegram=False)
    for i in range(C.CONT_MEDIA_CAP_SESSIONS):
        sid = f"2026-08-14T10-00-00Z_kamla_c_{i:016x}"
        _disc(ledger, sid)
        ledger.set_state(sid, "INGESTED")
    # far more than BATCH_SIZE fresh rows sort AHEAD of the held one
    for i in range(C.BATCH_SIZE + 5):
        _disc(ledger, f"2026-08-14T09-00-00Z_kamla_c_{i:016x}")
    held = "2026-08-14T11-00-00Z_kamla_c_00000000000000ff"
    _disc(ledger, held)
    (cfg.work / held).mkdir(parents=True)
    (cfg.work / held / "video.mp4").write_bytes(b"x" * 32)

    assert drv._pick_download(ledger) == held, \
        "the row holding the cap must be reachable past the batch window"


# ---------------------------- MAJOR: session.json is not a retranslate job

def test_session_json_family_never_routes_into_the_fix_that_reads_it():
    """retranslate_from_sidecars READS session.json for the head offset,
    so mapping STR_SJ_INVALID to it made the fix depend on the artifact
    the FAIL says is broken: it raised identically on both attempts while
    the no-sidecar plan cleared the same FAIL in ONE. Having the required
    sidecars made a session strictly WORSE off."""
    for has_raw in (True, False):
        steps = [s for s, _ in fixmod.plan_fixes(
            [_R("STR_SJ_INVALID")], game="kamla", has_raw=has_raw)["steps"]]
        assert steps[0] == "FIX_SESSIONJSON_REWRITE", (has_raw, steps)
        assert "FIX_RETRANSLATE" not in steps, (has_raw, steps)


def test_a_session_needing_both_repairs_its_precondition_first():
    steps = [s for s, _ in fixmod.plan_fixes(
        [_R("STR_SJ_INVALID"), _R("SYN_TS_NOT_PTS")],
        game="kamla", has_raw=True)["steps"]]
    assert steps.index("FIX_SESSIONJSON_REWRITE") < \
        steps.index("FIX_RETRANSLATE"), steps


def test_unrelated_codes_still_retranslate():
    steps = [s for s, _ in fixmod.plan_fixes(
        [_R("STR_SENTINELS")], game="kamla", has_raw=True)["steps"]]
    assert "FIX_RETRANSLATE" in steps


def test_retranslate_fails_attributably_rather_than_crashing(tmp_path):
    """Whatever still reaches it must produce a typed FixFailed, never a
    bare KeyError/TypeError/ValueError escaping apply_fixes."""
    work = tmp_path / "s"
    (work / "raw").mkdir(parents=True)
    (work / "raw" / "inputs.jsonl").write_text("")
    for meta, sj in (
            ({"recording": {"started_at_utc": "2026-08-14T10:00:00Z"}},
             "{not json"),
            ({"recording": {"started_at_utc": "2026-08-14T10:00:00Z"}},
             json.dumps(["not", "an", "object"])),
            ({"recording": {}}, json.dumps({"created_at_utc":
                                            "2026-08-14T10:00:05Z"})),
            ({"recording": {"started_at_utc": "2026-08-14T10:00:00Z"}},
             json.dumps({"created_at_utc": "10/08/2026 15:34:03"})),
    ):
        (work / "raw" / "metadata.json").write_text(json.dumps(meta))
        (work / "session.json").write_text(sj)
        with pytest.raises(fixmod.FixFailed) as e:
            fixmod.retranslate_from_sidecars(work)
        assert "head offset" in str(e.value), str(e.value)


def test_has_raw_means_the_same_thing_everywhere(tmp_path):
    """validate required BOTH sidecars; the drivers required only
    inputs.jsonl, and retranslate opens metadata.json unconditionally —
    so a zip upload missing metadata.json was planned a retranslate that
    could never run, on both attempts."""
    work = tmp_path / "s"
    (work / "raw").mkdir(parents=True)
    (work / "raw" / "inputs.jsonl").write_text("")
    assert fixmod.has_raw_sidecars(work) is False
    (work / "raw" / "metadata.json").write_text("{}")
    assert fixmod.has_raw_sidecars(work) is True


# ----------------------------- MAJOR: the gate record is per-SEGMENT

def test_gate_record_only_reaches_the_segment_that_holds_the_window(
        tmp_path):
    """cutter gives each child its own row slice, so blanked rows land in
    exactly ONE segment. Handing the record to a sibling let
    validate._gate_destroyed downgrade that sibling's GENUINE
    CNT_ACTIONS_FEW / INP_KEYS_MISSING to an advisory, shipping a segment
    that violates two locked delivery bars under advisories that were
    false statements about it."""
    parent = tmp_path / "dossiers" / "P"
    parent.mkdir(parents=True)
    applied = [{"fix": "FIX_GATE_WINDOW", "ok": True,
                "params": {"windows": [[40.0, 42.0]]},
                "note": {"actions": ["interact"], "key_frames": 65}}]
    segments = [{"id": "P-p1", "t0": 0.0, "t1": 75.0},
                {"id": "P-p2", "t0": 80.0, "t1": 160.0}]
    fixmod._propagate_gate_record(parent, tmp_path / "dossiers",
                                  applied, segments)
    assert (tmp_path / "dossiers" / "P-p1" / "fixlog.json").exists()
    assert not (tmp_path / "dossiers" / "P-p2" / "fixlog.json").exists(), \
        "a segment whose rows were never blanked must inherit nothing"


def test_gate_record_propagates_when_bounds_are_unknown(tmp_path):
    """Never DROP a record silently: unknown/unreadable bounds keep the
    old behaviour and propagate."""
    parent = tmp_path / "dossiers" / "Q"
    parent.mkdir(parents=True)
    applied = [{"fix": "FIX_GATE_WINDOW", "ok": True, "params": {},
                "note": {"actions": [], "key_frames": 1}}]
    fixmod._propagate_gate_record(
        parent, tmp_path / "dossiers", applied,
        [{"id": "Q-p1", "t0": None, "t1": None}])
    assert (tmp_path / "dossiers" / "Q-p1" / "fixlog.json").exists()


# ------------------------- MAJOR: unusable keybind.json must not strip keys

@pytest.mark.parametrize("payload", [
    {},                                       # empty object
    {"move_up": 87, "interact": 69},          # Windows VK codes
    {"version": 1, "binds": {"move_up": "w"}},   # wrapper object
    {"move_up": None, "interact": None},      # nulls
    # {modifier, key} form carrying VK numbers — r-loop 7 covered only the
    # flat form; normalize_literal crashed on the numbers before the
    # usable-binding check could fall back (r-loop 8)
    {"move_up": {"modifier": None, "key": 87}},
    {"move_up": [{"modifier": "ctrl", "key": 87}]},
])
def test_unusable_keybind_falls_back_to_the_builtin(tmp_path, payload,
                                                    capsys):
    """resolve_keybind guarded the SHAPE but never checked that a usable
    binding survived. An empty resolver strips 100% of key presses, so
    the delivered rows carry an empty keyboard column and the session is
    REJECTED for INP_KEYS_MISSING + CNT_ACTIONS_FEW — both unfixable —
    with coaching telling the player to play more actively, for keys our
    own parser deleted."""
    from translator.keybind import bound_literals
    from translator.translate import resolve_keybind
    kb_path = tmp_path / "keybind.json"
    kb_path.write_text(json.dumps(payload))
    kb = resolve_keybind(keybind_path=kb_path, game_name="Kamla",
                         exe_name=None)
    assert bound_literals(kb), "every key press would have been stripped"
    assert "w" in bound_literals(kb)
    assert "bound no keys" in capsys.readouterr().err


def test_a_usable_keybind_is_still_respected(tmp_path):
    from translator.keybind import bound_literals
    from translator.translate import resolve_keybind
    kb_path = tmp_path / "keybind.json"
    kb_path.write_text(json.dumps({"move_up": "w", "interact": "e"}))
    kb = resolve_keybind(keybind_path=kb_path, game_name="Kamla",
                         exe_name=None)
    assert sorted(bound_literals(kb)) == ["e", "w"]


# ------------------- MAJOR: untrusted sidecar values degrade, never raise

@pytest.mark.parametrize("bad", ["1.0", "abc", [1], {"a": 1}, None, True,
                                 # json.loads accepts Infinity/1e999 and
                                 # arbitrary-precision ints; int() on them
                                 # raised OverflowError past both except
                                 # arms (r-loop 8)
                                 float("inf"), float("-inf"), 10**400])
def test_raw_numeric_fields_degrade_to_zero(bad):
    """A bare int() raised out of check_session_v2 into the driver, which
    writes QUARANTINED 'validation crashed' — TERMINAL, media held 48 h —
    for a session that would otherwise have PASSed, and out of
    bin_session so every retranslate failed too."""
    from translator.binner import raw_int
    assert isinstance(raw_int(bad), int)


@pytest.mark.parametrize("bad", [65, ["w"], {"k": "w"}, None])
def test_non_string_key_and_button_are_skipped(bad):
    from translator import keys as K
    assert K.normalize_event_key(bad) is None


def test_bin_session_survives_a_malformed_sidecar():
    """End to end through the binner: one malformed event must not take
    the whole session down."""
    from dataclasses import dataclass
    from translator.binner import bin_session
    from translator.keybind import bound_literals, build_resolver

    @dataclass
    class _V:
        fps: float = 30.0
        frame_count: int = 4
        width: int = 640
        height: int = 480
        duration_s: float = 0.133
        has_audio: bool = False
        codec: str = "h264"

        @property
        def frame_us(self):
            return 1_000_000.0 / self.fps

    kb = {"move_up": ["w"]}
    events = [
        {"t": 0, "type": "key", "key": "w", "action": "down"},
        {"t": 1000, "type": "key", "key": 65, "action": "down"},
        {"t": 2000, "type": "mouse_button", "button": 3, "action": "down"},
        {"t": 3000, "type": "mouse_raw", "dx": "1.0", "dy": "abc"},
        {"t": 4000, "type": "mouse_raw", "dx": [5], "dy": None},
    ]
    rows, stats = bin_session(events, _V(), kb, build_resolver(kb),
                              bound_literals(kb),
                              frame_pts_us=[0, 33333, 66666, 100000])
    assert len(rows) == 4
