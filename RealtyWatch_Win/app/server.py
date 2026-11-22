"""Локальный веб-интерфейс: http://127.0.0.1:<port>/"""
import json
import os
import urllib.parse
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import about, alerts, db, dedupe, exchange, geo, history, images, lan_sync, offsale, paths, purge, rules, runlog, settings, share, tiles, transfer, updater
from .paths import EXPORTS, WEB
from .runner import manager
from .scrapers import SCRAPERS

app = FastAPI(title="RealtyWatch")
app.mount("/static", StaticFiles(directory=WEB), name="static")

MEDIA = {".webp": "image/webp", ".jpg": "image/jpeg", ".png": "image/png", ".avif": "image/avif"}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


def _format_listings(rows, hist, liked, disliked, notes, merged_keys, primaries, auto_map, have_img, imp_notes=None):
    """Единый формат выдачи объявлений для GET /api/listings и дельт после слияния/разделения."""
    imp_notes = imp_notes or {}
    for r in rows:
        key = (r["source"], str(r["source_id"]))
        r["flags"] = json.loads(r["flags"]) if r["flags"] else {}
        raw = json.loads(r["raw"]) if r["raw"] else {}
        r["apartments"] = bool(raw.get("apartments") or raw.get("isApartments"))
        r.pop("raw", None)
        r["history"] = hist.get(key, [])
        r["liked"] = key in liked
        r["disliked"] = key in disliked
        r["note"], r["note_ts"] = notes.get(key, ("", ""))
        r["imported_notes"] = imp_notes.get(key, [])
        r["manual_merged"] = key in merged_keys
        r["manual_primary"] = key in primaries
        oid = r["object_id"] or f"{r['source']}:{r['source_id']}"
        r["object_id"] = oid
        r["auto_group"] = auto_map.get(key, oid) if key in merged_keys else oid
        r["has_img"] = key in have_img
        # объявления нет в поиске сайта столько прогонов, что ссылка на него уже никуда не ведёт
        r["off_site"] = offsale.off_search(r)
    return rows


def _listings_for_keys(con, keys):
    """Загружает и форматирует только затронутые объявления без пересылки всей базы."""
    klist = sorted({(str(s), str(i)) for s, i in (keys or ())})
    if not klist:
        return []
    rows = []
    hist = {}
    liked = set()
    disliked = set()
    notes = {}
    imp_notes = {}
    for off in range(0, len(klist), 400):
        chunk = klist[off:off + 400]
        cond = " OR ".join("(source=? AND source_id=?)" for _ in chunk)
        args = [x for pair in chunk for x in pair]
        rows.extend(dict(r) for r in con.execute(f"SELECT * FROM listings WHERE {cond}", args))
        for r in con.execute(f"SELECT source, source_id, ts, price FROM price_history WHERE {cond} ORDER BY ts", args):
            hist.setdefault((r["source"], str(r["source_id"])), []).append([r["ts"], r["price"]])
        liked.update((r["source"], str(r["source_id"])) for r in con.execute(f"SELECT source, source_id FROM likes WHERE {cond}", args))
        disliked.update((r["source"], str(r["source_id"])) for r in con.execute(f"SELECT source, source_id FROM dislikes WHERE {cond}", args))
        for r in con.execute(f"SELECT source, source_id, text, ts FROM notes WHERE {cond}", args):
            notes[(r["source"], str(r["source_id"]))] = (r["text"] or "", r["ts"] or "")
        for r in con.execute(
            f"SELECT source, source_id, origin_id, origin_name, text, ts, updated_at FROM imported_notes "
            f"WHERE ({cond}) AND COALESCE(text, '') != '' "
            f"ORDER BY COALESCE(updated_at, ts, '') ASC, origin_name ASC",
            args,
        ):
            imp_notes.setdefault((r["source"], str(r["source_id"])), []).append({
                "origin_id": r["origin_id"],
                "origin_name": r["origin_name"] or "",
                "text": r["text"] or "",
                "ts": r["updated_at"] or r["ts"] or "",
            })
    rows.sort(key=lambda r: r.get("first_seen") or "", reverse=True)
    by_group, primaries = dedupe.active_manual_merges(con)
    merged_keys = {k for ks in by_group.values() for k in ks}
    need_ag = {(r["source"], str(r["source_id"])) for r in rows} & merged_keys
    auto_map = dedupe.auto_group_map(con, target_keys=need_ag) if need_ag else {}
    have_img = {(r["source"], str(r["source_id"])) for r in rows if images.local_path(r["source"], r["source_id"]) is not None}
    return _format_listings(rows, hist, liked, disliked, notes, merged_keys, primaries, auto_map, have_img, imp_notes)


