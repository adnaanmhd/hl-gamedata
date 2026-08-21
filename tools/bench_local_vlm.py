#!/usr/bin/env python3
"""Benchmark a local Ollama VLM against recorded gemini-3.7-flash sweeps.

Replays every frame timestamp recorded in <session>-analysis/report.json
(vlm.samples) through a local model, using the engine's own prompt, frame
extraction and reply-parse rules (imported from tools/analyze_sample.py,
never copied), then scores frame-for-frame agreement and throughput.

R13-fallback preparation only: benchmarks a hybrid local/Gemini option.
Nothing in the pipeline, the sessions, or the reports is modified, and the
pipeline's model stays pinned to gemini-3.7-flash (R13).

Usage:
  uv run --with opencv-python-headless --with numpy \
      python tools/bench_local_vlm.py --out <dir> [--model qwen2.5vl:7b] \
      [--dry] [session-dir ...]      # default sessions: repo-root 2026-08-*
"""
from __future__ import annotations

import argparse
import base64
import collections
import importlib.util
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_engine():
    spec = importlib.util.spec_from_file_location(
        "analyze_sample", REPO / "tools" / "analyze_sample.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["analyze_sample"] = mod   # dataclasses needs it registered
    spec.loader.exec_module(mod)
    return mod


def ollama_chat(endpoint: str, model: str, content_parts: list[dict],
                timeout_s: int = 300) -> tuple[str, dict]:
    body = {"model": model,
            "messages": [{"role": "user", "content": content_parts}],
            "temperature": 0, "stream": False}
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        resp = json.load(r)
    return (resp["choices"][0]["message"]["content"] or "",
            resp.get("usage") or {})


def classify_local(eng, endpoint: str, model: str, grabber, game_title: str,
                   ts: list[float], stats: dict) -> list[dict]:
    """Engine's classify_frames, with the Gemini call swapped for Ollama.
    Prompt, batching, parse tolerance and defaulting are the engine's own."""
    samples: list[dict] = []
    todo = [(t, grabber.jpeg(t)) for t in ts]
    todo = [(t, j) for t, j in todo if j is not None]
    for i in range(0, len(todo), eng.VLM_BATCH):
        chunk = todo[i:i + eng.VLM_BATCH]
        parts: list[dict] = [{"type": "text",
                              "text": eng._vlm_prompt(game_title, len(chunk))}]
        for k, (t, jpg) in enumerate(chunk):
            parts.append({"type": "text", "text": f"FRAME index={k} t={t:.1f}s"})
            parts.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64,"
                       + base64.b64encode(jpg).decode()}})
        arr = None
        for attempt in (0, 1):
            t0 = time.monotonic()
            try:
                text, usage = ollama_chat(endpoint, model, parts)
            except Exception as e:  # noqa: BLE001 — benchmark keeps going
                stats["errors"].append(f"request failed: {e}")
                if attempt:
                    break
                continue
            stats["req_s"].append(time.monotonic() - t0)
            stats["usage"].append(usage)
            try:
                arr = json.loads(re.sub(r"^```(json)?|```$", "",
                                        text.strip(), flags=re.M))
                break
            except json.JSONDecodeError:
                if attempt:
                    stats["errors"].append(
                        f"unparseable reply: {text[:120]!r}")
        if arr is None:
            continue
        if isinstance(arr, dict):
            arr = arr["frames"] if isinstance(arr.get("frames"), list) \
                else [arr]
        by_idx: dict[int, dict] = {}
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
                stats["missing_idx"] += 1
                continue
            lab = o.get("label", "gameplay")
            conf = str(o.get("confidence", "low")).strip().lower()
            samples.append({
                "t": round(t, 2),
                "label": lab if lab in eng.LABELS else "other_non_gameplay",
                "conf": conf if conf in ("high", "medium", "low") else "low",
                "notif": eng._as_bool(o.get("notification_overlay")),
                "combat": eng._as_bool(o.get("combat_evidence")),
                "chat": eng._as_bool(o.get("visible_chat")),
                "guess": str(o.get("game_guess", "unknown")),
                "note": str(o.get("note", ""))[:120]})
    return samples


