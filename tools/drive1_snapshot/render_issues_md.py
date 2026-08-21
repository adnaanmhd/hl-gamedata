"""Render drive1-issues-<date>.json -> drive1-issues-<date>.md (exhaustive,
every row listed, no 'xN' summaries)."""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

SCRATCH = Path(os.environ.get("DRIVE1_WORKDIR", Path(__file__).resolve().parent))  # where run.sh put the listing + session_jsons
REPO = Path("/Users/adnaan/Documents/hl-projects/hl-gamedata")
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-20"
data = json.loads((SCRATCH / f"drive1-issues-{DATE}.json").read_text())
S, I = data["summary"], data["issues"]
OUT = REPO / f"drive1-issues-{DATE}.md"
NOW = (SCRATCH / "listing_finished_utc.txt").read_text().strip()


def prev_totals(path: Path):
    rows = [r for r in csv.DictReader(path.open())
            if r["player_email"] and r["player_email"] != "TOTAL"]
    return (sum(float(r["kamla_hours"]) for r in rows),
            sum(float(r["outer_wilds_hours"]) for r in rows),
            sum(int(r["total_sessions"]) for r in rows), len(rows))


PREV_NAME = os.environ.get("DRIVE1_PREV_CSV", "drive1-raw-hours-2026-08-19.csv")
pk, po, ps, pp = prev_totals(REPO / PREV_NAME)
PREV_BAD = int(sum(int(r["incorrect_upload_path"] or 0) for r in csv.DictReader((REPO / PREV_NAME).open()) if r["player_email"] not in ("", "TOTAL")))


def md_table(cols, rows):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |")
    return "\n".join(out)


def sec(title, cols, rows, note=None):
    lines = [f"### {title} — {len(rows)}", ""]
    if note:
        lines += [note, ""]
    if rows:
        lines += [md_table(cols, rows), ""]
    else:
        lines += ["_none_", ""]
    return lines


L = []
L += [f"# Drive I — issues list, snapshot {DATE}", "",
      f"Source: live `rclone lsjson -R` of `drive-collect:` finished **{NOW}** "
      f"({S['entries']} entries, {S['files']} files) + every `session.json` read for "
      f"real durations. Path rules are production's own `pipeline.ingest.parse_listing()` "
      f"(`<game>/<operator>/<player-email>/<session-id>/`). Companion CSV: "
      f"`drive1-raw-hours-{DATE}.csv`.", "",
      "## Snapshot", "",
      md_table(["", re.search(r"(\d{2}-\d{2})\.csv$", PREV_NAME).group(1) + " CSV", f"{DATE} (now)", "change"], [
          ["Kamla hours", f"{pk:.1f}", f"{S['kamla_hours']:.1f}", f"{S['kamla_hours']-pk:+.1f}"],
          ["Outer Wilds hours", f"{po:.1f}", f"{S['ow_hours']:.1f}", f"{S['ow_hours']-po:+.1f}"],
          ["Total hours", f"{pk+po:.1f}", f"{S['total_hours']:.1f}", f"{S['total_hours']-pk-po:+.1f}"],
          ["Sessions (clean path)", ps, S['kamla_sessions'] + S['ow_sessions'],
           f"{S['kamla_sessions'] + S['ow_sessions'] - ps:+d}"],
          ["Player rows", pp, S['players'], f"{S['players']-pp:+d}"],
          ["Incorrect-path uploads", PREV_BAD, S['quarantined'], f"{S['quarantined']-PREV_BAD:+d}"],
      ]), "",
      f"Players with Kamla footage: {S['players_kamla']} · with OW footage: {S['players_ow']} · "
      f"durations estimated from file size: {S['size_estimated_sessions']}.", "",
      "**How to read the total.** The CSV uses exactly the 08-19 method (every clean-path session "
      "folder that has a `session.json` counts, by its own `duration_seconds`) so the day-over-day "
      "numbers stay comparable. That method knowingly includes footage that is not usable yet; the "
      "honest deductions are:", "",
      md_table(["deduction", "sessions", "hours"], [
          ["same session id sitting at 2+ clean paths — extra copies (D1)",
           S["dup_sessions_extra_copies"], f"{S['dup_extra_hours']:.2f}"],
          ["clean-path folders with required files missing (C) — of which…",
           S["incomplete_sessions"], f"{S['incomplete_hours']:.2f}"],
          ["   …folders with NO video.mp4 at all",
           S["no_video_sessions"], f"{S['no_video_hours']:.2f}"],
          ["incorrect-path uploads — NOT in the hours columns, listed in B",
           S["quarantined"], f"{S['bad_path_hours']:.2f}"],
      ]), "",
      f"Usable-today estimate (total − duplicate copies − incomplete folders) ≈ "
      f"**{S['total_hours'] - S['dup_extra_hours'] - S['incomplete_hours']:.1f} h** raw, pre-trim, "
      "pre-validation. Not a delivered-hours number.", ""]

