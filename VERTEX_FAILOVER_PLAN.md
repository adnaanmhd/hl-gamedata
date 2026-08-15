# Vertex failover for `pipeline/vlm.py` — implementation plan (not yet built)

Written 2026-08-14 ~23:45 IST. Per Adnaan's instruction the code is **not
implemented yet** — this is the exact plan for when he says go. Everything
cited below was re-verified against the working tree tonight.

> **Supersede notes (2026-08-15, plan v2 — plan wins on conflict):** (1) §5's stop-before-commit
> sequencing is replaced: the code merges dark behind NEW config knob `VLM_FAILOVER_ENABLED=False`;
> the smoke test runs FROM THE VM (plan §7.6) and a one-line config flip enables it (plan R21).
> Tests must pass in both flag states. (2) Code moved at commit f49bdd6 — current pins:
> `_GENLANG` `vlm.py:24`, ladder `vlm.py:39-78`, `classify_stills` `vlm.py:103`, `confirm_flag`
> `vlm.py:160`; the ladder's network-except clause now also catches `http.client.HTTPException`
> (`vlm.py:62-64`), and `_generate_once` must carry that too.

## What it is — very simply

Google serves the same Gemini brain through two different doors: the
regular door (`generativelanguage.googleapis.com`) and the Vertex "express"
door (`aiplatform.googleapis.com`) — same key, same model, same answers.
Our analysis engine (`tools/analyze_sample.py`) already knows both doors:
if the first is jammed, it knocks on the second and remembers which one
worked. The pipeline's own Gemini client (`pipeline/vlm.py`) only knows the
first door.

That leaves a real gap today: when the regular door jams, the engine's
video sweep quietly walks through the Vertex door and succeeds — but the
wrapper's extra checks (classifying the scanner's still-frames, confirming
notification/chat flags) fail and park the session in `HOLD_VLM` anyway.
So a one-door outage still stalls sessions even though the second door was
open the whole time. This change teaches `pipeline/vlm.py` the same
two-door habit, copied from the engine.

**Pros:** one Google outage/quota surface stops stalling sessions; zero new
secrets, dependencies, or config; behavior copied from code that already
works; failure story unchanged (both doors jammed still = `HOLD_VLM`,
never a silent pass). **Cons:** a genuinely-down Gemini now takes up to
twice as long to give up before holding a session (it politely retries both
doors); ~40 lines more client code; and the Vertex door is **unverified
with our key** — the live smoke test below settles that, and if Vertex
answers "API not enabled" we stop and report the exact console click
instead of working around it.

---

## 1. Verified current state (2026-08-14 evening)

- `pipeline/vlm.py` is single-endpoint: `_GENLANG` constant at `vlm.py:23`;
  `generate()` at `vlm.py:38-77` runs the §13 ladder (5 tries, Retry-After
  honored, 2 s base / 60 s cap backoff — constants `config.py:70-72`)
  against genlang only, then raises `VLMError` → `HOLD_VLM` upstream.
- The engine already fails over: `tools/analyze_sample.py` `Gemini` class
  (`analyze_sample.py:387-470`), constants `GENLANG_BASE:80`,
  `VERTEX_BASE:81`. Its `validate.py` caller passes a `Gemini` instance
  into `eng.analyze` (`validate.py:629-639`), so the **sweep** has
  failover today; `vlm.generate` callers (`classify_stills` `vlm.py:102`,
  `confirm_flag` `vlm.py:157`, used in `validate.py:_build_aux`) do not.
- Project memory (`pipeline-build-facts`) lists exactly this as a
  review-deferred item: "no Vertex fallback in pipeline/vlm.py (engine
  client has one; key is genlang-verified)".

## 2. The engine's semantics — the behavioral contract to mirror

From `analyze_sample.py:424-470`, read in full:

| # | Behavior | Engine evidence |
|---|---|---|
| 1 | Two endpoints, in order: genlang (key in `x-goog-api-key` header), Vertex express (same key as `?key=` query param, no headers) | `_endpoints`, :397-403 |
| 2 | Sticky order: `[_which, 1-_which]` once an endpoint has succeeded, else `[0, 1]` | :429-430 |
| 3 | `401/403/404` → **switch endpoints immediately**, no retries burned | :444-445 (`break`) |
| 4 | `429/5xx` → retry ladder on that endpoint, then switch | :446-448 |
| 5 | Network errors/timeouts → retry ladder, then switch | :450-455 |
| 6 | Success → remember `_which = idx`, return text | :456-461 |
| 7 | Endpoint answered but reply carries no content (safety block) → **raise immediately, NO failover** — the door worked; the answer was a refusal | :462-465 (raise inside the loop) |
| 8 | Both endpoints exhausted → raise, carrying the last failure | :470 |

