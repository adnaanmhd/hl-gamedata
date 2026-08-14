"""Gemini client with the §13 failure policy, wrapping the engine's client.

Adds to tools/analyze_sample.py's Gemini (which stays the engine of record
for the sweep itself): honor Retry-After on 429, exponential backoff
(2 s base, 60 s max, 5 tries), and clean escalation — a sweep that cannot
finish is a HOLD_VLM, never a silent pass (F5).

Model is pinned to Adnaan's choice (gemini-3.7-flash, R13); do not
substitute.
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config as C

_GENLANG = "https://generativelanguage.googleapis.com/v1beta"


class VLMError(Exception):
    """Sweep could not finish — the session goes HOLD_VLM upstream."""


def _post(url: str, headers: dict, body: dict, timeout_s: int = 180) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def generate(api_key: str, model: str, parts: list[dict]) -> str:
    """One generateContent call under the §13 retry policy."""
    url = f"{_GENLANG}/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.0,
                                 "responseMimeType": "application/json"}}
    last = "no attempt made"
    for attempt in range(C.VLM_MAX_TRIES):
        try:
            resp = _post(url, {"x-goog-api-key": api_key}, body)
        except urllib.error.HTTPError as e:
            retry_after = e.headers.get("Retry-After") if e.headers else None
            last = f"HTTP {e.code}"
            if e.code == 429 or e.code >= 500:
                if attempt < C.VLM_MAX_TRIES - 1:
                    if retry_after and retry_after.isdigit():
                        delay = min(float(retry_after), C.VLM_BACKOFF_MAX_S)
                    else:
                        delay = min(C.VLM_BACKOFF_BASE_S * (2 ** attempt),
                                    C.VLM_BACKOFF_MAX_S)
                    time.sleep(delay)
                    continue
            raise VLMError(last)
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError) as e:
            last = f"network error: {e}"
            if attempt < C.VLM_MAX_TRIES - 1:
                time.sleep(min(C.VLM_BACKOFF_BASE_S * (2 ** attempt),
                               C.VLM_BACKOFF_MAX_S))
                continue
            raise VLMError(last)
        try:
            return "".join(p.get("text", "")
                           for p in resp["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            # safety block / malformed — never silently pass (F5)
            raise VLMError(
                f"response carries no content (safety block?): "
                f"{json.dumps(resp)[:200]}")
    raise VLMError(last)


def _parse_json_reply(text: str):
    try:
        return json.loads(re.sub(r"^```(json)?|```$", "", text.strip(),
                                 flags=re.M))
    except json.JSONDecodeError as e:
        raise VLMError(f"unparseable VLM reply: {text[:200]}") from e


_WINDOW_PROMPT = """You are auditing {n} frames from a PC gameplay recording \
(claimed game: "{game}"). Each frame was flagged because the screen holds \
still there. For EVERY frame output one JSON object; respond with ONLY a JSON \
array. Fields:
"index": int — from the text tag before that image.
"label": one of ["gameplay","menu","loading","pause","scoreboard","cutscene",\
"dialogue","map","other_non_gameplay"]. A quiet in-game scene (reading, \
standing still, dialogue box, map/ship-log screen) is still part of play — \
use gameplay/dialogue/map for those; pause/menu/loading only when the game \
is actually interrupted.
"confidence": "high" | "medium" | "low".
"note": why, max 12 words."""


def classify_stills(api_key: str, model: str, grabber, game_title: str,
                    ts: list[float]) -> list[dict]:
    """Classify candidate still-frames (scanner output). Returns
    [{t, label, conf, note}] sorted by t. Raises VLMError on failure."""
    out: list[dict] = []
    todo = [(t, grabber.jpeg(t)) for t in ts]
    todo = [(t, j) for t, j in todo if j is not None]
    for i in range(0, len(todo), 8):
        chunk = todo[i:i + 8]
        parts = [{"text": _WINDOW_PROMPT.format(n=len(chunk),
                                                game=game_title)}]
        for k, (t, jpg) in enumerate(chunk):
            parts.append({"text": f"FRAME index={k} t={t:.1f}s"})
            parts.append({"inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(jpg).decode()}})
        arr = _parse_json_reply(generate(api_key, model, parts))
        if isinstance(arr, dict):
            arr = arr.get("frames", [arr])
        by_idx = {}
        for o in arr:
            if isinstance(o, dict):
                try:
                    by_idx[int(o.get("index"))] = o
                except (TypeError, ValueError):
                    continue
        for k, (t, _) in enumerate(chunk):
            o = by_idx.get(k, {})
            lab = str(o.get("label", "gameplay"))
            conf = str(o.get("confidence", "low")).lower()
            out.append({"t": round(t, 2),
                        "label": lab if lab in (
                            "gameplay", "menu", "loading", "pause",
                            "scoreboard", "cutscene", "dialogue", "map",
                            "other_non_gameplay") else "other_non_gameplay",
                        "conf": conf if conf in ("high", "medium", "low")
                        else "low",
                        "note": str(o.get("note", ""))[:120]})
    out.sort(key=lambda s: s["t"])
    return out


_CONFIRM_PROMPT = {
    "notification": """This is a FULL-RESOLUTION crop of the bottom-right \
corner of a PC gameplay frame. Is there an OS/launcher notification popup \
burned in (Steam friend toast, Discord/Windows notification card)? Kill \
feeds, game chat, HUD elements in the same corner are NOT notifications. \
Respond ONLY with JSON: {"confirmed": true/false, "what": "<12 words>"}""",
    "chat": """This is a frame from a PC gameplay recording. Is typed \
PLAYER-chat text (human-written messages) readable anywhere on screen? \
NPC dialogue, subtitles, menu text and HUD labels do NOT count. \
Respond ONLY with JSON: {"confirmed": true/false, "what": "<12 words>"}""",
}


def confirm_flag(api_key: str, model: str, jpeg_bytes: bytes,
                 kind: str) -> tuple[bool, str]:
    """Second look at full resolution before a flag can gate (playbook §5.4:
    'always eyeball hits at full crop before claiming')."""
    parts = [{"text": _CONFIRM_PROMPT[kind]},
             {"inline_data": {"mime_type": "image/jpeg",
                              "data": base64.b64encode(jpeg_bytes).decode()}}]
    obj = _parse_json_reply(generate(api_key, model, parts))
    if isinstance(obj, list) and obj:
        obj = obj[0]
    if not isinstance(obj, dict):
        raise VLMError(f"confirm reply not an object: {obj!r}")
    v = obj.get("confirmed")
    confirmed = v if isinstance(v, bool) else \
        str(v).strip().lower() in ("true", "yes", "1")
    return confirmed, str(obj.get("what", ""))[:120]