# ---- meta issue: the double-count ----
L += ["## A. Bookkeeping issue (not on the drive)", "",
      "### A1. The \"1285.4 fh / 3644 sessions\" figure recorded for the 08-19 snapshot is a double-count", "",
      "`drive1-raw-hours-2026-08-19.csv` ends with a blank line and a `TOTAL` row. Summing the file "
      "with `csv.DictReader` without skipping that row counts everything twice: "
      "587.562+587.562 = 1175.1, 55.156+55.156 = 110.3, 1822×2 = 3644 — exactly the numbers in "
      "commit `4093ef5` (clean-slate ruling) and the `clean-slate-2026-08-20` memory. "
      f"**The true 08-19 figure was {pk+po:.1f} h ({pk:.1f} Kamla + {po:.1f} OW), {ps} sessions, {pp} players.** "
      "The \"~290 h/day growth\" is the same doubling (real 08-18→08-19 delta was +147 h in 15 h; "
      f"08-19→08-20 was +{S['total_hours']-pk-po:.0f} h in ~26 h — i.e. **~150–200 h/day**). "
      "Where the wrong number lives: commit `4093ef5` message; `R8_IMPLEMENTATION_PLAN.md` §0 status ledger "
      "and the §5 timeline arithmetic (\"7–11 days for the full drive\"); `FLIP_EXEC_KICKOFF_PROMPT.md` line 16; "
      "the `clean-slate-2026-08-20` memory (corrected in this session). "
      f"Re-done with today's real {S['total_hours']:.0f} h: conservative 8–12 min/fh → "
      f"~{S['total_hours']*8/60/24:.1f}–{S['total_hours']*12/60/24:.1f} days; optimistic 6–8 → "
      f"~{S['total_hours']*6/60/24:.1f}–{S['total_hours']*8/60/24:.1f} days (plus whatever lands before the flip). "
      "The rulings themselves (process all of Drive I, Kamla-first, stop at 500 delivered Kamla h) do not depend on the number; "
      "only the projections do.", ""]

# ---- B. incorrect upload paths ----
L += ["## B. Incorrect upload paths (pipeline QUARANTINES these — not processed, not paid until renamed)", ""]
bad_cats = sorted(k for k in I if k.startswith("incorrect upload path: "))
tot_bad = sum(len(I[k]) for k in bad_cats)
L += [f"{tot_bad} upload folders in total. Hours column = `session.json` duration "
      "(or file-size estimate when marked est). `same id clean?` = the same session id ALSO "
      "exists at a correct path (so the bad copy is a leftover, not lost footage).", ""]
for k in bad_cats:
    rows = [[it["operator"], it["player"] or "—", f"`{it['path']}`",
             ", ".join(it["files"]) or "(empty)",
             f"{it['hours']:.2f}{' est' if it['hours_estimated'] else ''}",
             "yes" if it["same_id_also_at_clean_path"] else "no"]
            for it in I[k]]
    L += sec("B. " + k.replace("incorrect upload path: ", ""),
             ["operator", "player", "path", "files inside", "hours", "same id clean?"], rows)