Deviations we keep deliberately (kickoff-sanctioned):
- Per endpoint we run **our §13 ladder** (5 tries, Retry-After, 2–60 s
  backoff), not the engine's 3-try `(2,5)` s sleeps — §13 is the
  pipeline's locked retry policy; the engine contributes *when to switch*,
  not *how to pace retries*.
- We skip the engine's 404 nicety (`list_models` suggestion, :466-469) —
  the smoke test owns that diagnosis.
- Stickiness is **module-level** (`vlm._which`), per the kickoff, not
  per-instance — `vlm.py` has no class. Validation workers are
  subprocesses (`run.py:_validate_worker`), so each worker process
  discovers the working endpoint once and keeps it for its lifetime —
  exactly the "process lifetime" the kickoff specifies. (Worst case:
  `workers` processes each burn one discovery ladder. Accepted.)

## 3. Planned change to `pipeline/vlm.py` (the only production file)

Everything below `_GENLANG` down through `generate()` is restructured; the
public surface (`VLMError`, `generate(api_key, model, parts)`,
`classify_stills`, `confirm_flag`, `_post`) is unchanged, so **no caller
changes anywhere**.

```python
_GENLANG = "https://generativelanguage.googleapis.com/v1beta"
# Vertex AI express-mode base — copied VERBATIM from tools/analyze_sample.py
# (VERTEX_BASE, the engine of record). Do not edit independently.
_VERTEX = "https://aiplatform.googleapis.com/v1/publishers/google"

# Sticky working endpoint for this process (engine's `_which`):
# None = undiscovered, 0 = genlang, 1 = vertex express.
_which: int | None = None


class _EndpointFailed(Exception):
    """One endpoint's §13 ladder is exhausted or hard-refused — the caller
    may try the other endpoint. Never leaves this module."""


def _endpoints(api_key: str, model: str) -> list[tuple[str, dict]]:
    return [
        (f"{_GENLANG}/models/{model}:generateContent",
         {"x-goog-api-key": api_key}),
        (f"{_VERTEX}/models/{model}:generateContent?key={api_key}", {}),
    ]
```

`_generate_once(url, headers, body, tag)` — the existing ladder body
(`vlm.py:44-77`) moved verbatim into a helper, with two changes:

- transport-level final failures (`429`/`5xx` exhaustion, `401/403/404`
  and any other HTTP status immediately, network-error exhaustion) raise
  `_EndpointFailed(f"HTTP {code} ({tag})")` instead of `VLMError` — `tag`
  is `"genlang"`/`"vertex"`. **The URL must never appear in these strings:
  the Vertex URL embeds `?key=...`, and these strings flow into ledger
  `detail`, `HOLD_VLM` evidence, and Telegram alerts.** (The engine logs
  `url.split('?')[0]`; we go stricter and log only the tag.)
- the no-content/safety-block branch (`vlm.py:69-76`) still raises
  `VLMError` directly — it must **propagate through the failover
  unswitched** (contract row 7).

`generate(api_key, model, parts)` becomes the failover shell:

```python
def generate(api_key: str, model: str, parts: list[dict]) -> str:
    """One generateContent call: §13 ladder against the sticky-preferred
    endpoint, then once against the other (engine semantics); both failing
    → VLMError (HOLD_VLM upstream, unchanged)."""
    global _which
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.0,
                                 "responseMimeType": "application/json"}}
    eps = _endpoints(api_key, model)
    order = [_which, 1 - _which] if _which is not None else [0, 1]
    last = "no attempt made"
    for idx in order:
        url, headers = eps[idx]
        tag = "vertex" if idx == 1 else "genlang"
        try:
            text = _generate_once(url, headers, body, tag)
        except _EndpointFailed as e:
            last = str(e)
            continue
        _which = idx
        return text
    raise VLMError(last)
```

Notes: sticky endpoint that later fails → the other endpoint is retried
(order always contains both — automatic failback, same as engine);
`_which` is only written on success; module docstring gains two lines
naming the failover and the engine as its source.

## 4. Test plan — `pipeline/tests/test_scanner_vlm.py`

All fakes monkeypatch `vlm._post` (the sanctioned choke point, existing
style) and `vlm.time.sleep`; route on `"generativelanguage" in url` vs
`"aiplatform" in url`.

**New module-scope autouse fixture** resetting `vlm._which = None` before
each test — stickiness must never leak between tests.

**Existing tests — audit result:**

