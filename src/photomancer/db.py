"""SQLite catalog - the single source of truth.

Per-path facts live in `files`; everything computed from the pixels or the
model is keyed by the content hash (sha256), so a moved file or an exact
duplicate reuses earlier work instead of repeating it.

VLM results are never overwritten: each row carries the run and the
`config_key` (model, quant, prompt, taxonomy, image size, EXIF context), and
the "current" result of a photo is the latest ok row for the active key.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS files(
    file_id    INTEGER PRIMARY KEY,
    path       TEXT NOT NULL UNIQUE,         -- relative to photo_root, '\\' separators
    sha256     TEXT,
    size       INTEGER,
    mtime      REAL,
    width      INTEGER,
    height     INTEGER,
    scanned_at TEXT,
    missing    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);

CREATE TABLE IF NOT EXISTS exif(
    sha256 TEXT PRIMARY KEY, camera TEXT, lens TEXT, focal_mm REAL, focal_35mm REAL,
    aperture REAL, shutter_s REAL, iso INTEGER, datetime TEXT, lat REAL, lon REAL,
    is_long_exposure INTEGER, is_high_iso INTEGER, is_telephoto INTEGER, has_gps INTEGER
);

CREATE TABLE IF NOT EXISTS colors(
    sha256 TEXT, rank INTEGER, lab_l REAL, lab_a REAL, lab_b REAL, hex TEXT,
    fraction REAL, color_name TEXT, PRIMARY KEY(sha256, rank)
);
CREATE TABLE IF NOT EXISTS color_stats(
    sha256 TEXT PRIMARY KEY, mean_L REAL, std_L REAL, colourfulness REAL,
    is_monochrome INTEGER, tonality TEXT
);

CREATE TABLE IF NOT EXISTS dedup(sha256 TEXT PRIMARY KEY, phash TEXT, group_id INTEGER);

CREATE TABLE IF NOT EXISTS runs(
    run_id INTEGER PRIMARY KEY, config_key TEXT, model_id TEXT, quant TEXT,
    prompt_version TEXT, taxonomy_version TEXT, params_json TEXT,
    started_at TEXT, finished_at TEXT, n_ok INTEGER DEFAULT 0, n_failed INTEGER DEFAULT 0,
    wall_s REAL, label TEXT
);

CREATE TABLE IF NOT EXISTS vlm_results(
    result_id INTEGER PRIMARY KEY, sha256 TEXT, run_id INTEGER, config_key TEXT,
    json TEXT, primary_genre TEXT, subgenre TEXT, confidence REAL,
    status TEXT,                -- ok | failed
    error TEXT, warnings TEXT, latency_s REAL, tokens_in INTEGER, tokens_out INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS vlm_sha_key ON vlm_results(sha256, config_key, status);

CREATE TABLE IF NOT EXISTS stage_status(
    file_id INTEGER, stage TEXT, status TEXT, error TEXT, attempts INTEGER DEFAULT 0,
    updated_at TEXT, PRIMARY KEY(file_id, stage)
);

CREATE TABLE IF NOT EXISTS xmp_written(
    file_id INTEGER PRIMARY KEY, sha256 TEXT, result_id INTEGER, sidecar TEXT,
    content_sha TEXT, written_at TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Catalog:
    """Thread-safe wrapper: one connection, one lock, WAL journal."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=60)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version', ?)",
                          (str(SCHEMA_VERSION),))
        self.conn.commit()
        self.lock = threading.RLock()

    # -- generic helpers --
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()):
        with self.lock:
            r = self.conn.execute(sql, args).fetchone()
        return r[0] if r is not None and len(r) == 1 else r

    def x(self, sql: str, args: tuple = (), many: bool = False) -> None:
        with self.lock:
            (self.conn.executemany if many else self.conn.execute)(sql, args)
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # -- stage status --
    def set_stage(self, file_id: int, stage: str, status: str, error: str | None = None) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO stage_status(file_id, stage, status, error, attempts, updated_at) "
                "VALUES(?,?,?,?,1,?) ON CONFLICT(file_id, stage) DO UPDATE SET "
                "status=excluded.status, error=excluded.error, attempts=attempts+1, "
                "updated_at=excluded.updated_at",
                (file_id, stage, status, error, now_iso()))
            self.conn.commit()

    # -- runs --
    def new_run(self, config_key: str, model_id: str, quant: str, prompt_version: str,
                taxonomy_version: str, params_json: str, label: str = "") -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO runs(config_key, model_id, quant, prompt_version, taxonomy_version, "
                "params_json, started_at, label) VALUES(?,?,?,?,?,?,?,?)",
                (config_key, model_id, quant, prompt_version, taxonomy_version, params_json,
                 now_iso(), label))
            self.conn.commit()
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, n_ok: int, n_failed: int, wall_s: float) -> None:
        self.x("UPDATE runs SET finished_at=?, n_ok=?, n_failed=?, wall_s=? WHERE run_id=?",
               (now_iso(), n_ok, n_failed, wall_s, run_id))

    # -- results --
    def current_result(self, sha: str, config_key: str | None = None):
        """Latest ok result for this content (active key first, else any key if None)."""
        if config_key:
            return self.one("SELECT * FROM vlm_results WHERE sha256=? AND config_key=? AND "
                            "status='ok' ORDER BY result_id DESC LIMIT 1", (sha, config_key))
        return self.one("SELECT * FROM vlm_results WHERE sha256=? AND status='ok' "
                        "ORDER BY result_id DESC LIMIT 1", (sha,))