# ---- C. incomplete ----
k = "canonical session missing required files (pipeline holds it as INCOMPLETE)"
rows = [[it["path"].split("/")[0], it["path"].split("/")[1], it["path"].split("/")[2],
         it["path"].split("/")[3], ", ".join(it["missing"]), ", ".join(it["present"]),
         it["newest_file_utc"][:16], "YES" if it["older_than_48h"] else "no"]
        for it in sorted(I.get(k, []), key=lambda x: x["path"])]
L += ["## C. Clean-path sessions with required files missing", ""]
L += sec("C. missing one or more of video.mp4 / frames.csv / session.json / inputs.jsonl / metadata.json",
         ["game", "operator", "player", "session", "MISSING", "present", "newest file (UTC)", ">48 h old?"],
         rows,
         "The pipeline keeps these as INCOMPLETE and never validates them; >48 h old means the "
         "upload is abandoned, not in flight (the program's own escalation threshold).")

# ---- D. duplicates ----
L += ["## D. Duplicates", ""]
k = "same session id uploaded at 2+ clean paths (counted each time)"
L += sec("D1. " + k, ["session id", "player emails claiming it", "paths", "byte-identical video?"],
         [[it["session_id"],
           ", ".join(sorted({p.split("/")[2] for p in it["paths"]})),
           "<br>".join(f"`{p}`" for p in it["paths"]),
           "yes" if it["byte_identical"] else "NO"] for it in I.get(k, [])],
         "Every one of these is the SAME recording under TWO player emails (same operator). "
         "The CSV counts both copies (as 08-19 did); the pipeline would reject the second as "
         "INT_DUP_CROSS, but the player sheets would show both names — a double-pay claim waiting "
         "to happen. Settled rule is accept-earliest.")
k = "byte-identical video.mp4 under DIFFERENT session ids (re-upload under a new name)"
L += sec("D2. " + k, ["md5", "paths", "hours (each)"],
         [[it["md5"][:10] + "…", "<br>".join(f"`{p}`" for p in it["paths"]), it["hours_each"]]
          for it in I.get(k, [])])

# ---- E. game attribution ----
L += ["## E. Game attribution", ""]
k = "game folder != game_title inside session.json"
L += sec("E1. " + k, ["path", "folder", "session.json says"],
         [[f"`{it['path']}`", it["folder_game"], it["session_json_game"]] for it in I.get(k, [])])
k = "game folder != game slug in the session id"
L += sec("E2. " + k, ["path", "folder", "session-id slug"],
         [[f"`{it['path']}`", it["folder_game"], it["sid_game"]] for it in I.get(k, [])])

# ---- F. durations ----
L += ["## F. Duration / clip-length problems", ""]
k = "duration estimated from file size (no usable session.json)"
L += sec("F1. " + k, ["path", "session.json", "video bytes", "est. hours"],
         [[f"`{it['path']}`", it["session_json"], f"{it['video_bytes']:,}", it["est_hours"]]
          for it in I.get(k, [])])
k = "session shorter than 70 s (undeliverable clip length)"
L += sec("F2. " + k, ["path", "duration (s)"],
         [[f"`{it['path']}`", it["duration_s"]] for it in I.get(k, [])])
k = "zero-duration session"
L += sec("F3. " + k, ["path"], [[f"`{it['path']}`"] for it in I.get(k, [])])
k = "player row with sessions but zero hours"
L += sec("F4. " + k, ["player", "sessions"],
         [[it["player"], it["sessions"]] for it in I.get(k, [])])

# ---- G. folder hygiene ----
L += ["## G. Folder / naming hygiene", ""]
k = "same operator team spelled two ways (splits their totals)"
L += sec("G1. " + k, ["variants (game · folder · sessions)"],
         [[" / ".join(f"{v['game']} · `{v['folder']}` · {v['sessions']}" for v in it["variants"])]
          for it in I.get(k, [])])
k = "player appears under two DIFFERENT operator teams"
L += sec("G2. " + k, ["player", "operator folders"],
         [[it["player"], "; ".join(it["operators"])] for it in I.get(k, [])])