| Test | Under failover | Action |
|---|---|---|
| `test_generate_retries_429_with_retry_after` | succeeds on primary within the ladder — unchanged | none |
| `test_generate_gives_up_after_max_tries` | now exhausts BOTH endpoints, still `VLMError` | none (assertion unchanged) |
| `test_generate_safety_block_raises` | must NOT fail over | strengthen: assert exactly **1** POST |
| `test_generate_hard_client_error_no_retry` | 403 now switches to vertex (which also 403s) | assertion `calls["n"] == 1` → `== 2`; rename `..._no_retry_switches_endpoint` |

**New tests (kickoff's three + two engine-semantic guards):**

1. `test_failover_primary_exhausts_secondary_succeeds_and_sticks` —
   genlang always 429, vertex answers. Call 1: returns; genlang POSTs
   == `C.VLM_MAX_TRIES`, vertex == 1; `vlm._which == 1`. Call 2: returns;
   genlang count **unchanged** (stickiness), vertex == 2.
2. `test_failover_both_fail_raises_vlmerror` — genlang 403, vertex 404 →
   `VLMError`; exactly 2 POSTs (no retries burned on hard client errors);
   message carries the last tag, not a URL (assert `"key=" not in str(e)`).
3. `test_primary_healthy_secondary_never_called` — genlang answers; assert
   no `aiplatform` URL ever seen; `vlm._which == 0`.
4. `test_403_switches_without_burning_retries` — genlang 403 (1 POST
   only), vertex succeeds (contract row 3).
5. `test_sticky_vertex_fails_back_to_genlang` — seed `vlm._which = 1`;
   vertex 429-exhausts, genlang succeeds → returns, `vlm._which == 0`
   (contract row 2's `1-_which` half).

## 5. Live smoke test (after unit tests, before commit)

Script in the session scratchpad (never committed), run as
`python3 smoke_vertex.py`:

- Parses `~/.config/hl-gamedata/secrets.env` itself (same parser rules as
  `config.load_secrets`); **the key is never printed, and any URL echoed
  is printed with the query string stripped**.
- Per endpoint, one text-only `generateContent`:
  `parts=[{"text": "reply with []"}]`, model from `GEMINI_MODEL`
  (`gemini-3.7-flash`), 60 s timeout; prints HTTP status + first ~200
  chars of the response body per endpoint.
- Decision table:
  - **both 200** → proceed to full suite + commit;
  - **Vertex 403** (`PERMISSION_DENIED` / "API not enabled") → **STOP, no
    commit**; report: Adnaan must enable the **Vertex AI API** on the
    key's Google Cloud project (likely `hl-gamedata-pipeline`) —
    console.cloud.google.com → APIs & Services → Enable → "Vertex AI API";
  - **Vertex 404** on the model path → **STOP, no commit**; report that
    `gemini-3.7-flash` may be named differently under
    `publishers/google/models/` on Vertex — needs Adnaan's call (R13 pins
    the model; we do not substitute);
  - **genlang failing** → stop regardless — that's the primary door and a
    separate alarm (note: the vault carries an open question "was the
    pasted Gemini API key rotated?" — a genlang 401/403 here likely means
    the rotation happened and `secrets.env` is stale).

## 6. Finish line

- Full suite:
  `PYTHONPATH=. uv run --with pytest pytest pipeline/tests translator/tests -q`
  — green required.
- Commit **exactly two files** (`pipeline/vlm.py`,
  `pipeline/tests/test_scanner_vlm.py`) — the repo currently carries many
  unrelated dirty files, so `git add` those two paths explicitly, never
  `-A`. Message, first line verbatim from the kickoff:

  ```
  pipeline/vlm.py: genlang→vertex express failover (mirrors engine) + tests

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  ```
- No push.

## 7. Risks & edge cases, stated

- **Time-to-HOLD doubles when Gemini is fully down**: two ladders ≈ 2×
  (2+4+8+16+32 s) ≈ ~4 min worst case per call before `HOLD_VLM`. Bounded,
  mirrors the engine, and §13's design already treats HOLD as
  retry-next-cycle — accepted.
- **Per-worker discovery**: up to `cfg.workers` processes each pay one
  failed-primary ladder after a genlang outage begins. Accepted (kickoff
  explicitly scopes stickiness to process lifetime).
- **Thread-safety**: `_which` is a single int assignment (atomic under the
  GIL); today only worker processes call `generate`. Fine even under the
  Task 1B overlap driver, whose D/U threads never touch the VLM.
- **No result cache / fleet throttle**: explicitly out of scope
  (kickoff) — this change alters availability, not quota consumption.
- **Estimated size**: ~45 lines changed/added in `vlm.py`, ~110 in tests.
  ~1–2 h including the smoke run.
