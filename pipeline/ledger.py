"""SQLite ledger — the permanent record behind payment (plan §8).

Rules: the ledger and dossiers are NEVER deleted; every state transition
appends to the `events` audit table; a daily backup copy is kept (14).
`duration_delivered_s` summed per player/game is the paid number.

Schema is plan §8 verbatim plus one additive column `duration_raw_s`
(pre-trim clip length probed at ingest) — needed for the §14 "collected"
line; additive-only so §8 stays a valid subset.

Concurrency: one process (run.py holds the run lock), up to three writer
threads (D/V/U under the R20 overlap driver), ONE connection per thread,
short transactions; validation subprocesses never touch the ledger — the
owning thread writes. WAL + busy_timeout so concurrent writers queue
briefly instead of raising `database is locked`, and a crashed process
never corrupts the file.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY,
  parent_id TEXT NULL,
  game TEXT, operator_email TEXT, player_email TEXT,
  drive_path TEXT, drive_ctime TEXT,
  md5_video TEXT, bytes INTEGER,
  state TEXT, bin INTEGER NULL,
  reasons_json TEXT,
  fix_attempts INTEGER DEFAULT 0,
  duration_delivered_s REAL NULL,
  duration_raw_s REAL NULL,
  rrd_sampled INTEGER DEFAULT 0,
  delivered_at TEXT NULL, dossier_path TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS batches(
  batch_no INTEGER PRIMARY KEY, started TEXT, finished TEXT, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, session_id TEXT, ts TEXT,
  from_state TEXT, to_state TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS incomplete(
  drive_path TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, missing_json TEXT
);
-- Per-piece payment memory (RULED C, Adnaan 2026-08-18 at D0; r-loop 9
-- #1/#18): which pieces of a torn-down tree were already paid on a sent
-- sheet. Written by recal_refix_reset BEFORE teardown; read by
-- build_sheet_rows to exclude a re-delivered same-id piece's hours while
-- the recovered fix-failed hours stay payable. NEVER auto-deleted —
-- supersede/heal/rebuild leave it alone; evidence of money moved
-- outlives byte changes (a stale id collision surfaces LOUDLY on the
-- sheet side instead of paying twice or silently swallowing).
CREATE TABLE IF NOT EXISTS paid_pieces(
  root_id TEXT, session_id TEXT, seconds REAL NULL, seg TEXT NULL,
  recorded_at TEXT,
  PRIMARY KEY(root_id, session_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_state ON sessions(state);
CREATE INDEX IF NOT EXISTS idx_sessions_md5 ON sessions(md5_video);
CREATE INDEX IF NOT EXISTS idx_events_sid ON events(session_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(_SCHEMA)
        # additive migration (like duration_raw_s, §8 stays a subset):
        # uploaded_reported_at marks a root whose uploaded-hours a payment
        # sheet has COUNTED — the late-arrival guard keys on its absence
        # (d3/review-r4: any session becoming countable after its cohort
        # window was reported would otherwise be dropped forever)
        # accepted_reported_at marks a node whose ACCEPTED hours (or reject
        # labels) a payment sheet has counted. It is a SECOND, independent
        # mark: uploaded_reported_at used to do both jobs, and a root
        # stamped while its split children were still in flight could never
        # re-enter a sheet, so footage that shipped to the client was never
        # paid for (RULED, Adnaan 2026-08-18)
        cols = {r["name"] for r in
                self.db.execute("PRAGMA table_info(sessions)")}
        if "uploaded_reported_at" not in cols:
            self.db.execute("ALTER TABLE sessions "
                            "ADD COLUMN uploaded_reported_at TEXT NULL")
        if "accepted_reported_at" not in cols:
            self.db.execute("ALTER TABLE sessions "
                            "ADD COLUMN accepted_reported_at TEXT NULL")
        # tree_sealed_at is the whole-tree SEAL (r-loop 8), written ONLY by
        # recal_refix_reset when it tears down a tree whose accepted hours
        # are already on a SENT sheet. The root's accepted_reported_at used
        # to carry this second meaning too, so an ordinary daily send
        # stamping a DELIVERED/REJECTED root's own count locked its live
        # children's future hours out of every sheet forever.
        if "tree_sealed_at" not in cols:
            self.db.execute("ALTER TABLE sessions "
                            "ADD COLUMN tree_sealed_at TEXT NULL")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---------------------------------------------------------------- rows
    def get(self, session_id: str) -> sqlite3.Row | None:
        cur = self.db.execute("SELECT * FROM sessions WHERE session_id=?",
                              (session_id,))
        return cur.fetchone()

    def by_state(self, *states: str) -> list[sqlite3.Row]:
        q = ",".join("?" for _ in states)
        cur = self.db.execute(
            f"SELECT * FROM sessions WHERE state IN ({q}) "
            f"ORDER BY drive_ctime, session_id", states)
        return cur.fetchall()

    def by_md5(self, md5: str) -> list[sqlite3.Row]:
        cur = self.db.execute(
            "SELECT * FROM sessions WHERE md5_video=? ORDER BY drive_ctime",
            (md5,))
        return cur.fetchall()

    def insert_session(self, *, session_id: str, game: str,
                       operator_email: str, player_email: str,
                       drive_path: str, drive_ctime: str, md5_video: str,
                       bytes_: int, state: str, parent_id: str | None = None,
                       detail: str = "") -> None:
        now = _now()
        self.db.execute(
            "INSERT INTO sessions(session_id, parent_id, game, operator_email,"
            " player_email, drive_path, drive_ctime, md5_video, bytes, state,"
            " reasons_json, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, parent_id, game, operator_email, player_email,
             drive_path, drive_ctime, md5_video, bytes_, state, "[]",
             now, now))
        self.db.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state, detail)"
            " VALUES(?,?,?,?,?)", (session_id, now, "", state, detail))
        self.db.commit()

    def set_state(self, session_id: str, to_state: str,
                  detail: str = "") -> None:
        row = self.get(session_id)
        from_state = row["state"] if row else ""
        now = _now()
        self.db.execute(
            "UPDATE sessions SET state=?, updated_at=? WHERE session_id=?",
            (to_state, now, session_id))
        self.db.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state, detail)"
            " VALUES(?,?,?,?,?)",
            (session_id, now, from_state, to_state, detail))
        self.db.commit()

    _UPDATE_ALLOWED = frozenset({
        "bin", "reasons_json", "fix_attempts",
        "duration_delivered_s", "duration_raw_s", "rrd_sampled",
        "delivered_at", "dossier_path", "md5_video", "bytes",
        "game", "drive_path", "drive_ctime", "parent_id",
        "operator_email", "player_email", "uploaded_reported_at",
        "accepted_reported_at", "tree_sealed_at"})

    def update(self, session_id: str, **fields) -> None:
        """Update non-state columns (state changes go through set_state)."""
        assert "state" not in fields, "use set_state for state transitions"
        bad = set(fields) - self._UPDATE_ALLOWED
        assert not bad, f"unknown ledger fields: {bad}"
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [_now(), session_id]
        self.db.execute(
            f"UPDATE sessions SET {sets}, updated_at=? WHERE session_id=?",
            vals)
        self.db.commit()

    def update_where_md5(self, session_id: str, md5_video: str,
                         **fields) -> int:
        """update() with a compare-and-set guard on md5_video (r-loop 11
        #7). The daily stamps run in hl-H while hl-S concurrently
        supersedes/heals, and the stamp window spans Telegram sends
        (minutes) — a stamp landing unconditionally on a reset slot
        strands the corrected re-upload's hours off every future sheet.
        supersede and the different-md5 heal both write the new md5
        atomically WITH their mark clears, so md5 equality is exactly
        'the bytes this sheet counted'. Returns rows changed (0 = the
        bytes changed under the caller; skip loudly — the new hours stay
        countable)."""
        assert "state" not in fields, "use set_state for state transitions"
        bad = set(fields) - self._UPDATE_ALLOWED
        assert not bad, f"unknown ledger fields: {bad}"
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [_now(), session_id, md5_video]
        cur = self.db.execute(
            f"UPDATE sessions SET {sets}, updated_at=?"
            f" WHERE session_id=? AND md5_video=?", vals)
        self.db.commit()
        return cur.rowcount

    def set_reasons(self, session_id: str, reasons: list[dict],
                    bin_: int | None) -> None:
        self.db.execute(
            "UPDATE sessions SET reasons_json=?, bin=?, updated_at=?"
            " WHERE session_id=?",
            (json.dumps(reasons), bin_, _now(), session_id))
        self.db.commit()

    # ---------------------------------------------- per-piece payment memory
    def record_paid_piece(self, root_id: str, session_id: str,
                          seconds: float | None, seg: str | None) -> None:
        """Remember that `session_id`'s delivered hours were counted on a
        sent sheet before its tree was torn down (RULED C, Adnaan
        2026-08-18; r-loop 9 #1/#18). INSERT OR IGNORE: the first record
        describes the payment that actually happened — later passes never
        overwrite it. `seconds` is the piece's duration_delivered_s at
        record time (the sheet-side match key); `seg` is forensic (the
        child's split-segment detail when known)."""
        self.db.execute(
            "INSERT OR IGNORE INTO paid_pieces"
            "(root_id, session_id, seconds, seg, recorded_at)"
            " VALUES(?,?,?,?,?)",
            (root_id, session_id, seconds, seg, _now()))
        self.db.commit()

    def paid_pieces_for(self, root_id: str) -> dict:
        """{session_id: seconds} of the pieces already paid under this
        root. Never auto-cleared — see the schema comment."""
        return {r["session_id"]: r["seconds"] for r in self.db.execute(
            "SELECT session_id, seconds FROM paid_pieces WHERE root_id=?",
            (root_id,))}

    # ------------------------------------------------------------ supersede
    def archive_dossier(self, session_id: str, dossier_root: Path) -> None:
        """Move the current dossier generation under history/.

        The dossier is the evidence of record behind payment (design §13),
        and fix._append_fixlog APPENDS to fixlog.json while
        validate._write_verdict overwrites verdict.json and
        deliver.finalize_rejected overwrites coaching.md. So without this,
        a second pass over DIFFERENT bytes silently merges into the first
        one's record: the prior verdict is gone and the audit trail
        contains fixes applied to bytes that are no longer there.

        Extracted from supersede() so the QUARANTINED-path heal can call
        it too (r-loop 5) — that branch states it is "a FRESH-upload
        event: reset the slot like supersede does" and duplicated every
        part of supersede EXCEPT this one.
        """
        dossier = Path(dossier_root) / session_id
        if not dossier.exists():
            return
        hist = dossier / "history"
        hist.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dst = hist / f"superseded-{stamp}"
        dst.mkdir(exist_ok=True)
        for f in dossier.iterdir():
            if f.name != "history":
                shutil.move(str(f), dst / f.name)

    def latest_prev_md5(self, session_id: str) -> str:
        """The newest prev_md5 breadcrumb for the sid ('' if none) —
        the real md5 the '' sentinel replaced, in the shared
        heal/supersede breadcrumb format the download-time deferral
        parses. ZIP_ADJ_CHANGED markers deliberately avoid the literal
        token (entry 55), so this can never pick one up."""
        prev = self.db.execute(
            "SELECT detail FROM events WHERE session_id=? AND detail"
            " LIKE '%prev_md5=%' ORDER BY ts DESC, rowid DESC LIMIT 1",
            (session_id,)).fetchone()
        return prev["detail"].rsplit("prev_md5=", 1)[1].strip() \
            if prev else ""

    def supersede(self, session_id: str, *, new_md5: str, new_bytes: int,
                  new_ctime: str, dossier_root: Path) -> None:
        """Same session-id re-uploaded with different video md5 after a
        reject: archive the prior verdict in the dossier history/, reset
        fix_attempts, re-enter as fresh (§9.1 supersede rule). Hours always
        follow the latest state."""
        row = self.get(session_id)
        assert row is not None
        self.archive_dossier(session_id, dossier_root)
        now = _now()
        # r20 #7 (N4): '' means unknowable REGARDLESS of the stored md5
        # — requiring a real stored md5 made M4's guard self-defeat:
        # the first '' supersede creates the very stamps+''-md5 row
        # shape a second '' supersede (bad-archive quarantine, then the
        # coached corrected re-zip) then full-cleared with zero byte
        # evidence, re-opening the r19 #5 double-pay. The breadcrumb
        # below is still written only over a REAL prior md5, so the
        # deferral's newest-breadcrumb lookup keeps naming the bytes
        # the sheets actually counted.
        zip_unknowable = not new_md5
        # r20 #3 (N4): a REAL md5 landing over a stored-'' slot is not
        # automatically CHANGED bytes either — '' is the unknowable
        # sentinel, and the newest prev_md5 breadcrumb names the bytes
        # the sheets counted. Equal proves the bytes identical (the
        # plain-file re-upload of the SAME footage after a corrupt zip,
        # the scan's payload-switch): preserve the payment columns.
        # Different — or no breadcrumb to consult — keeps the full
        # clear (known-new bytes / nothing provable).
        proven_identical = bool(new_md5) and not row["md5_video"] \
            and new_md5 == self.latest_prev_md5(session_id)
        preserve = zip_unknowable or proven_identical
        # uploaded_reported_at cleared below for a REAL new md5: the
        # stamp belonged to the OLD upload's sheet — inherited, it
        # blocked the corrected re-upload's hours from the late-arrival
        # guard on every future sheet (review-r5 #7).
        # accepted_reported_at goes with it for the same reason: the new
        # bytes' delivered hours are genuinely new, and an inherited
        # accepted mark would seal them out of every future sheet (the
        # §4 bug in its other form). tree_sealed_at likewise (r-loop 8):
        # new bytes = new hours; a stale whole-tree seal would lock the
        # re-upload's tree out of every sheet.
        #
        # The zip-class '' writer must NOT make those clears (r19 #5):
        # '' means the bytes are UNKNOWABLE from the Drive listing, and
        # every rationale above starts from "the bytes changed". An
        # identical-bytes re-zip (new Drive ctime, same footage) had its
        # counted stamps cleared here, the download-time adjudicator
        # correctly declined to re-clear — and restored nothing — so the
        # same hours and reject label re-entered a second sent sheet: a
        # silent double-pay. The payment columns now ride the deferral
        # for this writer: PRESERVED here, cleared at download iff the
        # bytes prove changed (the prev_md5 breadcrumb below + the
        # durable ZIP_ADJ_CHANGED marker at ingest.download). The
        # never-downloads case is money-safe too: preserved stamps mean
        # the root cannot re-count.
        #
        # ONE exception rides the preserve arm (r20 #2≡#6, N3): on this
        # writer's production population the row is REJECTED/QUARANTINED
        # (supersede applies after a reject/quarantine only), where an
        # accepted mark is the refix doctrine's LABELS-only mark — "a
        # REJECTED node carrying an accepted mark had its LABELS
        # counted, not hours" (recal_refix_reset, which clears it on its
        # own re-runs). Preserved, it stranded a later-DELIVERED
        # identical-bytes re-run's hours off every sheet silently and
        # forever (build_sheet_rows skips any accepted-marked node and
        # no re-entry arm fires); cleared, it costs at most one
        # re-printed reject label if the re-run re-rejects — no money
        # moves through a label. On a DELIVERED row the mark IS an
        # hours mark and stays preserved (belt-and-braces: no caller
        # supersedes a DELIVERED row).
        pay_clears = (("" if row["state"] == "DELIVERED"
                       else " accepted_reported_at=NULL,")
                      if preserve else
                      " duration_raw_s=NULL, uploaded_reported_at=NULL,"
                      " accepted_reported_at=NULL, tree_sealed_at=NULL,")
        self.db.execute(
            "UPDATE sessions SET md5_video=?, bytes=?, drive_ctime=?,"
            " fix_attempts=0, bin=NULL, reasons_json='[]',"
            " duration_delivered_s=NULL, rrd_sampled=0,"
            " delivered_at=NULL," + pay_clears + " updated_at=?"
            " WHERE session_id=?",
            (new_md5, new_bytes, new_ctime, now, session_id))
        detail = f"superseded: new md5 {new_md5}"
        if zip_unknowable and row["md5_video"]:
            # zip-class supersede (ingest's re-upload branch passes ""):
            # the new bytes' md5 is UNKNOWABLE from the Drive listing, so
            # remember the pre-reset md5 in the SAME breadcrumb format the
            # quarantine heal writes (ingest.download parses it with
            # rsplit("prev_md5=", 1)). The download-time deferral then
            # adjudicates this '' writer exactly like the heal class: a
            # stamp that falsely landed in the window (the CAS-miss ''
            # arm cannot tell the two '' writers apart) self-heals when
            # the bytes prove CHANGED; an identical-bytes re-zip lets it
            # stand (r-loop 13 #1/#3).
            detail += f"; prev_md5={row['md5_video']}"
        self.db.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state, detail)"
            " VALUES(?,?,?,?,?)",
            (session_id, now, row["state"], "DISCOVERED", detail))
        self.db.execute(
            "UPDATE sessions SET state='DISCOVERED' WHERE session_id=?",
            (session_id,))
        self.db.commit()

    # ----------------------------------------------------------- incomplete
    def incomplete_seen(self, drive_path: str, missing: list[str]) -> None:
        now = _now()
        self.db.execute(
            "INSERT INTO incomplete(drive_path, first_seen, last_seen,"
            " missing_json) VALUES(?,?,?,?)"
            " ON CONFLICT(drive_path) DO UPDATE SET last_seen=?,"
            " missing_json=?",
            (drive_path, now, now, json.dumps(missing), now,
             json.dumps(missing)))
        self.db.commit()

    def incomplete_resolved(self, drive_path: str) -> None:
        self.db.execute("DELETE FROM incomplete WHERE drive_path=?",
                        (drive_path,))
        self.db.commit()

    def incomplete_missing(self, drive_path: str) -> list | None:
        """missing_json for one incomplete row; None if no row exists."""
        row = self.db.execute(
            "SELECT missing_json FROM incomplete WHERE drive_path=?",
            (drive_path,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["missing_json"] or "[]")
        except json.JSONDecodeError:
            return []

    def incomplete_list(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM incomplete ORDER BY first_seen").fetchall()

    # -------------------------------------------------------------- batches
    def start_batch(self, sessions: list[str] | None = None) -> int:
        """Open a batch. The sid list is written at START (not only at
        finish) so an in-flight batch — the only kind that ever needs
        resuming — regroups exactly after a kill (plan §6)."""
        cur = self.db.execute(
            "INSERT INTO batches(started, summary_json) VALUES(?, ?)",
            (_now(), json.dumps({"sessions": sessions or []})))
        self.db.commit()
        return cur.lastrowid

    def open_batches(self) -> list[sqlite3.Row]:
        """Batches started but never finished — resume regroup input."""
        return self.db.execute(
            "SELECT * FROM batches WHERE finished IS NULL "
            "ORDER BY batch_no").fetchall()

    def finish_batch(self, batch_no: int, summary: dict) -> None:
        self.db.execute(
            "UPDATE batches SET finished=?, summary_json=? WHERE batch_no=?",
            (_now(), json.dumps(summary), batch_no))
        self.db.commit()

    # ---------------------------------------------------------------- sums
    def delivered_hours(self, game: str | None = None,
                        since_iso: str | None = None) -> float:
        q = ("SELECT COALESCE(SUM(duration_delivered_s),0) s FROM sessions"
             " WHERE state='DELIVERED'")
        args: list = []
        if game:
            q += " AND game=?"
            args.append(game)
        if since_iso:
            q += " AND delivered_at>=?"
            args.append(since_iso)
        return self.db.execute(q, args).fetchone()["s"] / 3600.0

    def collected_hours(self, game: str | None = None) -> float:
        """Raw footage hours seen so far (pre-trim, ingested or later).
        Tracks the 600 h/game collection buffer (R16)."""
        q = ("SELECT COALESCE(SUM(duration_raw_s),0) s FROM sessions"
             " WHERE duration_raw_s IS NOT NULL AND parent_id IS NULL")
        args: list = []
        if game:
            q += " AND game=?"
            args.append(game)
        return self.db.execute(q, args).fetchone()["s"] / 3600.0

    def player_rollup(self, since_iso: str | None = None) -> list[sqlite3.Row]:
        """Per player/game: sessions uploaded, delivered, rejected, hours.

        Uploads count PARENT rows only (one per Drive upload); delivered /
        rejected / hours include split children too — a segment's delivered
        seconds are the player's paid seconds (F9)."""
        q = """
        SELECT game, operator_email, player_email,
               SUM(CASE WHEN parent_id IS NULL THEN 1 ELSE 0 END) uploaded,
               SUM(CASE WHEN state='DELIVERED' THEN 1 ELSE 0 END) delivered,
               SUM(CASE WHEN state='REJECTED' THEN 1 ELSE 0 END) rejected,
               COALESCE(SUM(CASE WHEN state='DELIVERED'
                        THEN duration_delivered_s END),0)/3600.0 hours
        FROM sessions WHERE state NOT IN ('DUPLICATE')
        """
        args: list = []
        if since_iso:
            q += " AND updated_at>=?"
            args.append(since_iso)
        q += " GROUP BY game, operator_email, player_email ORDER BY game, operator_email, player_email"
        return self.db.execute(q, args).fetchall()

    # -------------------------------------------------------------- backup
    def backup_daily(self, backups_dir: Path,
                     keep: int = 14) -> Path | None:
        """One backup file per UTC day, REFRESHED on every call — a
        write-once daily file left the 03:00 IST GCS sync mirroring a copy
        up to ~21.5 h stale (review-r3 #36); prune to `keep` newest."""
        backups_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dst = backups_dir / f"ledger-{today}.db"
        # sqlite3 backup API — safe against a live WAL db; write to a tmp
        # then replace so a kill mid-backup can't leave a torn DR copy.
        # Stale tmps from crashed backups poisoned the SAME day's later
        # attempts (Connection.backup onto a torn file raises) and were
        # mirrored into the DR bucket forever (review-r5 #4/#28)
        for old_tmp in backups_dir.glob(".ledger-*.db.tmp"):
            old_tmp.unlink(missing_ok=True)
        tmp = backups_dir / f".ledger-{today}.db.tmp"
        try:
            bck = sqlite3.connect(tmp)
            with bck:
                self.db.backup(bck)
            bck.close()
            tmp.replace(dst)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        old = sorted(backups_dir.glob("ledger-*.db"))
        for f in old[:-keep]:
            f.unlink()
        return dst

    # ------------------------------------------------------------- queries
    def counts_by_state(self) -> dict[str, int]:
        cur = self.db.execute(
            "SELECT state, COUNT(*) n FROM sessions GROUP BY state")
        return {r["state"]: r["n"] for r in cur.fetchall()}

    def delivered_last_24h(self) -> float:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(
            timespec="seconds")
        return self.delivered_hours(since_iso=since)