k = "player email contains capital letters"
L += sec("G3. " + k, ["player"], [[it["player"]] for it in I.get(k, [])],
         "Drive paths are case-sensitive; a lowercase copy of the same address would become a second player row.")
k = "same email in two letter-cases (two separate rows)"
L += sec("G4. " + k, ["variants"], [[", ".join(it["variants"])] for it in I.get(k, [])])
k = "player email has a typo'd domain"
L += sec("G5. " + k, ["player", "domain"], [[it["player"], it["domain"]] for it in I.get(k, [])])
k = "player folder with no files at all"
L += sec("G6. " + k, ["operator", "player"],
         [[it["operator"], it["player"]] for it in sorted(I.get(k, []), key=lambda x: x["path"])])
k = "operator folder with no files at all"
L += sec("G7. " + k, ["path"], [[f"`{it['path']}`"] for it in I.get(k, [])])
k = "unexpected extra files inside a session folder"
L += sec("G8. " + k, ["path", "extra files"],
         [[f"`{it['path']}`", ", ".join(f"`{x}`" for x in it["extra"])] for it in I.get(k, [])],
         "`session.rrd`, `rrd_creation.py`, `key_binding.json`/`keybind.json` are normal and not listed. "
         "Misspelt names (`video .mp4`, `Frames.csv`, `sesssion.rrd`) mean the REAL required file is "
         "missing — those folders also appear in C. `Copy of …` / `(2)` sets mean the whole session "
         "was uploaded twice into one folder; `frames.xlsx` means someone opened frames.csv in Excel.")
k = "zip payload instead of loose files"
L += sec("G9. " + k, ["path", "files"],
         [[f"`{it['path']}`", ", ".join(it["files"])] for it in I.get(k, [])])
k = "file outside kamla/ and outer_wilds/ (ignored by pipeline)"
L += sec("G10. " + k, ["path", "size"],
         [[f"`{it['path']}`", f"{it['size']:,}"] for it in I.get(k, [])])

# ---- H. identity ----
L += ["## H. Player identity (needs a human call — the ≥150-distinct-players target depends on it)", ""]
k = "SUSPECTED same person under several emails (same machine + near-identical names)"
L += sec("H1. " + k, ["machine id", "# emails", "emails", "combined hours"],
         [[it["machine_id"], it["n_emails"], ", ".join(it["emails"]), it["combined_hours"]]
          for it in I.get(k, [])],
         "**[speculation]** — a name-similarity heuristic on emails sharing one capture machine. "
         "Only a human (or the operator) can confirm whether these are one person.")
k = "one capture machine id (_c_xxxx) used by several player emails (venue PC or one person with many emails — review)"
L += sec("H2. " + k, ["machine id", "# emails", "emails"],
         [[it["machine_id"], it["n_players"], ", ".join(it["players"])] for it in I.get(k, [])],
         "Shared venue PCs are expected under an operator; listed in full so the identity question can be settled per machine.")

# ---- I. regressions vs 08-19 ----
L += ["## I. Changes against the 08-19 snapshot that should not happen on a read-only drive", ""]
k = "player's hours/sessions went DOWN since 08-19 (deleted or moved on Drive I)"
L += sec("I1. " + k, ["player", "hours 08-19", "hours now", "sessions 08-19", "sessions now", "row gone?"],
         [[it["player"], it["hours_0819"], it["hours_now"], it["sessions_0819"], it["sessions_now"],
           "YES" if it["row_gone"] else "no"] for it in I.get(k, [])],
         "A drop means something was deleted, renamed or moved out of the clean path since 08-19 "
         "(e.g. an operator 'fixing' a folder by moving it). Drive I is supposed to be append-only.")

# ---- index ----
L += ["## Issue counts (index)", "", md_table(["category", "count"],
      [[k, v] for k, v in sorted(S["issue_counts"].items(), key=lambda kv: -kv[1])]), ""]

OUT.write_text("\n".join(L))
print(f"wrote {OUT} ({len(L)} lines)")