@app.get("/api/listings")
def listings():
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM listings ORDER BY first_seen DESC")]
        hist = {}
        for r in con.execute("SELECT source, source_id, ts, price FROM price_history ORDER BY ts"):
            hist.setdefault((r["source"], str(r["source_id"])), []).append([r["ts"], r["price"]])
        liked = {(r["source"], str(r["source_id"])) for r in con.execute("SELECT source, source_id FROM likes")}
        disliked = {(r["source"], str(r["source_id"])) for r in con.execute("SELECT source, source_id FROM dislikes")}
        notes = {(r["source"], str(r["source_id"])): (r["text"] or "", r["ts"] or "")
                 for r in con.execute("SELECT source, source_id, text, ts FROM notes")}
        imp_notes = {}
        for r in con.execute(
            "SELECT source, source_id, origin_id, origin_name, text, ts, updated_at FROM imported_notes "
            "WHERE COALESCE(text, '') != '' ORDER BY COALESCE(updated_at, ts, '') ASC, origin_name ASC"
        ):
            imp_notes.setdefault((r["source"], str(r["source_id"])), []).append({
                "origin_id": r["origin_id"],
                "origin_name": r["origin_name"] or "",
                "text": r["text"] or "",
                "ts": r["updated_at"] or r["ts"] or "",
            })
        by_group, primaries = dedupe.active_manual_merges(con)
        merged_keys = {k for ks in by_group.values() for k in ks}
    finally:
        con.close()
    auto_map = dedupe.auto_group_map(rows=rows, target_keys=merged_keys) if merged_keys else {}
    have_img = images.existing_set()
    return _format_listings(rows, hist, liked, disliked, notes, merged_keys, primaries, auto_map, have_img, imp_notes)


def _mark(table, other, body):
    """Отметка по каждому объявлению объекта — так она переживает пересборку объектов. Лайк и дизлайк
    взаимоисключающие: поставленная отметка снимает противоположную."""
    keys = [k.split(":", 1) for k in body.get("keys", [])]
    con = db.connect()
    try:
        for source, sid in keys:
            if body.get("on"):
                con.execute(f"INSERT OR IGNORE INTO {table} VALUES(?,?,?)", (source, sid, db.now()))
                con.execute(f"DELETE FROM {other} WHERE source=? AND source_id=?", (source, sid))
            else:
                con.execute(f"DELETE FROM {table} WHERE source=? AND source_id=?", (source, sid))
        con.commit()
    finally:
        con.close()
    return {"ok": True}


@app.post("/api/like")
def like(body: dict = Body(...)):
    """Избранное (♥)."""
    res = _mark("likes", "dislikes", body)
    keys = [tuple(k.split(":", 1)) for k in body.get("keys", []) if ":" in str(k)]
    lan_sync.on_local_favorite(keys, bool(body.get("on")))
    return res


@app.post("/api/dislike")
def dislike(body: dict = Body(...)):
    """Скрытые (дизлайк): квартира убирается из просмотра, но собирается и обновляется как все."""
    return _mark("dislikes", "likes", body)


