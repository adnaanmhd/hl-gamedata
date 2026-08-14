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
import re
import shutil
import sys
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
    ("frame_id not zero-based", "STR_ROWS_MISMATCH", True),
    ("!= record_*_px", "STR_SJ_INVALID", True),
]

# qa FAILs handled elsewhere (never through the generic table)
_QA_SKIP = ("frame-sync drift", "controls-to-video sync",
            "mouse motion missing", "under 70s")


def _map_qa_issues(issues: list[str], reasons: list[dict],
                   has_raw: bool) -> None:
    seen: set[str] = set()
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
            reasons.append(_reason("QA_FAIL_UNMAPPED", True, has_raw, {},
                                   msg))


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

    if tripwire:
        if not C.VLM_GAME_TRIPWIRE_GATES:
            # Adnaan 08-14 (post-plan): VLM game identity is report-only in
            # Phase 1 — log the unanimous mismatch loudly, gate nothing
            advisories.append(
                f"VLM GAME MISMATCH (report-only per Adnaan 08-14 ruling): "
                f"video looks like '{top}' ({v}/{total} votes, "
                f"unanimous-level) but session claims '{claimed}'")
            return
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
        reasons.append(_reason(
            "STR_GAME_MISMATCH", True, True,
            {"actual": top or claimed},
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
        # the frozen SPAN (refined) drives the keep-vs-cut decision, but a
        # cut must remove the whole flagged window (VLM bounds included) —
        # fade-in/out residue left at a segment head re-triggers detection
        cut0, cut1 = min(w0, w["t0"]), max(w1, w["t1"])
        if span <= C.KEEP_GATE_MAX_S and span <= C.KEEP_GATE_MAX_FRAC * dur:
            if action_frames:
                reasons.append(_reason(
                    "INP_FROZEN_ACTIONS", True, True,
                    {"t0": w0, "t1": w1},
                    f"{desc}: {action_frames} action frames inside a kept "
                    f"<= {C.KEEP_GATE_MAX_S:.0f}s frozen window"))
            else:
                advisories.append(
                    f"{desc}: {span:.1f}s frozen blip kept (no inputs "
                    f"inside; under the keep+gate bar)")
        else:
            reasons.append(_reason(
                "CNT_MID_NONGAMEPLAY", True, True,
                {"cut": [cut0, cut1]},
                f"{desc}: frozen {span:.1f}s "
                f"({span / dur:.2%} of clip) — over the keep+gate bar "
                f"({C.KEEP_GATE_MAX_S:.0f}s / "
                f"{C.KEEP_GATE_MAX_FRAC:.1%}); split"))

    for xw in aux.get("extra_windows", []):
        span = xw["t1"] - xw["t0"]
        desc = (f"scanner-found static [{xw['label']}] "
                f"{xw['t0']}-{xw['t1']}s")
        if span <= C.KEEP_GATE_MAX_S and span <= C.KEEP_GATE_MAX_FRAC * dur:
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
    for n in aux.get("notifs", []):
        if not n.get("confirmed"):
            advisories.append(
                f"possible notification at {n['t']}s (unconfirmed at full "
                f"crop: {n.get('what', '')})")
            continue
        if n["t"] <= 3.0 or n["t"] >= dur - 3.0:
            reasons.append(_reason(
                "CNT_NOTIF_EDGE", True, True,
                {"t": n["t"], "edge": "head" if n["t"] <= 3.0 else "tail"},
                f"notification at {n['t']}s (edge): {n.get('what', '')}"))
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
        edge = c["t"] <= 3.0 or c["t"] >= dur - 3.0
        reasons.append(_reason(
            "CNT_CHAT_PII", True, edge, {"t": c["t"]},
            f"player-chat text burned in at {c['t']}s: {c.get('what', '')}"))


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

    # duration / actions / drops (§10.4 escalations)
    if dur and dur < C.MIN_CLIP_S:
        reasons.append(_reason("CNT_SHORT", True, False,
                               {"duration_s": round(dur, 1)},
                               f"clip {dur:.1f}s under the hard "
                               f"{C.MIN_CLIP_S:.0f}s minimum"))
    elif dur > C.SESSION_SOFT_MAX_S:
        advisories.append(f"clip {dur / 60:.0f} min exceeds the 30 min "
                          f"guidance — accepted with note (R16)")
    if inv and inv.get("distinct_actions", 99) < C.MIN_DISTINCT_ACTIONS:
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
            if aux.get("video_active", True):
                reasons.append(_reason(
                    "INP_KEYS_MISSING", True, False, {},
                    f"zero key frames in {inv.get('rows')} rows while the "
                    f"video shows live motion (movement needs WASD in both "
                    f"games) — re-record (never fabricate)"))
            else:
                advisories.append(
                    "zero key frames but the video is near-static — "
                    "keyboard-missing not provable; see black/frozen check")
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
            reasons.append(_reason(
                "INP_OSKEYS", True, True, {"keys": inv["os_keys"]},
                f"OS/system keys in input_keys: {inv['os_keys']}"))
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
                    "video mostly black/frozen — borderless-windowed "
                    "coaching")))
    if aux.get("tamper"):
        reasons.append(_reason("INT_TAMPER", True, False, {},
                               aux["tamper"]))
    if not rep.get("audio", {}).get("has_audio", True):
        # audio NEVER blocks (spec has no audio requirement; R-round-3)
        advisories.append("no audio track — warn note only, never blocks")

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
        V.probe(work_dir / "video.mp4")
    except Exception as e:
        res = MapResult(bin=2, hold_vlm=False, engine_verdict="",
                        reasons=[_reason(
                            "STR_VIDEO_UNREADABLE", True, True, {},
                            f"ffprobe cannot read video.mp4: {e}")])
        _write_verdict(dossier_dir, work_dir.name, res)
        return res

    eng = load_engine()
    gem = None
    if gemini_key and not skip_vlm:
        gem = eng.Gemini(gemini_key, gemini_model or "gemini-3.7-flash")
    raw_dir = work_dir / "raw"
    raw_by_sid = {}
    if (raw_dir / "metadata.json").exists() and \
            (raw_dir / "inputs.jsonl").exists():
        raw_by_sid[work_dir.name] = raw_dir

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
    aux["has_raw"] = bool(raw_by_sid)
    aux["vlm_required"] = True

    res = map_reasons(rep, aux, expected_game)
    _write_verdict(dossier_dir, work_dir.name, res)
    _archive_analysis(work_dir, dossier_dir)
    return res


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


