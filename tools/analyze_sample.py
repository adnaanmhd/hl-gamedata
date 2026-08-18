#!/usr/bin/env python3
"""Automated sample analysis — SAMPLE_ANALYSIS_PLAYBOOK.md (§0–§8) as one command.

Scope: **delivered v2 session directories only** (video.mp4 + frames.csv +
session.json + session.rrd + rrd_creation.py, no key_binding.json). Raw
bundles and v1 deliveries are detected and rejected with the command to run
instead of this one.

Per session (playbook section in brackets):
  [§0] format detection + game-identity checks (session_id slug vs game_title
       vs what the video actually looks like)
  [§1] structural QA via translator qa-v2 (check_session_v2) — includes
       frame-sync vs real PTS and the client's controls-to-video grounding —
       plus the spec-strict §1.5.2 tail nit that qa-v2 relaxes to 4 intervals
  [§2] content inventory: keys/actions/buttons, modality presence, dropped-
       frame (irregular-interval) count, OS-key pollution, L+R modifier bleed
  [§3] lag numbers are read from the qa-v2 grounding result (no second
       optical-flow pass)
  [§4] ffprobe stream summary + audio presence/level
  [§5] Gemini VLM sweep: frames sampled every --vlm-interval seconds, label
       changes refined at --refine-step, classifying gameplay / menu / loading
       / pause / scoreboard / cutscene / dialogue / map / other, plus
       notification overlays, combat evidence (mouse-button cross-check) and
       a game-identity guess. Inputs during detected non-gameplay windows are
       cross-referenced against frames.csv. Review artifacts are rendered:
       contact sheet, first/last-frame strip, a filmstrip per flagged window,
       full-res corner crops for notification flags.
  [§6] confidence-tiered verdict: HIGH-confidence detections gate the
       verdict (exceptions that stay advisory: clean mid-clip windows under
       5s, and frozen-context cases where frame stillness could not be
       measured); LOW-confidence ones are advisory with artifacts for a
       human eyeball. Fixes are recommended with exact commands, never
       applied.
  [§8] report.md + report.json in a sibling `<session>-analysis/` directory;
       with >1 session also a batch report (feedback-compliance table) in the
       sessions' common parent.

Verdict → exit code: 0 deliverable · 1 fix-in-post · 2 re-record · 3 error.
A batch exits with the worst verdict.

VLM: reads GEMINI_API_KEY from the environment (never hardcode a key here).
Model defaults to gemini-3.7-flash; override with --model or GEMINI_MODEL.
Without a key (or with --skip-vlm) the video-content checks are marked NOT
RUN and the verdict carries "pending manual video review".

Usage:
  uv run --with numpy --with opencv-python-headless \
      python tools/analyze_sample.py <session_dir> [...] [--raw-root DIR]
      [--model NAME] [--skip-vlm] [--vlm-interval 4] [--refine-step 1]
"""
from __future__ import annotations

import argparse
import base64
import bisect
import collections
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from translator import sync                                    # noqa: E402
from translator.v2 import (check_session_v2,                   # noqa: E402
                           _applied_shift_us)

# ---------------------------------------------------------------- constants

API_KEY_ENV = "GEMINI_API_KEY"
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
GENLANG_BASE = "https://generativelanguage.googleapis.com/v1beta"
VERTEX_BASE = "https://aiplatform.googleapis.com/v1/publishers/google"
VLM_BATCH = 8            # frames per generateContent request
VLM_JPEG_WIDTH = 640     # downscale before sending
VLM_TIMEOUT_S = 180

LABELS = ("gameplay", "menu", "loading", "pause", "scoreboard", "cutscene",
          "dialogue", "map", "other_non_gameplay")
GATING_LABELS = {"menu", "loading", "pause", "scoreboard", "cutscene",
                 "other_non_gameplay"}
SOFT_LABELS = {"dialogue", "map"}    # gameplay-adjacent: never gate

SEVERITY = {"deliverable": 0, "fix in post": 1, "re-record": 2, "error": 3}

UV_PRE = "PYTHONPATH=. uv run --with numpy --with opencv-python-headless"


# ---------------------------------------------------------------- helpers

def _run(cmd: list[str], timeout_s: int = 600, **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            cmd, 124, "", f"timed out after {timeout_s}s: {' '.join(cmd[:3])}")
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            cmd, 127, "", f"{cmd[0]} not found on PATH — install ffmpeg/ffprobe")