@app.post("/api/note")
def note_set(body: dict = Body(...)):
    """Личная заметка к квартире (по каждому её объявлению, чтобы переживала пересборку и слияния)."""
    keys = [tuple(str(k).split(":", 1)) for k in body.get("keys", []) if ":" in str(k)]
    text = str(body.get("text") or "").strip()[:2000]
    ts = db.now() if text else ""
    con = db.connect()
    try:
        for source, sid in keys:
            if text:
                con.execute("INSERT OR REPLACE INTO notes(source, source_id, text, ts) VALUES(?,?,?,?)",
                            (source, sid, text, ts))
            else:
                con.execute("DELETE FROM notes WHERE source=? AND source_id=?", (source, sid))
        con.commit()
    finally:
        con.close()
    lan_sync.on_local_note(keys, text, ts)
    return {"ok": True, "text": text, "ts": ts}


@app.get("/img/import/{source}/{source_id}")
def import_image(source: str, source_id: str):
    """Локальное фото для карточек импорта (из images или архива import_images для неактуальных)."""
    p = exchange.import_image_path(source, source_id)
    if p is None:
        return Response(status_code=404)
    return FileResponse(p, media_type=MEDIA.get(p.suffix.lower(), "image/jpeg"), headers={"Cache-Control": "max-age=86400"})


@app.get("/img/{source}/{source_id}")
def image(source: str, source_id: str):
    """Локальная копия фото; если её нет — одна попытка скачать прямо сейчас."""
    p = images.local_path(source, source_id)
    if p is None:
        con = db.connect()
        try:
            r = con.execute("SELECT photo, img_tries FROM listings WHERE source=? AND source_id=?", (source, source_id)).fetchone()
            if r and r["photo"] and (r["img_tries"] or 0) < images.MAX_TRIES:
                if images.download(source, source_id, r["photo"]):
                    p = images.local_path(source, source_id)
                else:
                    con.execute("UPDATE listings SET img_tries=COALESCE(img_tries,0)+1 WHERE source=? AND source_id=?", (source, source_id))
                    con.commit()
        finally:
            con.close()
    if p is None:
        return Response(status_code=404)
    return FileResponse(p, media_type=MEDIA.get(p.suffix, "image/jpeg"), headers={"Cache-Control": "max-age=86400"})


@app.get("/tiles/{z}/{x}/{y}.png")
def map_tile(z: str, x: str, y: str):
    """Прокси и локальный дисковый кэш тайлов карты (работает без интернета по сохранённым участкам)."""
    try:
        zi, xi, yi = int(z), int(x), int(y)
        data = tiles.get_tile(zi, xi, yi)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if data is None:
        raise HTTPException(502, "не удалось загрузить тайл карты")
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


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
    try:
        return history.changes(con, days, bool(baseline), hours=hours or None, last=bool(last))
    finally:
        con.close()


@app.get("/api/last_update")
def last_update():
    con = db.connect()
    try:
        return history.last_update(con)
    finally:
        con.close()


@app.get("/api/dashboard")
def dashboard(days: int = 0):
    con = db.connect()
    try:
        return history.dashboard(con, days or None)
    finally:
        con.close()


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
    try:
        con.execute("UPDATE listings SET img_tries=0")  # ручной запуск — новый круг попыток
        con.commit()
    finally:
        con.close()
    images.repair_async(db.connect, log=lambda m: manager.log("db", m))
    return {"started": True}


@app.get("/api/db/info")
def db_info():
    return transfer.stats()


