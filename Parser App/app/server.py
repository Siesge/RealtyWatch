"""Локальный веб-интерфейс: http://127.0.0.1:<port>/"""
import json

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, dedupe, geo, history, images, rules, settings, transfer
from .paths import EXPORTS, WEB
from .runner import manager
from .scrapers import SCRAPERS

app = FastAPI(title="RealtyWatch")
app.mount("/static", StaticFiles(directory=WEB), name="static")

MEDIA = {".webp": "image/webp", ".jpg": "image/jpeg", ".png": "image/png", ".avif": "image/avif"}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/listings")
def listings():
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM listings ORDER BY first_seen DESC")]
    hist = {}
    for r in con.execute("SELECT source, source_id, ts, price FROM price_history ORDER BY ts"):
        hist.setdefault((r["source"], r["source_id"]), []).append([r["ts"], r["price"]])
    liked = {(r["source"], r["source_id"]) for r in con.execute("SELECT source, source_id FROM likes")}
    con.close()
    for r in rows:
        key = (r["source"], r["source_id"])
        r["flags"] = json.loads(r["flags"]) if r["flags"] else {}
        r["raw"] = json.loads(r["raw"]) if r["raw"] else {}
        r["history"] = hist.get(key, [])
        r["liked"] = key in liked
        r["object_id"] = r["object_id"] or f"{r['source']}:{r['source_id']}"
        r["has_img"] = images.local_path(*key) is not None
    return rows


@app.post("/api/like")
def like(body: dict = Body(...)):
    """Избранное хранится по каждому объявлению объекта — так оно переживает пересборку объектов."""
    keys = [k.split(":", 1) for k in body.get("keys", [])]
    con = db.connect()
    for source, sid in keys:
        if body.get("on"):
            con.execute("INSERT OR IGNORE INTO likes VALUES(?,?,?)", (source, sid, db.now()))
        else:
            con.execute("DELETE FROM likes WHERE source=? AND source_id=?", (source, sid))
    con.commit()
    con.close()
    return {"ok": True}


@app.get("/img/{source}/{source_id}")
def image(source: str, source_id: str):
    """Локальная копия фото; если её нет — одна попытка скачать прямо сейчас."""
    p = images.local_path(source, source_id)
    if p is None:
        con = db.connect()
        r = con.execute("SELECT photo, img_tries FROM listings WHERE source=? AND source_id=?", (source, source_id)).fetchone()
        if r and r["photo"] and (r["img_tries"] or 0) < images.MAX_TRIES:
            if images.download(source, source_id, r["photo"]):
                p = images.local_path(source, source_id)
            else:
                con.execute("UPDATE listings SET img_tries=COALESCE(img_tries,0)+1 WHERE source=? AND source_id=?", (source, source_id))
                con.commit()
        con.close()
    if p is None:
        return Response(status_code=404)
    return FileResponse(p, media_type=MEDIA.get(p.suffix, "image/jpeg"), headers={"Cache-Control": "max-age=86400"})


_scheduler = None


def scheduler():
    global _scheduler
    if _scheduler is None:
        from .scheduler import Scheduler

        _scheduler = Scheduler(manager)
    return _scheduler


@app.get("/api/schedule")
def schedule_get():
    return scheduler().get()


@app.post("/api/schedule")
def schedule_set(body: dict = Body(...)):
    return scheduler().update(body)


@app.get("/api/changes")
def changes(days: int = 7, baseline: int = 0):
    con = db.connect()
    rows = history.changes(con, days, bool(baseline))
    con.close()
    return rows


@app.get("/api/dashboard")
def dashboard():
    con = db.connect()
    d = history.dashboard(con)
    con.close()
    return d


@app.post("/api/recheck_removed")
def recheck_removed():
    if manager.any_running():
        raise HTTPException(409, "Дождитесь окончания сбора")
    import threading

    threading.Thread(target=history.recheck_removed, args=(SCRAPERS, settings.load(), lambda m: manager.log("db", m)),
                     daemon=True).start()
    return {"started": True}


@app.post("/api/images/repair")
def images_repair():
    con = db.connect()
    con.execute("UPDATE listings SET img_tries=0")  # ручной запуск — новый круг попыток
    con.commit()
    con.close()
    images.repair_async(db.connect, log=lambda m: manager.log("db", m))
    return {"started": True}


@app.get("/api/db/info")
def db_info():
    return transfer.stats()


@app.post("/api/db/export")
def db_export(body: dict = Body(default={})):
    dest = body.get("path") or str(EXPORTS / transfer.default_name())
    path = transfer.export_zip(dest)
    manager.log("db", f"экспорт базы: {path}")
    return {"path": path}


@app.post("/api/db/import")
def db_import(body: dict = Body(...)):
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    try:
        res = transfer.import_zip(body["path"], body.get("mode", "merge"))
    except (ValueError, KeyError, OSError) as e:
        raise HTTPException(400, str(e))
    manager.log("db", f"импорт базы ({res['mode']}): добавлено {res['added']}, обновлено {res['updated']}, фото {res['images']}")
    return res


@app.get("/api/settings")
def get_settings():
    return settings.load()


@app.post("/api/settings")
def set_settings(cfg: dict = Body(...)):
    cur = settings.load()
    for k in ("filters", "zone", "sites", "browser"):
        if k in cfg:
            cur[k] = cfg[k]
    settings.save(cur)
    return cur


@app.post("/api/reclassify")
def reclassify():
    """Пересчитать зону и отсев для всей базы по текущим настройкам (без обращения к сайтам)."""
    cfg = settings.load()
    con = db.connect()
    n = 0
    for r in con.execute("SELECT * FROM listings").fetchall():
        item = dict(r)
        item["flags"] = {}
        item["zone"] = geo.classify(item, cfg["zone"])
        reasons = rules.evaluate(item, cfg["filters"])
        con.execute("UPDATE listings SET zone=?, excluded=?, flags=? WHERE source=? AND source_id=?",
                    (item["zone"], "; ".join(reasons) or None, json.dumps(item["flags"], ensure_ascii=False),
                     item["source"], item["source_id"]))
        n += 1
    con.commit()
    objects = dedupe.rebuild(con)
    con.close()
    return {"updated": n, "objects": objects}


@app.post("/api/run")
def run(body: dict = Body(...)):
    sources = body.get("sources") or list(SCRAPERS)
    return {"started": manager.start(sources)}


@app.post("/api/stop")
def stop(body: dict = Body(default={})):
    manager.stop(body.get("sources"))
    return {"ok": True}


@app.get("/api/status")
def status(after: int = 0):
    return manager.snapshot(after)


@app.get("/api/runs")
def runs():
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 50")]
    con.close()
    return rows
