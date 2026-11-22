"""Экспорт и импорт базы одним .zip: realty.db + images/ + settings.json + manifest.json.

Импорт:
  merge   — объединить: новые объявления добавляются, по существующим берётся более свежая версия,
            история цен, прогоны и избранное объединяются, недостающие фото докладываются;
  replace — заменить базу целиком.
Перед любым импортом текущая база копируется в database/backups/.
"""
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import db, dedupe, history, trash
from .paths import BACKUPS, DB_DIR, IMAGES

FORMAT = "realtywatch-db"
VERSION = 1


def _stamp():
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def default_name():
    return f"RealtyWatch_база_{_stamp()}.zip"


def _snapshot(dest):
    """Согласованная копия базы (даже во время записи) через backup API SQLite."""
    src = db.connect()
    history.ensure_snapshots_fresh(src)
    out = sqlite3.connect(dest)
    src.backup(out)
    out.close()
    src.close()


def stats():
    con = db.connect()
    r = con.execute("""SELECT COUNT(*) n, COUNT(DISTINCT object_id) objects, SUM(excluded IS NULL AND active=1) passing,
                       (SELECT COUNT(*) FROM likes) likes FROM listings""").fetchone()
    manual_merges = len(dedupe.active_manual_merges(con)[0])
    con.close()
    img_files = [p for p in IMAGES.rglob("*") if p.is_file() and p.suffix != ".part"]
    db_size = sum(p.stat().st_size for p in DB_DIR.glob("realty.db*"))
    backups = sorted(BACKUPS.glob("*.zip"), key=lambda p: p.stat().st_mtime) if BACKUPS.exists() else []
    last = backups[-1] if backups else None
    return {"listings": r["n"], "objects": r["objects"], "passing": r["passing"] or 0, "likes": r["likes"],
            "manual_merges": manual_merges,
            "images": len(img_files), "size_mb": round((db_size + sum(p.stat().st_size for p in img_files)) / 2**20, 1),
            "folder": str(DB_DIR), "backups": len(backups),
            "last_backup": {"name": last.name, "ts": datetime.fromtimestamp(last.stat().st_mtime).isoformat(timespec="seconds"),
                            "mb": round(last.stat().st_size / 2**20, 1)} if last else None}


def export_zip(dest, with_images=True):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "realty.db"
        _snapshot(snap)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "realty.db")
            settings_file = DB_DIR / "settings.json"
            if settings_file.exists():
                z.write(settings_file, "settings.json")
            if with_images:
                for p in IMAGES.rglob("*"):
                    if p.is_file() and p.suffix != ".part":
                        # фото уже сжаты — не тратим время на повторное сжатие
                        z.write(p, "images/" + p.relative_to(IMAGES).as_posix(), compress_type=zipfile.ZIP_STORED)
            z.writestr("manifest.json", json.dumps({"format": FORMAT, "version": VERSION, "exported_at": datetime.now().isoformat(timespec="seconds"),
                                                    **{k: v for k, v in stats().items() if k != "folder"}}, ensure_ascii=False, indent=2))
    return str(dest)


def backup(reason, with_images=False):
    """Резервная копия базы в database/backups перед опасной операцией (импорт, очистка)."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    return export_zip(BACKUPS / f"{reason}_{_stamp()}.zip", with_images=with_images)


def _label(name):
    """«перед_очисткой_2026-11-07_1130.zip» → «перед очисткой»."""
    return re.sub(r"_\d{4}-\d{2}-\d{2}_\d{4}\.zip$", "", name).replace("_", " ")


def _peek(path):
    """Что внутри копии: годится ли она для восстановления и есть ли в ней фотографии."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile):
        return {"ok": False, "images": False}
    return {"ok": "realty.db" in names, "images": any(n.startswith("images/") and not n.endswith("/") for n in names)}