@app.post("/api/objects/merge")
def objects_merge(body: dict = Body(...)):
    """Ручное объединение двух карточек: параметры и первенство остаются у первой (primary_id)."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    p_id = str(body.get("primary_id") or "").strip()
    s_id = str(body.get("secondary_id") or "").strip()
    con = db.connect()
    p_keys, s_keys = [], []
    try:
        q_keys = "SELECT source, source_id FROM listings WHERE object_id=? OR (object_id IS NULL AND source||':'||source_id=?)"
        p_keys = [(r[0], str(r[1])) for r in con.execute(q_keys, (p_id, p_id))]
        s_keys = [(r[0], str(r[1])) for r in con.execute(q_keys, (s_id, s_id))]
        res = dedupe.merge_objects(con, p_id, s_id)
        touched = res.pop("_keys", ())
        res["updated"] = _listings_for_keys(con, touched)
        res["snapshots"] = history.mark_snapshots_dirty(con) if body.get("defer_snapshots") else history.rebuild_snapshots(con)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()
    manager.log("db", f"объединение карточек вручную: {p_id} ← {s_id} (объявлений в карточке: {res['items']})")
    lan_sync.on_local_merge("merge", primary_keys=p_keys, secondary_keys=s_keys)
    return {"ok": True, **res}


@app.post("/api/objects/unmerge")
def objects_unmerge(body: dict = Body(...)):
    """Точечное разделение одной объединённой карточки обратно на исходные."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    oid = str(body.get("object_id") or "").strip()
    con = db.connect()
    u_keys = []
    try:
        u_keys = [(r[0], str(r[1])) for r in con.execute(
            "SELECT source, source_id FROM listings WHERE object_id=? OR (object_id IS NULL AND source||':'||source_id=?)",
            (oid, oid),
        )]
        res = dedupe.unmerge_object(con, oid)
        touched = res.pop("_keys", ())
        res["updated"] = _listings_for_keys(con, touched)
        res["snapshots"] = history.mark_snapshots_dirty(con) if body.get("defer_snapshots") else history.rebuild_snapshots(con)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()
    manager.log("db", f"карточка {oid} разделена вручную (частей: {res['parts']})")
    lan_sync.on_local_merge("unmerge", keys=u_keys)
    return {"ok": True, **res}


@app.post("/api/objects/exclude")
def objects_exclude(body: dict = Body(...)):
    """Исключает из ручного объединения исходную авто-группу выбранного объявления."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    oid = str(body.get("object_id") or "").strip()
    source = str(body.get("source") or "").strip()
    sid = str(body.get("source_id") or "").strip()
    if (not source or not sid) and (body.get("key") or body.get("keys")):
        raw_k = body.get("key") or (body.get("keys") or [None])[0]
        if raw_k and ":" in str(raw_k):
            source, sid = str(raw_k).split(":", 1)
    con = db.connect()
    try:
        res = dedupe.exclude_from_merge(con, oid, source, sid)
        touched = res.pop("_keys", ())
        res["updated"] = _listings_for_keys(con, touched)
        res["snapshots"] = history.mark_snapshots_dirty(con) if body.get("defer_snapshots") else history.rebuild_snapshots(con)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()
    manager.log("db", f"исключена группа ({', '.join(res['excluded_keys'])}) из объединения {oid}")
    lan_sync.on_local_merge("exclude", primary_keys=[(source, sid)])
    return {"ok": True, **res}


@app.post("/api/db/unmerge")
def db_unmerge():
    """Сброс всех ручных объединений с пересчётом карточек по правилам по умолчанию."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    con = db.connect()
    try:
        res = dedupe.unmerge_all(con)
        res["snapshots"] = history.rebuild_snapshots(con)
    finally:
        con.close()
    manager.log("db", f"ручные объединения сброшены ({res['unmerged']} шт.), объектов в базе: {res['objects']}")
    lan_sync.on_local_merge("unmerge_all")
    return {"ok": True, **res}


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


@app.post("/api/cards/export")
def cards_export(body: dict = Body(default={})):
    """Экспорт выбранных карточек или избранного в переносимый архив .rwcards.zip."""
    kind = body.get("kind") or ("favorites" if body.get("liked_only") else "cards")
    dest = body.get("path")
    if not dest:
        paths.EXPORTS.mkdir(exist_ok=True)
        dest = str(paths.EXPORTS / exchange.default_name(kind))
    try:
        res = exchange.export_cards_zip(
            dest,
            object_ids=body.get("object_ids"),
            liked_only=bool(body.get("liked_only")),
            kind=kind,
        )
    except (ValueError, KeyError, OSError) as e:
        raise HTTPException(400, str(e))
    manager.log("db", f"карточки выгружены (.rwcards.zip): {res['path']} ({res['cards']} карточек, фото {res['images']})")
    return res


@app.get("/api/exports/download")
def exports_download(name: str):
    """Скачивание сохранённого .rwcards.zip из папки exports в браузерном режиме."""
    safe = Path(name).name
    if not safe or safe != name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "неверное имя файла")
    p = paths.EXPORTS / safe
    if not p.is_file():
        raise HTTPException(404, "файл не найден")
    return FileResponse(p, media_type="application/zip", filename=safe)


