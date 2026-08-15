"""Gemini client with the §13 failure policy, wrapping the engine's client.

Adds to tools/analyze_sample.py's Gemini (which stays the engine of record
for the sweep itself): honor Retry-After on 429, exponential backoff
(2 s base, 60 s max, 5 tries), and clean escalation — a sweep that cannot
finish is a HOLD_VLM, never a silent pass (F5).

Two resilience layers on top of the §13 ladder (plan §10a):
- Endpoint failover (R21), mirroring the engine's two-door client: genlang
  then Vertex express, sticky on success, 401/403/404 switch immediately.
  DARK behind config.VLM_FAILOVER_ENABLED until the §7.6 smoke passes.
- Quota ladder (R23): on a rung exhausting its endpoints, step the model
  down config.VLM_MODEL_LADDER, then the prev-key rung
  (GEMINI_API_KEY_PREV at the rung-0 model), then VLMError -> HOLD_VLM.
  Rungs are sticky for the rest of the run: run.py injects the run's
  current rung into every validation worker and keeps the max reported
  back (run-level stickiness across spawn pool generations).

Model of record stays Adnaan's choice (gemini-3.7-flash, R13/R23); the
ladder substitutes only under sustained rate limiting, and every verdict
that used a lower rung is flagged (models_used -> dossier + batch line).
Safety-blocks never fail over and never ladder — the model answered;
shopping the refusal to another door/model would be verdict-shopping (F5).
SECRETS: keys must never appear in error strings; the Vertex URL embeds
?key=, so errors carry endpoint TAGS, never URLs.
"""
from __future__ import annotations

import base64
import http.client
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config as C

_GENLANG = "https://generativelanguage.googleapis.com/v1beta"
# Vertex AI express-mode base — copied VERBATIM from tools/analyze_sample.py
# (VERTEX_BASE, the engine of record). Do not edit independently.
_VERTEX = "https://aiplatform.googleapis.com/v1/publishers/google"

# Sticky working endpoint for this process (engine's `_which`):
# None = undiscovered, 0 = genlang, 1 = vertex express.
_which: int | None = None
# Sticky R23 rung for this process; run.py seeds it in each worker from the
# run-level value and absorbs the max back (plan §10a).
_rung: int = 0
# (rung, key tag, model, endpoint) records that ANSWERED for the session
# being validated — begin_session() clears it; never holds a secret.
_session_models: list[dict] = []

_prev_key_cache: str | None = None


class VLMError(Exception):
    """Sweep could not finish — the session goes HOLD_VLM upstream."""


class _EndpointFailed(Exception):
    """One endpoint's §13 ladder is exhausted or hard-refused — the caller
    may try the other endpoint / next rung. Never leaves this module."""


def begin_session() -> None:
    """Reset the per-session models_used record (rung stays sticky)."""
    _session_models.clear()


def session_models() -> list[dict]:
    """(rung, key tag, model, endpoint) that answered since begin_session."""
    return list(_session_models)


def _prev_key() -> str:
    """GEMINI_API_KEY_PREV from secrets.env (R23 last-resort rung); read
    once per process. Empty string = rung not armed."""
    global _prev_key_cache
    if _prev_key_cache is None:
        _prev_key_cache = C.load_secrets().get("GEMINI_API_KEY_PREV", "")
    return _prev_key_cache


def _post(url: str, headers: dict, body: dict, timeout_s: int = 180) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def _endpoints(api_key: str, model: str) -> list[tuple[str, dict]]:
    return [
        (f"{_GENLANG}/models/{model}:generateContent",
         {"x-goog-api-key": api_key}),
        (f"{_VERTEX}/models/{model}:generateContent?key={api_key}", {}),
    ]


def _ladder(api_key: str, model: str) -> list[tuple[str, str, str]]:
    """R23 rungs as (key, model, key_tag). Rung 0 is the caller's model on
    the caller's key; then the config ladder's remaining models; last the
    prev-key rung at the rung-0 model (skipped when unarmed)."""
    models = [model] + [m for m in C.VLM_MODEL_LADDER if m != model]
    rungs = [(api_key, m, "current") for m in models]
    rungs.append((_prev_key(), model, "prev"))
    return rungs


