"""
Incident Photo Candidate Database
=================================
Every candidate photograph this pipeline ever looks at is stored here --
including the ones that were REJECTED. That is the point: when a photo
turns out to be wrong (or a good one was thrown away), the question is
always "why did it pick that?", and an answer only exists if the losers
were kept too.

WHY SQLITE *AND* A JSONL LOG
----------------------------
SQLite is the query surface (the dashboard and the daily metrics read
it). But a .db is a binary file, and every workflow in this repo ends
with a `git pull --rebase` retry loop -- git cannot merge two binary
files, so two workflows finishing close together would deadlock the push
and silently lose state. That exact failure mode (a silently dropped
push) is what caused the duplicate-post bug earlier in this project, so
it is not a theoretical worry.

So the DURABLE record is data/photo_log.jsonl: append-only, one JSON row
per line, marked `merge=union` in .gitattributes, which git merges
cleanly by concatenating both sides. The .db is DERIVED -- gitignored,
and rebuilt from the log whenever it is missing (which is every CI run).
Both are written inside one call, so there is only ever one write path.

Tables: articles, image_candidates, verification_results,
license_results, publication_results, permission_requests, daily_metrics.
"""

import datetime as dt
import json
import os
import sqlite3

from config import settings

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_ROOT, settings.PHOTO_DB_PATH)
LOG_PATH = os.path.join(_ROOT, settings.PHOTO_LOG_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    article_id    TEXT PRIMARY KEY,
    headline      TEXT,
    link          TEXT,
    category      TEXT,
    score         INTEGER,
    event_id      TEXT,
    first_seen    TEXT,
    last_checked  TEXT,
    retry_index   INTEGER DEFAULT 0,
    next_retry_at TEXT,
    image_status  TEXT,
    decision      TEXT,
    resolved      INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS image_candidates (
    candidate_id  TEXT PRIMARY KEY,
    article_id    TEXT,
    event_id      TEXT,
    image_url     TEXT,
    thumbnail_url TEXT,
    source_name   TEXT,
    source_url    TEXT,
    caption       TEXT,
    published_at  TEXT,
    discovered_at TEXT,
    tier          TEXT,
    rank_in_story INTEGER
);
CREATE TABLE IF NOT EXISTS verification_results (
    candidate_id   TEXT PRIMARY KEY,
    article_id     TEXT,
    event_match    INTEGER,
    location_match INTEGER,
    person_match   INTEGER,
    time_match     INTEGER,
    is_file_photo  INTEGER,
    confidence     REAL,
    image_age      TEXT,
    age_bucket     TEXT,
    reason         TEXT,
    verdict        TEXT
);
CREATE TABLE IF NOT EXISTS license_results (
    candidate_id         TEXT PRIMARY KEY,
    article_id           TEXT,
    license_status       TEXT,
    license_name         TEXT,
    license_url          TEXT,
    copyright_owner      TEXT,
    photographer         TEXT,
    attribution_required INTEGER,
    permission_required  INTEGER,
    reusable             INTEGER
);
CREATE TABLE IF NOT EXISTS publication_results (
    article_id   TEXT PRIMARY KEY,
    candidate_id TEXT,
    decision     TEXT,
    acted_by     TEXT,
    acted_at     TEXT,
    note         TEXT
);
CREATE TABLE IF NOT EXISTS permission_requests (
    candidate_id    TEXT PRIMARY KEY,
    article_id      TEXT,
    source_name     TEXT,
    photographer    TEXT,
    copyright_owner TEXT,
    contact         TEXT,
    source_url      TEXT,
    image_url       TEXT,
    discovered_at   TEXT,
    status          TEXT
);
CREATE TABLE IF NOT EXISTS daily_metrics (
    day           TEXT PRIMARY KEY,
    stories       INTEGER,
    exact_images  INTEGER,
    wrong_images  INTEGER,
    no_image      INTEGER,
    free_images   INTEGER,
    copyrighted   INTEGER,
    manual_review INTEGER,
    file_photos   INTEGER,
    computed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_cand_article ON image_candidates(article_id);
CREATE INDEX IF NOT EXISTS idx_cand_event   ON image_candidates(event_id);
CREATE INDEX IF NOT EXISTS idx_art_event    ON articles(event_id);
"""

_TABLES = ("articles", "image_candidates", "verification_results",
           "license_results", "publication_results", "permission_requests",
           "daily_metrics")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _write_row(conn, table, row):
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute("INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (table, cols, marks),
                 list(row.values()))


def connect():
    """Opens the DB, rebuilding it from the JSONL log first if it is
    missing -- a fresh clone or a CI runner, which is the normal case on
    GitHub Actions since the .db itself is never committed."""
    fresh = not os.path.exists(DB_PATH)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    if fresh and os.path.exists(LOG_PATH):
        replay(conn)
    return conn


def replay(conn):
    """Rebuilds every table from the append-only log. Later rows for the
    same primary key overwrite earlier ones, so a replay lands on exactly
    the state the incremental writes produced."""
    rows = 0
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # A torn line (e.g. from a union merge) must not take the
                # whole history down with it.
                continue
            if rec.get("t") not in _TABLES:
                continue
            try:
                _write_row(conn, rec["t"], rec["r"])
                rows += 1
            except sqlite3.Error:
                continue
    conn.commit()
    return rows


def save(table, row, conn=None):
    """The single write path: appends to the durable log AND upserts into
    SQLite. Never write to one without the other."""
    assert table in _TABLES, table
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": table, "r": row}, ensure_ascii=False) + "\n")
    own = conn is None
    conn = conn or connect()
    try:
        _write_row(conn, table, row)
        conn.commit()
    finally:
        if own:
            conn.close()
    return row


def query(sql, params=(), conn=None):
    own = conn is None
    conn = conn or connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        if own:
            conn.close()


def get_article(article_id, conn=None):
    rows = query("SELECT * FROM articles WHERE article_id = ?", (article_id,), conn)
    return rows[0] if rows else None


def due_retries(limit=5, conn=None):
    """Articles with no usable photo yet whose next rung on the retry
    ladder has come due. `resolved` is set once a genuinely reusable
    photo is found, which takes the story off the ladder for good."""
    return query(
        "SELECT * FROM articles WHERE resolved = 0 AND next_retry_at IS NOT NULL "
        "AND next_retry_at <= ? ORDER BY next_retry_at LIMIT ?",
        (now_iso(), limit), conn)


def candidates_for(article_id, conn=None):
    """Every candidate ever considered for a story, winners and losers,
    joined with why it was verified and what its rights are."""
    return query(
        "SELECT c.*, v.confidence, v.verdict, v.age_bucket, v.is_file_photo, "
        "       v.event_match, v.location_match, v.time_match, v.person_match, v.reason, "
        "       l.license_status, l.license_name, l.copyright_owner, l.reusable "
        "FROM image_candidates c "
        "LEFT JOIN verification_results v ON v.candidate_id = c.candidate_id "
        "LEFT JOIN license_results     l ON l.candidate_id = c.candidate_id "
        "WHERE c.article_id = ? ORDER BY c.rank_in_story",
        (article_id,), conn)


def siblings_for_event(event_id, conn=None):
    """All candidates across ALL articles that describe the same real
    event -- the cross-source view that makes "source A owns it, source C
    published a reusable one" visible."""
    return query(
        "SELECT c.*, l.license_status, l.reusable, v.confidence, v.verdict "
        "FROM image_candidates c "
        "LEFT JOIN license_results     l ON l.candidate_id = c.candidate_id "
        "LEFT JOIN verification_results v ON v.candidate_id = c.candidate_id "
        "WHERE c.event_id = ? ORDER BY l.reusable DESC, v.confidence DESC",
        (event_id,), conn)