@app.post("/api/imports/upload")
async def imports_upload(request: Request, name: str = ""):
    """Приём файла .rwcards.zip из браузера (--server) во временную папку и предпросмотр."""
    raw_name = name or urllib.parse.unquote(request.headers.get("x-filename", ""))
    data = await request.body()
    try:
        return exchange.stage_uploaded_bundle(data, raw_name)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/imports/preview")
def imports_preview(body: dict = Body(...)):
    """Предпросмотр пакета .rwcards.zip: подсчёт новых, совпадений, дублей и неактуальных без записи в базу."""
    try:
        return exchange.preview_cards_zip(body["path"])
    except (ValueError, KeyError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/imports/apply")
def imports_apply(body: dict = Body(...)):
    """Импорт карточек .rwcards.zip в слой «Импорт»."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    try:
        res = exchange.import_cards_zip(
            body["path"],
            likes_mode=body.get("likes_mode", "all"),
            channel=body.get("channel", "file"),
            apply_manual_merges=body.get("apply_manual_merges", True),
        )
    except (ValueError, KeyError, OSError) as e:
        raise HTTPException(400, str(e))
    if res.get("skipped_empty"):
        manager.log("db", f"импорт карточек пропущен: все {res['total_cards']} уже есть без изменений")
    else:
        manager.log(
            "db",
            f"импорт карточек №{res['batch_id']} ({res['origin_name']}): "
            f"новых {res['new_cards']}, дополнено {res['matched_cards']}, "
            f"повторов {res['duplicate_cards']}, неактуальных {res['stale_cards']}, "
            f"заметок {res['updated_notes']}",
        )
    return res


@app.get("/api/imports")
def imports_list(limit: int = 200):
    """Журнал всех партий импорта карточек."""
    con = db.connect()
    try:
        return exchange.list_batches(con, limit=max(1, min(int(limit), 1000)))
    finally:
        con.close()


@app.get("/api/imports/{batch_id}")
def imports_detail(batch_id: int):
    """Состав конкретной партии импорта (актуальные карточки и срез неактуальных)."""
    con = db.connect()
    try:
        return exchange.get_batch_detail(con, batch_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    finally:
        con.close()


@app.post("/api/imports/{batch_id}/delete")
def imports_delete(batch_id: int):
    """Удаляет запись партии из журнала импортов без удаления объявлений из базы."""
    con = db.connect()
    try:
        return exchange.delete_batch(con, batch_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    finally:
        con.close()


@app.post("/api/db/purge")
def db_purge(body: dict = Body(...)):
    """Очистка за период {from, to} (даты ГГГГ-ММ-ДД, пустая — без границы); dry_run — только посчитать."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    try:
        if body.get("dry_run"):
            con = db.connect()
            try:
                return purge.preview(con, body.get("from") or None, body.get("to") or None)
            finally:
                con.close()
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


@app.get("/api/db/launches")
def db_launches(date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"),
                limit: int = Query(400, ge=1, le=2000)):
    """Запуски сбора за период: что собрал каждый сайт и чем закончил (для вкладки «Прогоны» и выбора на удаление)."""
    con = db.connect()
    try:
        return purge.launches(con, date_from or None, date_to or None, limit=limit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        con.close()


@app.post("/api/db/runs/delete")
def db_runs_delete(body: dict = Body(...)):
    """Удаление выбранных прогонов {ids}; dry_run — только посчитать."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    try:
        ids = [int(x) for x in (body.get("ids") or [])]
    except (TypeError, ValueError):
        raise HTTPException(400, "неверные номера прогонов")
    if not ids:
        raise HTTPException(400, "не выбрано ни одного прогона")
    if body.get("dry_run"):
        con = db.connect()
        try:
            return purge.runs_preview(con, ids)
        finally:
            con.close()
    try:
        res = purge.purge_runs(ids)
    except ValueError as e:
        raise HTTPException(400, str(e))
    manager.log("db", f"удалены прогоны {', '.join(f'№{i}' for i in ids)}: объявлений {res['listings']}, "
                      f"точек графика {res['snapshots']}, изменений {res['events']}, записей цен {res['prices']}; "
                      f"возвращено в продажу {res['unremoved']}. Резервная копия: {res['backup']}")
    return res


@app.get("/api/db/backups")
def db_backups():
    return transfer.backups()


@app.post("/api/db/backups/delete")
def db_backups_delete(body: dict = Body(default={})):
    """Удаляет резервные копии: {names} или все, если список не задан."""
    try:
        res = transfer.delete_backups(body.get("names") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    manager.log("db", f"резервные копии удалены: {res['deleted']} шт., освободилось {res['mb']} МБ"
                      f"{' (в корзину)' if res['trash'] else ' (безвозвратно — корзины на диске нет)'}")
    return res


@app.post("/api/db/backups/restore")
def db_backups_restore(body: dict = Body(...)):
    """Восстановление базы из резервной копии (нынешнее состояние уходит в новую копию)."""
    if manager.any_running():
        raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
    try:
        res = transfer.restore(body["name"])
    except (ValueError, KeyError, OSError) as e:
        raise HTTPException(400, str(e))
    manager.log("db", f"восстановление из копии {res['restored']}: объявлений {res['added']}, фото {res['images']}. "
                      f"Нынешнее состояние сохранено: {res['backup']}")
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
    for k in ("filters", "zone", "sites", "browser", "ui", "lan", "updates"):
        if k in cfg:
            if k == "lan" and isinstance(cfg["lan"], dict):
                prev_id = (cur.get("lan") or {}).get("device_id", "")
                merged_lan = {**(cur.get("lan") or {}), **cfg["lan"]}
                if not str(merged_lan.get("device_id") or "").strip() and prev_id:
                    merged_lan["device_id"] = prev_id
                cur["lan"] = merged_lan
            elif k == "updates" and isinstance(cfg["updates"], dict):
                merged_upd = {**(cur.get("updates") or {}), **cfg["updates"]}
                merged_upd["auto_check"] = bool(merged_upd.get("auto_check", True))
                iv = str(merged_upd.get("interval") or "week")
                merged_upd["interval"] = iv if iv in updater.INTERVAL_SECONDS else "week"
                cur["updates"] = merged_upd
            else:
                cur[k] = cfg[k]
    settings.save(cur)
    if "lan" in cfg:
        lan_sync.reconfigure()
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
    try:
        before = {(r["source"], r["source_id"]): r["zone"] for r in con.execute("SELECT source, source_id, zone FROM listings")}
    finally:
        con.close()
    res = _reclassify(cfg)
    con = db.connect()
    try:
        after = {(r["source"], r["source_id"]): r["zone"] for r in con.execute("SELECT source, source_id, zone FROM listings")}
    finally:
        con.close()
    res["moved_in"] = sum(1 for k, z in after.items() if z == "A" and before.get(k) != "A")
    res["moved_out"] = sum(1 for k, z in after.items() if z != "A" and before.get(k) == "A")
    manager.log("db", f"круг изменён: центр {old['center'][0]:.5f}, {old['center'][1]:.5f} → {lat:.5f}, {lon:.5f}, "
                      f"радиус {old['radius_m']} → {round(radius)} м; в круг вошло {res['moved_in']}, вышло {res['moved_out']} объявлений; "
                      f"история рынка пересчитана ({res['snapshots']} проверок)")
    return res


def _reclassify(cfg):
    con = db.connect()
    try:
        updates = []
        for r in con.execute("SELECT * FROM listings").fetchall():
            item = dict(r)
            item["flags"] = {}
            item["zone"] = geo.classify(item, cfg["zone"])
            reasons = rules.evaluate(item, cfg["filters"])
            updates.append((item["zone"], "; ".join(reasons) or None, json.dumps(item["flags"], ensure_ascii=False),
                            item["source"], item["source_id"]))
        con.executemany("UPDATE listings SET zone=?, excluded=?, flags=? WHERE source=? AND source_id=?", updates)
        con.commit()
        objects = dedupe.rebuild(con)
        snapshots = history.rebuild_snapshots(con)
        return {"updated": len(updates), "objects": objects, "snapshots": snapshots}
    finally:
        con.close()


@app.post("/api/run")
def run(body: dict = Body(...)):
    sources = body.get("sources") or list(SCRAPERS)
    return {"started": manager.start(sources)}


@app.post("/api/run/continue")
def run_continue(body: dict = Body(default={})):
    try:
        return manager.continue_run(source=body.get("source"), run_id=body.get("run_id"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/stop")
def stop(body: dict = Body(default={})):
    manager.stop(body.get("sources"))
    return {"ok": True}


@app.get("/api/status")
def status(after: int = 0, lan_after: int = 0):
    snap = manager.snapshot(after)
    snap["lan"] = lan_sync.summary(lan_after=lan_after)
    snap["update"] = updater.service.summary()
    return snap


@app.get("/api/update/status")
def update_status():
    return updater.service.status()


@app.post("/api/update/check")
def update_check():
    try:
        return updater.service.check_for_updates(manual=True, download=True)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/update/apply")
def update_apply():
    try:
        return updater.service.apply_update()
    except RuntimeError as e:
        if str(e) == "BUSY_RUNNING":
            raise HTTPException(409, "Сначала дождитесь окончания или остановите сбор данных")
        raise HTTPException(400, str(e))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/update/ack")
def update_ack(body: dict = Body(default={})):
    return updater.service.acknowledge(
        dismiss_banner=bool(body.get("dismiss_banner")),
        clear_just_updated=bool(body.get("clear_just_updated")),
    )


@app.get("/api/lan/status")
def lan_status(refresh: bool = False):
    return lan_sync.status(refresh=refresh)


@app.post("/api/lan/config")
def lan_config(body: dict = Body(...)):
    if not isinstance(body, dict):
        raise HTTPException(400, "ожидался JSON-объект")
    cur = settings.load()
    lan = dict(cur.get("lan") or {})
    if "enabled" in body:
        lan["enabled"] = bool(body["enabled"])
    if "auto_discovery" in body:
        lan["auto_discovery"] = bool(body["auto_discovery"])
    if "port" in body:
        try:
            port = int(body["port"])
        except (TypeError, ValueError):
            raise HTTPException(400, "порт должен быть числом")
        if not (1024 <= port <= 65534):
            raise HTTPException(400, "порт должен быть в диапазоне 1024–65534")
        lan["port"] = port
    if "device_name" in body:
        name = str(body["device_name"] or "").strip()[:80]
        if not name:
            raise HTTPException(400, "имя устройства не может быть пустым")
        lan["device_name"] = name
    cur["lan"] = lan
    settings.save(cur)
    lan_sync.reconfigure()
    return lan_sync.status()


@app.post("/api/lan/discover")
def lan_discover(body: dict | None = Body(default=None)):
    b = body if isinstance(body, dict) else {}
    auto_enable = bool(b.get("auto_enable", True))
    wait_sec = b.get("wait_sec", 1.5)
    target_ports = b.get("target_ports") or ()
    try:
        return lan_sync.discover_now(wait_sec=wait_sec, auto_enable=auto_enable, target_ports=target_ports)
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/pin")
def lan_pin():
    if not lan_sync.service._lan_cfg()["enabled"]:
        raise HTTPException(400, "сначала включите доступ по локальной сети")
    return lan_sync.create_pin()


@app.post("/api/lan/pair")
def lan_pair(body: dict = Body(...)):
    ip = str(body.get("ip") or "").strip()
    port = body.get("port", 8766)
    pin = str(body.get("pin") or "").strip()
    try:
        return lan_sync.pair_with(ip, port, pin)
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/peers/{device_id}/delete")
def lan_peer_delete(device_id: str):
    try:
        return lan_sync.delete_peer(device_id)
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))


@app.post("/api/lan/send/cards")
def lan_send_cards(body: dict = Body(...)):
    peer_ids = body.get("peer_ids")
    if peer_ids is None and body.get("peer_id"):
        peer_ids = [body["peer_id"]]
    if not isinstance(peer_ids, list) or not peer_ids:
        raise HTTPException(400, "укажите хотя бы одно устройство")
    try:
        return lan_sync.send_cards(
            [str(x) for x in peer_ids],
            object_ids=body.get("object_ids"),
            liked_only=bool(body.get("liked_only", False)),
        )
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/send/favorites")
def lan_send_favorites(body: dict = Body(...)):
    peer_ids = body.get("peer_ids")
    if peer_ids is None and body.get("peer_id"):
        peer_ids = [body["peer_id"]]
    if not isinstance(peer_ids, list) or not peer_ids:
        raise HTTPException(400, "укажите хотя бы одно устройство")
    try:
        return lan_sync.send_favorites([str(x) for x in peer_ids])
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/send/db")
def lan_send_db(body: dict = Body(...)):
    peer_id = str(body.get("peer_id") or "").strip()
    if not peer_id and isinstance(body.get("peer_ids"), list) and body["peer_ids"]:
        peer_id = str(body["peer_ids"][0] or "").strip()
    if not peer_id:
        raise HTTPException(400, "укажите устройство-получатель")
    try:
        return lan_sync.send_db(peer_id)
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/inbox/{req_id}/accept")
def lan_inbox_accept(req_id: str, body: dict | None = Body(default=None)):
    try:
        return lan_sync.accept_inbox(req_id, body or {})
    except lan_sync.LanBusyError as e:
        raise HTTPException(409, str(e))
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/inbox/{req_id}/reject")
def lan_inbox_reject(req_id: str):
    try:
        return lan_sync.reject_inbox(req_id)
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))


@app.post("/api/lan/peers/{device_id}/live-config")
def lan_peer_live_config(device_id: str, body: dict = Body(...)):
    try:
        return lan_sync.set_peer_live_cfg(device_id, body)
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))


@app.post("/api/lan/spotlight")
def lan_spotlight(body: dict = Body(...)):
    oid = str(body.get("object_id") or "").strip()
    if not oid:
        raise HTTPException(400, "укажите object_id карточки")
    peer_ids = body.get("peer_ids")
    if peer_ids is None and body.get("peer_id"):
        peer_ids = [body["peer_id"]]
    try:
        return lan_sync.send_spotlight(oid, peer_ids=[str(x) for x in peer_ids] if peer_ids else None)
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/spotlight/import")
def lan_spotlight_import(body: dict = Body(...)):
    dev_id = str(body.get("from_device_id") or "").strip()
    src = str(body.get("source") or "").strip()
    sid = str(body.get("source_id") or "").strip()
    if not dev_id or not src or not sid:
        raise HTTPException(400, "укажите from_device_id, source и source_id")
    try:
        return lan_sync.import_spotlight_card(dev_id, src, sid)
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/live/favorite/apply")
def lan_live_favorite_apply(body: dict = Body(...)):
    keys = body.get("keys") or []
    on = bool(body.get("on", True))
    dev_id = str(body.get("from_device_id") or "").strip()
    try:
        return lan_sync.apply_live_favorite(keys, on, from_device_id=dev_id, import_missing=on)
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/lan/pull/db")
def lan_pull_db(body: dict = Body(...)):
    peer_id = str(body.get("peer_id") or "").strip()
    if not peer_id:
        raise HTTPException(400, "укажите устройство-источник")
    try:
        return lan_sync.pull_db(peer_id)
    except lan_sync.LanBusyError as e:
        raise HTTPException(409, str(e))
    except lan_sync.LanNotFoundError as e:
        raise HTTPException(404, str(e))
    except (ValueError, ConnectionError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/runs")
def runs():
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 50")]
    finally:
        con.close()
    have_log = runlog.existing_ids()
    for r in rows:
        raw_res = r.pop("resume_state", None)
        r["can_continue"] = bool(raw_res) and r["status"] in ("error", "blocked", "partial", "stopped") and not manager.running(r["source"])
        r["has_log"] = r["id"] in have_log
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