def _norm_game(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


@dataclass
class Finding:
    check: str                    # machine tag, e.g. "non_gameplay_window"
    tier: str                     # "high" | "low" | "info" | "capture-side"
    message: str
    t_start_s: float | None = None
    t_end_s: float | None = None
    fix: str | None = None        # recommended command, when fix-in-post
    artifact: str | None = None   # relative path of the rendered evidence


@dataclass
class SessionAnalysis:
    session_dir: str
    out_dir: str
    format: str = "unknown"
    game_title: str = ""
    id_slug: str = ""
    vlm_game_guess: str = ""
    frames: int = 0
    fps: float = 0.0
    duration_s: float = 0.0
    qa_status: str = "NOT RUN"
    qa_issues: list = field(default_factory=list)
    tail_nit: str = ""
    inventory: dict = field(default_factory=dict)
    lag: dict = field(default_factory=dict)
    video_probe: dict = field(default_factory=dict)
    audio: dict = field(default_factory=dict)
    vlm: dict = field(default_factory=dict)     # samples, windows, flags
    findings: list = field(default_factory=list)
    verdict: str = "error"
    verdict_reasons: list = field(default_factory=list)
    pending_human: list = field(default_factory=list)
    error: str = ""
    # "host" when `error` came from an OSError: the string laundering hid
    # the exception type from run.py's host/crash split, turning transient
    # I/O errors into terminal quarantines (r-loop 9 #15)
    error_kind: str = ""


# ---------------------------------------------------------------- §0 format

def detect_format(sdir: Path) -> str:
    if (sdir / "inputs.jsonl").exists() and (sdir / "metadata.json").exists():
        return "raw"
    sj = sdir / "session.json"
    if not sj.exists():
        return "unknown"
    try:
        s = json.loads(sj.read_text())
    except Exception:
        return "unknown"
    if (sdir / "key_binding.json").exists() or "canonical" in s:
        return "v1"
    if "game_title" in s and (sdir / "frames.csv").exists():
        return "v2"
    return "unknown"


def id_slug(session_id: str) -> str:
    m = re.search(r"Z_(.+?)_c_[0-9a-f]+$", session_id or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- §2 inventory

_OS_KEY_PAT = re.compile(
    r"win|cmd|meta|media|volume|mute|print|caps.?lock|num.?lock|scroll.?lock|"
    r"apps|sleep|browser|launch|insert|menu|snapshot|sys.?req|prt.?sc|break",
    re.I)
_FKEY_PAT = re.compile(r"^f([1-9]|1[0-9]|2[0-4])$", re.I)


def _mod_side(tok: str):
    """('shift'|'ctrl'|'alt', 'l'|'r') for a modifier token, else None."""
    t = tok.lower()
    for base in ("shift", "ctrl", "control", "alt"):
        if base in t:
            b = "ctrl" if base == "control" else base
            if t.startswith("l") or t.endswith("_l") or "left" in t:
                return (b, "l")
            if t.startswith("r") or t.endswith("_r") or "right" in t:
                return (b, "r")
            return (b, "?")
    return None


def inventory(rows, col, fps: float, duration_ms: int) -> dict:
    keys = collections.Counter()
    acts = collections.Counter()
    btns = collections.Counter()
    kf = bf = mot = kna = 0
    bleed_frames = 0
    bleed_example = ""
    os_keys = collections.Counter()
    # Guarded, and float-tolerant like pipeline/validate.py:728 already is
    # (r-loop 6). check_session_v2 wraps this exact cast and emits
    # "FAIL: timestamp_ms column unparseable", which maps to STR_TS_NONMONO
    # -- blocking but FIXABLE. The wrapper then re-derived the same column
    # with a bare int() and raised, so a session the checker had just given
    # a one-attempt repairable verdict became QUARANTINED "validation
    # crashed": terminal, manual queue, media held 48h. r-loop 5 guarded
    # the reads around this one and left the cast strict.
    ts = []
    ts_unparseable = 0
    for r in rows:
        try:
            ts.append(int(float(r[col["timestamp_ms"]])))
        except (TypeError, ValueError, IndexError):
            ts_unparseable += 1
    for r in rows:
        ks = [t for t in (r[col["input_keys"]] or "").split("|") if t]
        al = [a for a in (r[col["input_actions"]] or "").split("|") if a]
        bs = [b for b in (r[col["input_mouse_buttons"]] or "").split("|") if b]
        for t in ks:
            keys[t] += 1
            if _OS_KEY_PAT.search(t) or _FKEY_PAT.match(t):
                os_keys[t] += 1
        for a in al:
            acts[a] += 1
        for b in bs:
            btns[b] += 1
        kf += bool(ks)
        bf += bool(bs)
        kna += bool(ks and not al)
        dx, dy = r[col["input_mouse_dx"]], r[col["input_mouse_dy"]]
        mot += (dx not in ("", "0.0", "0") or dy not in ("", "0.0", "0"))
        sides = {}
        for t in ks:
            ms = _mod_side(t)
            if ms:
                sides.setdefault(ms[0], set()).add(ms[1])
        for base, ss in sides.items():
            if {"l", "r"} <= ss:
                bleed_frames += 1
                bleed_example = bleed_example or \
                    f"frame {r[col['frame_id']]}: L+R {base} together"
                break
    dts = [b - a for a, b in zip(ts, ts[1:])]
    med = sorted(dts)[len(dts) // 2] if dts else 0
    irregular = [(i, d) for i, d in enumerate(dts) if med and abs(d - med) > 0.2 * med]
    frame_iv = 1000.0 / fps if fps else 0.0
    return {
        "rows": len(rows), "key_frames": kf, "btn_frames": bf,
        "motion_frames": mot, "keys_no_action": kna,
        "keys": dict(keys.most_common()), "buttons": dict(btns.most_common()),
        "actions": dict(acts.most_common()),
        "distinct_actions": len(acts),
        "os_keys": dict(os_keys), "bleed_frames": bleed_frames,
        "bleed_example": bleed_example,
        "median_dt_ms": med, "irregular_intervals": len(irregular),
        "irregular_pct": round(100.0 * len(irregular) / len(dts), 1) if dts else 0.0,
        "ts_unparseable": ts_unparseable,
        "ts_last_ms": ts[-1] if ts else None,
        "tail_gap_ms": abs(ts[-1] - duration_ms) if ts else None,
        "frame_interval_ms": frame_iv,
        "timestamps_ms": ts,
    }


# ---------------------------------------------------------------- §4 probe

def probe_streams(video: Path) -> dict:
    p = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
              "-of", "json", str(video)])
    if p.returncode != 0:
        return {"error": p.stderr.strip()[:300]}
    d = json.loads(p.stdout)
    out = {"streams": []}
    for s in d.get("streams", []):
        out["streams"].append({k: s.get(k) for k in
                               ("codec_type", "codec_name", "width", "height",
                                "avg_frame_rate", "nb_frames", "sample_rate",
                                "channels")})
    out["has_audio"] = any(s["codec_type"] == "audio" for s in out["streams"])
    return out


def audio_levels(video: Path) -> dict:
    p = _run(["ffmpeg", "-i", str(video), "-map", "0:a:0", "-af",
              "volumedetect", "-f", "null", "-"])
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", p.stderr)
    mx = re.search(r"max_volume:\s*(-?[\d.]+) dB", p.stderr)
    return {"mean_db": float(mean.group(1)) if mean else None,
            "max_db": float(mx.group(1)) if mx else None}


# ---------------------------------------------------------------- artifacts

def contact_sheet(video: Path, outdir: Path) -> list[str]:
    vf = ("fps=1/8,drawtext=text='%{pts\\:hms}':fontcolor=yellow:fontsize=28:"
          "x=8:y=8:box=1:boxcolor=black@0.6,scale=256:-1,tile=6x4")
    pat = outdir / "sheet_%02d.jpg"
    p = _run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", vf,
              str(pat), "-y"])
    if p.returncode != 0:   # drawtext can fail without a usable font
        vf = "fps=1/8,scale=256:-1,tile=6x4"
        _run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", vf,
              str(pat), "-y"])
    return sorted(f.name for f in outdir.glob("sheet_*.jpg"))


def edges_strip(video: Path, n_frames: int, outdir: Path) -> str | None:
    sel = f"select='lt(n\\,3)+gt(n\\,{n_frames - 4})',scale=320:180,tile=6x1"
    out = outdir / "edges.png"
    p = _run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", sel,
              "-vsync", "0", "-frames:v", "1", str(out), "-y"])
    return out.name if p.returncode == 0 and out.exists() else None


class FrameGrabber:
    def __init__(self, video: Path):
        import cv2
        self._cv2 = cv2
        self.cap = cv2.VideoCapture(str(video))

    def opened(self) -> bool:
        return bool(self.cap.isOpened())

    def at(self, t_s: float):
        self.cap.set(self._cv2.CAP_PROP_POS_MSEC, max(t_s, 0.0) * 1000.0)
        ok, frame = self.cap.read()
        return frame if ok else None

    def jpeg(self, t_s: float, width: int = VLM_JPEG_WIDTH) -> bytes | None:
        frame = self.at(t_s)
        if frame is None:
            return None
        cv2 = self._cv2
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, max(int(h * width / w), 8)))
        ok, buf = cv2.imencode(".jpg", frame,
                               [cv2.IMWRITE_JPEG_QUALITY, 82])
        return buf.tobytes() if ok else None

    def filmstrip(self, t0: float, t1: float, outpath: Path, n: int = 6):
        cv2 = self._cv2
        tiles = []
        span = max(t1 - t0, 0.2)
        for i in range(n):
            t = t0 + span * (i + 0.5) / n
            fr = self.at(t)
            if fr is None:
                continue
            h, w = fr.shape[:2]
            fr = cv2.resize(fr, (int(w * 180 / h), 180))
            cv2.putText(fr, f"{t:7.1f}s", (6, 20), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 255), 2)
            tiles.append(fr)
        if not tiles:
            return None
        import numpy as np
        strip = np.hstack(tiles)
        cv2.imwrite(str(outpath), strip)
        return outpath.name

    def motion_between(self, t_s: float, dt: float = 0.25):
        """Mean abs gray diff between frames t and t+dt (160x90 downscale)."""
        cv2 = self._cv2
        f1, f2 = self.at(t_s), self.at(t_s + dt)
        if f1 is None or f2 is None:
            return None
        import numpy as np
        g1 = cv2.cvtColor(cv2.resize(f1, (160, 90)), cv2.COLOR_BGR2GRAY)
        g2 = cv2.cvtColor(cv2.resize(f2, (160, 90)), cv2.COLOR_BGR2GRAY)
        return float(np.mean(cv2.absdiff(g1, g2)))

    def corner_crop(self, t_s: float, outpath: Path):
        """Full-res bottom-right region (Steam-toast territory)."""
        fr = self.at(t_s)
        if fr is None:
            return None
        h, w = fr.shape[:2]
        crop = fr[int(h * 0.60):, int(w * 0.55):]
        self._cv2.putText(crop, f"{t_s:.1f}s", (6, 24),
                          self._cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        self._cv2.imwrite(str(outpath), crop)
        return outpath.name

    def close(self):
        self.cap.release()


# ---------------------------------------------------------------- §5 Gemini

class GeminiError(Exception):
    pass


class Gemini:
    """Minimal REST client (urllib only). Tries the Gemini API endpoint
    first, then Vertex AI express mode; sticks with whichever answers."""

    def __init__(self, api_key: str, model: str):
        self.key = api_key
        self.model = model
        self._which = None      # 0 = generativelanguage, 1 = vertex express
        self.requests = 0

    def _endpoints(self):
        return [
            (f"{GENLANG_BASE}/models/{self.model}:generateContent",
             {"x-goog-api-key": self.key}),
            (f"{VERTEX_BASE}/models/{self.model}:generateContent?key={self.key}",
             {}),
        ]

    def list_models(self) -> list[str]:
        req = urllib.request.Request(
            f"{GENLANG_BASE}/models?pageSize=1000",
            headers={"x-goog-api-key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
            return [m["name"].split("/")[-1] for m in d.get("models", [])]
        except Exception:
            return []

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=VLM_TIMEOUT_S) as r:
            return json.load(r)

    def generate(self, parts: list[dict]) -> str:
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": 0.0,
                                     "responseMimeType": "application/json"}}
        eps = self._endpoints()
        order = ([self._which, 1 - self._which]
                 if self._which is not None else [0, 1])
        last = None
        for idx in order:
            url, headers = eps[idx]
            for attempt in range(3):
                try:
                    resp = self._post(url, headers, body)
                except urllib.error.HTTPError as e:
                    detail = ""
                    try:
                        detail = e.read().decode()[:400]
                    except Exception:
                        pass
                    last = f"HTTP {e.code} at {url.split('?')[0]}: {detail}"
                    if e.code in (401, 403, 404):
                        break                        # try other endpoint
                    if e.code in (429, 500, 502, 503) and attempt < 2:
                        time.sleep((2, 5)[attempt])
                        continue
                    break
                except (urllib.error.URLError, TimeoutError, OSError,
                        json.JSONDecodeError) as e:
                    last = f"network error at {url.split('?')[0]}: {e}"
                    if attempt < 2:
                        time.sleep((2, 5)[attempt])
                    continue
                try:
                    self._which = idx
                    self.requests += 1
                    return "".join(
                        p.get("text", "")
                        for p in resp["candidates"][0]["content"]["parts"])
                except (KeyError, IndexError, TypeError):
                    raise GeminiError(
                        f"response carries no content (safety block?): "
                        f"{json.dumps(resp)[:300]}")
        if "HTTP 404" in (last or ""):
            avail = [m for m in self.list_models() if "flash" in m]
            raise GeminiError(f"model '{self.model}' not served ({last}); "
                              f"flash models visible to this key: {avail}")
        raise GeminiError(last or "no endpoint answered")