def _build_aux(work_dir: Path, rep: dict, gem, *, gemini_key: str,
               gemini_model: str, vlm_expected: bool) -> dict:
    """Scanner timeline + AFK + refined windows + notif/chat confirmation."""
    aux: dict = {"vlm_extra_failed": False}
    video = work_dir / "video.mp4"
    try:
        ts_ms, active, _has_action, tamper = _read_rows(
            work_dir / "frames.csv")
    except Exception as e:
        ts_ms, active, tamper = [], [], None
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
        # losing the scanner loses the 2s-rule precision, AFK and
        # black-frozen checks — never a silent pass (F5): hold when the
        # session was supposed to get the full battery
        aux.setdefault("notes", []).append(
            "scanner unavailable — AFK/black-frozen/2s-precision not run")
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
                                "baseline": round(baseline, 3)}

        # black/frozen whole-clip detection
        if tl.luma:
            black_frac = sum(1 for v in tl.luma if v < 16) / len(tl.luma)
            if black_frac > 0.5:
                aux["black_frozen"] = True
                aux["black_frozen_evidence"] = (
                    f"{black_frac:.0%} of frames are near-black")
            elif baseline < 0.3 and tl.duration_s > 30:
                aux["black_frozen"] = True
                aux["black_frozen_evidence"] = (
                    f"whole-clip motion baseline {baseline:.2f} — screen "
                    f"essentially frozen")

        # 1-frame-accurate bounds for the engine's gating windows
        refined: dict = {}
        engine_windows = [w for w in vlm_rep.get("windows", [])
                          if w.get("gating")]
        for w in engine_windows:
            r = scanner.refine_window(tl, w["t0"], w["t1"],
                                      ratio=C.STILLNESS_FROZEN_BELOW,
                                      baseline=baseline)
            if r:
                refined[(w["t0"], w["t1"])] = r
        aux["refined"] = refined

        def _overlaps_engine(t0: float, t1: float) -> bool:
            return any(t0 < w["t1"] + 1.0 and t1 > w["t0"] - 1.0
                       for w in engine_windows)

        # static candidates the 4s VLM sweep can miss entirely (a 2s pause
        # between samples) — the whole reason the scanner exists (§10.3)
        statics = scanner.static_windows(tl, ratio=C.STILLNESS_FROZEN_BELOW,
                                         baseline=baseline, min_s=0.8)
        statics = [(a, b) for a, b in statics
                   if not _overlaps_engine(a, b)
                   and a > 1.0 and b < tl.duration_s - 1.0]

        # AFK: >30s zero input + near-static (OW dialogue/map/reading are
        # gameplay — VLM label check below removes those)
        if ts_ms and active:
            for a, b in scanner.zero_input_runs(ts_ms, active, C.AFK_MIN_S):
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
                    if lab.get("label") in ("menu", "loading", "pause",
                                            "scoreboard", "cutscene",
                                            "other_non_gameplay") and \
                            lab.get("conf") in ("high", "medium"):
                        lo = tl.frame_at(a)
                        hi = tl.frame_at(b)
                        af = sum(1 for i in range(lo, min(hi, len(active)))
                                 if i < len(active) and active[i])
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
                    # a MISSING VLM reply must never confirm a cut of real
                    # content — require an actual non-dialogue/map label
                    if lab.get("label") and \
                            lab["label"] not in ("dialogue", "map"):
                        afk_windows.append((a, b))
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
