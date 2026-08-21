"""Drive I (collection) raw-hours snapshot + exhaustive issues list.

Same method and columns as drive1-raw-hours-2026-08-19.csv (session a9775c11):
  * rclone lsjson -R --hash --drive-use-created-date drive-collect:  -> listing
  * every session.json copied down (rclone copy --include session.json)
  * pipeline.ingest.parse_listing() decides canonical vs quarantined paths
    (the SAME rule production uses: game/operator/player/session, player must
    be an email, session folder must match the id pattern)
  * hours = session.json duration_seconds; if unreadable, estimated from the
    video.mp4 byte size at ~2.6 GB/h (flagged)

Outputs (in the project root / scratchpad):
  drive1-raw-hours-<date>.csv      per-player table + TOTAL row
  drive1-issues-<date>.md          exhaustive issues list
  drive1-issues-<date>.json        machine-readable version
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path("/Users/adnaan/Documents/hl-projects/hl-gamedata")
SCRATCH = Path(os.environ.get("DRIVE1_WORKDIR", Path(__file__).resolve().parent))  # where run.sh put the listing + session_jsons
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-08-20"
LISTING = SCRATCH / "drive1_listing.json"
SESSION_JSON_DIR = SCRATCH / "session_jsons"
OUT_CSV = REPO / f"drive1-raw-hours-{DATE}.csv"
OUT_MD = REPO / f"drive1-issues-{DATE}.md"
OUT_JSON = SCRATCH / f"drive1-issues-{DATE}.json"
PREV_CSV = REPO / os.environ.get("DRIVE1_PREV_CSV", "drive1-raw-hours-2026-08-19.csv")

sys.path.insert(0, str(REPO))
from pipeline.ingest import parse_listing, _EMAIL_RE, _SESSION_RE  # noqa: E402
from pipeline import config as C  # noqa: E402

BITRATE_BPS = 2.6e9 / 3600.0  # ~2.6 GB/h capture bitrate (program benchmark)
DATE_FOLDER_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
NORMAL_EXTRAS = {"keybind.json", "key_binding.json", "session.rrd", "rrd_creation.py"}
MIN_CLIP_S = 70.0  # delivery requirement: >=70 s clip (CLAUDE.md)
NOW_UTC = (SCRATCH / "listing_finished_utc.txt").read_text().strip()
import datetime as _dt
CUTOFF_48H = (_dt.datetime.strptime(NOW_UTC, "%Y-%m-%dT%H:%M:%SZ")
              - _dt.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def read_session_json(drive_path: str) -> tuple[dict | None, str]:
    p = SESSION_JSON_DIR / drive_path / "session.json"
    if not p.exists():
        return None, "absent"
    try:
        return json.loads(p.read_text()), "ok"
    except (OSError, json.JSONDecodeError) as e:
        return None, f"unreadable ({type(e).__name__})"


COPY_SUFFIX_RE = re.compile(r"^(.*) \(\d+\)$")


def classify_bad_path(path: str, why: str) -> str:
    """Bucket a quarantined path into the categories used in the 08-18/19
    report, plus the new shapes seen on 08-20."""
    parts = Path(path).parts
    n = len(parts)
    if n == 3:
        return "missing player-folder level / files directly in the player folder (depth 3)"
    if n == 5:
        p4, p5 = parts[3], parts[4]
        if DATE_FOLDER_RE.match(p4):
            return "extra date-subfolder nesting (depth 5)"
        if _SESSION_RE.match(p4) and _SESSION_RE.match(p5):
            return "session nested inside another session-id folder (depth 5)"
        if _SESSION_RE.match(p4):
            return "freeform junk folder inside a session-id folder (depth 5)"
        m = COPY_SUFFIX_RE.match(p4)
        if m and _SESSION_RE.match(m.group(1)):
            return "session nested inside a Drive '(N)' copy-suffixed session folder (depth 5)"
        if not _SESSION_RE.match(p4) and _SESSION_RE.match(p5):
            return "extra non-standard subfolder between player and session (depth 5)"
        return "other depth-5 nesting"
    if n > 5:
        return f"too deep (depth {n})"
    if n == 4:
        if "is not an email" in why:
            return "player folder is not an email (depth 4)"
        if "doesn't match the id pattern" in why:
            sess = parts[3]
            m = COPY_SUFFIX_RE.match(sess)
            if m and _SESSION_RE.match(m.group(1)):
                return "session folder carries a Drive '(N)' copy suffix (depth 4)"
            if re.search(r"_c_[0-9a-f]{16}$", sess):
                return "truncated / mangled session id (depth 4)"
            return "junk/placeholder session-folder name (depth 4)"
    if n < 3:
        return f"too shallow (depth {n})"
    return "other"


def alpha_core(email: str) -> str:
    return re.sub(r"[^a-z]", "", email.split("@")[0].lower())


def main() -> None:
    entries = json.loads(LISTING.read_text())
    sessions, quarantined, out_of_tree = parse_listing(entries)

    files_by_dir: dict[str, list[dict]] = defaultdict(list)
    dirs: set[str] = set()
    for e in entries:
        if e.get("IsDir"):
            dirs.add(e["Path"])
            continue
        parent = str(Path(e["Path"]).parent)
        files_by_dir[parent].append(e)

    def video_bytes(d: str) -> int:
        return next((f.get("Size", 0) for f in files_by_dir.get(d, [])
                     if f.get("Name") == "video.mp4"), 0)

    rows: dict[str, dict] = {}

    def row_for(player: str) -> dict:
        if player not in rows:
            rows[player] = {
                "player_email": player, "operators": set(),
                "kamla_hours": 0.0, "outer_wilds_hours": 0.0,
                "kamla_sessions": 0, "outer_wilds_sessions": 0,
                "incorrect_upload_path": 0,
                "estimated_durations_of_incorrect_upload_paths": 0.0,
                "game_folder_mismatch": 0,
            }
        return rows[player]

    issues: dict[str, list] = defaultdict(list)
    session_info = []  # per canonical session, for the issue scans

    # ---------------- canonical sessions ----------------
    for ds in sessions:
        r = row_for(ds.player_email)
        r["operators"].add(ds.operator_email)
        sj, sj_status = read_session_json(ds.drive_path)
        dur_s = None
        claimed_game = None
        if sj is not None:
            dur_s = sj.get("duration_seconds")
            claimed_game = norm(sj.get("game_title", ""))
        estimated = False
        if not isinstance(dur_s, (int, float)):
            estimated = True
            vb = video_bytes(ds.drive_path)
            dur_s = vb / BITRATE_BPS if vb else 0.0
            issues["duration estimated from file size (no usable session.json)"].append({
                "path": ds.drive_path, "session_json": sj_status,
                "video_bytes": vb, "est_hours": round(dur_s / 3600, 3)})
        if ds.game == "kamla":
            r["kamla_hours"] += dur_s / 3600.0
            r["kamla_sessions"] += 1
        elif ds.game == "outer_wilds":
            r["outer_wilds_hours"] += dur_s / 3600.0
            r["outer_wilds_sessions"] += 1
        if claimed_game and claimed_game in C.GAMES and claimed_game != ds.game:
            r["game_folder_mismatch"] += 1
            issues["game folder != game_title inside session.json"].append({
                "path": ds.drive_path, "folder_game": ds.game,
                "session_json_game": claimed_game})
        if ds.slug_game and ds.slug_game != ds.game:
            issues["game folder != game slug in the session id"].append({
                "path": ds.drive_path, "folder_game": ds.game,
                "sid_game": ds.slug_game})
        missing = [f for f in C.REQUIRED_FILES if f not in ds.files] \
            if ds.payload != "zip" else []
        if missing:
            newest = max((v["ctime"] for v in ds.files.values() if v["ctime"]), default="")
            issues["canonical session missing required files (pipeline holds it as INCOMPLETE)"].append({
                "path": ds.drive_path, "missing": missing,
                "present": sorted(ds.files), "newest_file_utc": newest,
                "older_than_48h": bool(newest and newest < CUTOFF_48H)})
        if ds.payload == "zip":
            issues["zip payload instead of loose files"].append({
                "path": ds.drive_path, "files": sorted(ds.files)})
        extra = sorted(f for f in ds.files
                       if f not in C.REQUIRED_FILES and f not in NORMAL_EXTRAS
                       and not f.lower().endswith(".zip"))
        if extra and ds.payload != "zip":
            issues["unexpected extra files inside a session folder"].append({
                "path": ds.drive_path, "extra": extra})
        if not estimated and dur_s < MIN_CLIP_S:
            issues[f"session shorter than {int(MIN_CLIP_S)} s (undeliverable clip length)"].append({
                "path": ds.drive_path, "duration_s": round(dur_s, 1)})
        if not estimated and dur_s == 0:
            issues["zero-duration session"].append({"path": ds.drive_path})
        session_info.append({
            "sid": ds.session_id, "path": ds.drive_path, "game": ds.game,
            "operator": ds.operator_email, "player": ds.player_email,
            "dur_s": dur_s, "estimated": estimated,
            "md5": ds.files.get("video.mp4", {}).get("md5", ""),
            "vbytes": ds.files.get("video.mp4", {}).get("size", 0),
        })

    # ---------------- quarantined (incorrect upload paths) ----------------
    unattributed_count = 0
    unattributed_dur = 0.0
    for path, why in quarantined:
        parts = Path(path).parts
        player = next((p for p in parts if _EMAIL_RE.match(p)), None)
        sj, sj_status = read_session_json(path)
        dur_s = sj.get("duration_seconds") if sj else None
        est = False
        if not isinstance(dur_s, (int, float)):
            est = True
            vb = video_bytes(path)
            dur_s = vb / BITRATE_BPS if vb else 0.0
        cat = classify_bad_path(path, why)
        sid = next((p for p in reversed(parts) if _SESSION_RE.match(p)), None)
        op = parts[1] if len(parts) > 1 else ""
        issues["incorrect upload path: " + cat].append({
            "operator": op, "player": player, "path": path, "why": why,
            "session_id_seen": sid, "files": sorted(f["Name"] for f in files_by_dir[path]),
            "hours": round(dur_s / 3600, 3), "hours_estimated": est})
        if player is None:
            unattributed_count += 1
            unattributed_dur += dur_s
            continue
        r = row_for(player)
        pi = parts.index(player)
        if pi > 0:
            r["operators"].add(parts[pi - 1])
        r["incorrect_upload_path"] += 1
        r["estimated_durations_of_incorrect_upload_paths"] += dur_s / 3600.0
    if unattributed_count:
        r = row_for("UNKNOWN")
        r["incorrect_upload_path"] += unattributed_count
        r["estimated_durations_of_incorrect_upload_paths"] += unattributed_dur / 3600.0

    # quarantined session ids that ALSO exist at a clean path (already healed)
    clean_sids = {s["sid"] for s in session_info}
    for cat, lst in list(issues.items()):
        if cat.startswith("incorrect upload path"):
            for it in lst:
                it["same_id_also_at_clean_path"] = bool(
                    it["session_id_seen"] and it["session_id_seen"] in clean_sids)

    # ---------------- out-of-tree files ----------------
    oot = [e for e in entries if not e.get("IsDir")
           and Path(e["Path"]).parts[0] not in C.GAMES]
    for e in oot:
        issues["file outside kamla/ and outer_wilds/ (ignored by pipeline)"].append({
            "path": e["Path"], "size": e.get("Size", 0)})

    # ---------------- duplicate session ids across paths ----------------
    by_sid = defaultdict(list)
    for s in session_info:
        by_sid[s["sid"]].append(s)
    for sid, grp in by_sid.items():
        if len(grp) > 1:
            issues["same session id uploaded at 2+ clean paths (counted each time)"].append({
                "session_id": sid,
                "paths": [g["path"] for g in grp],
                "md5s": [g["md5"] for g in grp],
                "byte_identical": len({g["md5"] for g in grp}) == 1})

    # ---------------- duplicate videos (same md5, different session id) ----
    by_md5 = defaultdict(list)
    for s in session_info:
        if s["md5"]:
            by_md5[s["md5"]].append(s)
    for md5, grp in by_md5.items():
        if len({g["sid"] for g in grp}) > 1:
            issues["byte-identical video.mp4 under DIFFERENT session ids (re-upload under a new name)"].append({
                "md5": md5, "paths": [g["path"] for g in grp],
                "hours_each": round(grp[0]["dur_s"] / 3600, 3)})

    # ---------------- same player under several operator folders ---------
    for p, r in sorted(rows.items()):
        teams = {tuple(sorted(norm(o).split("_"))) for o in r["operators"]}
        if len(teams) > 1:
            issues["player appears under two DIFFERENT operator teams"].append({
                "player": p, "operators": sorted(r["operators"])})

    # ---------------- operator-folder name variants ----------------------
    op_names = Counter()
    for s in session_info:
        op_names[(s["game"], s["operator"])] += 1
    by_key = defaultdict(list)
    for (g, op), n in op_names.items():
        key = tuple(sorted(norm(op).split("_")))
        by_key[key].append((g, op, n))
    for key, lst in by_key.items():
        names = {op for _, op, _ in lst}
        if len(names) > 1:
            issues["same operator team spelled two ways (splits their totals)"].append({
                "variants": [{"game": g, "folder": op, "sessions": n} for g, op, n in sorted(lst)]})

    # ---------------- capture-machine id shared by several emails ----------
    mid_players = defaultdict(set)
    for si in session_info:
        mid_players[si["sid"][-16:]].add(si["player"])
    shared = {k: v for k, v in mid_players.items() if len(v) > 1}
    for mid, pl in sorted(shared.items(), key=lambda kv: -len(kv[1])):
        issues["one capture machine id (_c_xxxx) used by several player emails (venue PC or one person with many emails — review)"].append({
            "machine_id": mid, "n_players": len(pl), "players": sorted(pl)})
        pl = sorted(pl)
        parent = {e: e for e in pl}

        def find(x):
            while parent[x] != x:
                x = parent[x]
            return x
        for i in range(len(pl)):
            for j in range(i + 1, len(pl)):
                a, b = alpha_core(pl[i]), alpha_core(pl[j])
                if not a or not b:
                    continue
                short, long_ = (a, b) if len(a) <= len(b) else (b, a)
                if len(short) >= 6 and (short in long_ or a[:7] == b[:7]):
                    parent[find(pl[i])] = find(pl[j])
        clusters = defaultdict(list)
        for e in pl:
            clusters[find(e)].append(e)
        for cl in clusters.values():
            if len(cl) > 1:
                hrs = sum(si["dur_s"] for si in session_info if si["player"] in cl) / 3600
                issues["SUSPECTED same person under several emails (same machine + near-identical names)"].append({
                    "machine_id": mid, "emails": sorted(cl), "n_emails": len(cl),
                    "combined_hours": round(hrs, 3)})

    # ---------------- player-email hygiene ----------------
    all_players = set(rows)
    lower_map = defaultdict(set)
    for p in sorted(all_players):
        lower_map[p.lower()].add(p)
    for low, variants in sorted(lower_map.items()):
        if len(variants) > 1:
            issues["same email in two letter-cases (two separate rows)"].append(
                {"variants": sorted(variants)})
    for p in sorted(all_players):
        if p != p.lower():
            issues["player email contains capital letters"].append({"player": p})
        dom = p.rsplit("@", 1)[-1].lower() if "@" in p else ""
        if dom in ("gmai.com", "gmial.com", "gamil.com", "gmail.co", "gmail.con"):
            issues["player email has a typo'd domain"].append({"player": p, "domain": dom})

    # ---------------- empty player / operator folders ----------------
    for d in sorted(dirs):
        parts = Path(d).parts
        if parts[0] not in C.GAMES:
            continue
        has_files = any(k == d or k.startswith(d + "/") for k in files_by_dir)
        if len(parts) == 3 and _EMAIL_RE.match(parts[2]) and not has_files:
            issues["player folder with no files at all"].append({
                "operator": parts[1], "player": parts[2], "path": d})
        if len(parts) == 2 and not has_files:
            issues["operator folder with no files at all"].append({"path": d})

    # ---------------- players with sessions but ~0 hours --------------
    for p, r in sorted(rows.items()):
        if (r["kamla_sessions"] + r["outer_wilds_sessions"]) > 0 and \
                (r["kamla_hours"] + r["outer_wilds_hours"]) < 1e-6:
            issues["player row with sessions but zero hours"].append({
                "player": p, "sessions": r["kamla_sessions"] + r["outer_wilds_sessions"]})

    # ---------------- compare against the 08-19 snapshot ----------------
    prev = {}
    if PREV_CSV.exists():
        for pr in csv.DictReader(PREV_CSV.open()):
            if pr["player_email"] and pr["player_email"] != "TOTAL":
                prev[pr["player_email"]] = pr
    for p, pr in prev.items():
        cur = rows.get(p)
        ph, ps = float(pr["total_hours"]), int(pr["total_sessions"])
        ch = (cur["kamla_hours"] + cur["outer_wilds_hours"]) if cur else 0.0
        cs = (cur["kamla_sessions"] + cur["outer_wilds_sessions"]) if cur else 0
        if cs < ps or ch < ph - 0.01:
            issues["player's hours/sessions went DOWN since 08-19 (deleted or moved on Drive I)"].append({
                "player": p, "hours_0819": ph, "hours_now": round(ch, 3),
                "sessions_0819": ps, "sessions_now": cs, "row_gone": cur is None})

    # ---------------- write CSV ----------------
    header = ["player_email", "operator(s)", "kamla_hours", "outer_wilds_hours",
              "total_hours", "kamla_sessions", "outer_wilds_sessions",
              "total_sessions", "incorrect_upload_path",
              "estimated_durations_of_incorrect_upload_paths",
              "game_folder_mismatch"]
    out_rows = []
    for player, r in rows.items():
        th = r["kamla_hours"] + r["outer_wilds_hours"]
        ts = r["kamla_sessions"] + r["outer_wilds_sessions"]
        out_rows.append([
            player, "; ".join(sorted(r["operators"])),
            round(r["kamla_hours"], 3), round(r["outer_wilds_hours"], 3), round(th, 3),
            r["kamla_sessions"], r["outer_wilds_sessions"], ts,
            r["incorrect_upload_path"],
            round(r["estimated_durations_of_incorrect_upload_paths"], 3),
            r["game_folder_mismatch"]])
    out_rows.sort(key=lambda row: row[4], reverse=True)
    totals = ["TOTAL", "",
              round(sum(r[2] for r in out_rows), 3), round(sum(r[3] for r in out_rows), 3),
              round(sum(r[4] for r in out_rows), 3),
              sum(r[5] for r in out_rows), sum(r[6] for r in out_rows), sum(r[7] for r in out_rows),
              sum(r[8] for r in out_rows), round(sum(r[9] for r in out_rows), 3),
              sum(r[10] for r in out_rows)]
    with OUT_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(out_rows)
        w.writerow([])
        w.writerow(totals)

    # ---------------- hour adjustments (NOT applied to the CSV; method kept
    # identical to 08-19 so the snapshots stay comparable) ----------------
    dup_extra_hours = 0.0
    for sid, grp in by_sid.items():
        if len(grp) > 1:
            grp_sorted = sorted(grp, key=lambda g: g["path"])
            dup_extra_hours += sum(g["dur_s"] for g in grp_sorted[1:]) / 3600
    inc_paths = {it["path"] for it in issues.get(
        "canonical session missing required files (pipeline holds it as INCOMPLETE)", [])}
    novideo_paths = {it["path"] for it in issues.get(
        "canonical session missing required files (pipeline holds it as INCOMPLETE)", [])
        if "video.mp4" in it["missing"]}
    inc_hours = sum(si["dur_s"] for si in session_info if si["path"] in inc_paths) / 3600
    novideo_hours = sum(si["dur_s"] for si in session_info if si["path"] in novideo_paths) / 3600
    bad_path_hours = sum(r[9] for r in [])  # placeholder, filled below

    # ---------------- summary + issues out ----------------
    n_est = len(issues.get("duration estimated from file size (no usable session.json)", []))
    summary = {
        "date": DATE, "entries": len(entries),
        "files": sum(len(v) for v in files_by_dir.values()),
        "players": len(rows), "valid_sessions": len(sessions),
        "quarantined": len(quarantined), "out_of_tree_files": out_of_tree,
        "kamla_hours": totals[2], "ow_hours": totals[3], "total_hours": totals[4],
        "kamla_sessions": totals[5], "ow_sessions": totals[6],
        "size_estimated_sessions": n_est,
        "dup_sessions_extra_copies": sum(len(g) - 1 for g in by_sid.values() if len(g) > 1),
        "dup_extra_hours": round(dup_extra_hours, 3),
        "incomplete_sessions": len(inc_paths), "incomplete_hours": round(inc_hours, 3),
        "no_video_sessions": len(novideo_paths), "no_video_hours": round(novideo_hours, 3),
        "bad_path_hours": totals[9],
        "players_kamla": sum(1 for r in out_rows if r[5] > 0),
        "players_ow": sum(1 for r in out_rows if r[6] > 0),
        "issue_counts": {k: len(v) for k, v in sorted(issues.items())},
    }
    OUT_JSON.write_text(json.dumps({"summary": summary, "issues": issues}, indent=1))
    print(json.dumps(summary, indent=1))
    print(f"wrote {OUT_CSV}")
    print(f"wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