def _as_bool(v) -> bool:
    """JSON-sloppy bool: the model sometimes emits \"true\"/\"false\" strings,
    and bool(\"false\") would read as True."""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1")


def _vlm_prompt(game_title: str, n: int) -> str:
    return f"""You are auditing {n} frames sampled from a PC first-person gameplay \
recording (claimed game: "{game_title}"). For EVERY frame, output one JSON object; \
respond with ONLY a JSON array, no prose. Fields:
"index": int — the index in the text tag right before that image.
"label": one of {list(LABELS)}.
  gameplay = live in-game action. HUD, crosshair, kill feed, viewmodel, and
    in-game chat are all still gameplay.
  menu = full-screen menu/settings/lobby, typically with a mouse cursor.
  loading = loading screen, progress bar, or plain transition card.
  pause = pause overlay over the frozen game.
  scoreboard = match-end or Tab/death scoreboard dominating the screen
    (use it even when translucent gameplay continues behind it).
  cutscene = non-interactive cinematic.
  dialogue = in-game interactive dialogue/text box (part of play).
  map = full-screen in-game map/ship log/computer screen (part of play).
  other_non_gameplay = desktop, another app, corrupted frame, anything else.
"confidence": "high" | "medium" | "low".
"notification_overlay": true ONLY for an OS/launcher popup burned into the frame
  (Steam friend toast, Discord/Windows notification — usually a corner card).
  Kill feeds or game chat in the same corner are NOT notifications → false.
"combat_evidence": true if the frame shows the player firing/attacking
  (muzzle flash, weapon discharge, hit marker, melee swing mid-motion).
"visible_chat": true if typed player-chat text is readable anywhere on screen.
"game_guess": best guess which game this actually is, or "unknown".
"note": why, in 12 words or fewer."""


