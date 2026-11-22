"""SQLite-база в database/realty.db. Каждый поток открывает своё соединение."""
import json
import sqlite3
import threading
from datetime import datetime

from .paths import DB_DIR

DB_FILE = DB_DIR / "realty.db"

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
CREATE TABLE IF NOT EXISTS likes(source TEXT, source_id TEXT, ts TEXT, PRIMARY KEY(source, source_id));
-- «Скрытые» (дизлайк): квартира не нравится, и её не надо показывать в подборке. Сбор её не пропускает —
-- цены и снятие ведутся как у всех, скрыта она только в просмотре и возвращается кнопкой «Скрытые»
CREATE TABLE IF NOT EXISTS dislikes(source TEXT, source_id TEXT, ts TEXT, PRIMARY KEY(source, source_id));
-- Личные заметки к квартире (по каждому объявлению объекта, как лайк/дизлайк — чтобы не терялись при пересборке)
CREATE TABLE IF NOT EXISTS notes(source TEXT, source_id TEXT, text TEXT, ts TEXT, PRIMARY KEY(source, source_id));
-- Ручное объединение карточек: объявления одной group_id склеиваются поверх автоматических правил;
-- is_primary=1 — главное объявление первой карточки, от которого берутся описание и все параметры
CREATE TABLE IF NOT EXISTS manual_merges(
    source TEXT NOT NULL, source_id TEXT NOT NULL, group_id TEXT NOT NULL,
    is_primary INTEGER DEFAULT 0, ts TEXT, PRIMARY KEY(source, source_id)
);
-- События рынка для экрана «Изменения»: new | price | removed | returned.
-- baseline=1 — объявление попало в базу при первичной загрузке сайта (не новое для рынка)
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id INTEGER, source TEXT, source_id TEXT,
    kind TEXT, old_value INTEGER, new_value INTEGER, note TEXT, baseline INTEGER DEFAULT 0
);
-- Сводка рынка после каждого запуска сбора (когда отработали все сайты) — точка на графиках дашборда
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id INTEGER, data TEXT);
-- Служебные отметки базы: например, по какой версии правил собраны объекты (dedupe_rules)
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
-- Пакеты импорта карточек (.rwcards.zip или LAN)
CREATE TABLE IF NOT EXISTS import_batches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    channel TEXT,
    origin_id TEXT,
    origin_name TEXT,
    source_file TEXT,
    bundle_id TEXT,
    likes_mode TEXT,
    stats_json TEXT,
    total_listings INTEGER DEFAULT 0,
    total_cards INTEGER DEFAULT 0,
    new_cards INTEGER DEFAULT 0,
    matched_cards INTEGER DEFAULT 0,
    duplicate_cards INTEGER DEFAULT 0,
    stale_cards INTEGER DEFAULT 0,
    updated_notes INTEGER DEFAULT 0
);
-- Элементы каждого пакета импорта (срез карточки для истории и просмотра неактуальных)
CREATE TABLE IF NOT EXISTS import_items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    export_card_id TEXT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    status TEXT,
    stale_reason TEXT,
    sender_liked INTEGER DEFAULT 0,
    sender_active INTEGER DEFAULT 1,
    sender_last_seen TEXT,
    sender_price INTEGER,
    snapshot_json TEXT
);
-- Чужие заметки по объявлениям с указанием отправителя (личная таблица notes остаётся неприкосновенной)
CREATE TABLE IF NOT EXISTS imported_notes(
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    origin_id TEXT NOT NULL,
    origin_name TEXT,
    text TEXT,
    ts TEXT,
    updated_at TEXT,
    batch_id INTEGER,
    PRIMARY KEY(source, source_id, origin_id)
);
-- Доверенные пиры локальной сети и очередь синхронизации (база для следующих этапов)
CREATE TABLE IF NOT EXISTS sync_peers(
    device_id TEXT PRIMARY KEY,
    name TEXT,
    ip TEXT,
    port INTEGER,
    token_hash TEXT,
    peer_token TEXT,
    paired_at TEXT,
    last_seen TEXT,
    last_db_ts TEXT,
    live_cfg_json TEXT
);
CREATE TABLE IF NOT EXISTS sync_outbox(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id TEXT,
    kind TEXT,
    event_id TEXT,
    payload_path TEXT,
    created_at TEXT,
    tries INTEGER DEFAULT 0
);
"""

# Колонки, добавленные после первой версии (ALTER TABLE для старых баз)
MIGRATIONS = {
    "object_id": "TEXT",          # объект недвижимости: общий для дублей с разных сайтов
    "img_tries": "INTEGER DEFAULT 0",  # неудачные попытки скачать фото
    "missed": "INTEGER DEFAULT 0",     # полных прогонов подряд, где объявления не было в поиске
    "removed_at": "TEXT",              # когда снято
    "removed_reason": "TEXT",          # почему считается снятым (подсказка в интерфейсе)
    "imported_only": "INTEGER DEFAULT 0",  # 1 — добавлено выборочным импортом, пока не встречалось в своём сборе
}
INDEXES = """
CREATE INDEX IF NOT EXISTS ix_obj ON listings(object_id);
CREATE INDEX IF NOT EXISTS ix_match ON listings(rooms, floor);
CREATE INDEX IF NOT EXISTS ix_ev ON events(ts);
CREATE INDEX IF NOT EXISTS ix_ev_l ON events(source, source_id);
CREATE INDEX IF NOT EXISTS ix_mm_g ON manual_merges(group_id);
CREATE INDEX IF NOT EXISTS ix_imp_items_batch ON import_items(batch_id);
CREATE INDEX IF NOT EXISTS ix_imp_items_key ON import_items(source, source_id);
CREATE INDEX IF NOT EXISTS ix_imp_notes_key ON imported_notes(source, source_id);
CREATE INDEX IF NOT EXISTS ix_runs_launch ON runs(launch_key);
"""

FIELDS = [
    "url", "title", "price", "price_m2", "rooms", "area", "living_area", "kitchen_area", "floor", "floors",
    "address", "lat", "lon", "zone", "renovation", "renovation_raw", "building_type", "build_year",
    "is_new_building", "seller_type", "description", "photo", "published_at", "flags", "excluded",
    "fetch_mode", "details_fetched", "cluster_key", "raw",
]


def now():
    return datetime.now().isoformat(timespec="seconds")


def hidden_fresh(con, since=None):
    """Сколько скрытых квартир (дизлайк, без ♥) получили объявления, которым дизлайк не ставили, — то есть
    добавившиеся в карточку уже после скрытия. since — считать только объявления, впервые найденные с этого
    момента (для уведомления о прошедшем сборе); без него — все такие, как кружок на кнопке «Скрытые»."""
    return con.execute("""
        WITH hid AS (SELECT DISTINCT l.object_id FROM listings l JOIN dislikes d USING(source, source_id)
                     WHERE l.object_id IS NOT NULL),
             fav AS (SELECT DISTINCT l.object_id FROM listings l JOIN likes k USING(source, source_id)
                     WHERE l.object_id IS NOT NULL)
        SELECT COUNT(DISTINCT l.object_id) FROM listings l
        WHERE l.object_id IN hid AND l.object_id NOT IN fav
          AND NOT EXISTS (SELECT 1 FROM dislikes d WHERE d.source = l.source AND d.source_id = l.source_id)
          AND (? IS NULL OR l.first_seen >= ?)""", (since, since)).fetchone()[0]


_init_lock = threading.Lock()
_initialized = False


def connect():
    global _initialized
    with _init_lock:
        if not _initialized:
            con = sqlite3.connect(DB_FILE, timeout=30)
            con.execute("PRAGMA journal_mode=WAL")
            migrate(con)
            con.close()
            _initialized = True
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.execute("PRAGMA synchronous=NORMAL")
    con.row_factory = sqlite3.Row
    return con


SCHEMA_VERSION = 4

# Полный перечень колонок развивающихся таблиц: гарантирует бесконфликтную миграцию базы
# с любой прошлой версии RealtyWatch без потери заметок, лайков, дизлайков и ручных объединений.
TABLE_MIGRATIONS = {
    "listings": {
        "url": "TEXT", "title": "TEXT", "price": "INTEGER", "price_m2": "INTEGER",
        "rooms": "INTEGER", "area": "REAL", "living_area": "REAL", "kitchen_area": "REAL",
        "floor": "INTEGER", "floors": "INTEGER", "address": "TEXT", "lat": "REAL", "lon": "REAL", "zone": "TEXT",
        "renovation": "TEXT", "renovation_raw": "TEXT", "building_type": "TEXT", "build_year": "INTEGER",
        "is_new_building": "INTEGER", "seller_type": "TEXT", "description": "TEXT", "photo": "TEXT",
        "published_at": "TEXT", "flags": "TEXT", "excluded": "TEXT", "fetch_mode": "TEXT",
        "details_fetched": "INTEGER DEFAULT 0", "cluster_key": "TEXT", "raw": "TEXT",
        "first_seen": "TEXT", "last_seen": "TEXT", "active": "INTEGER DEFAULT 1",
        **MIGRATIONS,
    },
    "runs": {
        "mode": "TEXT", "started": "TEXT", "finished": "TEXT", "status": "TEXT",
        "pages": "INTEGER", "found": "INTEGER", "new": "INTEGER", "changed": "INTEGER",
        "removed": "INTEGER", "message": "TEXT",
        "declared": "INTEGER", "collected": "INTEGER", "requests": "INTEGER",
        "launch_key": "TEXT", "resume_state": "TEXT",
    },
    "likes": {"ts": "TEXT"},
    "dislikes": {"ts": "TEXT"},
    "notes": {"text": "TEXT", "ts": "TEXT"},
    "manual_merges": {"group_id": "TEXT", "is_primary": "INTEGER DEFAULT 0", "ts": "TEXT"},
    "events": {
        "ts": "TEXT", "run_id": "INTEGER", "source": "TEXT", "source_id": "TEXT",
        "kind": "TEXT", "old_value": "INTEGER", "new_value": "INTEGER", "note": "TEXT", "baseline": "INTEGER DEFAULT 0",
    },
    "snapshots": {"ts": "TEXT", "run_id": "INTEGER", "data": "TEXT"},
    "import_batches": {
        "ts": "TEXT", "channel": "TEXT", "origin_id": "TEXT", "origin_name": "TEXT",
        "source_file": "TEXT", "bundle_id": "TEXT", "likes_mode": "TEXT", "stats_json": "TEXT",
        "total_listings": "INTEGER DEFAULT 0", "total_cards": "INTEGER DEFAULT 0",
        "new_cards": "INTEGER DEFAULT 0", "matched_cards": "INTEGER DEFAULT 0",
        "duplicate_cards": "INTEGER DEFAULT 0", "stale_cards": "INTEGER DEFAULT 0",
        "updated_notes": "INTEGER DEFAULT 0",
    },
    "import_items": {
        "export_card_id": "TEXT", "status": "TEXT", "stale_reason": "TEXT",
        "sender_liked": "INTEGER DEFAULT 0", "sender_active": "INTEGER DEFAULT 1",
        "sender_last_seen": "TEXT", "sender_price": "INTEGER", "snapshot_json": "TEXT",
    },
    "imported_notes": {"origin_name": "TEXT", "text": "TEXT", "ts": "TEXT", "updated_at": "TEXT", "batch_id": "INTEGER"},
    "sync_peers": {
        "name": "TEXT", "ip": "TEXT", "port": "INTEGER", "token_hash": "TEXT", "peer_token": "TEXT",
        "paired_at": "TEXT", "last_seen": "TEXT", "last_db_ts": "TEXT", "live_cfg_json": "TEXT",
    },
    "sync_outbox": {
        "peer_id": "TEXT", "kind": "TEXT", "event_id": "TEXT",
        "payload_path": "TEXT", "created_at": "TEXT", "tries": "INTEGER DEFAULT 0",
    },
}


def migrate(con):
    """Создаёт таблицы и добавляет недостающие колонки (в т.ч. в старой базе после обновления версии)."""
    con.executescript(SCHEMA)
    for table, expected in TABLE_MIGRATIONS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, decl in expected.items():
            if name not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    con.executescript(INDEXES)
    con.execute("INSERT OR REPLACE INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.commit()


# Колонки прогонов, добавленные позже
RUN_MIGRATIONS = {
    "declared": "INTEGER",      # сколько объявлений заявил сайт
    "collected": "INTEGER",     # сколько уникальных собрано
    "requests": "INTEGER",      # запросов к сайту за прогон
    "launch_key": "TEXT",       # идентификатор общего запуска (сохраняет принадлежность при донажатии «Продолжить»)
    "resume_state": "TEXT",     # JSON чекпоинта незавершённого прогона (NULL — завершён или закрыт новым запуском)
}


def reset():
    """После замены файла базы (импорт) — заново проверить схему при следующем подключении."""
    global _initialized
    with _init_lock:
        _initialized = False


def get(con, source, source_id):
    r = con.execute("SELECT * FROM listings WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    return dict(r) if r else None


def event(con, source, source_id, kind, old=None, new=None, note=None, run_id=None, baseline=0, ts=None):
    con.execute("INSERT INTO events(ts, run_id, source, source_id, kind, old_value, new_value, note, baseline) VALUES(?,?,?,?,?,?,?,?,?)",
                (ts or now(), run_id, source, source_id, kind, old, new, note, baseline))


def remove(con, source, source_id, reason, run_id=None):
    con.execute("UPDATE listings SET active=0, removed_at=?, removed_reason=? WHERE source=? AND source_id=?",
                (now(), reason, source, source_id))
    event(con, source, source_id, "removed", note=reason, run_id=run_id)


def set_price(con, source, source_id, price, note, run_id=None):
    """Цена, узнанная не из поиска (например, со страницы объявления при проверке)."""
    old = con.execute("SELECT price, area FROM listings WHERE source=? AND source_id=?", (source, source_id)).fetchone()
    if not old or not price or old["price"] == price:
        return False
    ppm = round(price / old["area"]) if old["area"] else None
    con.execute("UPDATE listings SET price=?, price_m2=? WHERE source=? AND source_id=?", (price, ppm, source, source_id))
    con.execute("INSERT INTO price_history VALUES(?,?,?,?)", (source, source_id, now(), price))
    event(con, source, source_id, "price", old["price"], price, note=note, run_id=run_id)
    return True


def upsert(con, item, run_id=None, baseline=0):
    """Сохраняет объявление и пишет события рынка. Возвращает 'new' | 'changed' | 'same'."""
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
        event(con, item["source"], item["source_id"], "new", new=row["price"], run_id=run_id, baseline=baseline, ts=ts)
        return "new"
    # Не затираем поля, полученные со страницы объявления, пустыми значениями из выдачи
    for k in FIELDS:
        if row[k] is None and old.get(k) is not None:
            row[k] = old[k]
    if row["photo"] and not str(row["photo"]).startswith("http"):
        row["photo"] = old["photo"]
    if row["photo"] != old["photo"]:
        con.execute("UPDATE listings SET img_tries=0 WHERE source=? AND source_id=?", (item["source"], item["source_id"]))
    changed = old["price"] != row["price"] or not old["active"]
    if row["price"] and old["price"] and old["price"] != row["price"]:
        con.execute("INSERT INTO price_history VALUES(?,?,?,?)", (item["source"], item["source_id"], ts, row["price"]))
        event(con, item["source"], item["source_id"], "price", old["price"], row["price"], run_id=run_id, ts=ts)
    if not old["active"]:
        event(con, item["source"], item["source_id"], "returned", new=row["price"], note=old.get("removed_reason"), run_id=run_id, ts=ts)
    con.execute(
        f"UPDATE listings SET {','.join(f'{k}=?' for k in FIELDS)}, last_seen=?, active=1, missed=0, "
        f"imported_only=0, removed_at=NULL, removed_reason=NULL WHERE source=? AND source_id=?",
        [*[row[k] for k in FIELDS], ts, item["source"], item["source_id"]],
    )
    return "changed" if changed else "same"


def missing(con, source, seen):
    """Активные объявления сайта (собранные локально), которых не было в поиске этого (полного) прогона; seen — полученные номера."""
    return [dict(r) for r in con.execute(
        "SELECT * FROM listings WHERE source=? AND active=1 AND COALESCE(imported_only, 0)=0", (source,))
            if str(r["source_id"]) not in seen]
