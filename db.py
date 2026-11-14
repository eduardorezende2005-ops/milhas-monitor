"""Banco SQLite persistente (histórico completo de campanhas, fontes, alertas e execuções)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, name TEXT, url TEXT, tier INTEGER, method TEXT, method_url TEXT,
  status TEXT DEFAULT 'pendente', last_check TEXT, last_ok TEXT, consecutive_errors INTEGER DEFAULT 0,
  total_errors INTEGER DEFAULT 0, total_checks INTEGER DEFAULT 0, items_found INTEGER DEFAULT 0,
  promos_found INTEGER DEFAULT 0, last_error TEXT, access_notes TEXT, needs_login INTEGER,
  etag TEXT, last_modified TEXT, cooldown_until TEXT
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, url TEXT UNIQUE, title TEXT, published_at TEXT,
  fetched_at TEXT, content_hash TEXT, text TEXT, is_promo INTEGER, reason TEXT, promo_id INTEGER, extracted TEXT
);
CREATE TABLE IF NOT EXISTS promos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT, category TEXT, title TEXT, program TEXT,
  origin TEXT, destination TEXT, partner TEXT, bonus_pct INTEGER, ppr REAL, discount_pct INTEGER,
  milheiro REAL, milheiro_calculado INTEGER DEFAULT 0, bonus_points INTEGER, club_required TEXT,
  registration_required TEXT, min_transfer INTEGER, limit_text TEXT, credit_deadline TEXT, validity TEXT,
  conditions TEXT, start_at TEXT, end_at TEXT, end_source TEXT, status TEXT, official_url TEXT,
  official_verified_at TEXT, first_seen TEXT, last_seen TEXT, last_verified TEXT, best_tier INTEGER,
  published_at TEXT, notified_ending INTEGER DEFAULT 0, expired_flag INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS promos_cat ON promos(category, program);
CREATE TABLE IF NOT EXISTS promo_sources (
  promo_id INTEGER, item_id INTEGER, source_id TEXT, url TEXT, title TEXT, seen_at TEXT, tier INTEGER, end_at TEXT,
  PRIMARY KEY (promo_id, url)
);
CREATE TABLE IF NOT EXISTS promo_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, promo_id INTEGER, field TEXT, old TEXT, new TEXT, at TEXT, source_id TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, promo_id INTEGER, kind TEXT, title TEXT, message TEXT, url TEXT,
  created_at TEXT, sent TEXT DEFAULT '', UNIQUE(promo_id, kind, title)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, trigger TEXT, status TEXT,
  sources_ok INTEGER, sources_error INTEGER, sources_skipped INTEGER, items_new INTEGER, promos_new INTEGER,
  promos_updated INTEGER, requests INTEGER, log TEXT
);
CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, source_id TEXT, at TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def get_state(con, key, default=None):
    r = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_state(con, key, value):
    con.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))


def rows(con, sql, *args) -> list[dict]:
    return [dict(r) for r in con.execute(sql, args).fetchall()]
