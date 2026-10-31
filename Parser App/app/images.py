"""Локальные копии главных фото: database/images/<сайт>/<id>.<ext>.
Неудачные загрузки повторяются (после сбора, при запуске, по кнопке) — до MAX_TRIES попыток на фото."""
import threading
from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests as cffi

from .paths import IMAGES

MAX_TRIES = 6
EXTS = {"image/webp": "webp", "image/jpeg": "jpg", "image/png": "png", "image/avif": "avif"}
REFERER = {"cian": "https://www.cian.ru/", "yandex": "https://realty.yandex.ru/", "avito": "https://www.avito.ru/"}

_lock = threading.Lock()
_running = False


def local_path(source, source_id):
    folder = IMAGES / source
    for ext in EXTS.values():
        p = folder / f"{source_id}.{ext}"
        if p.exists():
            return p
    return None


def download(source, source_id, url, session=None):
    """Скачивает одно фото. True — файл на месте."""
    if not url or not url.startswith("http"):
        return False
    s = session or cffi.Session(impersonate="chrome")
    try:
        r = s.get(url, headers={"Referer": REFERER.get(source, "")}, timeout=30)
    except Exception:
        return False
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
    if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) < 1500:
        return False
    folder = IMAGES / source
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{source_id}.part"
    tmp.write_bytes(r.content)
    tmp.replace(folder / f"{source_id}.{EXTS.get(ctype, 'jpg')}")
    return True


def missing(con, sources=None):
    q = "SELECT source, source_id, photo FROM listings WHERE photo IS NOT NULL AND COALESCE(img_tries,0) < ?"
    args = [MAX_TRIES]
    if sources:
        q += f" AND source IN ({','.join('?' * len(sources))})"
        args += list(sources)
    return [r for r in con.execute(q, args) if local_path(r["source"], r["source_id"]) is None]


def repair(connect, sources=None, log=None, stop=None, workers=4):
    """Догружает недостающие фото. connect — фабрика соединений с базой (вызов из любого потока)."""
    global _running
    with _lock:
        if _running:
            return None
        _running = True
    try:
        con = connect()
        todo = missing(con, sources)
        no_url = con.execute("SELECT COUNT(*) FROM listings WHERE photo IS NULL AND active=1").fetchone()[0]
        if not todo:
            return {"ok": 0, "failed": 0, "no_url": no_url}
        if log:
            log(f"фото: догружаю {len(todo)}")
        local = threading.local()
        ok = failed = 0

        def job(r):
            if stop is not None and stop.is_set():
                return None
            if not hasattr(local, "s"):
                local.s = cffi.Session(impersonate="chrome")  # своя сессия на поток
            return r, download(r["source"], r["source_id"], r["photo"], local.s)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for res in pool.map(job, todo):
                if res is None:
                    continue
                r, good = res
                if good:
                    ok += 1
                    con.execute("UPDATE listings SET img_tries=0 WHERE source=? AND source_id=?", (r["source"], r["source_id"]))
                else:
                    failed += 1
                    con.execute("UPDATE listings SET img_tries=COALESCE(img_tries,0)+1 WHERE source=? AND source_id=?",
                                (r["source"], r["source_id"]))
        con.commit()
        con.close()
        if log:
            log(f"фото: загружено {ok}, не удалось {failed}" + (f", без адреса фото {no_url}" if no_url else ""))
        return {"ok": ok, "failed": failed, "no_url": no_url}
    finally:
        with _lock:
            _running = False


def repair_async(connect, sources=None, log=None):
    t = threading.Thread(target=repair, args=(connect, sources, log), daemon=True, name="images")
    t.start()
    return t
