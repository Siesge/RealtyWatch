"""Общая подготовка тестов: всё во временной папке — база, настройки, журналы, расписание, профили.
Реальная база, настройки, профили сайтов и logs/app.log не затрагиваются. Данные синтетические.
Тесты никогда не обращаются к настоящим сайтам: HTTP разрешён только к 127.0.0.1, браузер с реальным
профилем не запускается (тест капчи использует свой браузер без окна и локальный сайт-имитацию)."""
import json
import logging
import os
import random
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Журнал тестов не пишется в logs/app.log: обработчик ставится до импорта runner (setup_logging его не трогает)
logging.getLogger().addHandler(logging.NullHandler())

from app import alerts, db, human, images, net, paths, runlog, settings, tiles, transfer, updater  # noqa: E402
from app import runner  # noqa: E402

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(paths.BROWSERS))
os.environ.setdefault("RW_NO_UPDATER", "1")

TMP_ROOT = Path(tempfile.mkdtemp(prefix="rw-tests-"))
_REQUEST = net.Http.request
_TILE_FETCH = tiles._fetch_url
_UPDATER_FETCH = updater._fetch_bytes


def _local_only(self, method, url, **kw):
    if not url.startswith("http://127.0.0.1"):
        raise AssertionError(f"тест обратился к настоящему сайту: {url} — подмените загрузку страницы")
    return _REQUEST(self, method, url, **kw)


def _local_tile_only(url, timeout=tiles.FETCH_TIMEOUT_SEC):
    if not url.startswith("http://127.0.0.1"):
        raise OSError(f"тест обратился к внешнему серверу тайлов: {url}")
    return _TILE_FETCH(url, timeout=timeout)


def _local_updater_only(url, accept="*/*", timeout=updater.HTTP_TIMEOUT_SEC, max_bytes=updater.MAX_ZIP_BYTES):
    if not url.startswith("http://127.0.0.1"):
        raise OSError(f"тест обратился к внешнему серверу обновлений: {url}")
    return _UPDATER_FETCH(url, accept=accept, timeout=timeout, max_bytes=max_bytes)


def _no_real_browser(self):
    raise AssertionError("тест запустил браузер с настоящим профилем — подмените загрузку страницы")


net.Http.request = _local_only
tiles._fetch_url = _local_tile_only
updater._fetch_bytes = _local_updater_only
net.Browser.start = _no_real_browser


class LocalBrowser(net.Browser):
    """Настоящий Chromium без окна, со временным профилем — только для локального сайта-имитации:
    любой запрос не к 127.0.0.1 обрывается, так что тест не может дойти до настоящего сайта."""

    def start(self):
        if self.ctx:
            return
        from patchright.sync_api import sync_playwright
        self._profile = tempfile.mkdtemp(dir=TMP_ROOT)
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(self._profile, headless=True, viewport={"width": 1280, "height": 900})
        self.ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith("http://127.0.0.1") else route.abort())
        self.ctx.on("response", self._on_response)
        self.ctx.on("request", self._on_request)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.mouse = human.Mouse(self.page, self.stop, net.Stopped)

    def _place_window(self, *a):
        pass

    def close(self):
        super().close()
        shutil.rmtree(getattr(self, "_profile", ""), ignore_errors=True)


def has_browser():
    return paths.BROWSERS.exists() and any(paths.BROWSERS.glob("chromium-*"))


def fast_timing(test):
    """Паузы и движения мыши в сотни раз короче — тесты с браузером идут секунды, а не минуты."""
    orig_pause, orig_scale = net.pause, human.TIME_SCALE

    def quick(delay, stop=None):
        if stop is not None and stop.is_set():
            raise net.Stopped()
        time.sleep(min(0.05, random.uniform(*delay) * 0.02))
    net.pause, human.TIME_SCALE = quick, 0.03
    test.addCleanup(setattr, net, "pause", orig_pause)
    test.addCleanup(setattr, human, "TIME_SCALE", orig_scale)


def isolate(test):
    """Свежая база, настройки, фото, резервные копии, журналы и профили — во временной папке на каждый тест.
    Настоящие database/, data/, logs/ и profiles/ тесты не видят: очистка базы в тестах удаляет только временные файлы."""
    d = Path(tempfile.mkdtemp(dir=TMP_ROOT))
    db.DB_FILE = d / "realty.db"
    db.reset()
    settings.SETTINGS_FILE = d / "settings.json"
    net.PROFILES = d / "profiles"
    runlog.RUNS_DIR, runlog.SNAP_DIR = d / "runs", d / "snapshots"
    images.IMAGES = transfer.IMAGES = d / "images"
    tiles.TILES = d / "tiles"
    transfer.BACKUPS, transfer.DB_DIR = d / "backups", d
    alerts.FILE = d / "sound.json"
    updater.DATA_DIR = d / "data"
    updater.APP_ROOT = d / "app_root"
    (updater.APP_ROOT / "app").mkdir(parents=True, exist_ok=True)
    for p in (db.DB_FILE, settings.SETTINGS_FILE, images.IMAGES, tiles.TILES, transfer.BACKUPS, alerts.FILE, runlog.RUNS_DIR, updater.DATA_DIR, updater.APP_ROOT):
        assert TMP_ROOT in Path(p).parents, f"тест работает вне временной папки: {p}"
    images.repair = lambda *a, **k: None
    runner.pause = lambda *a, **k: None
    test.addCleanup(shutil.rmtree, d, ignore_errors=True)
    return d


