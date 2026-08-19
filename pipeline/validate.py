"""Phase II — validation wrapper around tools/analyze_sample.py (plan §10).

The engine's design is locked (standalone, v2-only, sibling -analysis/
output, recommend-only, Gemini pinned): we WRAP it — run it, consume the
STRUCTURED fields of its report, layer the scanner/AFK/notification checks
automation needs, and map everything onto the §10 reason-code registry.
The only prose ever matched are the two qa-v2 exact phrases
("frame-sync drift" FAIL vs "cannot verify frame sync" WARN — plan §10.5)
plus the lag summary line, whose format our own vendored translator/sync.py
generates (stable, self-owned).

Output per session: verdict.json in the dossier —
{bin, hold_vlm, engine_verdict, reasons: [{code, blocking, fixable, params,
evidence}], advisories, metrics}.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import threading
import uuid
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config as C
from . import scanner
from . import vlm as vlmmod

_ENGINE = None


def load_engine():
    """Import tools/analyze_sample.py once per process (wrap, don't fork)."""
    global _ENGINE
    if _ENGINE is None:
        path = C.REPO_ROOT / "tools" / "analyze_sample.py"
        spec = importlib.util.spec_from_file_location("hl_analyze_sample",
                                                      path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["hl_analyze_sample"] = mod
        spec.loader.exec_module(mod)
        _ENGINE = mod
    return _ENGINE


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _reason(code: str, blocking: bool, fixable: bool, params: dict,
            evidence: str) -> dict:
    return {"code": code, "blocking": blocking, "fixable": fixable,
            "params": params, "evidence": evidence[:400]}


@dataclass
class MapResult:
    bin: int | None                # 1/2/3; None when held
    hold_vlm: bool
    engine_verdict: str
    reasons: list[dict] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


# --------------------------------------------------------------- QA mapping

_QA_STR_MAP = [
    ("header != v2 schema", "STR_HEADER_BAD", True),
    ("camera columns non-null", "STR_CAMERA_NONNULL", True),
    ("not float-formatted", "STR_SENTINELS", True),
    ("row count", "STR_ROWS_MISMATCH", True),
    ("video frame count", "STR_ROWS_MISMATCH", True),
    ("not strictly increasing", "STR_TS_NONMONO", True),
    ("timestamp_ms[0]", "STR_TS_TAIL", True),
    ("timestamp_ms[-1]", "STR_TS_TAIL", True),
    ("non-v2 key tokens", "INP_TOKEN_CASE", True),
    ("non-v2 mouse button", "INP_TOKEN_CASE", True),
    ("input_keys but null input_actions", "INP_KEYS_NO_ACTION", True),
    ("fan-out", "INP_FANOUT", True),
    ("raw recomputation", "SYN_TS_NOT_PTS", True),
    ("key_binding.json present", "ARR_V1_FORMAT", True),
    ("missing required fields", "STR_SJ_INVALID", True),
    ("not timezone-aware", "STR_SJ_INVALID", True),
    ("ended_at_utc <=", "STR_SJ_INVALID", True),
    ("duration_ms", "STR_SJ_INVALID", True),
    ("frame_count differs", "STR_SJ_INVALID", True),
    ("localization", "STR_SJ_INVALID", True),
    ("platform not in", "STR_SJ_INVALID", True),
    ("input_mouse_convention", "STR_SJ_INVALID", True),
    ("maps_to", "STR_SJ_INVALID", True),
    ("camera mapping", "STR_SJ_INVALID", True),
    ("non-camera mapping", "STR_SJ_INVALID", True),
    ("video duration", "STR_SJ_INVALID", True),
    ("!= record_*_px", "STR_SJ_INVALID", True),
    # r-loop 3: the r-loop-1/2 "FAIL, never crash" hardening introduced FAIL
    # strings that matched NO needle here, so they fell through to
    # QA_FAIL_UNMAPPED — which is blocking+UNFIXABLE whenever has_raw is
    # False (split children always, and any zip carrying only out/). Those
    # sessions were REJECTED on the spot without a single fix attempt, even
    # though FIX_SESSIONJSON_REWRITE recomputes precisely the fields the
    # new FAILs describe. Deliberately NOT mapped, because no fix can clear
    # them and mapping would burn two attempts and two paid VLM sweeps
    # before rejecting anyway: ragged/short rows (lost columns) and
    # "frames.csv unreadable/empty" — for those, QA_FAIL_UNMAPPED's
    # retranslate-when-sidecars-exist behaviour is already the right
    # answer (the retranslate rewrites the CSV without reading it).
    # "session.json unreadable" / "is not a JSON object" WERE in that
    # list, but the rationale predates r-loop 7's _read_session_json {}
    # rebuild and was falsified by it (r19 #13): FIX_SESSIONJSON_REWRITE
    # recomputes a valid session.json from video+CSV ground truth
    # starting from the {} read, while the unmapped route planned a
    # retranslate that READS session.json for its head offset and
    # deterministically FixFails both attempts — or bin-3 rejected the
    # sidecar-less twin on the spot. Both now map to STR_SJ_INVALID
    # below: the rewrite precedes any retranslate in every plan, and
    # STR_SJ_INVALID never routes through one (the r-loop-7 rule).
    # "frame_id column unparseable" is deliberately NOT mapped: with no raw
    # sidecars STR_ROWS_MISMATCH plans FIX_ROWS_SURGERY, which only
    # truncates/appends up to 2 TAIL rows and never rewrites a frame_id
    # cell — so it no-ops, and both attempts plus two paid VLM sweeps are
    # spent reaching the same reject, with the operator-facing reason
    # degraded to the bare fix-failed marker (r-loop 4). Same trap the
    # ragged-row note above avoids.
    # "frame_id not zero-based sequential" WAS mapped to STR_ROWS_MISMATCH
    # and is now unmapped for exactly that reason (r-loop 6): the ids are
    # parseable but wrong, so with no sidecars surgery is planned, and at
    # the usual delta of 0 it truncates nothing, appends nothing, rewrites
    # the identical rows and REPORTS SUCCESS — two attempts and three paid
    # sweeps to reach the same reject under the bare fix-failed marker.
    # Nothing is lost by unmapping: QA_FAIL_UNMAPPED still plans the
    # retranslate whenever sidecars exist (which is the only repair that
    # re-zeroes ids), still downgrades to advisory when a genuinely
    # repairable FAIL rides along (r-loop 5), and otherwise rejects at once
    # with a truthful reason instead of a burned budget.
    ("session.json numeric fields malformed", "STR_SJ_INVALID", True),
    ("session.json timestamps", "STR_SJ_INVALID", True),
    ("session.json unreadable", "STR_SJ_INVALID", True),
    ("session.json is not a JSON object", "STR_SJ_INVALID", True),
    ("game_title not a string", "STR_SJ_INVALID", True),
    ("timestamp_ms column unparseable", "STR_TS_NONMONO", True),
]

# qa FAILs handled elsewhere (never through the generic table)
_QA_SKIP = ("frame-sync drift", "controls-to-video sync",
            "mouse motion missing", "under 70s")


def _map_qa_issues(issues: list[str], reasons: list[dict],
                   has_raw: bool) -> None:
    seen: set[str] = set()
    start = len(reasons)
    unmapped: list[int] = []
    for issue in issues:
        if not issue.startswith("FAIL:"):
            continue
        msg = issue[5:].strip()
        if any(k in msg for k in _QA_SKIP):
            continue
        for needle, code, fixable in _QA_STR_MAP:
            if needle in msg:
                if code not in seen:
                    seen.add(code)
                    reasons.append(_reason(code, True, fixable, {}, msg))
                break
        else:
            # never silently downgrade an unmapped FAIL; retranslate is the
            # universal strong fix when sidecars exist (R3)
            unmapped.append(len(reasons))
            reasons.append(_reason("QA_FAIL_UNMAPPED", True, has_raw, {},
                                   msg))
    # An unmapped FAIL must never UPGRADE a repairable verdict into a
    # wrongful reject (r-loop 5). QA_FAIL_UNMAPPED is blocking+unfixable
    # whenever has_raw is False, and one unfixable blocking reason forces
    # bin 3 -- so a session that also raised a blocking+FIXABLE reason in
    # this same qa pass was rejected outright with ZERO fix attempts,
    # although the planned fix would have cleared BOTH FAILs. The
    # canonical pair is "row count N != frame_count M" (-> STR_ROWS_MISMATCH
    # -> FIX_ROWS_SURGERY, which truncates the offending tail rows) landing
    # together with "frame_id column unparseable" on a no-sidecar upload.
    #
    # Downgrade to advisory ONLY in that company. Alone, an unmapped FAIL
    # keeps its blocking verdict, so the r-loop-4 reasoning still holds:
    # mapping frame_id-unparseable to STR_ROWS_MISMATCH outright would
    # plan a surgery that no-ops and burn both attempts plus two paid VLM
    # sweeps before rejecting anyway. This is the narrow middle: run the
    # repair that was already planned, and let the re-validation decide.
    if unmapped:
        repairable = any(r["blocking"] and r["fixable"]
                         for r in reasons[start:]
                         if r["code"] != "QA_FAIL_UNMAPPED")
        if repairable:
            for i in unmapped:
                reasons[i]["blocking"] = False


_LAG_RE = re.compile(r"video (\d+(?:\.\d+)?)ms")
_CORR_RE = re.compile(r"\|corr\|=(\d+(?:\.\d+)?)")


def _map_sync(rep: dict, aux: dict, reasons: list[dict],
              advisories: list[str]) -> None:
    lag = rep.get("lag", {})
    fs = lag.get("frame_sync") or ""
    # the engine carries the qa-v2 severity prefix ("FAIL: ..."/"WARN: ...")
    # — strip it before the two exact-phrase matches (plan §10.5); a missed
    # match here would let real drift ship (review finding #1)
    fs_body = fs.split(": ", 1)[1] if fs.split(": ", 1)[0] in (
        "FAIL", "WARN", "OK") else fs
    if fs_body.startswith("frame-sync drift"):
        reasons.append(_reason("SYN_TS_NOT_PTS", True, True, {}, fs))
    elif fs_body.startswith("cannot verify frame sync"):
        advisories.append(f"frame sync unverifiable: {fs}")

    summary = lag.get("summary") or ""
    qa_fail = next((i for i in rep.get("qa_issues", [])
                    if i.startswith("FAIL:") and "controls-to-video sync"
                    in i), None)
    if qa_fail:
        m = _LAG_RE.search(qa_fail)
        lag_ms = float(m.group(1)) if m else None
        reasons.append(_reason("SYN_LAG_CONST", True, True,
                               {"lag_ms": lag_ms}, qa_fail[5:].strip()))
        return
    if "within" in summary:
        m = _LAG_RE.search(summary)
        if m and float(m.group(1)) > C.LAG_TARGET_MS:
            reasons.append(_reason(
                "SYN_LAG_CONST", True, True, {"lag_ms": float(m.group(1))},
                f"lag over the {C.LAG_TARGET_MS:.0f}ms client target: "
                f"{summary}"))
        return
    if "correlation too weak" in summary:
        m = _CORR_RE.search(summary)
        corr = float(m.group(1)) if m else 1.0
        inv = rep.get("inventory", {})
        rows = max(inv.get("rows") or 1, 1)
        motion_frac = (inv.get("motion_frames") or 0) / rows
        # suspect only on a strong signal (plenty of recorded motion, video
        # visibly moving, correlation ~zero) — a wrong reject is a wrongly
        # unpaid player, so the gate is deliberately tighter than the
        # measurability floor
        if corr < 0.05 and motion_frac >= 0.10 and aux.get("video_active"):
            reasons.append(_reason(
                "SYN_UNMEASURABLE_SUSPECT", True, False,
                {"corr": corr, "motion_frac": round(motion_frac, 3)},
                f"no correlation despite visible action: {summary}"))
        else:
            advisories.append(f"controls-to-video sync unverifiable "
                              f"(benign): {summary}")
    elif "inactive" in summary:
        advisories.append(f"controls-to-video sync skipped: {summary}")


def _map_game_identity(rep: dict, expected_game: str | None,
                       reasons: list[dict], advisories: list[str]) -> None:
    claimed = _norm(rep.get("game_title", ""))
    votes = rep.get("vlm", {}).get("game_votes", {}) or {}
    n_frames = len(rep.get("vlm", {}).get("samples", [])) or 1
    merged: dict[str, int] = {}
    for g, n in votes.items():
        k = _norm(g)
        merged[k] = merged.get(k, 0) + n
    top, v, total = "", 0, sum(merged.values())
    if merged:
        top = max(merged, key=lambda k: merged[k])
        v = merged[top]
    tc, cc = top.replace("_", ""), claimed.replace("_", "")
    mismatch = bool(tc and cc) and tc not in cc and cc not in tc
    tripwire = (mismatch and v >= C.TRIPWIRE_MIN_VOTES
                and v / max(total, 1) >= C.TRIPWIRE_MIN_VOTE_FRAC
                and v / n_frames >= C.TRIPWIRE_MIN_FRAME_FRAC)

    if tripwire and not C.VLM_GAME_TRIPWIRE_GATES:
        # Adnaan 08-14 (post-plan): VLM game identity is report-only in
        # Phase 1 — log the unanimous mismatch loudly, gate nothing, and
        # FALL THROUGH to the label checks below: the R1 scope reject and
        # the misfile check are metadata rules, not VLM gates (review-2 #15)
        advisories.append(
            f"VLM GAME MISMATCH (report-only per Adnaan 08-14 ruling): "
            f"video looks like '{top}' ({v}/{total} votes, "
            f"unanimous-level) but session claims '{claimed}'")
    elif tripwire:
        if top in C.GAMES:
            reasons.append(_reason(
                "STR_GAME_MISMATCH", True, True, {"actual": top},
                f"video is '{top}' ({v}/{total} votes, unanimous-level) but "
                f"session claims '{claimed}' — reroute + re-translate"))
        else:
            reasons.append(_reason(
                "CNT_WRONG_GAME", True, False, {"actual": top},
                f"video is '{top}' ({v}/{total} votes, unanimous-level) — "
                f"out of Phase-1 scope (R1)"))
        return
    if claimed and claimed not in C.GAMES:
        reasons.append(_reason(
            "CNT_WRONG_GAME", True, False, {"claimed": claimed},
            f"session claims '{claimed}' — out of Phase-1 scope (R1)"))
        return
    if expected_game and claimed and expected_game != claimed:
        # reroute target is the VETTED in-scope claim — never a
        # below-tripwire VLM guess (review-2 #6)
        reasons.append(_reason(
            "STR_GAME_MISMATCH", True, True,
            {"actual": claimed},
            f"Drive folder says '{expected_game}' but session claims "
            f"'{claimed}' — misfiled; reroute"))
        return
    if mismatch:
        advisories.append(
            f"VLM game guess '{top}' ({v}/{total} votes) differs from "
            f"claimed '{claimed}' but is below the unanimity tripwire — "
            f"verify manually")


def _map_windows(rep: dict, aux: dict, reasons: list[dict],
                 advisories: list[str]) -> None:
    dur = float(rep.get("duration_s") or 0)
    windows = list(rep.get("vlm", {}).get("windows", []))
    for w in windows:
        if not w.get("gating"):
            continue
        desc = (f"non-gameplay [{'+'.join(w.get('labels', []))}] "
                f"{w['t0']}-{w['t1']}s")
        if w.get("tier") != "high":
            advisories.append(f"{desc} — low confidence; see filmstrip")
            continue
        at_head = w["t0"] <= 1.0
        at_tail = w["t1"] >= dur - 1.0
        action_frames = (w.get("inputs") or {}).get("action_frames", 0)
        if at_head and at_tail:
            reasons.append(_reason(
                "CNT_MID_NONGAMEPLAY", True, False, {},
                f"{desc} spans the ENTIRE clip — nothing to keep"))
            continue
        if at_head:
            cut_at = w["t1"] + 0.5
            remain = dur - cut_at
            if remain < C.MIN_CLIP_S:
                reasons.append(_reason(
                    "CNT_SHORT", True, False, {"post_cut_s": round(remain, 1)},
                    f"{desc} at clip start; trimming leaves {remain:.0f}s "
                    f"(<{C.MIN_CLIP_S:.0f}s)"))
            else:
                reasons.append(_reason(
                    "CNT_EDGE_NONGAMEPLAY", True, True,
                    {"edge": "head", "cut_at_s": round(cut_at, 2)}, desc))
            continue
        if at_tail:
            keep_end = w["t0"] - 0.5
            if keep_end < C.MIN_CLIP_S:
                reasons.append(_reason(
                    "CNT_SHORT", True, False,
                    {"post_cut_s": round(keep_end, 1)},
                    f"{desc} at clip end; cutting leaves {keep_end:.0f}s "
                    f"(<{C.MIN_CLIP_S:.0f}s)"))
            else:
                reasons.append(_reason(
                    "CNT_EDGE_NONGAMEPLAY", True, True,
                    {"edge": "tail", "cut_at_s": round(keep_end, 2)}, desc))
            continue
        # mid-clip: the §5 keep-vs-cut rule needs frozen confirmation +
        # 1-frame-accurate bounds
        ratio = w.get("stillness_ratio")
        refined = (aux.get("refined") or {}).get((w["t0"], w["t1"]))
        if ratio is not None and ratio >= C.STILLNESS_FROZEN_BELOW:
            advisories.append(
                f"{desc} mid-clip but frames keep moving ({ratio:.0%} of "
                f"gameplay baseline) — likely overlay over live play; "
                f"inputs legitimate")
            continue
        if ratio is None and refined is None:
            advisories.append(
                f"{desc} mid-clip with unmeasured stillness — "
                f"confirm on filmstrip before gating")
            continue
        w0, w1 = refined if refined else (w["t0"], w["t1"])
        span = w1 - w0
        # RULED (Adnaan 2026-08-18): when the scanner measured the frozen
        # run, the TRIGGER moves onto it too -- count actions over exactly
        # the span we are going to blank. That preserves review-r3 #3's
        # invariant (gate everything the trigger measured) by moving the
        # trigger rather than widening the gate; widening GATE_PAD_FRAMES
        # to cover the drift was REJECTED because it blanks up to ~2s of
        # genuine input per gate to protect a counter. With no refined
        # span the VLM window IS the measurement, so both stay as they
        # were.
        raf = (aux.get("refined_action_frames") or {}).get(
            (w["t0"], w["t1"]))
        if refined is not None and raf is not None:
            action_frames = raf
        # the frozen SPAN (refined) drives the keep-vs-cut decision, but a
        # cut must remove the whole flagged window (VLM bounds included) —
        # fade-in/out residue left at a segment head re-triggers detection
        cut0, cut1 = min(w0, w["t0"]), max(w1, w["t1"])
        # R2 (Adnaan 08-17): ABSOLUTE bar only — the old
        # `and span <= KEEP_GATE_MAX_FRAC * dur` conjunct is gone. It made
        # the identical blip keepable in a long parent and cuttable in the
        # short child cut out of that parent, which is precisely what made
        # splitting self-perpetuating. Verdicts no longer depend on dur.
        #
        # BOTH quantities must clear the bar (r-loop 3). `span` is the
        # REFINED frozen run; `[cut0, cut1]` is the union with the VLM
        # window and is what actually SHIPS (on a keep) and what gets
        # BLANKED (on a gate). Testing only the refined run meant a
        # non-gameplay stretch of ANY length kept, so long as its longest
        # contiguous still run was under the bar: a 30s cutscene built from
        # 3-4s held shots separated by hard cuts scored span=4.9 and was
        # KEPT, shipping 30s of cutscene mid-clip (with action_frames=0 it
        # produced no reason at all and went straight to READY). At the old
        # 2.0s bar the same input cut, so raising the bar is what exposed
        # it. Keeping the decision on a quantity that is not what ships
        # would also blank up to 30s of real input on the gate path.
        keep_span = max(span, cut1 - cut0)
        if keep_span <= C.KEEP_GATE_MAX_S:
            if action_frames:
                # gate the FULL flagged window [cut0, cut1], not just the
                # refined span: action_frames was counted over the whole
                # VLM window, and gating a narrower span left counted
                # actions un-blanked — the reason re-fired until the fix
                # budget wrongly rejected the session (review-r3 #3).
                # Same doctrine as the cut path: cover everything the
                # trigger measured.
                # Gate the span the trigger counted: the measured
                # frozen run when we have one (+ GATE_PAD_FRAMES, applied
                # by gate.gate_windows, correctly sized for the +-1-frame
                # scanner jitter it was built for), else the full flagged
                # window as before.
                g0, g1 = ((w0, w1) if (refined is not None
                                       and raf is not None)
                          else (cut0, cut1))
                reasons.append(_reason(
                    "INP_FROZEN_ACTIONS", True, True,
                    {"t0": g0, "t1": g1},
                    f"{desc}: {action_frames} action frames inside a kept "
                    f"<= {C.KEEP_GATE_MAX_S:.0f}s frozen window "
                    f"(gating measured span {g0}-{g1}s)"))
            else:
                advisories.append(
                    f"{desc}: {span:.1f}s frozen blip kept (no inputs "
                    f"inside; under the keep+gate bar)")
        else:
            why = (f"frozen {span:.1f}s" if span >= cut1 - cut0 else
                   f"frozen {span:.1f}s inside a "
                   f"{cut1 - cut0:.1f}s non-gameplay window")
            reasons.append(_reason(
                "CNT_MID_NONGAMEPLAY", True, True,
                {"cut": [cut0, cut1]},
                f"{desc}: {why} "
                f"({(cut1 - cut0) / dur:.2%} of clip) — over the "
                f"{C.KEEP_GATE_MAX_S:.0f}s keep+gate bar; split"))

    for xw in aux.get("extra_windows", []):
        span = xw["t1"] - xw["t0"]
        desc = (f"scanner-found static [{xw['label']}] "
                f"{xw['t0']}-{xw['t1']}s")
        # R1 (Adnaan 08-17): a SCANNER-found window may propose a cut only
        # when it is longer than KEEP_GATE_MAX_S — the same 5s bar R3 sets
        # for the VLM path, one threshold and not two. Short windows are
        # GATING-ONLY: they may still raise INP_FROZEN_ACTIONS if inputs
        # happened inside, but they never create a child row. That is what
        # defuses the 40-candidate cap in validate_session(): a capped
        # parent under-scans, its child (fewer candidates) falls under the
        # cap and finds junk the parent never examined, and re-splits. With
        # cuts restricted to >5s that feedback loop cannot run — only ~95
        # windows ledger-wide (~0.2 per session) clear the bar at all.
        if span <= C.KEEP_GATE_MAX_S:
            if xw.get("action_frames"):
                reasons.append(_reason(
                    "INP_FROZEN_ACTIONS", True, True,
                    {"t0": xw["t0"], "t1": xw["t1"]},
                    f"{desc}: {xw['action_frames']} action frames inside"))
            else:
                advisories.append(f"{desc}: kept (no inputs inside)")
        else:
            reasons.append(_reason(
                "CNT_MID_NONGAMEPLAY", True, True,
                {"cut": [xw["t0"], xw["t1"]]}, desc))

    for t0, t1 in aux.get("afk_windows", []):
        reasons.append(_reason(
            "CNT_AFK", True, True, {"cut": [t0, t1]},
            f"AFK: {t1 - t0:.0f}s zero input over a near-static screen "
            f"at {t0:.0f}-{t1:.0f}s"))


def _map_flags(rep: dict, aux: dict, reasons: list[dict],
               advisories: list[str]) -> None:
    dur = float(rep.get("duration_s") or 0)
    # the hard length gate judges the PROBED duration (D2 doctrine);
    # the claimed one is only the fallback
    dur_true = float(aux.get("probed_duration_s") or 0) or dur

    def _edge_short(t: float, edge: str) -> float | None:
        """Post-cut remainder when the planned edge cut (plan_fixes cuts
        at t±1.0) would leave under MIN_CLIP_S, else None (r-loop 12
        #7). The CNT_EDGE_NONGAMEPLAY arm has had this check since day
        one; without it here, a 70-74s clip burned a fix attempt and a
        paid sweep reaching an INEVITABLE CNT_SHORT — decided fully at
        map time — under a reason that misdirects the re-record
        coaching ('under 70s' instead of 'the edge cut leaves too
        little')."""
        remain = (dur_true - (t + 1.0)) if edge == "head" else (t - 1.0)
        return round(remain, 1) if remain < C.MIN_CLIP_S else None

    for n in aux.get("notifs", []):
        if not n.get("confirmed"):
            advisories.append(
                f"possible notification at {n['t']}s (unconfirmed at full "
                f"crop: {n.get('what', '')})")
            continue
        # edge-ness is judged on the PROBED duration too (r13 #5/G3):
        # notif/chat timestamps come from the sweep, which is clamped to
        # min(claim, probed) — comparing them to a corrupt claim (the
        # r12 #9 ms-in-seconds class) turned a fixable tail-edge flag
        # into an unfixable terminal reject. dur_true already encodes
        # the claimed-only fallback.
        if n["t"] <= 3.0 or n["t"] >= dur_true - 3.0:
            edge = "head" if n["t"] <= 3.0 else "tail"
            short = _edge_short(n["t"], edge)
            if short is not None:
                reasons.append(_reason(
                    "CNT_SHORT", True, False, {"post_cut_s": short},
                    f"edge notification at {n['t']}s; trimming leaves "
                    f"{short:.0f}s (<{C.MIN_CLIP_S:.0f}s)"))
            else:
                reasons.append(_reason(
                    "CNT_NOTIF_EDGE", True, True,
                    {"t": n["t"], "edge": edge},
                    f"notification at {n['t']}s (edge): "
                    f"{n.get('what', '')}"))
        else:
            reasons.append(_reason(
                "CNT_NOTIF_MID", True, False, {"t": n["t"]},
                f"mid-clip notification at {n['t']}s: {n.get('what', '')} "
                f"— reject + DND coaching (R-round-3)"))
    for c in aux.get("chats", []):
        if not c.get("confirmed"):
            advisories.append(
                f"possible chat text at {c['t']}s (unconfirmed: "
                f"{c.get('what', '')})")
            continue
        edge = c["t"] <= 3.0 or c["t"] >= dur_true - 3.0
        short = _edge_short(c["t"], "head" if c["t"] <= 3.0 else "tail") \
            if edge else None
        if short is not None:
            reasons.append(_reason(
                "CNT_SHORT", True, False, {"post_cut_s": short},
                f"edge chat text at {c['t']}s; cutting leaves "
                f"{short:.0f}s (<{C.MIN_CLIP_S:.0f}s)"))
        else:
            reasons.append(_reason(
                "CNT_CHAT_PII", True, edge, {"t": c["t"]},
                f"player-chat text burned in at {c['t']}s: "
                f"{c.get('what', '')}"))


def _joint_edge_short(reasons: list[dict]) -> None:
    """Joint head+tail composition of the r12 #7 map-time CNT_SHORT
    (r-loop 14 #7): the individual edge arms judge each edge ALONE, so a
    70-78s clip with one confirmed head flag and one confirmed tail flag
    that each pass individually still planned BOTH cuts — the cutter
    then dropped every segment (< MIN_CLIP_S) and the session terminally
    rejected under 'split produced no >=70s segment': a burned attempt,
    a pointless ffmpeg cut, and a misdirecting reason. The joint outcome
    is fully decided at map time, so it is judged here, composing
    EXACTLY the cut points plan_fixes will derive (its
    CNT_EDGE_NONGAMEPLAY / CNT_NOTIF_EDGE / CNT_CHAT_PII accumulation,
    including the t±1.0 margins) — entry 26's _map_windows geometry is
    untouched. Skips when an individual arm already emitted CNT_SHORT:
    the session is already terminally short-bound and the reason list
    stays duplicate-free (reject-reason reporting is exhaustive with no
    xN counts)."""
    if any(r["code"] == "CNT_SHORT" for r in reasons):
        return
    head_cut: float | None = None
    tail_cut: float | None = None
    for r in reasons:
        if not r.get("blocking") or not r.get("fixable"):
            continue
        p = r.get("params") or {}
        if r["code"] == "CNT_EDGE_NONGAMEPLAY":
            if p.get("edge") == "head":
                head_cut = max(head_cut or 0.0, p["cut_at_s"])
            else:
                tail_cut = min(tail_cut or 1e9, p["cut_at_s"])
        elif r["code"] in ("CNT_NOTIF_EDGE", "CNT_CHAT_PII"):
            t = p.get("t", 0.0)
            if p.get("edge") == "head" or t <= 3.0:
                head_cut = max(head_cut or 0.0, t + 1.0)
            else:
                tail_cut = min(tail_cut or 1e9, t - 1.0)
    if head_cut is None or tail_cut is None:
        return
    joint = round(tail_cut - head_cut, 1)
    if joint < C.MIN_CLIP_S:
        reasons.append(_reason(
            "CNT_SHORT", True, False, {"post_cut_s": joint},
            f"confirmed head+tail edge flags; the joint cut leaves "
            f"{joint:.0f}s (<{C.MIN_CLIP_S:.0f}s)"))


def map_gate_failures(fails: list[str], *, has_raw: bool) -> list[dict]:
    """Final-gate (§12.3) qa-v2 FAIL strings -> reason codes, so a
    failed-gate session re-enters Phase III with a real fix plan instead
    of empty reasons (review finding #2)."""
    reasons: list[dict] = []
    _map_qa_issues(fails, reasons, has_raw)
    # the skip-list entries are legitimate codes when the FINAL gate is the
    # thing that failed — map the two sync families explicitly
    for f in fails:
        if "frame-sync drift" in f:
            reasons.append(_reason("SYN_TS_NOT_PTS", True, True, {}, f))
        elif "controls-to-video sync" in f:
            m = _LAG_RE.search(f)
            reasons.append(_reason(
                "SYN_LAG_CONST", True, True,
                {"lag_ms": float(m.group(1)) if m else None}, f))
        elif "mouse motion missing" in f:
            reasons.append(_reason("INP_MOTION_MISSING", True, False, {}, f))
    return reasons


# video-independent codes: a HOLD_VLM must not delay these rejects
_VIDEO_INDEPENDENT = {"CNT_SHORT", "INP_MOTION_MISSING", "CNT_DROPS",
                      "INT_DUP_CROSS", "INT_TAMPER", "CNT_ACTIONS_FEW"}


def map_reasons(rep: dict, aux: dict,
                expected_game: str | None = None) -> MapResult:
    """Pure mapping: engine report + wrapper aux -> bin + reason codes."""
    reasons: list[dict] = []
    advisories: list[str] = []
    inv = rep.get("inventory", {}) or {}
    dur = float(rep.get("duration_s") or 0)
    vlm_rep = rep.get("vlm", {}) or {}
    vlm_ran = bool(vlm_rep.get("samples")) and "error" not in vlm_rep
    hold = bool(aux.get("vlm_required", True)) and not vlm_ran
    if aux.get("vlm_extra_failed"):
        hold = True

    _map_qa_issues(rep.get("qa_issues", []), reasons,
                   has_raw=bool(aux.get("has_raw")))
    _map_sync(rep, aux, reasons, advisories)
    _map_game_identity(rep, expected_game, reasons, advisories)
    if vlm_ran:
        _map_windows(rep, aux, reasons, advisories)
        _map_flags(rep, aux, reasons, advisories)
        _joint_edge_short(reasons)

    # duration / actions / drops (§10.4 escalations)
    # CNT_SHORT and the soft-max advisory judge the PROBED video duration
    # when the wrapper supplies it: `dur` is session.json's CLAIMED
    # duration_seconds, and a present-but-wrong claim under 70 terminally
    # rejected real >=70s footage (blocking, unfixable, video-independent)
    # while the SAME verdict planned the rewrite that recomputes the very
    # field (r-loop 9 #12). The claim stays the fallback for callers that
    # never probed. _map_windows geometry deliberately unchanged this pass.
    dur_true = float(aux.get("probed_duration_s") or 0) or dur
    if dur_true and dur_true < C.MIN_CLIP_S:
        reasons.append(_reason("CNT_SHORT", True, False,
                               {"duration_s": round(dur_true, 1)},
                               f"clip {dur_true:.1f}s under the hard "
                               f"{C.MIN_CLIP_S:.0f}s minimum"))
    elif dur_true > C.SESSION_SOFT_MAX_S:
        advisories.append(f"clip {dur_true / 60:.0f} min exceeds the 30 min "
                          f"guidance — accepted with note (R16)")
    if inv and inv.get("distinct_actions", 99) < C.MIN_DISTINCT_ACTIONS:
        # Blind to rows the PIPELINE blanked (r-loop 5). FIX_GATE_WINDOW
        # empties input_keys AND input_actions, and the session then goes
        # through a FULL re-validation whose inventory is recomputed from
        # the gated frames.csv -- with nothing subtracting what we
        # ourselves deleted. So a session whose 3rd distinct action occurs
        # only inside a frozen context (the OW Observatory terminal is an
        # unmodelled one) came back with 2 and was REJECTED on a blocking,
        # UNFIXABLE reason, and coaching.md told the player to "play
        # actively" for a stretch this pipeline erased. R1+R3 multiplied
        # the exposure: every window <=5s is now GATED rather than cut,
        # up to 40 per session. Same failure shape r-loop 4 already ruled
        # a major for the keybind inversion -- this is its second instance.
        destroyed = set((aux.get("gate_destroyed") or {}).get("actions")
                        or [])
        restored = set((inv.get("actions") or {})) | destroyed
        if destroyed and len(restored) >= C.MIN_DISTINCT_ACTIONS:
            advisories.append(
                f"only {inv.get('distinct_actions')} distinct actions in "
                f"the delivered rows, but {len(restored)} before this "
                f"pipeline gated {sorted(destroyed)} out of a confirmed "
                f"frozen window — not a player deficit, not a reject")
        else:
            reasons.append(_reason(
                "CNT_ACTIONS_FEW", True, False,
                {"distinct": inv.get("distinct_actions")},
                f"only {inv.get('distinct_actions')} distinct actions "
                f"(<{C.MIN_DISTINCT_ACTIONS}, R14)"))
    pct = inv.get("irregular_pct") or 0.0
    if pct > C.DROPS_REJECT_PCT:
        reasons.append(_reason(
            "CNT_DROPS", True, False, {"pct": pct},
            f"{pct}% irregular frame intervals (> "
            f"{C.DROPS_REJECT_PCT:.0f}% reject gate)"))
    elif pct > C.DROPS_WARN_PCT:
        advisories.append(f"{pct}% irregular frame intervals — deliver "
                          f"with warn (1-5% band)")

    # modalities (§10.4: blocking when video evidence confirms use)
    if inv:
        if inv.get("key_frames") == 0:
            gated_keys = int((aux.get("gate_destroyed") or {}).get(
                "key_frames") or 0)
            if gated_keys:
                # same as CNT_ACTIONS_FEW above: reachable for a
                # mouse-heavy session whose only key presses fall inside
                # frozen contexts. "re-record (never fabricate)" must
                # never be said about rows we blanked ourselves.
                advisories.append(
                    f"zero key frames in the delivered rows, but this "
                    f"pipeline gated {gated_keys} key frame(s) out of a "
                    f"confirmed frozen window — not a capture failure")
            elif aux.get("video_active", True):
                reasons.append(_reason(
                    "INP_KEYS_MISSING", True, False, {},
                    f"zero key frames in {inv.get('rows')} rows while the "
                    f"video shows live motion (movement needs WASD in both "
                    f"games) — re-record (never fabricate)"))
            else:
                advisories.append(
                    "zero key frames but the video is near-static — "
                    "keyboard-missing not provable; confirm on filmstrip "
                    "(the dead-black check no longer measures stillness)")
        qa_motion = any("mouse motion missing" in i
                        for i in rep.get("qa_issues", []))
        if qa_motion or inv.get("motion_frames") == 0:
            reasons.append(_reason(
                "INP_MOTION_MISSING", True, False, {},
                "mouse motion missing (dx/dy never non-zero) — "
                "unrecoverable; re-record (locked rule)"))
        if inv.get("btn_frames") == 0 and inv.get("motion_frames"):
            combat = vlm_rep.get("combat_ts", []) if vlm_ran else []
            if len(combat) >= 2:
                reasons.append(_reason(
                    "INP_BUTTONS_MISSING", True, False,
                    {"combat_ts": combat[:6]},
                    f"zero mouse-button events but video shows firing at "
                    f"{combat[:6]}s (08-12 defect class) — re-record + "
                    f"vendor note"))
            else:
                advisories.append(
                    "no mouse-button events; no combat evidence in video — "
                    "possibly genuine non-use (benign zero-buttons)")
        if inv.get("os_keys"):
            # the trigger judges the session's OWN binding (r16 #3): the
            # engine's os_keys is pattern-only, but the planned
            # FIX_KEY_HYGIENE deliberately KEEPS bound OS/F-keys (the
            # locked strip-unless-bound rule), so a keybind-blind
            # trigger made the fix a provable no-op — the reason
            # re-fired every revalidation, two burned attempts, and a
            # spec-conformant delivery took a wrongful terminal reject.
            # Bound hits surface as an advisory so the dossier still
            # shows them; unbound pollution keeps today's blocking
            # reason, which hygiene really does clear.
            from translator.v2 import key_canonical
            bound = aux.get("bound_literals") or frozenset()
            os_keys = inv["os_keys"]
            if not isinstance(os_keys, dict):
                os_keys = {t: 1 for t in os_keys}
            unbound = {t: n for t, n in os_keys.items()
                       if key_canonical(t) not in bound}
            kept = {t: n for t, n in os_keys.items() if t not in unbound}
            if unbound:
                reasons.append(_reason(
                    "INP_OSKEYS", True, True, {"keys": unbound},
                    f"OS/system keys in input_keys: {unbound}"))
            if kept:
                advisories.append(
                    f"OS-pattern keys BOUND in this session's keybind — "
                    f"kept per the strip-unless-bound rule: {kept}")
        if inv.get("bleed_frames"):
            reasons.append(_reason(
                "INP_BLEED", True, True,
                {"frames": inv["bleed_frames"]},
                f"L+R modifier bleed in {inv['bleed_frames']} frames "
                f"({inv.get('bleed_example', '')})"))

    if aux.get("black_frozen"):
        reasons.append(_reason(
            "CNT_BLACK_FROZEN", True, False, {},
            aux.get("black_frozen_evidence",
                    "video mostly dead-black — borderless-windowed "
                    "coaching")))
    if aux.get("tamper"):
        reasons.append(_reason("INT_TAMPER", True, False, {},
                               aux["tamper"]))
    if not rep.get("audio", {}).get("has_audio", True):
        # audio NEVER blocks (spec has no audio requirement; R-round-3)
        advisories.append("no audio track — warn note only, never blocks")

    # aux notes (VLM-omitted static windows, uncut-AFK audit trail, scanner
    # failures, statics cap) were write-only — nothing ever read the key, so
    # the r3 #9/#23 F5 advisory never reached verdict.json; surface them
    # with every other advisory (review-r4 #18/#22)
    advisories.extend(aux.get("notes", []))

    # bin logic (§10): 1 = no blocking; 2 = all blocking fixable; 3 = any
    # blocking unfixable. HOLD only when no video-independent unfixable
    # reason already decides the session.
    blocking = [r for r in reasons if r["blocking"]]
    unfixable = [r for r in blocking if not r["fixable"]]
    if hold:
        hard_now = [r for r in unfixable if r["code"] in _VIDEO_INDEPENDENT]
        if hard_now:
            return MapResult(bin=3, hold_vlm=False,
                             engine_verdict=rep.get("verdict", ""),
                             reasons=reasons, advisories=advisories,
                             metrics=_metrics(rep, aux))
        return MapResult(bin=None, hold_vlm=True,
                         engine_verdict=rep.get("verdict", ""),
                         reasons=reasons, advisories=advisories,
                         metrics=_metrics(rep, aux))
    b = 3 if unfixable else (2 if blocking else 1)
    return MapResult(bin=b, hold_vlm=False,
                     engine_verdict=rep.get("verdict", ""),
                     reasons=reasons, advisories=advisories,
                     metrics=_metrics(rep, aux))


def _metrics(rep: dict, aux: dict) -> dict:
    inv = rep.get("inventory", {}) or {}
    return {
        "duration_s": rep.get("duration_s"),
        # the validate-time ffprobe truth (D2): the drivers backfill a
        # NULL ledger duration_raw_s from it — the download-time probe is
        # single-shot and swallowed, and an unprobed root is uncountable
        # and silently unpayable once its window passes (r-loop 11 #6)
        "probed_duration_s": aux.get("probed_duration_s"),
        "frames": rep.get("frames"),
        "fps": rep.get("fps"),
        "qa_status": rep.get("qa_status"),
        "distinct_actions": inv.get("distinct_actions"),
        "irregular_pct": inv.get("irregular_pct"),
        "key_frames": inv.get("key_frames"),
        "btn_frames": inv.get("btn_frames"),
        "motion_frames": inv.get("motion_frames"),
        "lag_summary": (rep.get("lag") or {}).get("summary"),
        "frame_sync": (rep.get("lag") or {}).get("frame_sync"),
        "vlm_requests": (rep.get("vlm") or {}).get("requests"),
        "scanner": aux.get("scanner_stats"),
        # R23: models that actually answered; any rung>0 entry flags the
        # verdict as fallback-model (dossier + batch line)
        "models_used": aux.get("models_used") or [],
    }


# ------------------------------------------------------------ full wrapper

def _read_rows(frames_csv: Path) -> tuple[list[int], list[bool], list[bool],
                                          str | None]:
    """(timestamp_ms, any-input flags, action flags, tamper evidence)."""
    ts: list[int] = []
    active: list[bool] = []
    has_action: list[bool] = []
    absurd = 0
    with frames_csv.open(newline="") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            ts.append(int(float(r.get("timestamp_ms") or 0)))
            dx = r.get("input_mouse_dx") or ""
            dy = r.get("input_mouse_dy") or ""
            act = bool(r.get("input_keys") or r.get("input_mouse_buttons")
                       or dx not in ("", "0.0", "0")
                       or dy not in ("", "0.0", "0"))
            active.append(act)
            has_action.append(bool(r.get("input_actions")))
            for v in (dx, dy):
                try:
                    if v and abs(float(v)) > 20000:
                        absurd += 1
                except ValueError:
                    pass
    tamper = (f"{absurd} rows carry impossible mouse deltas (>20000/frame)"
              if absurd > max(len(ts) // 100, 2) else None)
    return ts, active, has_action, tamper


def validate_session(work_dir: Path, dossier_dir: Path, *,
                     payload: str = "v2", expected_game: str | None = None,
                     gemini_key: str = "", gemini_model: str = "",
                     vlm_interval: float = 4.0, refine_step: float = 1.0,
                     skip_vlm: bool = False) -> MapResult:
    """Validate one downloaded session; writes verdict.json to the dossier.

    skip_vlm is for offline tests/benchmarks only — it makes the verdict
    carry hold_vlm=True (a session is never passed unlooked-at, F5) unless
    a video-independent reject already decides it.
    """
    work_dir = Path(work_dir)
    dossier_dir = Path(dossier_dir)
    dossier_dir.mkdir(parents=True, exist_ok=True)

    if payload == "v1":
        res = MapResult(bin=2, hold_vlm=False, engine_verdict="",
                        reasons=[_reason("ARR_V1_FORMAT", True, True, {},
                                         "obsolete v1 delivery — convert "
                                         "v1->v2 (playbook §6)")])
        _write_verdict(dossier_dir, work_dir.name, res)
        return res
    if payload == "raw":
        res = MapResult(bin=2, hold_vlm=False, engine_verdict="",
                        reasons=[_reason("ARR_RAW_ONLY", True, True, {},
                                         "raw-only bundle — translate-v2 is "
                                         "the whole fix")])
        _write_verdict(dossier_dir, work_dir.name, res)
        return res

    from translator import video as V
    try:
        probed_info = V.probe(work_dir / "video.mp4")
    except Exception as e:
        res = MapResult(bin=2, hold_vlm=False, engine_verdict="",
                        reasons=[_reason(
                            "STR_VIDEO_UNREADABLE", True, True, {},
                            f"ffprobe cannot read video.mp4: {e}")])
        _write_verdict(dossier_dir, work_dir.name, res)
        return res

    eng = load_engine()
    gem = None
    vlmmod.begin_session()
    if gemini_key and not skip_vlm:
        # LadderGemini, not eng.Gemini: gives the SWEEP the R21 endpoint
        # failover + R23 rung ladder without touching the engine (§10a)
        gem = vlmmod.LadderGemini(gemini_key,
                                  gemini_model or "gemini-3.7-flash")
    raw_dir = work_dir / "raw"
    raw_by_sid = {}
    seed_notes: list[str] = []
    if (raw_dir / "metadata.json").exists() and \
            (raw_dir / "inputs.jsonl").exists():
        raw_by_sid[work_dir.name] = raw_dir
        try:
            _seed_shift_record(work_dir)
        except Exception as e:
            # inference is best-effort and the recheck decides — but a
            # silent failure means qa re-bins at shift 0 and can FAIL sync
            # on a session that is fine, so leave a trail rather than
            # swallowing it whole (r-loop 3). aux does not exist yet; the
            # note is merged in below.
            seed_notes.append(
                f"shift-record seeding failed ({type(e).__name__}: {e}) — "
                f"raw sync verification runs at shift 0")

    analysis = eng.analyze(work_dir, raw_by_sid, gem, vlm_interval,
                           refine_step)
    if analysis.error:
        # a malformed frames.csv is a FIXABLE arrival defect, not a crash
        if "missing input columns" in analysis.error:
            res = MapResult(bin=2, hold_vlm=False, engine_verdict="error",
                            reasons=[_reason("STR_HEADER_BAD", True, True,
                                             {}, analysis.error)])
            _write_verdict(dossier_dir, work_dir.name, res)
            return res
        # a host-kind engine error (an OSError laundered into the error
        # string) must stay host-classed for the r-loop-6 carve-out:
        # re-raise as OSError so _validate_worker's isinstance split sees
        # host — cooldown, not terminal quarantine (r-loop 9 #15)
        if getattr(analysis, "error_kind", "") == "host":
            raise OSError(f"engine error: {analysis.error}")
        # anything else: hard failure — the orchestrator quarantines with
        # an alert instead of holding forever
        raise RuntimeError(f"engine error: {analysis.error}")
    from dataclasses import asdict
    rep = asdict(analysis)
    rep["findings"] = [asdict(f) if not isinstance(f, dict) else f
                       for f in analysis.findings]

    aux = _build_aux(work_dir, rep, gem,
                     gemini_key=gemini_key,
                     gemini_model=gemini_model or "gemini-3.7-flash",
                     vlm_expected=bool(gemini_key) and not skip_vlm)
    # r19 #11 (M5, RULED fix-now): this flag drives the STORED fixable
    # field on QA_FAIL_UNMAPPED — and so the ruled reject-label surface
    # (unfixable reasons only, judged per reason's own stored field). It
    # must judge sidecar USABILITY with the same rule as the plan gate
    # (fix.has_raw_sidecars: parse to a dict + a parseable
    # started_at_utc, r18 #4 + r19 #2), or a corrupt sidecar stores
    # fixable=true, the fix phase plans nothing and rejects 'unfixable',
    # and the reject line prints the false bare fix-failed marker.
    # raw_by_sid above deliberately stays existence-based: the engine
    # verify and the shift seeding degrade internally.
    from pipeline.fix import has_raw_sidecars
    aux["has_raw"] = has_raw_sidecars(work_dir)
    aux["vlm_required"] = True
    aux["gate_destroyed"] = _gate_destroyed(dossier_dir)
    # the INP_OSKEYS trigger judges the session's own binding (r16 #3);
    # on failure, degrade to the pre-fix all-unbound behavior with a
    # note, never crash validation
    try:
        aux["bound_literals"] = _session_bound_literals(work_dir,
                                                        expected_game)
    except Exception as e:
        aux["bound_literals"] = frozenset()
        aux.setdefault("notes", []).append(
            f"session keybind unresolvable for the OS-key trigger "
            f"({type(e).__name__}: {e}) — all OS-pattern keys treated as "
            f"unbound this pass")
    # the probed duration is the truth source for the hard length gates;
    # the engine's duration_s is session.json's CLAIM (r-loop 9 #12)
    aux["probed_duration_s"] = probed_info.duration_s
    if seed_notes:
        aux.setdefault("notes", []).extend(seed_notes)
    # every (key tag, model) that answered — R23 flag trail into the verdict
    aux["models_used"] = vlmmod.session_models()

    res = map_reasons(rep, aux, expected_game)
    _write_verdict(dossier_dir, work_dir.name, res)
    _archive_analysis(work_dir, dossier_dir)
    return res


def _session_bound_literals(work_dir: Path, game: str | None) -> frozenset:
    """The session's bound literals, resolved exactly as fix_key_hygiene
    does (r16 #3): the session's own raw/keybind.json when present
    (resolve_keybind's built-in fallback covers unusable files), else the
    built-in for the ledger game, plus KEYBIND_PATCHES. Feeds the
    INP_OSKEYS trigger so it judges the same binding the planned fix
    will."""
    from translator.keybind import bound_literals
    from translator.keybinds import KEYBINDS
    from translator.translate import resolve_keybind
    from translator.v2 import KEYBIND_PATCHES
    kbp = Path(work_dir) / "raw" / "keybind.json"
    if kbp.exists():
        kb = resolve_keybind(keybind_path=kbp, game_name=game,
                             exe_name=None)
    else:
        kb = dict(KEYBINDS.get(game or "", {}))
    kb.update(KEYBIND_PATCHES.get(game or "", {}))
    return bound_literals(kb)


def _seed_shift_record(work_dir: Path) -> None:
    """Uploaded translate-v2 sessions carry a sync shift baked into the CSV
    but NOT the out-root translation_report.json that records it — qa's raw
    recomputation would re-bin at shift 0 and spuriously fail, burning a
    fix attempt and a paid VLM sweep (review-2 #13). Recover the constant
    by exact-match search over the raw mouse events (±15 frames covers
    every shift the corrector has ever applied) and write the record where
    _applied_shift_us looks."""
    from bisect import bisect_right
    from datetime import datetime as _dt

    report_path = work_dir.parent / "translation_report.json"
    try:
        existing = json.loads(report_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}
    if work_dir.name in existing:
        return
    raw = work_dir / "raw"
    from translator import video as V
    s = json.loads((work_dir / "session.json").read_text())
    meta = json.loads((raw / "metadata.json").read_text())
    # naive->UTC, same guard as translator.v2._utc_aware (r-loop 2 added it
    # in build_session_json and _verify_against_raw; this twin was missed).
    # A NAIVE started_at_utc — the exact input that guard exists for — made
    # this subtraction raise TypeError, which the caller's bare
    # `except Exception: pass` swallowed. The record was then never seeded,
    # so _applied_shift_us returned 0 and qa re-binned the raw events at
    # the wrong head offset: a crash was traded for a WRONG verdict that
    # FAILs sync and burns a fix attempt plus a paid VLM sweep on a session
    # with nothing wrong with it (r-loop 3).
    from translator.v2 import _utc_aware
    started = _utc_aware(meta["recording"]["started_at_utc"])
    created = _utc_aware(s["created_at_utc"])
    base_head_us = (created - started).total_seconds() * 1e6
    dur_us = float(s["duration_seconds"]) * 1e6
    fps = float(s.get("fps") or 30.0)
    frame_us = 1e6 / fps

    pts = V.frame_pts(work_dir / "video.mp4")
    csv_dx: list[float] = []
    csv_dy: list[float] = []
    with (work_dir / "frames.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            csv_dx.append(float(row["input_mouse_dx"] or 0))
            csv_dy.append(float(row["input_mouse_dy"] or 0))
    n = len(csv_dx)
    if not pts or len(pts) != n:
        return
    events = []
    for line in (raw / "inputs.jsonl").open():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "mouse_raw" and isinstance(e.get("t"), int):
            events.append((e["t"], int(e.get("dx", 0) or 0),
                           int(e.get("dy", 0) or 0)))

    def matches(shift_us: float) -> bool:
        head = base_head_us - shift_us
        dx = [0.0] * n
        dy = [0.0] * n
        for t, edx, edy in events:
            if not (head <= t < head + dur_us):
                continue
            f = bisect_right(pts, int(t - head)) - 1
            if 0 <= f < n:
                dx[f] += edx
                dy[f] += edy
        return all(dx[i] == csv_dx[i] and dy[i] == csv_dy[i]
                   for i in range(n))

    if matches(0):
        return                     # no shift was applied; nothing to seed
    for k in range(-15, 16):
        if k == 0:
            continue
        su = round(k * frame_us)
        if matches(su):
            _locked_report_update(report_path, work_dir.name,
                                  {"shift_us": su, "inferred": True})
            return


def _locked_report_update(report_path: Path, name: str,
                          record: dict | None) -> None:
    """Merge one entry into the SHARED work-root translation_report.json
    (record=None DELETES the entry — see _locked_report_remove).
    Up to 8 validation workers race this file; a bare read-modify-write
    loses entries and the lost shift makes qa-v2 spuriously FAIL sync on
    revalidation (review-r1 #8). mkdir is the portable atomic lock; on
    timeout we write anyway — worst case equals the pre-lock behavior."""
    lock = report_path.parent / (report_path.name + ".lock")
    held = False
    # Owner stamp. The release used to be a bare `os.rmdir(lock)`, which
    # removes whatever directory is sitting at that PATH -- not
    # necessarily ours. The staleness breaker below rescinds a lock by
    # renaming it aside and letting the next waiter mkdir a fresh one, but
    # the rescinded holder's `held` flag is still True, so its finally
    # deleted the SUCCESSOR's lock and a third racer walked straight in --
    # two concurrent read-modify-writes, a lost {"shift_us": ...} entry,
    # and then translator.v2._applied_shift_us returns 0, qa-v2 re-bins the
    # raw mouse at head offset 0 and the session takes a spurious
    # SYN_TS_NOT_PTS: one of only two fix attempts and one paid Gemini
    # sweep burned on a session with nothing wrong with it. r-loop 3 cut
    # REPORT_LOCK_STALE_S 120s -> 20s, shrinking the window in which a
    # live-but-slow holder is judged dead by 6x, and r-loop 4 fixed this
    # same one-pid-many-racers problem for the grave NAME but not for the
    # release (r-loop 5). Unique per RACER, like the grave name.
    token = (f"{os.getpid()}-{threading.get_ident()}-"
             f"{uuid.uuid4().hex}")
    owner = lock / "owner"
    # Patience must OUTLAST the staleness threshold (C.REPORT_LOCK_WAIT_S >
    # C.REPORT_LOCK_STALE_S), or the one case that cannot resolve itself —
    # a holder that died mid-section — is also the one case the breaker
    # never gets to handle. The old numbers were inverted: max(5s,
    # CONT_POOL_MAX seconds) = 44s of patience against a 120s staleness
    # bar, so every waiter arriving while a dead holder's lock was 0-76s
    # old exhausted its wait and fell through to the unlocked write,
    # re-opening the r1 #8 lost update the longer wait was added to close
    # (r-loop 3).
    deadline = time.time() + C.REPORT_LOCK_WAIT_S
    while time.time() < deadline:
        try:
            os.mkdir(lock)
            try:
                owner.write_text(token)
            except OSError:
                # cannot stamp it -> cannot prove ownership later; give the
                # lock straight back rather than hold an unprovable one
                try:
                    os.rmdir(lock)
                except OSError:
                    pass
                raise FileExistsError
            held = True
            break
        except FileExistsError:
            # a worker SIGKILLed while holding the lock would disable it
            # forever (review-r2 #43): a lock older than 120 s is orphaned
            # (the guarded section is milliseconds) — break it by RENAME
            # aside then delete, like run._reclaim_stale_lock: a bare
            # stat-then-rmdir let two waiters both break it — B could
            # rmdir the lock A had JUST re-created, and both then held
            # it, resurrecting the r1 #8 lost update (review-r4 #38/#45).
            # Only one renamer ever wins os.rename; losers hit OSError
            # and just retry mkdir.
            try:
                if time.time() - lock.stat().st_mtime > \
                        C.REPORT_LOCK_STALE_S:
                    # unique per RACER, not per process: the continuous
                    # driver breaks this lock from S, U, H and every V
                    # session runner — all one pid, hence one grave path.
                    # os.rename over an existing EMPTY dir succeeds on
                    # POSIX, so two threads could both "win" and both hold
                    # the lock, losing one read-modify-write (r-loop 4).
                    grave = lock.with_name(
                        f"{lock.name}.stale-{os.getpid()}-"
                        f"{threading.get_ident()}-{uuid.uuid4().hex[:8]}")
                    os.rename(lock, grave)
                    shutil.rmtree(grave, ignore_errors=True)
                    continue
            except OSError:
                pass
            time.sleep(0.05)
    try:
        try:
            existing = json.loads(report_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            existing = {}
        if record is None:
            if name not in existing:
                return          # nothing to drop — don't create the file
            del existing[name]
        else:
            existing[name] = record
        # atomic replace: qa's unlocked readers must never see a torn
        # file (review-r2 #42); pid-unique tmp so two writers that ever
        # slip past the lock cannot collide on one tmp name (review-r3 #45)
        tmp = report_path.with_suffix(f".json.tmp{os.getpid()}")
        tmp.write_text(json.dumps(existing, indent=2))
        os.replace(tmp, report_path)
    finally:
        if held:
            # Release ONLY if the lock at that path is still the one we
            # took. If the breaker rescinded ours, someone else owns the
            # path now and deleting it would disarm the mutex under them.
            try:
                mine = owner.read_text() == token
            except OSError:
                mine = False        # rescinded, or never stamped
            if mine:
                try:
                    owner.unlink()
                    os.rmdir(lock)
                except OSError:
                    pass


def _locked_report_remove(report_path: Path, name: str) -> None:
    """Drop one sid's entry (same lock discipline). Supersede and the
    quarantine heal must remove the old upload's shift record: with the
    entry present, _seed_shift_record early-returns and qa validates the
    REPLACEMENT bytes against the OLD upload's shift — spurious
    SYN_TS_NOT_PTS, a burned fix attempt and a paid VLM sweep
    (review-r4 #7)."""
    _locked_report_update(report_path, name, None)


def _gate_destroyed(dossier_dir: Path) -> dict:
    """Inventory that FIX_GATE_WINDOW blanked on earlier attempts.

    map_reasons re-tests the inventory recomputed from the GATED
    frames.csv, and nothing subtracted the rows the pipeline itself
    emptied — so a session whose 3rd distinct action only occurs inside a
    frozen context came back with 2 and was rejected CNT_ACTIONS_FEW,
    blocking and UNFIXABLE, with coaching.md telling the player to "play
    actively" for actions WE deleted (r-loop 5).

    Read from the fixlog, which is the atomic evidence of record (r-loop
    4) — this is a claim about what WE DID, not about current file
    contents, so unlike the reverted coordinates sidecar it cannot go
    stale against the CSV. A later FIX_RETRANSLATE that repopulates those
    rows simply restores the inventory, and the deficit stops firing.
    Missing/corrupt log -> empty, i.e. exactly today's behaviour.
    """
    acts: set[str] = set()
    keys = 0
    try:
        log = json.loads((dossier_dir / "fixlog.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"actions": [], "key_frames": 0}
    if not isinstance(log, list):
        return {"actions": [], "key_frames": 0}
    # _append_fixlog writes ONE record per apply_fixes call, with the
    # per-fix entries NESTED under "fixes":
    #     [{"ts": ..., "fixes": [{"fix": ..., "ok": ..., "note": ...}, ...]}]
    # r-loop 5 read the top level directly, so every iteration hit the
    # `continue` and the carve-out was dead code — while its unit test
    # hand-built a FLAT list and passed against a shape production never
    # produces (r-loop 6 blocker). Accept both shapes so a hand-written or
    # older log still reads.
    entries = []
    for rec in log:
        if not isinstance(rec, dict):
            continue
        nested = rec.get("fixes")
        if isinstance(nested, list):
            entries.extend(x for x in nested if isinstance(x, dict))
        elif "fix" in rec:
            entries.append(rec)
    for entry in entries:
        if entry.get("fix") != "FIX_GATE_WINDOW" or not entry.get("ok"):
            continue
        d = (entry.get("note") or {})
        d = d.get("destroyed") if isinstance(d, dict) else None
        if not isinstance(d, dict):
            continue
        acts.update(a for a in (d.get("actions") or []) if isinstance(a, str))
        try:
            keys += int(d.get("key_frames") or 0)
        except (TypeError, ValueError):
            pass
    return {"actions": sorted(acts), "key_frames": keys}


def _write_verdict(dossier_dir: Path, session_id: str, res: MapResult) -> None:
    (dossier_dir / "verdict.json").write_text(json.dumps({
        "session": session_id, "bin": res.bin, "hold_vlm": res.hold_vlm,
        "engine_verdict": res.engine_verdict, "reasons": res.reasons,
        "advisories": res.advisories, "metrics": res.metrics}, indent=1))


def _archive_analysis(work_dir: Path, dossier_dir: Path) -> None:
    """Engine reports + evidence stills survive in the dossier forever."""
    src = work_dir.parent / f"{work_dir.name}-analysis"
    if not src.exists():
        return
    for name in ("report.md", "report.json"):
        if (src / name).exists():
            shutil.copy2(src / name, dossier_dir / name)
    art = src / "artifacts"
    if art.exists():
        dst = dossier_dir / "artifacts"
        dst.mkdir(exist_ok=True)
        for f in art.iterdir():
            shutil.copy2(f, dst / f.name)


def _dead_black_check(luma: list[float]) -> tuple[bool, str | None]:
    """Whole-clip capture-failure gate (recalibrated 2026-08-16, Adnaan;
    frac 0.995 same evening): reject only when >= DEAD_BLACK_REJECT_FRAC
    of frames sit under the DEAD_BLACK_LUMA_BELOW mean-luma bar — the
    uniform-black signature. Dark-but-live gameplay (Kamla scenes at luma
    7-16) must pass; partial blackouts belong to the mid-clip window
    machinery. The coaching wording downstream is for true capture
    failures only."""
    if not luma:
        return False, None
    frac = sum(1 for v in luma
               if v < C.DEAD_BLACK_LUMA_BELOW) / len(luma)
    if frac >= C.DEAD_BLACK_REJECT_FRAC:
        return True, (f"{frac:.1%} of frames are dead-black (mean luma < "
                      f"{C.DEAD_BLACK_LUMA_BELOW:g})")
    return False, None


def _build_aux(work_dir: Path, rep: dict, gem, *, gemini_key: str,
               gemini_model: str, vlm_expected: bool) -> dict:
    """Scanner timeline + AFK + refined windows + notif/chat confirmation."""
    aux: dict = {"vlm_extra_failed": False}
    video = work_dir / "video.mp4"
    try:
        ts_ms, active, has_action, tamper = _read_rows(
            work_dir / "frames.csv")
    except Exception as e:
        ts_ms, active, has_action, tamper = [], [], [], None
        aux.setdefault("notes", []).append(f"frames.csv unreadable for "
                                           f"aux checks: {e}")
    if tamper:
        aux["tamper"] = tamper

    vlm_rep = rep.get("vlm", {}) or {}
    tl = None
    if scanner.available():
        from translator import video as V
        try:
            pts = V.frame_pts(video)
            tl = scanner.scan_video(video, pts_us=pts)
        except Exception as e:
            tl = None
            aux.setdefault("notes", []).append(f"scanner failed: {e}")
    if tl is None:
        # losing the scanner loses keep-vs-cut boundary precision, AFK and
        # black-frozen checks — never a silent pass (F5): hold when the
        # session was supposed to get the full battery
        aux.setdefault("notes", []).append(
            "scanner unavailable — AFK / black-frozen / frozen-window "
            "boundary precision not run")
        if vlm_expected:
            aux["vlm_extra_failed"] = True

    statics: list[tuple[float, float]] = []
    afk_cands: list[tuple[float, float]] = []
    if tl is not None:
        gameplay_ts = [s["t"] for s in vlm_rep.get("samples", [])
                       if s.get("label") == "gameplay"]
        baseline = tl.baseline(gameplay_ts)
        aux["video_active"] = baseline >= 1.0
        aux["scanner_stats"] = {"frames": tl.n_frames,
                                "baseline": round(baseline, 3),
                                "timing": tl.timing}
        # A uniform-fps timeline is SYNTHETIC (see scanner.scan_video). Its
        # window bounds must never become cut points or gate spans: the
        # cutter maps them onto real PTS, so a 9s freeze detected at a
        # fabricated 660s removes 9s of genuine gameplay while leaving the
        # freeze in place — and the child then re-detects it and splits
        # again, the very cascade R1/R2 were ruled to stop. Scanner
        # findings degrade to advisory; the VLM/engine path is unaffected
        # because its window bounds do not come from this timeline
        # (r-loop 3).
        synthetic_timing = tl.timing != "real_pts"
        if synthetic_timing:
            aux.setdefault("notes", []).append(
                f"scanner timeline is SYNTHETIC ({tl.timing}: decoded "
                f"{tl.n_frames} frames but the container's packet count "
                f"disagreed) — scanner-found windows and AFK spans are "
                f"advisory only this pass; nothing derived from them may "
                f"cut or gate")

        # dead-black whole-clip detection (recalibrated 08-16 — config
        # DEAD_BLACK_*; the frozen-motion arm is gone: near-static video
        # stays advisory-only through video_active above)
        if tl.luma:
            dead, ev = _dead_black_check(tl.luma)
            if dead:
                aux["black_frozen"] = True
                aux["black_frozen_evidence"] = ev

        # 1-frame-accurate bounds for the engine's gating windows
        refined: dict = {}
        refined_af: dict = {}
        engine_windows = [w for w in vlm_rep.get("windows", [])
                          if w.get("gating")]
        for w in engine_windows:
            if synthetic_timing:
                break        # refined bounds would be fabricated too
            r = scanner.refine_window(tl, w["t0"], w["t1"],
                                      ratio=C.STILLNESS_FROZEN_BELOW,
                                      baseline=baseline)
            if r:
                refined[(w["t0"], w["t1"])] = r
                # Count action frames over the MEASURED frozen run, not
                # the VLM window (RULED, Adnaan 2026-08-18; r-loop-3 #6).
                # analyze_sample._windows sets window bounds as MIDPOINTS
                # between VLM sample times, so both the trigger and the
                # gate span were derived from VLM label boundaries -- which
                # are not stable across passes. One boundary sample
                # flipping label moves a bound 15-30 frames; the recheck
                # recounts, re-raises INP_FROZEN_ACTIONS, spends attempt 2
                # re-gating and rejects on pass 3. "Frozen" is a
                # MEASUREMENT: an action on a MOVING frame outside the
                # VLM's fuzzy edge is real gameplay, so counting it is a
                # false positive and blanking it destroys real data. The
                # VLM stays the CLASSIFIER (is this stretch menu/loading/
                # pause rather than a legitimately still moment of play?)
                # and stops being a boundary-finder. Same inclusive-end
                # count the scanner path uses for extra_windows below.
                lo = tl.frame_at(r[0])
                hi = tl.frame_at(r[1])
                refined_af[(w["t0"], w["t1"])] = sum(
                    1 for i in range(lo, min(hi + 1, len(has_action)))
                    if has_action[i])
        aux["refined"] = refined
        aux["refined_action_frames"] = refined_af

        # only HIGH-tier windows already gate; a low-tier (single-sample)
        # engine window must NOT shadow the scanner's independent static
        # detection of the same span, or 1-sample pauses ship ungated —
        # one sample would be strictly worse than none (review-2 #3)
        acted_windows = [w for w in engine_windows
                         if w.get("tier") == "high"]

        def _overlaps_engine(t0: float, t1: float) -> bool:
            return any(t0 < w["t1"] + 1.0 and t1 > w["t0"] - 1.0
                       for w in acted_windows)

        # static candidates the 4s VLM sweep can miss entirely (a 2s pause
        # between samples) — the whole reason the scanner exists (§10.3)
        statics = scanner.static_windows(tl, ratio=C.STILLNESS_FROZEN_BELOW,
                                         baseline=baseline,
                                         min_s=C.SCANNER_STATIC_MIN_S)
        statics = [(a, b) for a, b in statics
                   if not _overlaps_engine(a, b)
                   and a > 1.0 and b < tl.duration_s - 1.0]
        if synthetic_timing and statics:
            aux.setdefault("notes", []).append(
                f"{len(statics)} scanner static window(s) found on the "
                f"synthetic timeline — reported for the filmstrip, NOT "
                f"classified or acted on (bounds are not real PTS)")
            statics = []

        # AFK: >30s zero input + near-static (OW dialogue/map/reading are
        # gameplay — VLM label check below removes those)
        if ts_ms and active and not synthetic_timing:
            for a, b in scanner.zero_input_runs(ts_ms, active,
                                                C.AFK_MIN_S):
                m = tl.window_motion(a, b)
                if m is not None and baseline > 0 and \
                        m < C.STILLNESS_FROZEN_BELOW * baseline:
                    afk_cands.append((a, b))

    capped = len(statics) > 40
    if capped:
        aux.setdefault("notes", []).append(
            f"scanner found {len(statics)} static candidates; classifying "
            f"the 40 longest (coverage bound, logged — no silent caps)")
        statics = sorted(statics, key=lambda w: w[0] - w[1])[:40]
        statics.sort()

    extra_windows: list[dict] = []
    afk_windows: list[tuple[float, float]] = []
    notifs: list[dict] = []
    chats: list[dict] = []
    if gem is not None and (statics or afk_cands or vlm_rep.get("notif_ts")
                            or vlm_rep.get("chat_ts")):
        eng = load_engine()
        grabber = None
        try:
            grabber = eng.FrameGrabber(video)
            if statics:
                mids = [round((a + b) / 2, 2) for a, b in statics]
                labels = vlmmod.classify_stills(
                    gemini_key, gemini_model, grabber,
                    rep.get("game_title", ""), mids)
                by_t = {s["t"]: s for s in labels}
                for (a, b), mid in zip(statics, mids):
                    lab = by_t.get(round(mid, 2), {})
                    if not lab:
                        # the VLM reply omitted this frame: measured
                        # stillness found it, nobody looked at it — F5
                        # says that must never pass SILENTLY
                        # (review-r3 #9/#23); surfaced as an advisory
                        aux.setdefault("notes", []).append(
                            f"static window {a:.1f}-{b:.1f}s got no VLM "
                            f"verdict — confirm on filmstrip (F5)")
                        continue
                    if lab.get("label") in ("menu", "loading", "pause",
                                            "scoreboard", "cutscene",
                                            "other_non_gameplay") and \
                            lab.get("conf") in ("high", "medium"):
                        lo = tl.frame_at(a)
                        hi = tl.frame_at(b)
                        # has_action (input_actions), NOT `active`:
                        # FIX_GATE_WINDOW blanks only keys+actions, so a
                        # motion-inclusive count could never be cleared by
                        # its own fix — the reason re-fired until the fix
                        # budget wrongly rejected the session
                        # (review-r3 #2)
                        # INCLUSIVE of the end frame, matching the window's
                        # two other consumers: gate.gate_windows selects
                        # rows on `t0 <= t <= t1` and the engine's
                        # rows_in_window uses bisect_right. A half-open
                        # count missed the frame AT the window end — and
                        # since static_windows rounds its bounds while
                        # frame_at bisects the unrounded times, up to two
                        # trailing frames went uncounted. On the shortest
                        # admissible window (SCANNER_STATIC_MIN_S = 0.8s,
                        # ~24 frames) that is ~8%, and the input that ENDS
                        # a freeze — the click that dismisses the loading
                        # screen — lands exactly there, so the window was
                        # reported as "kept (no inputs inside)" and shipped
                        # an action recorded on a non-gameplay frame
                        # (r-loop 3).
                        af = sum(1 for i in range(lo,
                                                  min(hi + 1,
                                                      len(has_action)))
                                 if has_action[i])
                        extra_windows.append(
                            {"t0": a, "t1": b, "label": lab["label"],
                             "action_frames": af})
            if afk_cands:
                mids = [round((a + b) / 2, 2) for a, b in afk_cands]
                labels = vlmmod.classify_stills(
                    gemini_key, gemini_model, grabber,
                    rep.get("game_title", ""), mids)
                by_t = {s["t"]: s for s in labels}
                for (a, b), mid in zip(afk_cands, mids):
                    lab = by_t.get(round(mid, 2), {})
                    # cutting real content needs a CONFIDENT non-reading
                    # label; a missing or low-confidence reply must never
                    # confirm the cut (review-2 #12 — OW Nomai reading is
                    # gameplay per the locked AFK rule)
                    if lab.get("label") and \
                            lab.get("conf") in ("high", "medium") and \
                            lab["label"] not in ("dialogue", "map"):
                        afk_windows.append((a, b))
                    else:
                        aux.setdefault("notes", []).append(
                            f"AFK candidate {a:.0f}-{b:.0f}s NOT cut "
                            f"(label={lab.get('label')!r} "
                            f"conf={lab.get('conf')!r})")
            for t in vlm_rep.get("notif_ts", []):
                fr = grabber.at(t)
                if fr is None:
                    continue
                jpg = _corner_jpeg(fr)
                ok, what = vlmmod.confirm_flag(gemini_key, gemini_model,
                                               jpg, "notification")
                notifs.append({"t": t, "confirmed": ok, "what": what})
            for t in vlm_rep.get("chat_ts", []):
                jpg = grabber.jpeg(t, width=1280)
                if jpg is None:
                    continue
                ok, what = vlmmod.confirm_flag(gemini_key, gemini_model,
                                               jpg, "chat")
                chats.append({"t": t, "confirmed": ok, "what": what})
        except vlmmod.VLMError as e:
            aux["vlm_extra_failed"] = True
            aux.setdefault("notes", []).append(f"VLM extras failed: {e}")
        finally:
            if grabber is not None:
                grabber.close()
    elif vlm_expected and (statics or afk_cands):
        # candidates exist but no VLM to classify them — cannot pass
        # unlooked-at (F5)
        aux["vlm_extra_failed"] = True

    aux["extra_windows"] = extra_windows
    aux["afk_windows"] = afk_windows
    aux["notifs"] = notifs
    aux["chats"] = chats
    return aux


def _corner_jpeg(frame) -> bytes:
    """Full-res bottom-right crop (Steam-toast territory) as JPEG."""
    import cv2
    h, w = frame.shape[:2]
    crop = frame[int(h * 0.60):, int(w * 0.55):]
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes() if ok else b""
