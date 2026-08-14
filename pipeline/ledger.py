"""SQLite ledger — the permanent record behind payment (plan §8).

Rules: the ledger and dossiers are NEVER deleted; every state transition
appends to the `events` audit table; a daily backup copy is kept (14).
`duration_delivered_s` summed per player/game is the paid number.

Schema is plan §8 verbatim plus one additive column `duration_raw_s`
(pre-trim clip length probed at ingest) — needed for the §14 "collected"
line; additive-only so §8 stays a valid subset.

Concurrency: one writer process (run.py holds the run lock); workers return
results to the parent, which does all ledger writes. WAL mode so a crashed
process never corrupts the file.
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
        self.db.executescript(_SCHEMA)
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

    def update(self, session_id: str, **fields) -> None:
        """Update non-state columns (state changes go through set_state)."""
        assert "state" not in fields, "use set_state for state transitions"
        allowed = {"bin", "reasons_json", "fix_attempts",
                   "duration_delivered_s", "duration_raw_s", "rrd_sampled",
                   "delivered_at", "dossier_path", "md5_video", "bytes",
                   "game", "drive_path", "drive_ctime", "parent_id"}
        bad = set(fields) - allowed
        assert not bad, f"unknown ledger fields: {bad}"
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [_now(), session_id]
        self.db.execute(
            f"UPDATE sessions SET {sets}, updated_at=? WHERE session_id=?",
            vals)
        self.db.commit()

    def set_reasons(self, session_id: str, reasons: list[dict],
                    bin_: int | None) -> None:
        self.db.execute(
            "UPDATE sessions SET reasons_json=?, bin=?, updated_at=?"
            " WHERE session_id=?",
            (json.dumps(reasons), bin_, _now(), session_id))
        self.db.commit()

    # ------------------------------------------------------------ supersede
    def supersede(self, session_id: str, *, new_md5: str, new_bytes: int,
                  new_ctime: str, dossier_root: Path) -> None:
        """Same session-id re-uploaded with different video md5 after a
        reject: archive the prior verdict in the dossier history/, reset
        fix_attempts, re-enter as fresh (§9.1 supersede rule). Hours always
        follow the latest state."""
        row = self.get(session_id)
        assert row is not None
        dossier = dossier_root / session_id
        if dossier.exists():
            hist = dossier / "history"
            hist.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dst = hist / f"superseded-{stamp}"
            dst.mkdir(exist_ok=True)
            for f in dossier.iterdir():
                if f.name != "history":
                    shutil.move(str(f), dst / f.name)
        now = _now()
        self.db.execute(
            "UPDATE sessions SET md5_video=?, bytes=?, drive_ctime=?,"
            " fix_attempts=0, bin=NULL, reasons_json='[]',"
            " duration_delivered_s=NULL, duration_raw_s=NULL, rrd_sampled=0,"
            " delivered_at=NULL, updated_at=? WHERE session_id=?",
            (new_md5, new_bytes, new_ctime, now, session_id))
        self.db.execute(
            "INSERT INTO events(session_id, ts, from_state, to_state, detail)"
            " VALUES(?,?,?,?,?)",
            (session_id, now, row["state"], "DISCOVERED",
             f"superseded: new md5 {new_md5}"))
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

    def incomplete_list(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM incomplete ORDER BY first_seen").fetchall()

    # -------------------------------------------------------------- batches
    def start_batch(self) -> int:
        cur = self.db.execute(
            "INSERT INTO batches(started, summary_json) VALUES(?, '{}')",
            (_now(),))
        self.db.commit()
        return cur.lastrowid

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
        """One backup per UTC day; prune to `keep` newest."""
        backups_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dst = backups_dir / f"ledger-{today}.db"
        if dst.exists():
            return None
        # sqlite3 backup API — safe against a live WAL db
        bck = sqlite3.connect(dst)
        with bck:
            self.db.backup(bck)
        bck.close()
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
