"""Локальный веб-интерфейс: http://127.0.0.1:<port>/"""
import json
import os

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import about, alerts, db, dedupe, geo, history, images, paths, purge, rules, runlog, settings, share, transfer
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
def changes(days: int = 7, baseline: int = 0, hours: float = 0, last: int = 0):
    con = db.connect()
    rows = history.changes(con, days, bool(baseline), hours=hours or None, last=bool(last))
    con.close()
    return rows


@app.get("/api/last_update")
def last_update():
    con = db.connect()
    rows = history.last_update(con)
    con.close()
    return rows


@app.get("/api/dashboard")
def dashboard(days: int = 0):
    con = db.connect()
    d = history.dashboard(con, days or None)
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


@app.get("/api/about")
def about_info():
    return about.info()


@app.post("/api/liked/export")
def liked_export(body: dict = Body(default={})):
    """Избранное одной автономной веб-страницей. Без окна программы (отладочный сервер) — в папку exports."""
    con = db.connect()
    try:
        if not share.has_liked(con):
            raise HTTPException(400, "в избранном пусто")
        EXPORTS.mkdir(exist_ok=True)
        res = share.export(body.get("path") or str(EXPORTS / share.default_name()), con)
    finally:
        con.close()
    manager.log("db", f"избранное выгружено: {res['path']} ({res['objects']} объектов, {res['mb']} МБ)")
    return res


@app.post("/api/db/purge")
def db_purge(body: dict = Body(...)):
    """Очистка за период {from, to} (даты ГГГГ-ММ-ДД, пустая — без границы); dry_run — только посчитать."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    try:
        if body.get("dry_run"):
            con = db.connect()
            res = purge.preview(con, body.get("from") or None, body.get("to") or None)
            con.close()
            return res
        res = purge.purge(body.get("from") or None, body.get("to") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    manager.log("db", f"очистка за период {body.get('from') or 'с начала'} — {body.get('to') or 'по сейчас'}: "
                      f"удалено объявлений {res['listings']}, проверок рынка {res['snapshots']}, событий {res['events']}, "
                      f"прогонов {res['runs']}; возвращено в продажу {res['unremoved']}. Резервная копия: {res['backup']}")
    return res


@app.post("/api/db/clear")
def db_clear(body: dict = Body(...)):
    if body.get("confirm") != "ОЧИСТИТЬ":
        raise HTTPException(400, "нужно подтверждение")
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    res = purge.clear_all()
    manager.log("db", f"база очищена полностью ({res['listings']} объявлений). Резервная копия: {res['backup']}")
    return res


@app.get("/api/sound")
def sound_get():
    return {"muted": alerts.muted()}


@app.post("/api/sound")
def sound_set(body: dict = Body(...)):
    on = alerts.set_muted(bool(body.get("muted")))
    if not on:
        alerts.chime()  # звук включён — короткий сигнал для проверки
    return {"muted": on}


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
    """Пересчитать зону и отсев для всей базы по текущим настройкам (без обращения к сайтам).
    История снимков рынка пересобирается под новые зону и правила."""
    return _reclassify(settings.load())


@app.post("/api/zone")
def set_zone(body: dict = Body(...)):
    """Новый круг (зона A) с карты: сохранить, пересчитать зоны всех объявлений и историю рынка."""
    if manager.any_running():
        raise HTTPException(409, "Идёт сбор данных — круг можно изменить после его окончания")
    try:
        lat, lon = (float(x) for x in body["center"])
        radius = float(body["radius_m"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "нужны центр [широта, долгота] и радиус в метрах")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not (50 <= radius <= 50_000):
        raise HTTPException(400, "центр или радиус вне допустимых значений (радиус 50 м – 50 км)")
    cfg = settings.load()
    old = dict(cfg["zone"])
    cfg["zone"].update(center=[round(lat, 6), round(lon, 6)], radius_m=round(radius), polygon=None)
    settings.save(cfg)
    con = db.connect()
    before = {(r["source"], r["source_id"]): r["zone"] for r in con.execute("SELECT source, source_id, zone FROM listings")}
    con.close()
    res = _reclassify(cfg)
    con = db.connect()
    after = {(r["source"], r["source_id"]): r["zone"] for r in con.execute("SELECT source, source_id, zone FROM listings")}
    con.close()
    res["moved_in"] = sum(1 for k, z in after.items() if z == "A" and before.get(k) != "A")
    res["moved_out"] = sum(1 for k, z in after.items() if z != "A" and before.get(k) == "A")
    manager.log("db", f"круг изменён: центр {old['center'][0]:.5f}, {old['center'][1]:.5f} → {lat:.5f}, {lon:.5f}, "
                      f"радиус {old['radius_m']} → {round(radius)} м; в круг вошло {res['moved_in']}, вышло {res['moved_out']} объявлений; "
                      f"история рынка пересчитана ({res['snapshots']} проверок)")
    return res


def _reclassify(cfg):
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
    snapshots = history.rebuild_snapshots(con)
    con.close()
    return {"updated": n, "objects": objects, "snapshots": snapshots}


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
    for r in rows:
        r["has_log"] = runlog.run_log_path(r["id"]) is not None
    return rows


def run_log_text(run_id):
    """Подробный журнал прогона + список сохранённых при сбоях страниц."""
    p = runlog.run_log_path(run_id)
    if p is None:
        return None, []
    snaps = sorted(x.name for x in runlog.SNAP_DIR.glob(f"{int(run_id):06d}_*")) if runlog.SNAP_DIR.exists() else []
    return p.read_text(encoding="utf-8", errors="replace"), snaps


@app.get("/api/runs/{run_id}/log")
def run_log(run_id: int):
    text, snaps = run_log_text(run_id)
    if text is None:
        raise HTTPException(404, "подробного журнала у этого прогона нет (он был до появления журналов или удалён)")
    return {"text": text, "snapshots": snaps, "file": f"logs/runs/{runlog.run_log_path(run_id).name}"}


@app.post("/api/runs/{run_id}/log/export")
def run_log_export(run_id: int):
    """Без окна программы (отладочный сервер): сохраняет журнал в папку exports."""
    text, snaps = run_log_text(run_id)
    if text is None:
        raise HTTPException(404, "журнала нет")
    paths.EXPORTS.mkdir(exist_ok=True)
    out = paths.EXPORTS / f"realtywatch-run-{run_id}.log"
    out.write_text(text + (("\nСохранённые страницы (logs/snapshots):\n" + "\n".join(snaps)) if snaps else ""), encoding="utf-8")
    return {"path": str(out)}


@app.post("/api/logs/open")
def logs_open():
    os.startfile(str(paths.LOGS))
    return {"ok": True}