def classify_frames(gem: Gemini, grabber: FrameGrabber, game_title: str,
                    ts: list[float]) -> list[dict]:
    """Classify frames at the given times; returns sample dicts sorted by t."""
    samples = []
    todo = [(t, grabber.jpeg(t)) for t in ts]
    todo = [(t, j) for t, j in todo if j is not None]
    for i in range(0, len(todo), VLM_BATCH):
        chunk = todo[i:i + VLM_BATCH]
        parts = [{"text": _vlm_prompt(game_title, len(chunk))}]
        for k, (t, jpg) in enumerate(chunk):
            parts.append({"text": f"FRAME index={k} t={t:.1f}s"})
            parts.append({"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(jpg).decode()}})
        for attempt in (0, 1):
            text = gem.generate(parts)
            try:
                arr = json.loads(re.sub(r"^```(json)?|```$", "",
                                        text.strip(), flags=re.M))
                break
            except json.JSONDecodeError:
                if attempt:
                    raise GeminiError(f"unparseable VLM reply: {text[:200]}")
        if isinstance(arr, dict):     # tolerate {"frames": [...]} or one obj
            arr = arr["frames"] if isinstance(arr.get("frames"), list) \
                else [arr]
        by_idx = {}
        for o in arr:
            if not isinstance(o, dict):
                continue
            try:
                by_idx[int(o.get("index"))] = o
            except (TypeError, ValueError):
                continue
        for k, (t, _) in enumerate(chunk):
            o = by_idx.get(k)
            if not o:
                continue
            lab = o.get("label", "gameplay")
            conf = str(o.get("confidence", "low")).strip().lower()
            samples.append({
                "t": round(t, 2),
                "label": lab if lab in LABELS else "other_non_gameplay",
                "conf": conf if conf in ("high", "medium", "low") else "low",
                "notif": _as_bool(o.get("notification_overlay")),
                "combat": _as_bool(o.get("combat_evidence")),
                "chat": _as_bool(o.get("visible_chat")),
                "guess": str(o.get("game_guess", "unknown")),
                "note": str(o.get("note", ""))[:120],
            })
    samples.sort(key=lambda s: s["t"])
    return samples


def vlm_sweep(gem: Gemini, grabber: FrameGrabber, game_title: str,
              duration_s: float, interval: float, refine_step: float) -> dict:
    """Baseline sweep + boundary refinement. Returns samples + windows."""
    base = [0.3]
    t = 2.0
    while t < duration_s - 0.6:
        base.append(round(t, 1))
        t += interval
    base.append(max(duration_s - 0.5, 0.3))
    base = sorted(set(base))
    samples = classify_frames(gem, grabber, game_title, base)

    refine: list[float] = []
    for a, b in zip(samples, samples[1:]):
        if (a["label"] != b["label"] or a["notif"] != b["notif"]) \
                and (b["t"] - a["t"]) > refine_step * 1.5:
            x = a["t"] + refine_step
            while x < b["t"] - refine_step / 2:
                refine.append(round(x, 1))
                x += refine_step
    if refine:
        samples = sorted(samples + classify_frames(gem, grabber, game_title,
                                                   refine),
                         key=lambda s: s["t"])

    windows = _windows(samples, duration_s)
    return {"model": gem.model, "requests": gem.requests,
            "baseline_interval_s": interval, "refine_step_s": refine_step,
            "samples": samples, "windows": windows,
            "notif_ts": [s["t"] for s in samples if s["notif"]],
            "chat_ts": [s["t"] for s in samples if s.get("chat")],
            "combat_ts": [s["t"] for s in samples
                          if s["combat"] and s["conf"] == "high"],
            "game_votes": dict(collections.Counter(
                s["guess"] for s in samples
                if s["guess"].lower() not in ("", "unknown")))}


def _windows(samples: list[dict], duration_s: float) -> list[dict]:
    """Group consecutive non-gameplay samples into timed windows."""
    wins = []
    run: list[dict] = []

    def flush(prev, nxt):
        if not run:
            return
        t0 = 0.0 if prev is None else (prev["t"] + run[0]["t"]) / 2
        t1 = duration_s if nxt is None else (run[-1]["t"] + nxt["t"]) / 2
        labels = list(dict.fromkeys(s["label"] for s in run))
        gating = any(l in GATING_LABELS for l in run_labels(run))
        tier = ("high" if gating and len(run) >= 2
                and all(s["conf"] == "high" for s in run) else "low")
        wins.append({"t0": round(t0, 2), "t1": round(t1, 2),
                     "labels": labels, "n_samples": len(run),
                     "sample_ts": [s["t"] for s in run],
                     "gating": gating, "tier": tier if gating else "low",
                     "notes": [s["note"] for s in run[:3]]})

    def run_labels(r):
        return [s["label"] for s in r]

    prev = None
    for i, s in enumerate(samples):
        if s["label"] != "gameplay":
            if not run:
                prev = samples[i - 1] if i else None
            run.append(s)
        else:
            flush(prev, s)
            run = []
    flush(prev, None)
    return wins


STILLNESS_FROZEN_BELOW = 0.4    # window motion / gameplay baseline


def stillness_ratio(grabber: FrameGrabber, w: dict,
                    gameplay_ts: list[float]) -> float | None:
    """How much the frames inside a window still move, relative to this
    session's own live-gameplay baseline. Near-zero = the game is frozen
    (pause / loading / static menu); high = the game keeps rendering under a
    translucent overlay (e.g. a respawn scoreboard), where inputs are
    legitimate. Deterministic — stabilizes the frozen-context gate against
    VLM confidence drift."""
    # Probe at the VLM sample times themselves (NOT the midpoint-padded
    # t0/t1 span, whose margins reach into neighboring live gameplay).
    win_ts = w.get("sample_ts") or []
    if len(win_ts) > 5:
        win_ts = win_ts[::max(len(win_ts) // 5, 1)][:5]
    if not win_ts:
        span = w["t1"] - w["t0"]
        n = min(5, max(2, int(span)))
        win_ts = [w["t0"] + span * (i + 0.5) / n for i in range(n)]

    lo, hi = min(win_ts), max(win_ts)

    def probe_bounded(t):
        """Probe WITHOUT reaching past the window's sample span — a forward
        probe at the last sample would otherwise diff against resumed
        gameplay and fake motion inside a frozen window."""
        if hi - t >= 0.2:
            m = grabber.motion_between(t, min(0.25, hi - t))
            if m == 0.0 and hi - t >= 0.45:   # dropped-frame gap: same
                m2 = grabber.motion_between(t, min(0.5, hi - t))  # frame twice
                m = max(m, m2) if m2 is not None else m
        elif t - lo >= 0.2:
            m = grabber.motion_between(max(t - 0.25, lo),
                                       min(0.25, t - lo))
        else:                                 # single-sample window: probe
            m = grabber.motion_between(t - 0.12, 0.24)   # tightly around it
        return m

    def probe_baseline(t):
        m = grabber.motion_between(t)
        if m == 0.0:
            m2 = grabber.motion_between(t, 0.5)
            m = max(m, m2) if m2 is not None else m
        return m

    wm = [m for t in win_ts if (m := probe_bounded(t)) is not None]
    bm = [m for t in gameplay_ts[:6] if (m := probe_baseline(t)) is not None]
    if not wm or not bm:
        return None
    base = sum(bm) / len(bm)
    if base < 0.5:     # baseline itself near-static: ratio meaningless
        return None
    return round((sum(wm) / len(wm)) / base, 2)


# ------------------------------------------------ cross-refs (§5.2 / §5.3)

def rows_in_window(inv: dict, rows, col, t0: float, t1: float) -> dict:
    ts = inv["timestamps_ms"]
    lo = bisect.bisect_left(ts, int(t0 * 1000))
    hi = bisect.bisect_right(ts, int(t1 * 1000))
    acts = collections.Counter()
    action_frames = 0
    for r in rows[lo:hi]:
        al = [a for a in (r[col["input_actions"]] or "").split("|") if a]
        if al:
            action_frames += 1
        for a in al:
            acts[a] += 1
    return {"rows": hi - lo, "action_frames": action_frames,
            "actions": dict(acts)}


# ---------------------------------------------------------------- §6 verdict

def build_verdict(a: SessionAnalysis, vlm_ran: bool) -> None:
    reasons_re, reasons_fix, advisories = [], [], []
    inv = a.inventory

    # fix_actions_from_v2.py is a verbatim file-copy no-op for games outside
    # translator/context.py CONTEXT_GAMES — only recommend it where it works.
    if _norm_game(a.game_title) == "outer_wilds":
        fix_actions_cmd = (f"{UV_PRE} --with rerun-sdk python "
                           f"tools/fix_actions_from_v2.py {a.session_dir} "
                           f"--out out/")
        ctx_note = ""
    else:
        fix_actions_cmd = None
        ctx_note = (" [needs a context table for this game in "
                    "translator/context.py first — fix_actions_from_v2.py "
                    "is a no-op without one]")

    # --- structural QA
    for issue in a.qa_issues:
        if not issue.startswith("FAIL:"):
            continue
        msg = issue[5:].strip()
        if "mouse motion missing" in msg:
            reasons_re.append("mouse motion missing (dx/dy never non-zero) — "
                              "unrecoverable (locked rule): re-record")
        elif "frame-sync drift" in msg:
            reasons_fix.append(
                (f"frame-sync drift ({msg}) — re-bin from the raw bundle "
                 f"(PTS-aware) via translate-v2; needs raws",
                 f"{UV_PRE} --with rerun-sdk python -m translator "
                 f"translate-v2 <raw-bundle> --out out/"))
        elif "controls-to-video sync" in msg:
            reasons_fix.append(
                (f"controls-to-video lag over the 150ms hard gate ({msg}) — "
                 f"translate-v2 auto-corrects from raws; without raws adapt "
                 f"the tools/fix_sync_from_v1.py shift pattern",
                 f"{UV_PRE} --with rerun-sdk python -m translator "
                 f"translate-v2 <raw-bundle> --out out/"))
        elif "fan-out" in msg or "null input_actions" in msg:
            reasons_fix.append(
                (f"action-resolution defect ({msg}) — re-resolve actions "
                 f"with context gating from the delivered files{ctx_note}",
                 fix_actions_cmd))
        else:
            reasons_fix.append(
                (f"qa-v2 FAIL: {msg} — mechanical rebuild needed", None))

    # --- modalities (with the §5.3 video cross-check)
    if inv.get("key_frames") == 0:
        reasons_re.append("keyboard capture missing (zero key frames in "
                          f"{inv.get('rows')} rows) — implausible for real "
                          "gameplay; confirm on the contact sheet, then "
                          "re-record")
    if inv.get("btn_frames") == 0 and not any("mouse motion missing" in r
                                              for r in reasons_re):
        combat = a.vlm.get("combat_ts", []) if vlm_ran else []
        if len(combat) >= 2:
            reasons_re.append(
                f"mouse buttons missing but video shows firing at "
                f"{combat[:6]}s (08-12 defect class) — re-record + vendor "
                f"bug report")
        elif vlm_ran:
            n_frames = len(a.vlm.get("samples", []))
            seen = (f"1 high-confidence firing frame (at {combat[0]}s)"
                    if len(combat) == 1 else "no firing evidence")
            advisories.append(Finding(
                "buttons_absent", "low",
                f"no mouse-button events; VLM saw {seen} in {n_frames} "
                f"sampled frames — possibly genuine non-use (sources "
                f"disagree: qa-v2 WARNs, playbook re-records only a "
                f"CONFIRMED capture failure); confirm visually before "
                f"accepting"))
        else:
            advisories.append(Finding(
                "buttons_absent", "low",
                "no mouse-button events; video cross-check NOT RUN — "
                "eyeball the contact sheet for firing before accepting"))

    # --- VLM windows
    dur = a.duration_s
    for w in a.vlm.get("windows", []):
        if not w["gating"]:
            advisories.append(Finding(
                "soft_context_window", "info",
                f"{'+'.join(w['labels'])} {w['t0']}–{w['t1']}s "
                f"(gameplay-adjacent; no action needed)",
                w["t0"], w["t1"], artifact=w.get("artifact")))
            continue
        at_head = w["t0"] <= 1.0
        at_tail = w["t1"] >= dur - 1.0
        acts = w.get("inputs", {})
        desc = (f"non-gameplay [{'+'.join(w['labels'])}] "
                f"{w['t0']}–{w['t1']}s ({w['n_samples']} samples, "
                f"{w['tier']} confidence)")
        if w["tier"] == "high":
            if at_head and at_tail:
                reasons_re.append(
                    f"{desc} spans the ENTIRE clip — no gameplay to keep; "
                    f"re-record")
            elif at_head:
                remain = dur - (w["t1"] + 0.5)
                if remain < 70.0:
                    reasons_re.append(
                        f"{desc} at clip START — trimming it would leave "
                        f"{remain:.0f}s, under the 70s delivery minimum; "
                        f"re-record")
                else:
                    reasons_fix.append(
                        (f"{desc} at clip START — retrim head "
                         f"({remain:.0f}s of clip remains)",
                         f"PYTHONPATH=. uv run --with rerun-sdk python "
                         f"tools/retrim_v2_session.py {a.session_dir} "
                         f"--head-s {w['t1'] + 0.5:.1f}"))
            elif at_tail:
                reasons_fix.append(
                    (f"{desc} at clip END — retrim_v2_session.py is "
                     f"head-only today: extend it for tail cuts or "
                     f"re-translate from the raw bundle", None))
            elif acts.get("action_frames"):
                ratio = w.get("stillness_ratio")
                if ratio is not None and ratio >= STILLNESS_FROZEN_BELOW:
                    advisories.append(Finding(
                        "overlay_over_live_play", "low",
                        f"{desc} mid-clip, but frames keep moving (motion "
                        f"{ratio:.0%} of gameplay baseline) — game likely "
                        f"live under a translucent overlay (e.g. respawn "
                        f"scoreboard), so inputs inside are legitimate; "
                        f"confirm on the filmstrip",
                        w["t0"], w["t1"], artifact=w.get("artifact")))
                elif ratio is None:
                    advisories.append(Finding(
                        "frozen_context_actions", "low",
                        f"{desc} MID-CLIP with {acts['action_frames']} "
                        f"action frames inside, but stillness could not be "
                        f"measured — confirm on the filmstrip before gating",
                        w["t0"], w["t1"], artifact=w.get("artifact")))
                else:
                    reasons_fix.append(
                        (f"{desc} MID-CLIP, frames static (motion "
                         f"{ratio:.0%} of gameplay baseline) with "
                         f"{acts['action_frames']} frames of gameplay "
                         f"actions during it "
                         f"({sorted(acts.get('actions', {}))}) — the "
                         f"client's frozen-context complaint; context-gate "
                         f"the actions{ctx_note}",
                         fix_actions_cmd))
            elif (w["t1"] - w["t0"]) >= 5.0:
                reasons_fix.append(
                    (f"{desc} mid-clip for {w['t1'] - w['t0']:.0f}s with no "
                     f"inputs — fails §6 'content clean'; no mid-clip cut "
                     f"tool exists: decide a trim strategy or re-record",
                     None))
            else:
                advisories.append(Finding(
                    "mid_clip_non_gameplay", "high",
                    f"{desc} mid-clip (<5s), no inputs during it — content-"
                    f"cleanliness judgment call (no mid-clip cut tool)",
                    w["t0"], w["t1"], artifact=w.get("artifact")))
        else:
            advisories.append(Finding(
                "non_gameplay_window", "low",
                f"{desc} — low confidence; eyeball the filmstrip"
                + (f"; {acts['action_frames']} action frames inside"
                   if acts.get("action_frames") else ""),
                w["t0"], w["t1"], artifact=w.get("artifact")))

    # --- lag over the 50ms client target (still under the 150ms hard gate)
    m = re.search(r"video (\d+(?:\.\d+)?)ms", a.lag.get("summary", ""))
    if m and "within" in a.lag.get("summary", "") \
            and float(m.group(1)) > sync.TARGET_ABS_LAG_MS:
        reasons_fix.append(
            (f"controls-to-video lag {m.group(1)}ms exceeds the client's "
             f"{sync.TARGET_ABS_LAG_MS:.0f}ms target (hard gate is "
             f"{sync.MAX_ABS_LAG_MS:.0f}ms) — §6 requires within-target for "
             f"deliverable; translate-v2 corrects it down from raws",
             f"{UV_PRE} --with rerun-sdk python -m translator translate-v2 "
             f"<raw-bundle> --out out/"))

    # --- chat burned into video (§5.5 — privacy/cleanliness judgment)
    chat_ts = a.vlm.get("chat_ts", [])
    if chat_ts:
        advisories.append(Finding(
            "visible_chat", "low",
            f"chat text visible on screen near t={chat_ts[:8]}s — typed "
            f"letters are stripped from inputs, but the text itself is "
            f"burned into the video; privacy/cleanliness judgment call"))

    # --- notifications, identity, audio, misc (advisory / capture-side)
    for t in a.vlm.get("notif_ts", []):
        advisories.append(Finding(
            "notification_overlay", "low",
            f"possible desktop notification at {t}s — check the corner crop "
            f"(kill feeds sit in the same corner; June OW repro was real)",
            t, t))
    votes = a.vlm.get("game_votes", {})
    if votes:
        merged: dict[str, int] = {}
        for g, n in votes.items():
            k = _norm_game(g)
            merged[k] = merged.get(k, 0) + n
        top = max(merged, key=lambda k: merged[k])
        total = sum(merged.values())
        a.vlm_game_guess = top
        claimed = _norm_game(a.game_title)
        # separator-insensitive compare ('outerwilds' == 'outer_wilds')
        tc, cc = top.replace("_", ""), claimed.replace("_", "")
        mismatch = tc and cc and tc not in cc and cc not in tc
        n_frames = len(a.vlm.get("samples", [])) or 1
        if mismatch and merged[top] >= 8 and merged[top] / total >= 0.9 \
                and merged[top] / n_frames >= 0.5:
            reasons_fix.append(
                (f"WRONG GAME: video is '{top}' ({merged[top]}/{total} "
                 f"votes, unanimous-level) but session claims "
                 f"'{a.game_title}' — the 08-12 mislabeling repro. Actions "
                 f"were resolved with the wrong game's keybind, so the "
                 f"delivery is invalid as-is: verify exe_name in the raw "
                 f"metadata and re-translate under the correct game", None))
        elif mismatch:
            advisories.append(Finding(
                "game_identity", "low",
                f"video may be '{top}' ({merged[top]}/{total} votes) but "
                f"session claims '{a.game_title}' — verify exe_name with "
                f"the vendor"))
    if a.id_slug and _norm_game(a.game_title) and \
            a.id_slug != _norm_game(a.game_title):
        advisories.append(Finding(
            "game_identity", "low",
            f"session_id slug '{a.id_slug}' != game_title "
            f"'{a.game_title}' — flag to vendor"))
    if not a.audio.get("has_audio"):
        advisories.append(Finding(
            "audio_missing", "capture-side",
            "no audio track (June-era complaint) — capture-side, "
            "unfixable in post; flag to vendor"))
    elif a.audio.get("max_db") is not None and a.audio["max_db"] < -50.0:
        advisories.append(Finding(
            "audio_silent", "low",
            f"audio track present but near-silent "
            f"(max {a.audio['max_db']} dB) — confirm it carries real signal"))
    if inv.get("os_keys"):
        advisories.append(Finding(
            "os_key_pollution", "low",
            f"possible OS/system keys in input_keys: {inv['os_keys']} — "
            f"should be stripped unless genuinely bound"))
    if inv.get("bleed_frames"):
        advisories.append(Finding(
            "modifier_bleed", "low",
            f"L+R modifier bleed in {inv['bleed_frames']} frames "
            f"({inv['bleed_example']}) — drop the spurious side"))
    if inv.get("irregular_pct", 0) > 5.0:
        advisories.append(Finding(
            "frame_drops", "capture-side",
            f"{inv['irregular_intervals']} irregular frame intervals "
            f"({inv['irregular_pct']}% — old-tool territory; 08-10+ builds "
            f"show 0–1). Holes are unfixable in post; sync survives only "
            f"because timestamps are real PTS"))
    if a.tail_nit:
        advisories.append(Finding("spec_tail_nit", "info", a.tail_nit))
    if a.duration_s and a.duration_s < 70.0:
        reasons_re.append(
            f"clip {a.duration_s:.1f}s under the hard 70s delivery minimum "
            f"— unfixable in post; re-record")
        advisories.append(Finding(
            "clip_short", "capture-side",
            f"clip {a.duration_s:.1f}s under the 70s minimum — "
            f"capture-side"))

    # --- roll up
    a.findings.extend(advisories)
    if reasons_re:
        a.verdict = "re-record"
        a.verdict_reasons = reasons_re + [m for m, _ in reasons_fix]
    elif reasons_fix:
        a.verdict = "fix in post"
        a.verdict_reasons = [m for m, _ in reasons_fix]
        for m, cmd in reasons_fix:
            if cmd:
                a.findings.append(Finding("fix_command", "info", m, fix=cmd))
    else:
        a.verdict = "deliverable"
        low = [f for f in a.findings if f.tier == "low"]
        if low:
            a.verdict_reasons = [f"{len(low)} low-confidence flag(s) pending "
                                 f"visual confirmation"]
    if not vlm_ran:
        a.pending_human = [
            "§5 video-content review NOT RUN (no VLM) — use the contact "
            "sheet + edges strip: non-gameplay segments (start/end AND "
            "mid-clip), inputs during frozen contexts, desktop "
            "notifications, chat, modality evidence"]
        a.verdict += " (pending manual video review)"
    else:
        low = [f for f in a.findings if f.tier == "low"]
        if low:
            a.pending_human = [f"confirm {len(low)} low-confidence flag(s) "
                               f"against the rendered artifacts"]


# ---------------------------------------------------------------- reports

def _md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(
            str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def write_report(a: SessionAnalysis, out_dir: Path) -> None:
    inv = a.inventory
    lines = [f"# Sample analysis — {Path(a.session_dir).name}",
             "",
             f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
             f"by tools/analyze_sample.py (playbook: SAMPLE_ANALYSIS_PLAYBOOK.md)",
             "",
             f"## Verdict: **{a.verdict.upper()}**", ""]
    if a.error:
        lines.append(f"- {a.error}")
    for r in a.verdict_reasons:
        lines.append(f"- {r}")
    for f in a.findings:
        if f.check == "fix_command" and f.fix:
            lines += ["", "```bash", f.fix, "```"]
    if a.pending_human:
        lines += ["", "**Pending human review:**"]
        lines += [f"- {p}" for p in a.pending_human]

    lines += ["", "## Session (§8 header)", "",
              _md_table(["field", "value"], [
                  ["format vintage", a.format],
                  ["game_title", a.game_title],
                  ["session_id slug", a.id_slug],
                  ["VLM game guess", a.vlm_game_guess or "n/a"],
                  ["frames / fps / length",
                   f"{a.frames} / {a.fps:g} / {a.duration_s:.1f}s"],
                  ["dropped-frame intervals",
                   f"{inv.get('irregular_intervals')} "
                   f"({inv.get('irregular_pct')}%) median dt "
                   f"{inv.get('median_dt_ms')}ms"],
                  ["frame-sync (PTS)", a.lag.get("frame_sync", "see QA")],
                  ["controls-to-video lag", a.lag.get("summary", "not measured")],
                  ["audio",
                   ("yes" if a.audio.get("has_audio") else "MISSING") +
                   (f" (mean {a.audio.get('mean_db')} dB, max "
                    f"{a.audio.get('max_db')} dB)"
                    if a.audio.get("max_db") is not None else "")],
                  ["modalities",
                   f"keyboard {'yes' if inv.get('key_frames') else 'MISSING'}"
                   f" · motion {'yes' if inv.get('motion_frames') else 'MISSING'}"
                   f" · buttons {'yes' if inv.get('btn_frames') else 'MISSING'}"],
                  ["distinct actions", inv.get("distinct_actions")],
              ]), ""]

    lines += ["## Structural QA (qa-v2)", "", f"Status: **{a.qa_status}**", ""]
    lines += [f"- {i}" for i in a.qa_issues] or ["- clean"]

    lines += ["", "## Content inventory (§2)", "",
              f"- key-frames {inv.get('key_frames')} · btn-frames "
              f"{inv.get('btn_frames')} · motion-frames "
              f"{inv.get('motion_frames')} · keys-no-action "
              f"{inv.get('keys_no_action')}",
              f"- keys: `{inv.get('keys')}`",
              f"- buttons: `{inv.get('buttons') or 'NONE'}`",
              f"- actions: `{inv.get('actions')}`"]

    if a.vlm.get("samples"):
        lines += ["", "## Video content (§5, VLM sweep)", "",
                  f"Model {a.vlm['model']} · {len(a.vlm['samples'])} frames "
                  f"classified in {a.vlm['requests']} requests · baseline "
                  f"1/{a.vlm['baseline_interval_s']:g}s, refinement "
                  f"{a.vlm['refine_step_s']:g}s", ""]
        wrows = []
        for w in a.vlm.get("windows", []):
            acts = w.get("inputs", {})
            ratio = w.get("stillness_ratio")
            wrows.append([f"{w['t0']}–{w['t1']}s", "+".join(w["labels"]),
                          w["tier"], w["n_samples"],
                          acts.get("action_frames", 0),
                          f"{ratio:.0%}" if ratio is not None else "n/a",
                          w.get("artifact") or ""])
        if wrows:
            lines += [_md_table(["window", "labels", "tier", "samples",
                                 "action frames inside",
                                 "motion vs gameplay", "filmstrip"], wrows)]
        else:
            lines += ["No non-gameplay windows detected."]

    lines += ["", "## Findings", ""]
    frows = [[f.tier, f.check,
              (f"{f.t_start_s}–{f.t_end_s}s"
               if f.t_start_s is not None else ""),
              f.message] for f in a.findings]
    lines += [_md_table(["tier", "check", "time", "detail"], frows)
              if frows else "None."]

    caps = [f for f in a.findings if f.tier == "capture-side"]
    lines += ["", "## Capture-side flags for the vendor", ""]
    lines += [f"- {f.message}" for f in caps] or ["- none"]

    lines += ["", "## Artifacts", "",
              "Contact sheet(s), first/last strip, window filmstrips and "
              "notification corner crops are in `artifacts/`.", ""]

    (out_dir / "report.md").write_text("\n".join(lines))
    d = asdict(a)
    d["inventory"].pop("timestamps_ms", None)
    (out_dir / "report.json").write_text(json.dumps(d, indent=1))


FEEDBACK_ITEMS = [
    ("same-literal action fan-out (07-27 complaint)",
     lambda a: _fb_qa(a, "fan-out")),
    ("inputs during frozen contexts",
     lambda a: _fb_frozen(a)),
    ("controls-to-video lag (150ms hard / 50ms target)",
     lambda a: a.lag.get("summary", "not measured")),
    ("audio track present",
     lambda a: "yes" if a.audio.get("has_audio") else "MISSING"),
    ("desktop notifications burned in",
     lambda a: f"{len(a.vlm.get('notif_ts', []))} flag(s)"
     if a.vlm.get("samples") else "not checked"),
    ("dropped frames (irregular intervals)",
     lambda a: f"{a.inventory.get('irregular_intervals')} "
               f"({a.inventory.get('irregular_pct')}%)"),
    ("frame sync vs real PTS",
     lambda a: _fb_sync(a)),
    ("input modalities captured",
     lambda a: ("all present" if a.inventory.get("key_frames")
                and a.inventory.get("motion_frames")
                and a.inventory.get("btn_frames") else "MISSING modality")),
    ("game identity matches session id",
     lambda a: "mismatch flagged" if any(f.check == "game_identity"
                                         for f in a.findings) else "ok"),
]


def _fb_qa(a, needle):
    hits = [i for i in a.qa_issues if needle in i]
    if not hits:
        return "ok"
    return hits[0][:60] + "…" if len(hits[0]) > 60 else hits[0]


def _fb_sync(a, short=False):
    """frame-sync cell from the parsed qa result (a.lag['frame_sync']) —
    never 'ok' when the check could not run (unreadable PTS / structural)."""
    fs = a.lag.get("frame_sync") or "not checked"
    if fs.startswith("OK"):
        return "ok"
    return fs[:24] + "…" if short and len(fs) > 24 else fs[:80]


def _fb_frozen(a):
    if not a.vlm.get("samples"):
        return "not checked"
    n = sum(w.get("inputs", {}).get("action_frames", 0)
            for w in a.vlm.get("windows", []) if w.get("gating"))
    return f"{n} action frames in non-gameplay windows"


def write_batch_report(analyses: list[SessionAnalysis], parent: Path) -> None:
    rows = []
    for a in analyses:
        inv = a.inventory
        rows.append([
            Path(a.session_dir).name, a.frames, f"{a.duration_s:.0f}s",
            "yes" if inv.get("key_frames") else "MISSING",
            f"{'yes' if inv.get('motion_frames') else 'MISSING'}/"
            f"{'yes' if inv.get('btn_frames') else 'MISSING'}",
            inv.get("distinct_actions"),
            inv.get("bleed_frames") or "-",
            _fb_sync(a, short=True),
            a.qa_status, f"**{a.verdict}**"])
    lines = [f"# Batch analysis — {len(analyses)} session(s)", "",
             f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
             "",
             _md_table(["session", "frames", "len", "keyboard",
                        "mouse (motion/btn)", "actions", "bleed", "sync",
                        "QA", "verdict"], rows),
             "", "## Feedback compliance (each past client complaint)", ""]
    for label, fn in FEEDBACK_ITEMS:
        cells = "; ".join(f"{Path(a.session_dir).name.split('_c_')[0]}: {fn(a)}"
                          for a in analyses)
        lines.append(f"- **{label}** — {cells}")
    per_game: dict[str, set] = {}
    for a in analyses:
        g = _norm_game(a.game_title) or "?"
        per_game.setdefault(g, set()).update(a.inventory.get("actions", {}))
    lines += ["", "## Per-game action coverage "
                  "(delivery requires ≥3 distinct per game)", ""]
    for g, acts_ in sorted(per_game.items()):
        flag = ("" if len(acts_) >= 3
                else " — **UNDER the ≥3-actions delivery requirement**")
        lines.append(f"- **{g}**: {len(acts_)} distinct action(s){flag}")

    caps = [(Path(a.session_dir).name, f.message)
            for a in analyses for f in a.findings if f.tier == "capture-side"]
    lines += ["", "## Capture-side flags for the vendor", ""]
    lines += [f"- `{s}`: {m}" for s, m in caps] or ["- none"]
    lines += ["", "## Only the user can decide", "",
              "- upload / delivery of the deliverable sessions",
              "- vendor comms on the capture-side flags above",
              "- any scope questions (e.g. a new game title appearing)"]
    (parent / "analysis_batch_report.md").write_text("\n".join(lines))
    (parent / "analysis_batch_report.json").write_text(json.dumps(
        [{k: v for k, v in asdict(a).items()
          if k not in ("inventory",)} | {"session": Path(a.session_dir).name}
         for a in analyses], indent=1, default=str))


# ---------------------------------------------------------------- pipeline

def frame_sync_line(r) -> str:
    """The report's frame-sync verdict, from a qa-v2 result.

    NOT a loose-needle search: the irregular-spacing WARN also mentions
    "REAL frame PTS". And "OK" is asserted from the checker's POSITIVE
    marker, never inferred from the absence of a complaint —
    check_session_v2 has nine early returns, and the old two-needle guess
    ("missing delivery file" / "header != v2 schema") recognised two of
    them. The other seven — session.json unreadable or not an object,
    frames.csv unreadable / empty / ragged, frame_id unparseable,
    timestamp_ms unparseable, and a key_binding-only failure — every one
    printed "OK (<=100ms vs real PTS)" for a check that never executed
    (r-loop 6). All of those are blocking FAILs, so nothing shipped
    unchecked; what shipped was a false OK in the report and the dossier.
    """
    drift = next((i for i in r.issues if "frame-sync drift" in i), None)
    if drift:
        return drift
    unverif = next((i for i in r.issues
                    if "cannot verify frame sync" in i), None)
    if unverif:
        return unverif
    if "frame_sync" in getattr(r, "checked", ()):
        return "OK (≤100ms vs real PTS)"
    return "not checked (QA stopped before the frame-sync check)"


def analyze(sdir: Path, raw_by_sid: dict, gem: Gemini | None,
            interval: float, refine_step: float) -> SessionAnalysis:
    out_dir = sdir.parent / f"{sdir.name}-analysis"
    art = out_dir / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    a = SessionAnalysis(session_dir=str(sdir), out_dir=str(out_dir))

    a.format = detect_format(sdir)
    if a.format != "v2":
        hint = {
            "raw": "raw capture bundle — run translate-v2 first "
                   "(CLAUDE.md autonomous task), then analyze the output",
            "v1": "obsolete v1 delivery — convert v1→v2 first "
                  "(mechanical; see playbook §6)",
            "unknown": "not a recognizable session directory",
        }[a.format]
        a.error = f"not a v2 delivery: {hint}"
        a.verdict = "error"
        write_report(a, out_dir)
        return a

    # Guarded, and NOT an early return (r-loop 5). These reads happen
    # BEFORE check_session_v2 below, so a raise here means the checker's
    # actionable verdict is never produced at all: the exception escapes
    # analyze() -> validate_session -> run.py wraps it -> the driver writes
    # QUARANTINED "validation crashed" and the session sits in the manual
    # queue holding its media for CONT_QUARANTINE_RECLAIM_H, instead of
    # taking the one-attempt FIX_SESSIONJSON_REWRITE the checker describes.
    # check_session_v2 already normalizes malformed numerics AFTER emitting
    # the type FAIL (translator/v2.py), so the r-loop-1/r-loop-3 "FAIL,
    # never crash" hardening bought nothing while these bare casts stood.
    try:
        s = json.loads((sdir / "session.json").read_text(
            encoding="utf-8", errors="replace"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        s = {}
    if not isinstance(s, dict):
        s = {}

    def _num(v, cast, default=0):
        """Same isinstance normalization translator/v2.py uses: a bool is
        not a number here, and a list/dict/None must not reach float()."""
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            return default
        try:
            return cast(v or 0)
        except (TypeError, ValueError):
            return default

    a.game_title = s.get("game_title", "") if isinstance(
        s.get("game_title"), str) else ""
    a.id_slug = id_slug(s.get("session_id", sdir.name)
                        if isinstance(s.get("session_id"), str)
                        else sdir.name)
    a.fps = _num(s.get("fps"), float, 0.0)
    a.duration_s = _num(s.get("duration_seconds"), float, 0.0)
    a.frames = _num(s.get("frame_count"), int, 0)

    # §1 structural QA (includes PTS frame-sync + controls-to-video grounding)
    raw = raw_by_sid.get(sdir.name)
    r = check_session_v2(sdir, raw_bundle=raw)
    a.qa_status, a.qa_issues = r.status, list(r.issues)
    for i in r.issues:                       # §3: parse the grounding line
        if "controls-to-video sync" in i:
            a.lag["summary"] = i.split("controls-to-video sync", 1)[1]\
                .lstrip(":— ").strip()
    a.lag["applied_shift_ms"] = _applied_shift_us(sdir) / 1000.0
    a.lag["frame_sync"] = frame_sync_line(r)

    # §2 inventory — guarded (r-loop 5). Unlike the session.json reads
    # above this one runs AFTER check_session_v2, so a.qa_issues already
    # carries the actionable FAILs and an early return preserves them;
    # the crash it replaces produced QUARANTINED "validation crashed".
    # translator/v2.py guards its own read of this file the same way.
    try:
        with (sdir / "frames.csv").open(newline="", encoding="utf-8",
                                        errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error, StopIteration) as e:
        if any(i.startswith("FAIL") for i in a.qa_issues):
            # check_session_v2 above already produced TYPED FAILs for
            # exactly this file (empty/unreadable frames.csv) whose
            # designed route is QA_FAIL_UNMAPPED -> FIX_RETRANSLATE when
            # sidecars exist (validate.py's own mapping comment). Setting
            # a.error here PREEMPTED map_reasons — the engine error raised
            # out of validate_session and terminally QUARANTINED a session
            # the pipeline can repair (r-loop 9 #15). Keep the qa verdict,
            # skip the inventory/VLM sections, return a normal analysis.
            a.qa_issues.append(
                f"WARN: inventory skipped (frames.csv unreadable: "
                f"{type(e).__name__}) — the structural FAILs above carry "
                f"the actionable story")
            build_verdict(a, vlm_ran=False)
            write_report(a, out_dir)
            return a
        a.error = f"frames.csv unreadable: {type(e).__name__}"
        if isinstance(e, OSError):
            a.error_kind = "host"
        a.verdict = "error"
        write_report(a, out_dir)
        return a
    col = {c: i for i, c in enumerate(header)}
    needed = {"frame_id", "timestamp_ms", "input_keys", "input_actions",
              "input_mouse_buttons", "input_mouse_dx", "input_mouse_dy"}
    if not needed <= set(col):
        a.error = "frames.csv missing input columns"
        a.verdict = "error"
        write_report(a, out_dir)
        return a
    # Ragged rows must not crash the wrapper either (r-loop 5). A single
    # corrupted delimiter merges two fields, so the row is SHORTER than the
    # header and inventory()'s positional reads raise IndexError -- again
    # escaping analyze() as "validation crashed" rather than the ragged-row
    # FAIL check_session_v2 already emits for exactly this file.
    _width = max(col.values()) + 1
    _ragged = [r for r in rows if len(r) < _width]
    if _ragged:
        rows = [r for r in rows if len(r) >= _width]
        a.qa_issues.append(f"WARN: {len(_ragged)} ragged row(s) skipped "
                           f"for the inventory (structural QA reports "
                           f"them as FAILs)")
    inv = inventory(rows, col, a.fps, _num(s.get("duration_ms"), int, 0))
    a.inventory = inv
    if inv["tail_gap_ms"] is not None and inv["frame_interval_ms"] and \
            inv["tail_gap_ms"] > inv["frame_interval_ms"]:
        a.tail_nit = (f"spec §1.5.2 strict: ts[-1] {inv['ts_last_ms']}ms is "
                      f"{inv['tail_gap_ms']}ms from duration_ms (spec wants "
                      f"≤1 frame interval; qa-v2 allows 4)")

    # §4 probe + audio
    a.video_probe = probe_streams(sdir / "video.mp4")
    a.audio = {"has_audio": a.video_probe.get("has_audio", False)}
    if a.audio["has_audio"]:
        a.audio.update(audio_levels(sdir / "video.mp4"))

    # artifacts always (they are the human fallback either way; ffmpeg-based,
    # so they work even without opencv)
    contact_sheet(sdir / "video.mp4", art)
    edges_strip(sdir / "video.mp4", len(rows), art)

    grabber = None
    try:
        grabber = FrameGrabber(sdir / "video.mp4")
        if not grabber.opened():
            grabber.close()
            grabber = None
            print("    ⚠ opencv cannot decode video.mp4 — VLM sweep and "
                  "stillness checks skipped", file=sys.stderr)
    except Exception as e:
        grabber = None
        print(f"    ⚠ opencv unavailable ({e}) — VLM sweep and stillness "
              f"checks skipped", file=sys.stderr)

    # §5 VLM sweep
    vlm_ran = False
    if gem is not None and grabber is not None:
        req0 = gem.requests
        try:
            a.vlm = vlm_sweep(gem, grabber, a.game_title, a.duration_s,
                              interval, refine_step)
            a.vlm["requests"] = gem.requests - req0    # per-session count
            if not a.vlm["samples"]:
                raise GeminiError("no frames could be decoded/classified — "
                                  "video content NOT inspected")
            vlm_ran = True
            gp_ts = [s["t"] for s in a.vlm["samples"]
                     if s["label"] == "gameplay"]
            gp_ts = gp_ts[::max(len(gp_ts) // 6, 1)]
            for w in a.vlm["windows"]:
                w["inputs"] = rows_in_window(inv, rows, col, w["t0"], w["t1"])
                if w["gating"]:
                    w["stillness_ratio"] = stillness_ratio(grabber, w, gp_ts)
                    r = w["stillness_ratio"]
                    # physically-confirmed-frozen upgrades a VLM-confidence
                    # demotion: measured stillness beats model mood swings
                    if w["tier"] == "low" and w["n_samples"] >= 2 \
                            and r is not None \
                            and r < STILLNESS_FROZEN_BELOW:
                        w["tier"] = "high"
                        w["tier_note"] = "upgraded: frames measured static"
                name = art / f"window_{w['t0']:.0f}-{w['t1']:.0f}s.jpg"
                got = grabber.filmstrip(w["t0"], w["t1"], name)
                w["artifact"] = f"artifacts/{got}" if got else None
            for t in a.vlm["notif_ts"]:
                grabber.corner_crop(t, art / f"notif_{t:.1f}s.jpg")
        except GeminiError as e:
            a.vlm = {"error": str(e)}
            print(f"    ⚠ VLM sweep failed: {e}", file=sys.stderr)
        except Exception as e:                 # any other bug: degrade, don't
            # kill the session. http.client.InvalidURL escapes Gemini
            # .generate's except tuple and embeds the full ?key= URL in its
            # message — scrub the key before report.json/stderr
            # (review-r4 #25)
            msg = str(e)
            if gem.key:
                msg = msg.replace(gem.key, "***")
            msg = re.sub(r"key=[^&\s]+", "key=***", msg)
            a.vlm = {"error": f"{type(e).__name__}: {msg}"}
            print(f"    ⚠ VLM sweep crashed: {a.vlm['error']}",
                  file=sys.stderr)
    if grabber is not None:
        grabber.close()

    build_verdict(a, vlm_ran)
    write_report(a, out_dir)
    return a


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="analyze_sample",
        description="Playbook analysis of delivered v2 sessions "
                    "(SAMPLE_ANALYSIS_PLAYBOOK.md).")
    ap.add_argument("sessions", nargs="+", type=Path)
    ap.add_argument("--raw-root", type=Path, default=None,
                    help="root of raw bundles for the qa-v2 off-by-one "
                         "recheck (matched by metadata session_id)")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"Gemini model (default {DEFAULT_MODEL}; or set "
                         f"GEMINI_MODEL)")
    ap.add_argument("--skip-vlm", action="store_true",
                    help="skip the video-content sweep (verdict marked "
                         "'pending manual video review')")
    ap.add_argument("--vlm-interval", type=float, default=4.0,
                    help="baseline sampling interval seconds (default 4)")
    ap.add_argument("--refine-step", type=float, default=1.0,
                    help="boundary refinement step seconds (default 1)")
    args = ap.parse_args(argv)

    gem = None
    if not args.skip_vlm:
        key = os.environ.get(API_KEY_ENV, "").strip()
        if key:
            gem = Gemini(key, args.model)
        else:
            print(f"⚠ {API_KEY_ENV} not set — running without the VLM sweep "
                  f"(§5 becomes manual)", file=sys.stderr)

    raw_by_sid = {}
    if args.raw_root:
        for m in Path(args.raw_root).glob("**/metadata.json"):
            try:
                raw_by_sid[json.loads(m.read_text()).get("session_id")] = m.parent
            except Exception:
                pass

    analyses = []
    for sdir in args.sessions:
        sdir = sdir.resolve()
        print(f"→ {sdir.name}")
        try:
            a = analyze(sdir, raw_by_sid, gem, args.vlm_interval,
                        args.refine_step)
        except Exception as e:
            out_dir = sdir.parent / f"{sdir.name}-analysis"
            a = SessionAnalysis(session_dir=str(sdir), out_dir=str(out_dir),
                                error=f"{type(e).__name__}: {e}",
                                verdict="error")
            try:                          # best-effort report even on crash
                out_dir.mkdir(parents=True, exist_ok=True)
                write_report(a, out_dir)
            except Exception:
                pass
            print(f"    ✗ analysis error: {a.error}", file=sys.stderr)
        analyses.append(a)
        icon = {"deliverable": "✓", "fix in post": "🔧",
                "re-record": "✗", "error": "‼"}.get(a.verdict.split(" (")[0], "?")
        print(f"  {icon} {a.verdict.upper()}  qa={a.qa_status}  "
              f"→ {a.out_dir}/report.md")
        if a.error:
            print(f"      - {a.error}")
        for reason in a.verdict_reasons:
            print(f"      - {reason}")

    if len(analyses) > 1:
        parent = Path(os.path.commonpath([a.session_dir for a in analyses]))
        if parent.is_file():
            parent = parent.parent
        write_batch_report(analyses, parent)
        print(f"\nBatch report: {parent}/analysis_batch_report.md")

    return max(SEVERITY.get(a.verdict.split(" (")[0], 3) for a in analyses)


if __name__ == "__main__":
    raise SystemExit(main())
