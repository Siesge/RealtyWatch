"""Тесты локального дискового кэша и прокси тайлов карты (/tiles/{z}/{x}/{y}.png),
а также диапазонных фильтров «Кухня, м²» (#fKmin—#fKmax) и «Год» (#fYmin—#fYmax)."""
import os
import socket
import struct
import threading
import time
import unittest
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import uvicorn
from fastapi import HTTPException

from app import db, dedupe, lan_sync, paths, purge, server, tiles, transfer
from tests import support
from tests.support import Case, LocalBrowser, con, needs_browser


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def _make_png(seed: int = 1, width: int = 64, height: int = 64) -> bytes:
    """Генерирует валидный PNG (> 200 байт) с корректной сигнатурой и непустым рисунком."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.append((x * 13 + y * 3 + seed * 17) & 0xFF)
            rows.append((y * 19 + x * 5 + seed * 29) & 0xFF)
            rows.append(((x ^ y) * 11 + seed * 43) & 0xFF)
    idat = zlib.compress(bytes(rows), level=6)
    data = tiles.PNG_MAGIC + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    assert tiles.is_valid_png(data), f"синтетический PNG не прошёл проверку размера: {len(data)}"
    return data


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class TileCacheAndProxyTests(Case):
    """Проверка кэша database/tiles/{z}/{x}/{y}.png, резервных серверов, stale-if-error и маршрута FastAPI."""

    def test_cache_miss_downloads_validates_and_writes_atomically(self):
        png = _make_png(10)
        calls: list[str] = []

        def fake_fetch(url: str, timeout: float = tiles.FETCH_TIMEOUT_SEC):
            calls.append(url)
            return 200, png

        with patch.object(tiles, "_fetch_url", side_effect=fake_fetch):
            resp = server.map_tile("13", "4873", "2948")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.media_type, "image/png")
        self.assertEqual(resp.headers.get("cache-control"), "public, max-age=86400")
        self.assertEqual(resp.body, png)
        self.assertEqual(calls, ["https://tile.openstreetmap.org/13/4873/2948.png"])

        saved = tiles.TILES / "13" / "4873" / "2948.png"
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_bytes(), png)
        self.assertEqual(list(tiles.TILES.rglob("*.part")), [])

    def test_cache_hit_makes_zero_network_calls(self):
        png = _make_png(20)
        p = tiles.tile_path(13, 4873, 2948)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png)

        with patch.object(tiles, "_fetch_url", side_effect=AssertionError("сеть не должна вызываться при свежем кэше")):
            self.assertEqual(tiles.get_tile(13, 4873, 2948), png)
            resp = server.map_tile("13", "4873", "2948")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.body, png)

    def test_fallback_to_cartodb_when_primary_blocks_or_returns_html(self):
        png_fb1 = _make_png(31)
        png_fb2 = _make_png(32)
        calls1: list[str] = []

        # 1) Основной OSM возвращает 403 -> переход на резервный CartoDB Voyager
        def fake_403(url: str, timeout: float = tiles.FETCH_TIMEOUT_SEC):
            calls1.append(url)
            if "openstreetmap.org" in url:
                return 403, b"Access blocked"
            return 200, png_fb1

        with patch.object(tiles, "_fetch_url", side_effect=fake_403):
            got1 = tiles.get_tile(13, 4873, 2949)
        self.assertEqual(got1, png_fb1)
        self.assertEqual(len(calls1), 2)
        self.assertIn("tile.openstreetmap.org/13/4873/2949.png", calls1[0])
        self.assertIn(".basemaps.cartocdn.com/rastertiles/voyager/13/4873/2949.png", calls1[1])
        self.assertEqual(tiles.tile_path(13, 4873, 2949).read_bytes(), png_fb1)

        # 2) Основной сервер возвращает HTTP 200 с HTML-заглушкой (> 100 байт) -> отклоняется по PNG-сигнатуре
        html_block = b"<html><body>Rate limit exceeded</body></html>" * 8

        def fake_html(url: str, timeout: float = tiles.FETCH_TIMEOUT_SEC):
            if "openstreetmap.org" in url:
                return 200, html_block
            return 200, png_fb2

        with patch.object(tiles, "_fetch_url", side_effect=fake_html):
            got2 = tiles.get_tile(13, 4874, 2949)
        self.assertEqual(got2, png_fb2)
        self.assertEqual(tiles.tile_path(13, 4874, 2949).read_bytes(), png_fb2)

        # Проверка границ is_valid_png
        self.assertFalse(tiles.is_valid_png(tiles.PNG_MAGIC + b"0" * 50))
        self.assertFalse(tiles.is_valid_png(html_block))

    def test_stale_if_error_serves_expired_tile_without_overwriting_and_miss_502(self):
        old_png = _make_png(41)
        fresh_png = _make_png(42)
        p = tiles.tile_path(14, 9746, 5896)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(old_png)
        expired_ts = time.time() - (35 * 86400)
        os.utime(p, (expired_ts, expired_ts))

        def offline_fetch(url: str, timeout: float = tiles.FETCH_TIMEOUT_SEC):
            if "openstreetmap.org" in url:
                raise OSError("network unreachable")
            return 503, b"Service Unavailable"

        # Офлайн: отдаётся устаревший валидный тайл с диска, сам файл НЕ затирается ошибкой
        with patch.object(tiles, "_fetch_url", side_effect=offline_fetch):
            resp = server.map_tile("14", "9746", "5896")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.body, old_png)
            self.assertEqual(p.read_bytes(), old_png)

            # Полный промах без интернета -> None / HTTP 502
            self.assertIsNone(tiles.get_tile(14, 9746, 5897))
            with self.assertRaises(HTTPException) as ctx502:
                server.map_tile("14", "9746", "5897")
            self.assertEqual(ctx502.exception.status_code, 502)
            self.assertFalse(tiles.tile_path(14, 9746, 5897).exists())

        # Когда сеть появляется снова — просроченный тайл штатно обновляется свежим PNG
        with patch.object(tiles, "_fetch_url", return_value=(200, fresh_png)):
            self.assertEqual(tiles.get_tile(14, 9746, 5896), fresh_png)
            self.assertEqual(p.read_bytes(), fresh_png)

    def test_invalid_coordinates_return_400(self):
        bad_cases = [
            ("25", "0", "0"),
            ("-1", "0", "0"),
            ("5", "-1", "0"),
            ("5", "0", "32"),
            ("5", "999999", "0"),
            ("abc", "0", "0"),
        ]
        for z, x, y in bad_cases:
            with self.subTest(coords=(z, x, y)):
                with self.assertRaises(HTTPException) as ctx:
                    server.map_tile(z, x, y)
                self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(ValueError):
            tiles.validate_coords(True, 0, 0)

    def test_inflight_deduplication_and_http_headers(self):
        png = _make_png(60)
        fetch_count = 0
        count_lock = threading.Lock()
        barrier = threading.Barrier(8)
        results: list[bytes | None] = [None] * 8

        def slow_fetch(url: str, timeout: float = tiles.FETCH_TIMEOUT_SEC):
            nonlocal fetch_count
            with count_lock:
                fetch_count += 1
            time.sleep(0.08)
            return 200, png

        def worker(idx: int):
            barrier.wait(timeout=5.0)
            results[idx] = tiles.get_tile(12, 2436, 1474)

        with patch.object(tiles, "_fetch_url", side_effect=slow_fetch):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

        self.assertEqual(fetch_count, 1, "параллельные запросы одного тайла должны объединяться в 1 сетевой вызов")
        self.assertTrue(all(r == png for r in results))

        # Проверка настоящих HTTP-заголовков клиента (_TILE_FETCH) на локальном 127.0.0.1 сервере
        seen_headers: dict[str, str] = {}

        class TileUpstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen_headers["user-agent"] = self.headers.get("User-Agent", "")
                seen_headers["accept"] = self.headers.get("Accept", "")
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(png)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), TileUpstream)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

        st, body = support._TILE_FETCH(f"http://127.0.0.1:{srv.server_port}/13/4873/2948.png", timeout=3.0)
        self.assertEqual(st, 200)
        self.assertEqual(body, png)
        self.assertEqual(seen_headers.get("user-agent"), tiles.USER_AGENT)
        self.assertEqual(seen_headers.get("accept"), tiles.ACCEPT_HEADER)

    def test_tiles_isolated_from_backup_and_purge(self):
        png = _make_png(70)
        p = tiles.tile_path(13, 4873, 2948)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png)

        c = con()
        db.upsert(c, {
            "source": "cian", "source_id": "701", "url": "https://cian.test/701",
            "title": "1-к., 42 м²", "price": 9_500_000, "rooms": 1, "area": 42.0,
            "floor": 3, "floors": 9, "address": "Ялта, ул. Садовая, 7",
            "lat": 44.4838, "lon": 34.1433, "zone": "A",
        })
        c.commit()
        c.close()

        zip_path = self.dir / "backup.zip"
        transfer.export_zip(zip_path, with_images=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
        self.assertFalse(any("tiles" in n for n in names), f"кэш тайлов не должен попадать в архив БД: {names}")

        purge.clear_all()
        self.assertTrue(p.is_file(), "полная очистка базы не должна удалять кэш тайлов карты")
        self.assertEqual(p.read_bytes(), png)


class KitchenAndBuildYearFilterTests(Case):
    """Статическая и браузерная проверка фильтров «Кухня, м²» (#fKmin—#fKmax), «Год» (#fYmin—#fYmax) и прокси карты."""

    def test_static_wiring_of_tile_proxy_and_range_filters(self):
        html = (paths.WEB / "index.html").read_text(encoding="utf-8")
        js = (paths.WEB / "app.js").read_text(encoding="utf-8")
        share_py = (paths.ROOT / "app" / "share.py").read_text(encoding="utf-8")

        # Порядок в .tb-row: после #fAmin идут #fKmin/#fKmax и #fYmin/#fYmax
        idx_amin = html.index('id="fAmin"')
        idx_kmin = html.index('id="fKmin"')
        idx_kmax = html.index('id="fKmax"')
        idx_ymin = html.index('id="fYmin"')
        idx_ymax = html.index('id="fYmax"')
        idx_reno = html.index('id="fRenoBox"')
        self.assertTrue(idx_amin < idx_kmin < idx_kmax < idx_ymin < idx_ymax < idx_reno)

        # makeMap использует локальный /tiles/{z}/{x}/{y}.png, прямых ссылок на tile.openstreetmap.org в app.js нет
        self.assertIn('L.tileLayer("/tiles/{z}/{x}/{y}.png"', js)
        self.assertNotIn("tile.openstreetmap.org", js)
        self.assertNotIn("/tiles/", share_py)

        for token in ("kitchen_area:", "build_year:", "kmin:", "kmax:", "ymin:", "ymax:", "Number.isFinite(f.kmin)"):
            self.assertIn(token, js)

    @needs_browser
    def test_browser_range_filters_presets_reset_and_tile_proxy(self):
        lan_sync.stop()
        c = con()
        ts_run = "2026-11-20T10:00:00"
        ts_ev = "2026-11-20T10:02:00"
        rid = c.execute(
            "INSERT INTO runs(source, mode, started, finished, status) VALUES('cian','http',?,?,'done')",
            (ts_run, "2026-11-20T10:05:00"),
        ).lastrowid

        # 6 тестовых объектов в зоне A:
        # 101: кухня 8.0, 1985 г.
        # 102: кухня 12.5, 2016 г.
        # 103: кухня 18.0, 2022 г.
        # 104: кухня None, год None
        # 105 + avito:205 (ручное слияние, у primary поля пустые -> берутся 14.0 м² и 2018 г. со второго объявления)
        # 106 + avito:206 (ручное слияние, у primary 9.5 м² и 1998 г., у второго 22.0 м² и 2024 г. -> приоритет primary)
        items = [
            {"source": "cian", "source_id": "101", "url": "https://cian.test/101", "title": "2-к, 48 м²",
             "price": 9_000_000, "rooms": 2, "area": 48.0, "kitchen_area": 8.0, "build_year": 1985,
             "floor": 2, "floors": 5, "address": "Ялта, ул. Старая, 1", "lat": 44.4840, "lon": 34.1420, "zone": "A"},
            {"source": "cian", "source_id": "102", "url": "https://cian.test/102", "title": "2-к, 60 м²",
             "price": 12_000_000, "rooms": 2, "area": 60.0, "kitchen_area": 12.5, "build_year": 2016,
             "floor": 4, "floors": 9, "address": "Ялта, ул. Средняя, 2", "lat": 44.4850, "lon": 34.1440, "zone": "A"},
            {"source": "cian", "source_id": "103", "url": "https://cian.test/103", "title": "2-к, 72 м²",
             "price": 15_000_000, "rooms": 2, "area": 72.0, "kitchen_area": 18.0, "build_year": 2022,
             "floor": 6, "floors": 12, "address": "Ялта, ул. Новая, 3", "lat": 44.4860, "lon": 34.1460, "zone": "A"},
            {"source": "cian", "source_id": "104", "url": "https://cian.test/104", "title": "2-к, 54 м²",
             "price": 10_500_000, "rooms": 2, "area": 54.0, "kitchen_area": None, "build_year": None,
             "floor": 3, "floors": 9, "address": "Ялта, ул. БезДанных, 4", "lat": 44.4870, "lon": 34.1480, "zone": "A"},
            {"source": "cian", "source_id": "105", "url": "https://cian.test/105", "title": "2-к, 63 м²",
             "price": 13_000_000, "rooms": 2, "area": 63.0, "kitchen_area": None, "build_year": None,
             "floor": 5, "floors": 10, "address": "Ялта, ул. ВторичнаяДоп, 5", "lat": 44.4880, "lon": 34.1500, "zone": "A"},
            {"source": "avito", "source_id": "205", "url": "https://avito.test/205", "title": "2-к, 66 м²",
             "price": 13_100_000, "rooms": 2, "area": 66.0, "kitchen_area": 14.0, "build_year": 2018,
             "floor": 6, "floors": 10, "address": "Ялта, ул. ВторичнаяДоп, 5А", "lat": 44.4882, "lon": 34.1502, "zone": "A"},
            {"source": "cian", "source_id": "106", "url": "https://cian.test/106", "title": "2-к, 51 м²",
             "price": 11_000_000, "rooms": 2, "area": 51.0, "kitchen_area": 9.5, "build_year": 1998,
             "floor": 3, "floors": 8, "address": "Ялта, ул. Приоритетная, 6", "lat": 44.4890, "lon": 34.1520, "zone": "A"},
            {"source": "avito", "source_id": "206", "url": "https://avito.test/206", "title": "2-к, 56 м²",
             "price": 11_200_000, "rooms": 2, "area": 56.0, "kitchen_area": 22.0, "build_year": 2024,
             "floor": 4, "floors": 8, "address": "Ялта, ул. Приоритетная, 6Б", "lat": 44.4892, "lon": 34.1522, "zone": "A"},
        ]
        for it in items:
            db.upsert(c, it, baseline=1)
        for sid, old_p, new_p in (("101", 9_500_000, 9_000_000), ("102", 12_500_000, 12_000_000), ("104", 11_000_000, 10_500_000)):
            db.event(c, "cian", sid, "price", old=old_p, new=new_p, ts=ts_ev, run_id=rid, baseline=0)
        c.commit()
        dedupe.rebuild(c)
        oid_105 = c.execute("SELECT object_id FROM listings WHERE source='cian' AND source_id='105'").fetchone()[0]
        oid_205 = c.execute("SELECT object_id FROM listings WHERE source='avito' AND source_id='205'").fetchone()[0]
        oid_106 = c.execute("SELECT object_id FROM listings WHERE source='cian' AND source_id='106'").fetchone()[0]
        oid_206 = c.execute("SELECT object_id FROM listings WHERE source='avito' AND source_id='206'").fetchone()[0]
        c.close()

        if oid_105 != oid_205:
            server.objects_merge({"primary_id": oid_105, "secondary_id": oid_205})
        if oid_106 != oid_206:
            server.objects_merge({"primary_id": oid_106, "secondary_id": oid_206})

        tile_png = _make_png(99)
        port = _free_port()
        uv_cfg = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error")
        uv_srv = uvicorn.Server(uv_cfg)

        patcher = patch.object(tiles, "_fetch_url", return_value=(200, tile_png))
        patcher.start()
        self.addCleanup(patcher.stop)

        srv_thread = threading.Thread(target=uv_srv.run, daemon=True)
        srv_thread.start()

        def stop_uvicorn():
            uv_srv.should_exit = True
            srv_thread.join(timeout=5.0)

        self.addCleanup(stop_uvicorn)

        deadline = time.time() + 8.0
        while not uv_srv.started and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(uv_srv.started, "локальный uvicorn-сервер не запустился вовремя")

        b = LocalBrowser("ui-tiles-filters", lambda h, u: False, lambda m: None, None, window="hidden")
        self.addCleanup(b.close)
        b.start()
        page = b.page

        js_errors: list[str] = []
        tile_statuses: list[int] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        page.on("console", lambda msg: js_errors.append(msg.text) if msg.type == "error" else None)
        page.on("response", lambda r: tile_statuses.append(r.status) if "/tiles/" in r.url else None)

        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        page.wait_for_function("() => document.getElementById('zc-A') !== null", timeout=10000)

        page.click('.nav button[data-view="cards"]')
        page.wait_for_selector("#cardsList .card", timeout=10000)
        page.click("#btnReset")
        page.wait_for_function("() => document.querySelectorAll('#cardsList .card').length === 6", timeout=5000)

        def visible_addrs() -> set[str]:
            return set(page.evaluate(
                "() => [...document.querySelectorAll('#cardsList .card .addr span')].map(e => e.textContent.trim())"
            ))

        # 1) По умолчанию (поля пустые) показываются все 6 объектов, включая БезДанных (null/null)
        all_addrs = visible_addrs()
        self.assertEqual(len(all_addrs), 6, all_addrs)
        self.assertTrue(any("БезДанных" in a for a in all_addrs))

        def apply_bounds(kmin="", kmax="", ymin="", ymax="", expected_count=None):
            page.fill("#fKmin", kmin)
            page.fill("#fKmax", kmax)
            page.fill("#fYmin", ymin)
            page.fill("#fYmax", ymax)
            if expected_count is not None:
                page.wait_for_function(
                    "(n) => document.querySelectorAll('#cardsList .card').length === n",
                    arg=expected_count,
                    timeout=5000,
                )
            else:
                page.wait_for_timeout(200)

        # 2) Диапазоны «Кухня, м²»: только «от», только «до», «от — до» (null всегда исключается при заданной границе)
        apply_bounds(kmin="10", expected_count=3)
        addrs_k_from = visible_addrs()
        self.assertEqual(len(addrs_k_from), 3, addrs_k_from)  # 12.5, 14.0 (из вторичного объявления), 18.0
        self.assertFalse(any("БезДанных" in a or "Старая" in a or "Приоритетная" in a for a in addrs_k_from))

        apply_bounds(kmax="10", expected_count=2)
        addrs_k_to = visible_addrs()
        self.assertEqual(len(addrs_k_to), 2, addrs_k_to)  # 8.0 и 9.5 (приоритет primary над 22.0)
        self.assertTrue(any("Старая" in a for a in addrs_k_to))
        self.assertTrue(any("Приоритетная" in a for a in addrs_k_to))

        apply_bounds(kmin="11", kmax="15", expected_count=2)
        addrs_k_range = visible_addrs()
        self.assertEqual(len(addrs_k_range), 2, addrs_k_range)  # 12.5 и 14.0

        # 3) Диапазоны «Год»: только «от», только «до», «от — до»
        apply_bounds(ymin="2015", expected_count=3)
        addrs_y_from = visible_addrs()
        self.assertEqual(len(addrs_y_from), 3, addrs_y_from)  # 2016, 2018, 2022
        self.assertFalse(any("БезДанных" in a for a in addrs_y_from))

        apply_bounds(ymax="2000", expected_count=2)
        addrs_y_to = visible_addrs()
        self.assertEqual(len(addrs_y_to), 2, addrs_y_to)  # 1985 и 1998 (приоритет primary над 2024)

        apply_bounds(ymin="2015", ymax="2019", expected_count=2)
        addrs_y_range = visible_addrs()
        self.assertEqual(len(addrs_y_range), 2, addrs_y_range)  # 2016 и 2018

        page.click("#btnPresetNew")
        page.wait_for_selector("#presetName", timeout=3000)
        self.assertIn("2015–2019 г.", page.input_value("#presetName"))
        page.click("#presetCancel")

        # 4) Совместный фильтр во вкладках «Таблица», «Карта» и «Изменения»
        apply_bounds(kmin="11", kmax="15", ymin="2015", ymax="2019", expected_count=2)

        # Проверка отсутствия обрезки 4-значного года и дробной кухни в полях ввода
        metrics = page.evaluate(
            """() => ["fKmin", "fKmax", "fYmin", "fYmax"].map(id => {
                const el = document.getElementById(id);
                return { id, w: el.getBoundingClientRect().width, cw: el.clientWidth, sw: el.scrollWidth };
            })"""
        )
        for m in metrics:
            self.assertGreaterEqual(m["w"], 50, m)
            self.assertLessEqual(m["sw"], m["cw"] + 2, f"значение обрезается в поле {m}")

        # Проверка имени пресета, сохранения и повторного применения через UI наборов
        page.click("#btnPresetNew")
        page.wait_for_selector("#presetName", timeout=3000)
        suggested = page.input_value("#presetName")
        self.assertIn("кухня 11–15 м²", suggested)
        page.click("#presetSave")
        page.click("#btnReset")
        page.wait_for_function("() => document.querySelectorAll('#cardsList .card').length === 6", timeout=5000)
        page.click("#presetBar [data-apply='0']")
        page.wait_for_function("() => document.querySelectorAll('#cardsList .card').length === 2", timeout=5000)
        restored = [page.input_value("#fKmin"), page.input_value("#fKmax"), page.input_value("#fYmin"), page.input_value("#fYmax")]
        self.assertEqual(restored, ["11", "15", "2015", "2019"])

        # Вкладка «Таблица»
        page.click('.nav button[data-view="table"]')
        page.wait_for_function("() => document.querySelectorAll('#view-table tbody tr').length === 2", timeout=5000)

        # Вкладка «Карта» + реальная загрузка тайлов через /tiles/{z}/{x}/{y}.png
        page.click('.nav button[data-view="map"]')
        page.wait_for_function(
            "() => document.querySelectorAll('#map path.leaflet-interactive:not([stroke-dasharray])').length === 2",
            timeout=5000,
        )
        deadline_tiles = time.time() + 5.0
        while not tile_statuses and time.time() < deadline_tiles:
            page.wait_for_timeout(100)
        self.assertTrue(tile_statuses, "Leaflet должен запрашивать тайлы через локальный маршрут /tiles/...")
        self.assertTrue(all(st == 200 for st in tile_statuses), tile_statuses)
        self.assertTrue(any(tiles.TILES.rglob("*.png")), "загруженные картой тайлы должны сохраняться в database/tiles/")

        # Вкладка «Изменения» (из 101, 102, 104 под фильтр 11..15 м² и 2015..2019 г. подходит только 102)
        page.click('.nav button[data-view="changes"]')
        page.wait_for_function("() => document.querySelectorAll('#view-changes .ch-bar').length === 1", timeout=5000)
        ch_count = page.evaluate("() => document.querySelectorAll('#view-changes .ch-grid > *').length")
        self.assertEqual(ch_count, 1)

        # 5) Кнопка «Сбросить» (#btnReset) очищает все 4 поля и возвращает все 6 карточек (включая null/null)
        page.click('.nav button[data-view="cards"]')
        page.click("#btnReset")
        page.wait_for_function("() => document.querySelectorAll('#cardsList .card').length === 6", timeout=5000)
        after_vals = [page.input_value("#fKmin"), page.input_value("#fKmax"), page.input_value("#fYmin"), page.input_value("#fYmax")]
        self.assertEqual(after_vals, ["", "", "", ""])
        self.assertEqual(js_errors, [], f"ошибки JS в браузере: {js_errors}")


if __name__ == "__main__":
    unittest.main()