def listings(source, n=120, seed=1):
    """Синтетические объявления в красном круге Ялты: разные квартиры, без дублей между собой."""
    rnd = random.Random(f"{source}{seed}")
    out = []
    for i in range(n):
        rooms, area = 1 + i % 3, 40 + (i * 7) % 45
        sid = str(10_000_000 + i) if source != "avito" else str(4_000_000_000 + i)
        out.append({
            "source_id": sid, "rooms": rooms, "area": float(area), "floor": 1 + i % 9, "floors": 9 + i % 4,
            "price": 8_000_000 + (i * 37_000) % 5_000_000, "lat": 44.4835 + rnd.uniform(-.004, .004),
            "lon": 34.1430 + rnd.uniform(-.005, .005), "address": f"Ялта, ул. Тестовая, {i + 1}",
            "description": "Квартира с евроремонтом", "url": f"https://example.test/{source}/{sid}",
            "title": f"{rooms}-к. квартира, {area} м², {1 + i % 9}/{9 + i % 4} эт.",
        })
    return out


def cian_offer(r):
    return {"cianId": int(r["source_id"]), "bargainTerms": {"priceRur": r["price"]}, "totalArea": r["area"],
            "roomsCount": r["rooms"], "floorNumber": r["floor"], "building": {"floorsCount": r["floors"]},
            "geo": {"coordinates": {"lat": r["lat"], "lng": r["lon"]}, "userInput": r["address"]},
            "fullUrl": r["url"], "description": r["description"], "decoration": "fine"}


def yandex_offer(r):
    return {"offerId": r["source_id"], "offerType": "SELL", "price": {"value": r["price"]}, "area": {"value": r["area"]},
            "roomsTotal": r["rooms"], "floorsOffered": [r["floor"]], "floorsTotal": r["floors"],
            "location": {"point": {"latitude": r["lat"], "longitude": r["lon"]}, "address": r["address"]},
            "apartment": {"renovation": "EURO"}, "description": r["description"], "shareUrl": r["url"]}


def avito_card(r):
    return (f'<div data-marker="item" data-item-id="{r["source_id"]}"><a data-marker="item-title" href="/yalta/kvartiry/x_{r["source_id"]}">'
            f'{r["title"]}</a><meta itemprop="price" content="{r["price"]}"><p>{r["description"]}</p>'
            f'<span data-marker="item-address">{r["address"]}</span></div>')


def avito_page(rows, count, ld=False, drawn=None, carousel=()):
    """Страница выдачи Авито. ld — данные для поисковиков со всеми объявлениями страницы; drawn — сколько
    карточек браузер успел дорисовать (None — все); carousel — карточки рекомендаций вне результатов поиска."""
    shown = rows if drawn is None else rows[:drawn]
    cards = "".join(avito_card(r) for r in shown)
    if carousel:
        cards += '<div data-marker="itemsCarousel/carousel">' + "".join(avito_card(r) for r in carousel) + "</div>"
    head = f'<span data-marker="page-title/count">{count}</span>' if count is not None else ""
    if ld:
        offers = [{"@type": "Offer", "price": r["price"], "priceCurrency": "RUB", "name": r["title"],
                   "url": f"https://www.avito.ru/yalta/kvartiry/x_{r['source_id']}", "image": f"https://img.test/{r['source_id']}.jpg"}
                  for r in rows]
        head += ('<script type="application/ld+json">' + json.dumps({"@context": "https://schema.org", "@graph": [
            {"@type": "Product", "name": "Квартиры", "offers": {"@type": "AggregateOffer", "offers": offers}}]}, ensure_ascii=False) + "</script>")
    return f'<html><body>{head}<div data-marker="catalog-serp">{cards}</div></body></html>'


def run(source, cls):
    """Синхронный прогон через настоящий RunManager. Возвращает журнал (список строк)."""
    cls.wait = lambda self: None
    cls.close = lambda self: None
    runner.SCRAPERS[source] = cls
    m = runner.RunManager()
    m.stops[source] = threading.Event()
    m._run(source)
    return [j["msg"] for j in m.journal]


def con():
    return db.connect()


def last_run():
    c = db.connect()
    r = dict(c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone())
    c.close()
    return r


def counts(source):
    c = db.connect()
    r = c.execute("SELECT COUNT(*), SUM(active=0), COALESCE(SUM(missed), 0) FROM listings WHERE source=?", (source,)).fetchone()
    c.close()
    return {"total": r[0], "removed": r[1] or 0, "missed": r[2]}


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = isolate(self)
