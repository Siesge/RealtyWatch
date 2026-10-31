"""SQLite-база в data/realty.db. Каждый поток открывает своё соединение."""
import json
import sqlite3
import threading
from datetime import datetime

from .paths import DATA

DB_FILE = DATA / "realty.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings(
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    url TEXT, title TEXT, price INTEGER, price_m2 INTEGER,
    rooms INTEGER, area REAL, living_area REAL, kitchen_area REAL,
    floor INTEGER, floors INTEGER, address TEXT, lat REAL, lon REAL, zone TEXT,
    renovation TEXT, renovation_raw TEXT, building_type TEXT, build_year INTEGER,
    is_new_building INTEGER, seller_type TEXT, description TEXT, photo TEXT,
    published_at TEXT, flags TEXT, excluded TEXT, fetch_mode TEXT,
    details_fetched INTEGER DEFAULT 0, cluster_key TEXT, raw TEXT,
    first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1,
    PRIMARY KEY(source, source_id)
);
CREATE TABLE IF NOT EXISTS price_history(source TEXT, source_id TEXT, ts TEXT, price INTEGER);
CREATE INDEX IF NOT EXISTS ix_ph ON price_history(source, source_id);
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, mode TEXT, started TEXT, finished TEXT,
    status TEXT, pages INTEGER, found INTEGER, new INTEGER, changed INTEGER, removed INTEGER, message TEXT
);
"""

FIELDS = [
    "url", "title", "price", "price_m2", "rooms", "area", "living_area", "kitchen_area", "floor", "floors",
    "address", "lat", "lon", "zone", "renovation", "renovation_raw", "building_type", "build_year",
    "is_new_building", "seller_type", "description", "photo", "published_at", "flags", "excluded",
    "fetch_mode", "details_fetched", "cluster_key", "raw",
]


def now():
    return datetime.now().isoformat(timespec="seconds")


_init_lock = threading.Lock()
_initialized = False


def connect():
    global _initialized
    with _init_lock:
        if not _initialized:
            con = sqlite3.connect(DB_FILE, timeout=30)
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            con.close()
            _initialized = True
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def get(con, source, source_id):
    r = con.execute("SELECT * FROM listings WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    return dict(r) if r else None


def upsert(con, item):
    """Сохраняет объявление. Возвращает 'new' | 'changed' | 'same'."""
    row = {k: item.get(k) for k in FIELDS}
    for k in ("flags", "raw"):
        if row[k] is not None and not isinstance(row[k], str):
            row[k] = json.dumps(row[k], ensure_ascii=False)
    if isinstance(row["excluded"], list):
        row["excluded"] = "; ".join(row["excluded"]) or None
    ts = now()
    old = get(con, item["source"], item["source_id"])
    if old is None:
        cols = ["source", "source_id", *FIELDS, "first_seen", "last_seen", "active"]
        con.execute(
            f"INSERT INTO listings({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
            [item["source"], item["source_id"], *[row[k] for k in FIELDS], ts, ts, 1],
        )
        if row["price"]:
            con.execute("INSERT INTO price_history VALUES(?,?,?,?)", (item["source"], item["source_id"], ts, row["price"]))
        return "new"
    # Не затираем поля, полученные со страницы объявления, пустыми значениями из выдачи
    for k in FIELDS:
        if row[k] is None and old.get(k) is not None:
            row[k] = old[k]
    changed = old["price"] != row["price"] or not old["active"]
    if row["price"] and old["price"] != row["price"]:
        con.execute("INSERT INTO price_history VALUES(?,?,?,?)", (item["source"], item["source_id"], ts, row["price"]))
    con.execute(
        f"UPDATE listings SET {','.join(f'{k}=?' for k in FIELDS)}, last_seen=?, active=1 WHERE source=? AND source_id=?",
        [*[row[k] for k in FIELDS], ts, item["source"], item["source_id"]],
    )
    return "changed" if changed else "same"


def mark_removed(con, source, run_started):
    """После полного прохода помечает неактивными объявления, которые не встретились."""
    cur = con.execute(
        "UPDATE listings SET active=0 WHERE source=? AND active=1 AND last_seen < ?",
        (source, run_started),
    )
    return cur.rowcount
