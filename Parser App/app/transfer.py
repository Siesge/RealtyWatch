"""Экспорт и импорт базы одним .zip: realty.db + images/ + settings.json + manifest.json.

Импорт:
  merge   — объединить: новые объявления добавляются, по существующим берётся более свежая версия,
            история цен, прогоны и избранное объединяются, недостающие фото докладываются;
  replace — заменить базу целиком.
Перед любым импортом текущая база копируется в database/backups/.
"""
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import db, dedupe
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
    out = sqlite3.connect(dest)
    src.backup(out)
    out.close()
    src.close()


def stats():
    con = db.connect()
    r = con.execute("""SELECT COUNT(*) n, COUNT(DISTINCT object_id) objects, SUM(excluded IS NULL AND active=1) passing,
                       (SELECT COUNT(*) FROM likes) likes FROM listings""").fetchone()
    con.close()
    img_files = [p for p in IMAGES.rglob("*") if p.is_file() and p.suffix != ".part"]
    db_size = sum(p.stat().st_size for p in DB_DIR.glob("realty.db*"))
    return {"listings": r["n"], "objects": r["objects"], "passing": r["passing"] or 0, "likes": r["likes"],
            "images": len(img_files), "size_mb": round((db_size + sum(p.stat().st_size for p in img_files)) / 2**20, 1),
            "folder": str(DB_DIR)}


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


def _backup():
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = BACKUPS / f"перед_импортом_{_stamp()}.zip"
    return export_zip(dest, with_images=False)  # фото при импорте не удаляются — копируем только базу


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


def import_zip(path, mode="merge"):
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        manifest = _check(z)
        backup = _backup()
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
    result["objects"] = dedupe.rebuild(con)
    con.close()
    result.update(backup=backup, mode=mode, exported_at=manifest.get("exported_at"))
    return result


def _replace(imported, z):
    src = sqlite3.connect(imported)
    dst = db.connect()
    src.backup(dst)  # перезаписывает текущую базу содержимым импортируемой
    dst.close()
    src.close()
    db.reset()
    if "settings.json" in z.namelist():
        (DB_DIR / "settings.json").write_bytes(z.read("settings.json"))
    con = db.connect()
    n = con.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    con.close()
    return {"added": n, "updated": 0}


def _merge(imported):
    con = db.connect()
    con.execute("ATTACH DATABASE ? AS imp", (str(imported),))
    main_cols = [r[1] for r in con.execute("PRAGMA main.table_info(listings)")]
    imp_cols = {r[1] for r in con.execute("PRAGMA imp.table_info(listings)")}
    cols = [c for c in main_cols if c in imp_cols and c != "object_id"]
    col_list = ",".join(cols)
    added = con.execute(f"""INSERT INTO listings({col_list}) SELECT {col_list} FROM imp.listings i
        WHERE NOT EXISTS (SELECT 1 FROM listings m WHERE m.source=i.source AND m.source_id=i.source_id)""").rowcount
    upd_cols = [c for c in cols if c not in ("source", "source_id", "first_seen")]
    updated = con.execute(f"""UPDATE listings SET ({",".join(upd_cols)}) =
        (SELECT {",".join("i." + c for c in upd_cols)} FROM imp.listings i WHERE i.source=listings.source AND i.source_id=listings.source_id)
        WHERE EXISTS (SELECT 1 FROM imp.listings i WHERE i.source=listings.source AND i.source_id=listings.source_id
                      AND i.last_seen > listings.last_seen)""").rowcount
    con.execute("""UPDATE listings SET first_seen = (SELECT MIN(i.first_seen) FROM imp.listings i
        WHERE i.source=listings.source AND i.source_id=listings.source_id AND i.first_seen < listings.first_seen)
        WHERE EXISTS (SELECT 1 FROM imp.listings i WHERE i.source=listings.source AND i.source_id=listings.source_id
                      AND i.first_seen < listings.first_seen)""")
    con.execute("""INSERT INTO price_history SELECT * FROM imp.price_history i WHERE NOT EXISTS
        (SELECT 1 FROM price_history p WHERE p.source=i.source AND p.source_id=i.source_id AND p.ts=i.ts AND p.price=i.price)""")
    con.execute("""INSERT INTO runs(source, mode, started, finished, status, pages, found, new, changed, removed, message)
        SELECT source, mode, started, finished, status, pages, found, new, changed, removed, message FROM imp.runs i
        WHERE NOT EXISTS (SELECT 1 FROM runs r WHERE r.source=i.source AND r.started=i.started)""")
    con.execute("INSERT OR IGNORE INTO likes SELECT * FROM imp.likes")
    con.commit()
    con.execute("DETACH DATABASE imp")
    con.close()
    return {"added": added, "updated": updated}