def norm_game(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def vote_winner(samples: list[dict]) -> tuple[str, int, int]:
    """(winning normalized guess, its votes, total named votes)."""
    votes = collections.Counter(norm_game(s["guess"]) for s in samples
                                if s["guess"].lower() not in ("", "unknown"))
    if not votes:
        return "", 0, 0
    top, n = votes.most_common(1)[0]
    return top, n, sum(votes.values())


def find_default_sessions() -> list[Path]:
    out = []
    for d in sorted(REPO.glob("2026-08-*")):
        if d.is_dir() and (d / "video.mp4").exists() \
                and (d.parent / f"{d.name}-analysis" / "report.json").exists():
            out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="*", type=Path)
    ap.add_argument("--model", default="qwen2.5vl:7b")
    ap.add_argument("--endpoint", default="http://localhost:11434")
    ap.add_argument("--out", type=Path, default=Path("."))
    ap.add_argument("--dry", action="store_true",
                    help="grab frames and count only; no model calls")
    args = ap.parse_args()

    eng = load_engine()
    sessions = args.sessions or find_default_sessions()
    if not sessions:
        print("no sessions found", file=sys.stderr)
        return 2

    stats = {"req_s": [], "usage": [], "errors": [], "missing_idx": 0}
    per_session = []
    agg = collections.Counter()
    disagreements = []

    if not args.dry:
        # warm-up: load the model once so timing excludes load latency
        g0 = eng.FrameGrabber(sessions[0] / "video.mp4")
        jpg = g0.jpeg(0.3)
        t0 = time.monotonic()
        ollama_chat(args.endpoint, args.model,
                    [{"type": "text", "text": "Reply with exactly []"},
                     {"type": "image_url", "image_url": {
                         "url": "data:image/jpeg;base64,"
                                + base64.b64encode(jpg).decode()}}])
        warmup_s = time.monotonic() - t0
    else:
        warmup_s = None

    for sess in sessions:
        rep = json.load(open(sess.parent / f"{sess.name}-analysis"
                             / "report.json"))
        gem_samples = (rep.get("vlm") or {}).get("samples") or []
        if not gem_samples:
            print(f"[skip] {sess.name}: no recorded vlm.samples")
            continue
        game_title = rep.get("game_title") or "unknown"
        ts = [s["t"] for s in gem_samples]
        grabber = eng.FrameGrabber(sess / "video.mp4")
        if not grabber.opened():
            print(f"[skip] {sess.name}: video unreadable")
            continue
        if args.dry:
            got = sum(1 for t in ts if grabber.jpeg(t) is not None)
            print(f"[dry] {sess.name}: {got}/{len(ts)} frames grabbable")
            continue

        t0 = time.monotonic()
        loc_samples = classify_local(eng, args.endpoint, args.model, grabber,
                                     game_title, ts, stats)
        wall = time.monotonic() - t0

        gem = {s["t"]: s for s in gem_samples}
        loc = {s["t"]: s for s in loc_samples}
        common = sorted(set(gem) & set(loc))
        c = collections.Counter()
        for t in common:
            a, b = gem[t], loc[t]
            c["n"] += 1
            c["label_eq"] += a["label"] == b["label"]
            c["gate_eq"] += ((a["label"] in eng.GATING_LABELS)
                             == (b["label"] in eng.GATING_LABELS))
            for f in ("notif", "combat", "chat"):
                c[f"{f}_eq"] += bool(a.get(f)) == bool(b.get(f))
                c[f"{f}_gem"] += bool(a.get(f))
                c[f"{f}_loc"] += bool(b.get(f))
            named = a["guess"].lower() not in ("", "unknown")
            if named:
                c["guess_named"] += 1
                c["guess_eq"] += norm_game(a["guess"]) == norm_game(b["guess"])
            if a["label"] != b["label"]:
                disagreements.append(
                    {"session": sess.name, "t": t,
                     "gemini": a["label"], "local": b["label"],
                     "gemini_note": a.get("note", ""),
                     "local_note": b.get("note", "")})
        gw = vote_winner(gem_samples)
        lw = vote_winner(loc_samples)
        per_session.append({
            "session": sess.name, "game_title": game_title,
            "frames_recorded": len(gem_samples),
            "frames_local": len(loc_samples),
            "compared": c["n"], "wall_s": round(wall, 1),
            "label_agree": c["label_eq"], "gate_agree": c["gate_eq"],
            "notif": [c["notif_eq"], c["notif_gem"], c["notif_loc"]],
            "combat": [c["combat_eq"], c["combat_gem"], c["combat_loc"]],
            "chat": [c["chat_eq"], c["chat_gem"], c["chat_loc"]],
            "guess_eq": [c["guess_eq"], c["guess_named"]],
            "vote_winner_gemini": gw, "vote_winner_local": lw})
        agg.update(c)
        print(f"[done] {sess.name}: {c['n']} frames compared in {wall:.0f}s "
              f"— label {c['label_eq']}/{c['n']}, "
              f"gate {c['gate_eq']}/{c['n']}")

    if args.dry:
        return 0

    req_s = stats["req_s"]
    ptoks = [u.get("prompt_tokens", 0) for u in stats["usage"]]
    result = {
        "model": args.model, "endpoint": args.endpoint,
        "warmup_load_s": round(warmup_s, 1) if warmup_s else None,
        "sessions": per_session,
        "aggregate": {
            "frames_compared": agg["n"],
            "label_agree": agg["label_eq"],
            "gate_agree": agg["gate_eq"],
            "notif": [agg["notif_eq"], agg["notif_gem"], agg["notif_loc"]],
            "combat": [agg["combat_eq"], agg["combat_gem"],
                       agg["combat_loc"]],
            "chat": [agg["chat_eq"], agg["chat_gem"], agg["chat_loc"]],
            "guess": [agg["guess_eq"], agg["guess_named"]],
        },
        "timing": {
            "requests": len(req_s),
            "total_s": round(sum(req_s), 1),
            "mean_req_s": round(statistics.mean(req_s), 2) if req_s else None,
            "median_req_s": round(statistics.median(req_s), 2)
            if req_s else None,
            "mean_prompt_tokens": round(statistics.mean(ptoks))
            if ptoks else None,
        },
        "missing_idx": stats["missing_idx"],
        "errors": stats["errors"][:20],
        "disagreements": disagreements,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / f"bench_{re.sub(r'[^a-zA-Z0-9._-]', '_', args.model)}.json"
    out_path.write_text(json.dumps(result, indent=1))

    n = max(agg["n"], 1)
    frames_per_s = agg["n"] / sum(req_s) if req_s else 0
    print(f"\n=== {args.model} vs recorded gemini-3.7-flash ===")
    print(f"frames compared      {agg['n']}")
    print(f"label exact          {agg['label_eq']}/{n} "
          f"({100 * agg['label_eq'] / n:.0f}%)")
    print(f"gating binary        {agg['gate_eq']}/{n} "
          f"({100 * agg['gate_eq'] / n:.0f}%)")
    print(f"notif eq/gem+/loc+   {agg['notif_eq']}/{agg['notif_gem']}"
          f"/{agg['notif_loc']}")
    print(f"combat eq/gem+/loc+  {agg['combat_eq']}/{agg['combat_gem']}"
          f"/{agg['combat_loc']}")
    print(f"chat eq/gem+/loc+    {agg['chat_eq']}/{agg['chat_gem']}"
          f"/{agg['chat_loc']}")
    print(f"game guess           {agg['guess_eq']}/{agg['guess_named']} "
          f"named-vote matches")
    print(f"speed                {result['timing']['mean_req_s']}s/request, "
          f"{frames_per_s:.2f} frames/s "
          f"(model load {result['warmup_load_s']}s)")
    print(f"detail → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