def _generate_once(url: str, headers: dict, body: dict, tag: str) -> str:
    """§13 retry ladder against ONE endpoint. Success returns the text.
    Transport-level failure raises _EndpointFailed (401/403/404 and any
    other hard HTTP status immediately — no retries burned; 429/5xx and
    network errors after the full ladder). A reply that carries no content
    (safety block) raises VLMError directly: the endpoint answered, so it
    must propagate unswitched and unladdered (engine contract row 7; F5).
    Error strings carry the endpoint tag, NEVER the URL (?key= leak)."""
    last = "no attempt made"
    for attempt in range(C.VLM_MAX_TRIES):
        try:
            resp = _post(url, headers, body)
        except urllib.error.HTTPError as e:
            retry_after = e.headers.get("Retry-After") if e.headers else None
            last = f"HTTP {e.code} ({tag})"
            if e.code == 429 or e.code >= 500:
                if attempt < C.VLM_MAX_TRIES - 1:
                    if retry_after and retry_after.isdigit():
                        delay = min(float(retry_after), C.VLM_BACKOFF_MAX_S)
                    else:
                        delay = min(C.VLM_BACKOFF_BASE_S * (2 ** attempt),
                                    C.VLM_BACKOFF_MAX_S)
                    time.sleep(delay)
                    continue
            raise _EndpointFailed(last)
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException, json.JSONDecodeError) as e:
            # http.client.InvalidURL embeds the FULL request URL (?key=) in
            # its message, and the whitespace that triggers it also splits
            # the key — so scrub the literal key text from the url AND any
            # key= remnant before {e} can enter `last` (review-r4 #25)
            msg = str(e)
            key_in_url = re.search(r"key=([^&]+)", url)
            if key_in_url:
                msg = msg.replace(key_in_url.group(1), "***")
            msg = re.sub(r"key=[^&\s]+", "key=***", msg)
            last = f"network error ({tag}): {msg}"
            if attempt < C.VLM_MAX_TRIES - 1:
                time.sleep(min(C.VLM_BACKOFF_BASE_S * (2 ** attempt),
                               C.VLM_BACKOFF_MAX_S))
                continue
            raise _EndpointFailed(last)
        try:
            return "".join(p.get("text", "")
                           for p in resp["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            # safety block / malformed — never silently pass (F5)
            raise VLMError(
                f"response carries no content (safety block?): "
                f"{json.dumps(resp)[:200]}")
    raise _EndpointFailed(last)


def generate(api_key: str, model: str, parts: list[dict]) -> str:
    """One generateContent call: §13 ladder against the sticky-preferred
    endpoint, then once against the other (R21, when enabled), then down
    the R23 rungs — each rung × both endpoints — until an answer or
    VLMError (HOLD_VLM upstream, unchanged)."""
    global _which, _rung
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.0,
                                 "responseMimeType": "application/json"}}
    rungs = _ladder(api_key, model)
    last = "no attempt made"
    for r in range(min(_rung, len(rungs) - 1), len(rungs)):
        key_r, model_r, key_tag = rungs[r]
        if not key_r:
            # unarmed rung: skip, but never mask the real transport
            # failure — it is the HOLD_VLM evidence
            if last == "no attempt made":
                last = f"rung {r} not armed (no {key_tag} key)"
            continue
        eps = _endpoints(key_r, model_r)
        if C.VLM_FAILOVER_ENABLED:
            order = [_which, 1 - _which] if _which is not None else [0, 1]
        else:
            order = [0]                       # genlang only (status quo)
        for idx in order:
            url, headers = eps[idx]
            tag = "vertex" if idx == 1 else "genlang"
            try:
                text = _generate_once(url, headers, body,
                                      f"{tag} rung{r}:{model_r}")
            except _EndpointFailed as e:
                last = str(e)
                continue
            _which = idx
            _rung = r                         # sticky for the run (R23)
            rec = {"rung": r, "key": key_tag, "model": model_r,
                   "endpoint": tag}
            if rec not in _session_models:
                _session_models.append(rec)
            return text
    raise VLMError(last)


_ladder_gemini_cls = None


def LadderGemini(api_key: str, model: str):
    """Engine-compatible Gemini whose every generateContent goes through
    this module's failover + rung ladder — the SWEEP gets the whole chain
    without touching the engine file (plan §10a: wrap, don't fork).

    Factory, not a class: the engine module loads lazily. Returns an
    instance of a subclass of the engine's Gemini, so eng.analyze treats
    it exactly like its own client (.model/.requests/.list_models).
    Failures surface as the engine's GeminiError, preserving its
    degrade-to-HOLD handling. Note the deliberate behavior change stated
    in the plan: with VLM_FAILOVER_ENABLED=False this also removes the
    engine's native always-on vertex fallback from the sweep — acceptable
    only because the flag stays False solely when the §7.6 matrix proved
    vertex dead for the active key."""
    global _ladder_gemini_cls
    if _ladder_gemini_cls is None:
        from .validate import load_engine
        eng = load_engine()

        class _LadderGemini(eng.Gemini):
            def generate(self, parts: list[dict]) -> str:
                try:
                    text = generate(self.key, self.model, parts)
                except VLMError as e:
                    raise eng.GeminiError(str(e)) from e
                self.requests += 1
                return text

        _ladder_gemini_cls = _LadderGemini
    return _ladder_gemini_cls(api_key, model)


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
            o = by_idx.get(k)
            if o is None:
                continue        # no reply for this frame — never fabricate
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