def backups():
    """Резервные копии, новые сверху: сколько их, сколько занимают и что в каждой."""
    items = []
    for p in sorted(BACKUPS.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True) if BACKUPS.exists() else []:
        st = p.stat()
        items.append({"name": p.name, "label": _label(p.name), "bytes": st.st_size,
                      "mb": round(st.st_size / 2**20, 1),
                      "ts": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"), **_peek(p)})
    total = sum(i["bytes"] for i in items)
    return {"items": items, "count": len(items), "bytes": total, "mb": round(total / 2**20, 1),
            "folder": str(BACKUPS), "trash": trash.available(BACKUPS if BACKUPS.exists() else DB_DIR)}


def backup_path(name):
    """Путь копии по имени файла. Только файл .zip из database/backups — чужого пути сюда не передать."""
    p = (BACKUPS / name).resolve()
    if p.parent != BACKUPS.resolve() or p.suffix.lower() != ".zip" or not p.is_file():
        raise ValueError(f"нет такой резервной копии: {name}")
    return p


def delete_backups(names=None):
    """Удаляет резервные копии (по умолчанию все) — в корзину, если на диске она есть, иначе насовсем."""
    chosen = [i for i in backups()["items"] if names is None or i["name"] in set(names)]
    paths = [backup_path(i["name"]) for i in chosen]
    n, trashed = trash.delete(paths)
    freed = sum(i["bytes"] for i, p in zip(chosen, paths) if not p.exists())
    return {"deleted": n, "mb": round(freed / 2**20, 1), "trash": trashed, "left": backups()["count"]}


def restore(name):
    """Восстановление из резервной копии: база, настройки поиска и фото из неё заменяют нынешние.
    Нынешнее состояние перед заменой тоже уходит в копию, так что откатиться можно и обратно."""
    res = import_zip(backup_path(name), mode="replace", before="перед_восстановлением")
    res["restored"] = name
    return res


def _check(z):
    names = set(z.namelist())
    if "realty.db" not in names:
        raise ValueError("В архиве нет realty.db — это не экспорт RealtyWatch")
    if "manifest.json" in names:
        m = json.loads(z.read("manifest.json"))
        if m.get("format") != FORMAT:
            raise ValueError("Неизвестный формат архива")
        return m
    return {}


def _extract_images(z, overwrite):
    n = 0
    for name in z.namelist():
        if name.startswith("images/") and not name.endswith("/"):
            target = IMAGES / name[len("images/"):]
            if target.exists() and not overwrite:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    return n


def import_zip(path, mode="merge", before="перед_импортом"):
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        manifest = _check(z)
        saved = backup(before)  # фото при импорте не удаляются — копируем только базу
        with tempfile.TemporaryDirectory() as tmp:
            z.extract("realty.db", tmp)
            imported = Path(tmp) / "realty.db"
            icon = sqlite3.connect(imported)
            db.migrate(icon)  # старый экспорт → текущая схема
            icon.close()
            if mode == "replace":
                result = _replace(imported, z)
            else:
                result = _merge(imported)
        result["images"] = _extract_images(z, overwrite=mode == "replace")
    con = db.connect()
    try:
        result["objects"] = dedupe.rebuild(con)
        history.rebuild_snapshots(con)
        history.backfill(con)
        con.execute("INSERT OR REPLACE INTO meta VALUES('dedupe_rules', ?)", (str(dedupe.RULES_VERSION),))
        con.commit()
    finally:
        con.close()
    result.update(backup=saved, mode=mode, exported_at=manifest.get("exported_at"))
    return result


def _replace(imported, z):
    src = sqlite3.connect(imported)
    dst = db.connect()
    try:
        src.backup(dst)  # перезаписывает текущую базу содержимым импортируемой
    finally:
        dst.close()
        src.close()
    db.reset()
    if "settings.json" in z.namelist():
        (DB_DIR / "settings.json").write_bytes(z.read("settings.json"))
    con = db.connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    finally:
        con.close()
    return {"added": n, "updated": 0}


def _merge(imported):
    con = db.connect()
    try:
        con.execute("ATTACH DATABASE ? AS imp", (str(imported),))
        main_cols = [r[1] for r in con.execute("PRAGMA main.table_info(listings)")]
        imp_cols = {r[1] for r in con.execute("PRAGMA imp.table_info(listings)")}
        cols = [c for c in main_cols if c in imp_cols and c != "object_id"]
        col_list = ",".join(cols)
        added = con.execute(f"""INSERT INTO listings({col_list}) SELECT {col_list} FROM imp.listings i
            WHERE NOT EXISTS (SELECT 1 FROM listings m WHERE m.source=i.source AND m.source_id=i.source_id)""").rowcount
        upd_cols = [c for c in cols if c not in ("source", "source_id", "first_seen", "imported_only")]
        updated = con.execute(f"""UPDATE listings SET ({",".join(upd_cols)}) =
            (SELECT {",".join("i." + c for c in upd_cols)} FROM imp.listings i WHERE i.source=listings.source AND i.source_id=listings.source_id)
            WHERE EXISTS (SELECT 1 FROM imp.listings i WHERE i.source=listings.source AND i.source_id=listings.source_id
                          AND i.last_seen > listings.last_seen)""").rowcount
        if "imported_only" in imp_cols:
            con.execute("""UPDATE listings SET imported_only=0
                WHERE COALESCE(imported_only, 0) != 0
                  AND EXISTS (SELECT 1 FROM imp.listings i
                              WHERE i.source=listings.source AND i.source_id=listings.source_id
                                AND COALESCE(i.imported_only, 0)=0)""")
        con.execute("""UPDATE listings SET first_seen = (SELECT MIN(i.first_seen) FROM imp.listings i
            WHERE i.source=listings.source AND i.source_id=listings.source_id AND i.first_seen < listings.first_seen)
            WHERE EXISTS (SELECT 1 FROM imp.listings i WHERE i.source=listings.source AND i.source_id=listings.source_id
                          AND i.first_seen < listings.first_seen)""")
        con.execute("""INSERT INTO price_history SELECT * FROM imp.price_history i WHERE NOT EXISTS
            (SELECT 1 FROM price_history p WHERE p.source=i.source AND p.source_id=i.source_id AND p.ts=i.ts AND p.price=i.price)""")
        con.execute("""INSERT INTO runs(source, mode, started, finished, status, pages, found, new, changed, removed, message,
                                         declared, collected, requests)
            SELECT source, mode, started, finished, status, pages, found, new, changed, removed, message,
                   declared, collected, requests FROM imp.runs i
            WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.source=i.source AND r.started=i.started)""")
        con.execute("INSERT OR IGNORE INTO likes SELECT * FROM imp.likes")
        if con.execute("SELECT 1 FROM imp.sqlite_master WHERE type='table' AND name='dislikes'").fetchone():
            con.execute("INSERT OR IGNORE INTO dislikes SELECT * FROM imp.dislikes")  # в старых архивах таблицы нет
        if con.execute("SELECT 1 FROM imp.sqlite_master WHERE type='table' AND name='notes'").fetchone():
            con.execute("""INSERT INTO notes(source, source_id, text, ts) SELECT i.source, i.source_id, i.text, i.ts FROM imp.notes i
                WHERE COALESCE(i.text, '') != ''
                  AND (NOT EXISTS (SELECT 1 FROM notes m WHERE m.source=i.source AND m.source_id=i.source_id)
                       OR COALESCE(i.ts, '') > COALESCE((SELECT m.ts FROM notes m WHERE m.source=i.source AND m.source_id=i.source_id), ''))
                ON CONFLICT(source, source_id) DO UPDATE SET text=excluded.text, ts=excluded.ts""")
        if con.execute("SELECT 1 FROM imp.sqlite_master WHERE type='table' AND name='imported_notes'").fetchone():
            con.execute("""INSERT INTO imported_notes(source, source_id, origin_id, origin_name, text, ts, updated_at, batch_id)
                SELECT i.source, i.source_id, i.origin_id, i.origin_name, i.text, i.ts, i.updated_at, NULL
                FROM imp.imported_notes i
                WHERE COALESCE(i.text, '') != ''
                  AND (NOT EXISTS (SELECT 1 FROM imported_notes m
                                   WHERE m.source=i.source AND m.source_id=i.source_id AND m.origin_id=i.origin_id)
                       OR COALESCE(i.updated_at, i.ts, '') > COALESCE(
                           (SELECT COALESCE(m.updated_at, m.ts, '') FROM imported_notes m
                            WHERE m.source=i.source AND m.source_id=i.source_id AND m.origin_id=i.origin_id), ''))
                ON CONFLICT(source, source_id, origin_id) DO UPDATE SET
                    origin_name=excluded.origin_name, text=excluded.text, ts=excluded.ts, updated_at=excluded.updated_at""")
        if con.execute("SELECT 1 FROM imp.sqlite_master WHERE type='table' AND name='manual_merges'").fetchone():
            _merge_manual_merges(con)
        con.commit()
        con.execute("DETACH DATABASE imp")
        return {"added": added, "updated": updated}
    finally:
        con.close()


def _merge_manual_merges(con):
    """Объединяет таблицы manual_merges основной и импортируемой баз без разрыва пересекающихся связок."""
    main_rows = [dict(r) for r in con.execute("SELECT source, source_id, group_id, is_primary, ts FROM manual_merges")]
    imp_rows = [dict(r) for r in con.execute("SELECT source, source_id, group_id, is_primary, ts FROM imp.manual_merges")]
    if not imp_rows:
        return
    if not main_rows:
        con.executemany(
            "INSERT OR IGNORE INTO manual_merges(source, source_id, group_id, is_primary, ts) VALUES(?,?,?,?,?)",
            [(r["source"], r["source_id"], r["group_id"], r["is_primary"], r["ts"]) for r in imp_rows],
        )
        dedupe.clean_manual_merges(con)
        return
    by_key, by_tag, info = {}, {}, {}
    for side, src_rows in (("main", main_rows), ("imp", imp_rows)):
        for r in src_rows:
            k = (r["source"], str(r["source_id"]))
            tag = (side, r["group_id"])
            by_key.setdefault(k, set()).add(tag)
            by_tag.setdefault(tag, set()).add(k)
            info.setdefault(k, []).append((side, r))
    seen_k, used_gids, out = set(), set(), []
    for k0 in sorted(by_key):
        if k0 in seen_k:
            continue
        comp_keys, comp_tags = set(), set()
        stack = [k0]
        seen_k.add(k0)
        while stack:
            k = stack.pop()
            comp_keys.add(k)
            for tag in by_key[k]:
                if tag in comp_tags:
                    continue
                comp_tags.add(tag)
                for nk in by_tag[tag]:
                    if nk not in seen_k:
                        seen_k.add(nk)
                        stack.append(nk)
        if len(comp_keys) < 2:
            continue
        cands = [(1 if r["is_primary"] else 0, r["ts"] or "", 1 if side == "main" else 0, k, r["group_id"])
                 for k in comp_keys for side, r in info[k]]
        _, win_ts, _, prim_key, win_gid = max(cands)
        if win_gid in used_gids:
            win_gid = f"{prim_key[0]}:{prim_key[1]}"
        used_gids.add(win_gid)
        ts = max((r["ts"] or "" for k in comp_keys for _, r in info[k]), default=win_ts)
        for src, sid in sorted(comp_keys):
            out.append((src, sid, win_gid, 1 if (src, sid) == prim_key else 0, ts))
    con.execute("DELETE FROM manual_merges")
    con.executemany(
        "INSERT OR REPLACE INTO manual_merges(source, source_id, group_id, is_primary, ts) VALUES(?,?,?,?,?)", out
    )
    dedupe.clean_manual_merges(con)
