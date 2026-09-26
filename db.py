import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "checker_history.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            prompt TEXT,
            max_tokens INTEGER,
            total INTEGER,
            active INTEGER
        );
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            latency_ms INTEGER,
            ttft_ms INTEGER,
            tokens_per_sec REAL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            capabilities TEXT,
            error TEXT
        );
        """)


def record_run(endpoint, prompt, max_tokens, results):
    init_db()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO runs (ts, endpoint, prompt, max_tokens, total, active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), endpoint, prompt, max_tokens,
             len(results), sum(1 for r in results if r.get("status") == "active")))
        run_id = cur.lastrowid
        for r in results:
            c.execute(
                "INSERT INTO results (run_id, model, status, latency_ms, ttft_ms, "
                "tokens_per_sec, prompt_tokens, completion_tokens, capabilities, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, r.get("model"), r.get("status"), r.get("latency_ms"),
                 r.get("ttft_ms"), r.get("tokens_per_sec"), r.get("prompt_tokens"),
                 r.get("completion_tokens"), json.dumps(r.get("capabilities") or {}),
                 r.get("error")))
    return run_id


def list_runs(limit=20):
    init_db()
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


def get_run(run_id):
    init_db()
    with _conn() as c:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            return None, None
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM results WHERE run_id=? ORDER BY latency_ms", (run_id,))]
        return dict(run), rows


def delete_run(run_id):
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM runs WHERE id=?", (run_id,))
