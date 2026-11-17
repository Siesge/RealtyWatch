"""ЦИАН: вид ремонта узнаётся отдельными выдачами по каждому виду (в самой выдаче поля о ремонте нет),
основная выдача идёт без фильтра по ремонту и с сортировкой по цене. Данные синтетические, к сайту
не обращаемся."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
import unittest

from app import db, net, rules, runlog, runner, settings
from app.net import Blocked, Browser
import app.scrapers.cian as cian_mod
from app.scrapers.cian import Cian, SORT, parse_state
from tests.support import Case, LocalBrowser, cian_offer, fast_timing, isolate, last_run, listings, needs_browser, run

# Описание объявления 333647311 по сути: ремонт сделан только в санузлах, а в «О квартире» — «без ремонта»
BATHROOMS_ONLY = "Вид неописуемый. Внутри сделан хороший ремонт в санузлах (ванная и туалет полностью готовы)."


class CianRepair(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.log = []
        self.c = Cian(settings.load(), self.log.append, None, lambda **k: None)
        self.c.wait = lambda *a, **k: None
        self.f = settings.load()["filters"]
        self.n = 1000

    def offer(self, decoration=None, description="Квартира в центре"):
        o = cian_offer(listings("cian", 1)[0])
        self.n += 1
        o["cianId"] = self.n
        o["description"] = description
        o["decoration"] = decoration
        return o

    def norm(self, o, repair=None):
        self.c.repairs = {str(o["cianId"]): repair} if repair else {}
        return self.c._normalize(o)

    def test_repair_wins_over_description(self):
        """«Хороший ремонт в санузлах» в описании не делает квартиру отремонтированной, если ЦИАН отдал её
        в выдаче «без ремонта»: такая квартира отсеивается."""
        item = self.norm(self.offer(description=BATHROOMS_ONLY), 1)
        self.assertEqual(item["renovation"], "none")
        self.assertEqual(item["renovation_raw"], "без ремонта", "на карточке видно, что указано на сайте")
        self.assertIn("без ремонта", rules.evaluate(item, self.f))

    def test_repair_codes(self):
        for code, reno in ((1, "none"), (2, "cosmetic"), (3, "euro"), (4, "design")):
            with self.subTest(code=code):
                self.assertEqual(self.norm(self.offer(description="требует ремонта"), code)["renovation"], reno,
                                 "вид ремонта с сайта важнее описания и в обратную сторону")

    def test_without_repair_old_rules(self):
        """Вид ремонта не указан — как раньше: отделка новостройки, затем описание."""
        self.assertEqual(self.norm(self.offer(decoration="fine"))["renovation"], "finish")
        self.assertEqual(self.norm(self.offer(decoration="fine", description="Квартира требует ремонта"))["renovation"], "none")
        self.assertEqual(self.norm(self.offer(description="Сделан евроремонт"))["renovation"], "euro")

    def test_no_guess_without_data(self):
        """Ни вида ремонта, ни описания — ремонт не указан, а не «с ремонтом»."""
        self.assertNotEqual(self.f.get("renovation"), "off", "фильтр по ремонту включён — ровно тот случай")
        item = self.norm(self.offer())
        self.assertIsNone(item["renovation"])
        self.assertIsNone(item["renovation_raw"])

    def test_queries(self):
        """Основная выдача — без фильтра по ремонту; у всех выдач сортировка по цене (без неё ЦИАН
        перемешивает объявления между страницами)."""
        q = self.c._json_query(1)
        self.assertNotIn("repair", q)
        self.assertEqual(q["sort"]["value"], SORT)
        self.assertEqual(self.c._json_query(2, 1)["repair"]["value"], [1])
        self.assertNotIn("repair", self.c._page_url(2))
        self.assertIn("sort=" + SORT, self.c._page_url(2))
        self.assertIn("repair%5B0%5D=4", self.c._page_url(1, 4))

    def run_listings(self, by_repair, main):
        """by_repair: код → объявления выдачи по этому виду ремонта; main — основная выдача."""
        calls = []

        def load(page, repair=None):
            calls.append((page, repair))
            offers = by_repair.get(repair, []) if repair else main
            return offers, len(offers)
        self.c._load = load
        return list(self.c.listings()), calls

    def test_listings_marks_repair_from_its_query(self):
        none, cosm, plain = self.offer(description=BATHROOMS_ONLY), self.offer(), self.offer(description="Сделан евроремонт")
        items, calls = self.run_listings({1: [none], 2: [cosm]}, [none, cosm, plain])
        self.assertEqual([c[1] for c in calls], [1, 2, 3, 4, None], "сначала выдачи по ремонту, затем основная")
        reno = {i["source_id"]: i["renovation"] for i in items}
        self.assertEqual(reno, {str(none["cianId"]): "none", str(cosm["cianId"]): "cosmetic",
                                str(plain["cianId"]): "euro"})
        self.assertIn("вид ремонта: без ремонта 1, косметический 1, не указан 1", self.log)

    def test_offer_only_in_repair_query_is_kept(self):
        """Объявление пришло в выдаче по ремонту, а основной не встретилось — оно всё равно в поиске."""
        a, b = self.offer(), self.offer()
        items, _ = self.run_listings({3: [b]}, [a])
        self.assertEqual(sorted(i["source_id"] for i in items), sorted([str(a["cianId"]), str(b["cianId"])]))
        self.assertEqual(len(items), 2, "без повторов")

    def test_no_duplicates_from_main(self):
        a = self.offer()
        items, _ = self.run_listings({}, [a, a])
        self.assertEqual(len(items), 1)

    def test_one_progress_bar_for_all_queries(self):
        """Полоса прогресса одна на весь сбор: сначала первые страницы всех выдач, потом страницы
        считаются подряд от 0 до общего числа — без нового отсчёта на каждую выдачу."""
        seen = []
        self.c.progress = lambda **k: seen.append((k["page"], k["pages"]))
        many = [self.offer() for _ in range(60)]  # основная: 3 страницы
        calls = []

        def load(page, repair=None):
            calls.append((page, repair))
            rows = {1: many[:30]}.get(repair, []) if repair else many  # «без ремонта»: 2 страницы
            return rows[(page - 1) * 28: page * 28], len(rows)
        self.c._load = load
        list(self.c.listings())
        self.assertEqual(calls[:5], [(1, 1), (1, 2), (1, 3), (1, 4), (1, None)], "сначала первые страницы всех выдач")
        self.assertEqual({p for _, p in seen}, {2 + 1 + 1 + 1 + 3}, "всего страниц — одно число на весь сбор")
        self.assertEqual([d for d, _ in seen], list(range(0, 9)), "прогресс идёт подряд, без сброса")
        self.assertEqual(len(calls), 8, "первая страница каждой выдачи не запрашивается второй раз")

    def test_warns_when_repair_queries_empty(self):
        self.run_listings({}, [self.offer(), self.offer()])
        warn = [m for m in self.log if m.startswith("⚠ вид ремонта ЦИАН не определён ни у одного из 2 объявлений")]
        self.assertEqual(len(warn), 1, self.log)

    def test_incremental_skips_repair_queries_for_known_offers_and_updates_new(self):
        """Если виды ремонта недавно собирались: известные объявления не перезапрашиваются (идёт только
        основная выдача), а для нового дешёвого объявления выдачи по ремонту останавливаются сразу, как оно найдено."""
        a = self.offer()
        b = self.offer()
        items1, calls1 = self.run_listings({2: [a]}, [a, b])
        self.assertEqual([c[1] for c in calls1], [1, 2, 3, 4, None])
        con = db.connect()
        for it in items1:
            db.upsert(con, it)
        con.commit()
        con.close()

        # Повторный запуск сразу после первого: оба объявления уже в базе — идём только в основную выдачу
        c2 = Cian(settings.load(), self.log.append, None, lambda **k: None)
        c2.wait = lambda *a, **k: None
        calls2 = []

        def load2(page, repair=None):
            calls2.append((page, repair))
            return ([], 0) if repair else ([a, b], 2)

        c2._load = load2
        items2 = list(c2.listings())
        self.assertEqual(calls2, [(1, None)], "выдачи по виду ремонта пропущены")
        self.assertEqual({i["source_id"]: i["renovation"] for i in items2}[str(a["cianId"])], "cosmetic")

        # Появилось новое дешёвое объявление с кодом ремонта 1 («без ремонта»): проверяется 1-я выдача и стоп
        cheap = self.offer(description=BATHROOMS_ONLY)
        cheap["bargainTerms"] = {"priceRur": 7_000_000}
        calls3 = []

        def load3(page, repair=None):
            calls3.append((page, repair))
            return ([cheap], 1) if repair == 1 else ([], 0) if repair else ([a, b, cheap], 3)

        c3 = Cian(settings.load(), self.log.append, None, lambda **k: None)
        c3.wait = lambda *a, **k: None
        c3._load = load3
        items3 = list(c3.listings())
        self.assertEqual(calls3, [(1, None), (1, 1)], "коды 2..4 не запрашивались — все новые уже нашлись")
        self.assertEqual({i["source_id"]: i["renovation"] for i in items3}[str(cheap["cianId"])], "none")

    def test_expired_repair_ts_runs_full_repair_scan(self):
        a = self.offer()
        items, _ = self.run_listings({3: [a]}, [a])
        con = db.connect()
        db.upsert(con, items[0])
        con.execute("INSERT OR REPLACE INTO meta VALUES('cian_repairs_ts', ?)", (str(time.time() - cian_mod.REPAIR_TTL - 60),))
        con.commit()
        con.close()
        calls = []
        c = Cian(settings.load(), self.log.append, None, lambda **k: None)
        c.wait = lambda *a, **k: None

        def load_exp(page, repair=None):
            calls.append((page, repair))
            return ([a], 1) if repair in (3, None) else ([], 0)

        c._load = load_exp
        list(c.listings())
        self.assertEqual([r for _, r in calls], [1, 2, 3, 4, None])
        con = db.connect()
        ts_val = float(con.execute("SELECT value FROM meta WHERE key='cian_repairs_ts'").fetchone()[0])
        con.close()
        self.assertLess(time.time() - ts_val, 60)

    def test_cached_http_block_skips_http_probes_and_auto_drops_block_snapshots(self):
        """В режиме auto: первая HTTP-блокировка пишет кэш на 12 ч и не плодит снимки; следующий прогон сразу идёт в браузер."""
        cfg = settings.load()
        cfg["sites"]["cian"]["mode"] = "auto"
        settings.save(cfg)

        c1 = Cian(cfg, self.log.append, None, lambda **k: None)
        snaps = []
        c1.runlog = SimpleNamespace(request=lambda *a: None, line=lambda *a: None,
                                  snapshot=lambda body, reason, ext="html": snaps.append((reason, ext)))
        self.assertFalse(c1.http.save_block_snapshots, "в auto снимок HTTP-заглушки не нужен")
        c1.http.post = lambda *a, **k: (_ for _ in ()).throw(Blocked("HTTP 200 api-captcha"))
        c1.http.get = lambda *a, **k: (_ for _ in ()).throw(Blocked("HTTP 200 servicepipe"))
        ssr = self._ssr_html_helper()
        fake_b = SimpleNamespace(goto=lambda u: ssr, evaluate=lambda js: {"offers": [self.offer()], "total": 1, "aggregated": 1}, raw_html=lambda: ssr)
        c1.get_browser = lambda: fake_b
        self.assertEqual(c1._load(1)[1], 1)
        self.assertEqual(snaps, [], "в auto рутинная HTTP-блокировка не сохраняется в snapshots")
        self.assertTrue(c1._http_state_file().exists())

        c2 = Cian(cfg, self.log.append, None, lambda **k: None)
        c2.wait = lambda *a, **k: None
        c2.http.post = lambda *a, **k: self.fail("при свежем http_state.json HTTP API вызываться не должен")
        c2.http.get = lambda *a, **k: self.fail("при свежем http_state.json HTTP cat.php вызываться не должен")
        c2.get_browser = lambda: fake_b
        list(c2.listings())
        self.assertTrue(c2.use_browser)

    def _ssr_html_helper(self):
        return (f"<script>window._cianConfig['frontend-serp'].concat("
                f"{json.dumps([{'key': 'initialState', 'value': {'results': {'aggregatedOffers': 1, 'totalOffers': 1, 'offers': [self.offer()]}}}])});</script>")

    def test_incremental_coverage_88_percent_is_complete_below_85_is_partial(self):
        self.c.finish(19, 18, 40, 432, 490)
        self.assertTrue(self.c.complete)
        self.assertEqual(self.c.incomplete_reason, "")

        self.c.finish(19, 18, 40, 380, 490)
        self.assertFalse(self.c.complete)
        self.assertIn("меньше 85%", self.c.incomplete_reason)

        self.c.finish(10, 18, 40, 450, 490)
        self.assertFalse(self.c.complete)

    def test_incremental_preserves_repair_only_listings_and_verifies_missing_main(self):
        """Ускоренный сбор не трогает объявления, пришедшие ранее только из выдач по ремонту (in_main=False),
        но исправно проверяет и снимает пропавшие из основной выдачи (in_main=True)."""
        self.addCleanup(runner.SCRAPERS.__setitem__, "cian", Cian)
        pool = [cian_offer(r) for r in listings("cian", 11)]
        main_all, m_gone, ex = pool[:10], pool[9], pool[10]
        main_id_gone, ex_id = str(m_gone["cianId"]), str(ex["cianId"])

        class FullCian(Cian):
            def _load(self, page, repair=None):
                if repair == 2:
                    return [ex], 1
                if repair is None:
                    return main_all, len(main_all)
                return [], 0

        run("cian", FullCian)
        con = db.connect()
        rows = {r["source_id"]: (r["active"], r["missed"], json.loads(r["raw"])["in_main"])
                for r in con.execute("SELECT source_id, active, missed, raw FROM listings WHERE source='cian'")}
        con.close()
        self.assertEqual(rows[ex_id], (1, 0, False))
        self.assertEqual(rows[main_id_gone], (1, 0, True))

        verified = []

        class IncCian(Cian):
            def _load(self, page, repair=None):
                if repair is None:
                    return main_all[:-1], len(main_all) - 1
                return [], 0

            def verify(self, row):
                verified.append(row["source_id"])
                return None, None, None

        for _ in range(3):
            run("cian", IncCian)

        self.assertNotIn(ex_id, verified, "объявление только из выдачи по ремонту не должно проверяться в ускоренном сборе")
        self.assertEqual(verified.count(main_id_gone), 2, "пропавшее из основной выдачи проверяется до снятия на 2-й прогон")
        con = db.connect()
        ex_row = tuple(con.execute("SELECT active, missed FROM listings WHERE source='cian' AND source_id=?", (ex_id,)).fetchone())
        gone_row = tuple(con.execute("SELECT active, missed FROM listings WHERE source='cian' AND source_id=?", (main_id_gone,)).fetchone())
        con.close()
        self.assertEqual(ex_row, (1, 0))
        self.assertEqual(gone_row, (0, 2))

    def test_legacy_rows_without_in_main_run_one_full_scan(self):
        """Старая база без флага in_main в raw один раз проходит полный сбор, после чего работает ускоренно."""
        a = self.offer()
        legacy = self.c._normalize(a)
        legacy["raw"].pop("in_main", None)
        legacy["raw"]["repair"] = 2
        con = db.connect()
        db.upsert(con, legacy)
        con.execute("INSERT OR REPLACE INTO meta VALUES('cian_repairs_ts', ?)", (str(time.time()),))
        con.commit()
        con.close()

        calls1 = []
        c1 = Cian(settings.load(), self.log.append, None, lambda **k: None)
        c1.wait = lambda *a, **k: None
        c1._load = lambda page, repair=None: (calls1.append((page, repair)), ([a], 1) if repair in (2, None) else ([], 0))[1]
        items1 = list(c1.listings())
        self.assertEqual([r for _, r in calls1], [1, 2, 3, 4, None])
        self.assertTrue(items1[0]["raw"]["in_main"])
        con = db.connect()
        for it in items1:
            db.upsert(con, it)
        con.commit()
        con.close()

        calls2 = []
        c2 = Cian(settings.load(), self.log.append, None, lambda **k: None)
        c2.wait = lambda *a, **k: None
        c2._load = lambda page, repair=None: (calls2.append((page, repair)), ([a], 1) if repair is None else ([], 0))[1]
        list(c2.listings())
        self.assertEqual(calls2, [(1, None)])

    def test_incremental_opportunistic_repair_page_offers_yielded_without_duplicates(self):
        """Попутные объявления с запрошенной в ускоренном сборе страницы ремонта отдаются без дублей и с in_main=False."""
        a = self.offer()
        items0, _ = self.run_listings({2: [a]}, [a])
        con = db.connect()
        for it in items0:
            db.upsert(con, it)
        con.commit()
        con.close()

        new_main = self.offer(description=BATHROOMS_ONLY)
        new_main["bargainTerms"] = {"priceRur": 7_000_000}
        extra_rep = self.offer()
        extra_rep["bargainTerms"] = {"priceRur": 7_100_000}

        c = Cian(settings.load(), self.log.append, None, lambda **k: None)
        c.wait = lambda *a, **k: None

        def load(page, repair=None):
            if repair is None:
                return [a, new_main], 2
            if repair == 1:
                return [new_main, extra_rep, a], 3
            return [], 0

        c._load = load
        items = list(c.listings())
        self.assertEqual([i["source_id"] for i in items],
                         [str(a["cianId"]), str(new_main["cianId"]), str(extra_rep["cianId"])])
        by_id = {i["source_id"]: i for i in items}
        self.assertTrue(by_id[str(a["cianId"])]["raw"]["in_main"])
        self.assertTrue(by_id[str(new_main["cianId"])]["raw"]["in_main"])
        self.assertFalse(by_id[str(extra_rep["cianId"])]["raw"]["in_main"])
        self.assertEqual(by_id[str(new_main["cianId"])]["renovation"], "none")
        self.assertEqual(by_id[str(extra_rep["cianId"])]["renovation"], "none")

    def test_incremental_ignores_off_search_active_rows_without_in_main(self):
        """Старая active=1 запись без in_main и repair (вне поиска, verify -> active) не блокирует
        ускоренный режим после первого успешного полного сбора."""
        self.addCleanup(runner.SCRAPERS.__setitem__, "cian", Cian)
        pool = [cian_offer(r) for r in listings("cian", 11)]
        off_search, main_pool = pool[0], pool[1:]
        off_id = str(off_search["cianId"])

        legacy_off = self.c._normalize(off_search)
        legacy_off["raw"] = {"category": "flatSale", "apartments": False}  # ни "repair", ни "in_main"
        con = db.connect()
        db.upsert(con, legacy_off)
        for o in main_pool:
            it = self.c._normalize(o)
            it["raw"].pop("in_main", None)
            it["raw"]["repair"] = 2
            db.upsert(con, it)
        con.execute("INSERT OR REPLACE INTO meta VALUES('cian_repairs_ts', ?)", (str(time.time()),))
        con.commit()
        con.close()

        calls = []
        verified = []

        class ProbeCian(Cian):
            def _load(self, page, repair=None):
                calls.append((page, repair))
                if repair == 2:
                    return [main_pool[0]], 1
                if repair is None:
                    return main_pool, len(main_pool)
                return [], 0

            def verify(self, row):
                verified.append(row["source_id"])
                return "active", "на ЦИАН объявление опубликовано, цена вне фильтра", None

        run("cian", ProbeCian)
        self.assertEqual(last_run()["status"], "done")
        self.assertEqual([r for _, r in calls], [1, 2, 3, 4, None])
        self.assertEqual(verified, [off_id])

        con = db.connect()
        row = con.execute("SELECT active, missed, raw FROM listings WHERE source='cian' AND source_id=?", (off_id,)).fetchone()
        con.close()
        self.assertEqual((row["active"], row["missed"]), (1, 0))
        self.assertNotIn("in_main", json.loads(row["raw"]))
        self.assertNotIn("repair", json.loads(row["raw"]))

        calls.clear()
        verified.clear()
        run("cian", ProbeCian)
        self.assertEqual(last_run()["status"], "done")
        self.assertEqual(calls, [(1, None)], "второй прогон идёт в ускоренном режиме несмотря на внепоисковую active=1 запись")
        self.assertEqual(verified, [off_id])


class CianStateAndFallback(Case):
    """Разбор состояния страницы ЦИАН, распознавание блокировок, откат API → HTTP cat.php → браузер и снимки."""

    def setUp(self):
        super().setUp()
        self.log = []
        self.c = Cian(settings.load(), self.log.append, None, lambda **k: None)
        self.offer = cian_offer(listings("cian", 1)[0])

    def _ssr_html(self, aggregated=42, total=95):
        payload = [
            {"key": "initialState", "value": {"agreementLink": "/doc"}},
            {"key": "initialState", "value": {"results": {
                "aggregatedOffers": aggregated, "totalOffers": total, "offers": [self.offer],
            }}},
        ]
        return ("<html><head><title>Купить квартиру</title>"
                "<script src='https://example.test/captcha.js'></script></head><body>"
                "<script>(window._cianConfig['frontend-serp'] = window._cianConfig['frontend-serp'] || [])"
                f".concat({json.dumps(payload, ensure_ascii=False)});</script></body></html>")

    def test_parse_state_multiple_initial_states(self):
        st = parse_state(self._ssr_html())
        self.assertIsNotNone(st)
        self.assertEqual(st["offers"], [self.offer])
        self.assertEqual((st["aggregated"], st["total"]), (42, 95))

    def test_parse_state_json_parse_zero_and_raw_fallback(self):
        encoded = json.dumps(json.dumps([
            {"key": "initialState", "value": {"results": {"aggregatedOffers": 0, "totalOffers": 5, "offers": []}}},
        ]))
        html_jp = f"<script>window._cianConfig['frontend-serp'].concat(JSON.parse({encoded}));</script>"
        st = parse_state(html_jp)
        self.assertEqual((st["aggregated"], st["total"], st["offers"]), (0, 5, []))

        html_raw = f'<script>window.__STATE__ = {{"results": {{"offers": {json.dumps([self.offer])}, "totalOffers": 1}}}};</script>'
        st_raw = parse_state(html_raw)
        self.assertEqual((st_raw["offers"], st_raw["total"], st_raw["aggregated"]), ([self.offer], 1, None))

    def test_is_blocked_and_is_ready(self):
        ok_html = self._ssr_html()
        self.assertFalse(self.c.is_blocked(ok_html, "https://krym.cian.ru/cat.php?deal_type=sale"))
        self.assertTrue(self.c.is_ready(ok_html, "https://krym.cian.ru/cat.php?deal_type=sale"))
        self.assertTrue(self.c._page_url(1).startswith("https://krym.cian.ru/cat.php?"))

        self.assertTrue(self.c.is_blocked(ok_html, "https://api.cian.ru/cian-captcha/?redirect_url=x"))
        self.assertTrue(self.c.is_blocked(ok_html, "https://www.cian.ru/xpvnsulc/?back_location=x"))
        self.assertTrue(self.c.is_blocked("<html><head><title>Captcha</title></head></html>", "https://www.cian.ru/"))
        self.assertTrue(self.c.is_blocked('<div id="form_captcha"></div>', "https://www.cian.ru/"))
        servicepipe = ('<!DOCTYPE html><html><body><div id="id_spinner"><js-challenge-loader class="spinner-loader">'
                       '</js-challenge-loader></div><div id="id_captcha_frame_div" style="display: none;"></div></body></html>')
        self.assertTrue(self.c.is_blocked(servicepipe, "https://www.cian.ru/cat.php?deal_type=sale"))
        self.assertFalse(self.c.is_ready(servicepipe, "https://www.cian.ru/cat.php?deal_type=sale"))
        self.assertTrue(self.c.is_ip_banned('<html><body><h1>Forbidden</h1><p id="REQUEST-IP">IP: 1.2.3.4</p></body></html>',
                                            "https://krym.cian.ru/sale/flat/1/"))
        self.assertFalse(self.c.is_ready("<html><head><title>Ок</title></head><body></body></html>", "https://www.cian.ru/cat.php"))

    def test_load_falls_back_from_blocked_api_to_http_cat_and_browser(self):
        calls = []
        ssr = self._ssr_html(aggregated=7, total=15)
        self.c.http.post = lambda url, **kw: (_ for _ in ()).throw(Blocked("HTTP 200 https://api.cian.ru/cian-captcha/"))

        def fake_get(url, **kw):
            calls.append(url)
            return SimpleNamespace(text=ssr)

        self.c.http.get = fake_get
        offers, count = self.c._load(1)
        self.assertEqual((offers, count, len(calls)), ([self.offer], 7, 1))
        self.assertTrue(self.c._api_blocked)
        self.assertFalse(self.c.use_browser, "при сработавшем cat.php по HTTP браузер не открывается")

        # Второе обращение сразу идёт на cat.php, минуя заблокированный API
        self.c.http.post = lambda *a, **kw: self.fail("API не должен вызываться повторно после блокировки")
        offers2, _ = self.c._load(2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(offers2, [self.offer])

        # Если cat.php по HTTP тоже заблокирован — откат в браузер (и разбор raw_html при пустом _JS_STATE)
        self.c.http.get = lambda *a, **kw: (_ for _ in ()).throw(Blocked("HTTP 403"))
        fake_b = SimpleNamespace(
            goto=lambda url: "<html>empty dom</html>",
            evaluate=lambda js: None,
            raw_html=lambda: ssr,
        )
        self.c.get_browser = lambda: fake_b
        offers3, count3 = self.c._load(1)
        self.assertTrue(self.c.use_browser)
        self.assertEqual((offers3, count3), ([self.offer], 7))

    def test_snapshot_last_uses_browser_html_on_first_call_error(self):
        saved = []
        self.c.runlog = SimpleNamespace(snapshot=lambda body, reason, ext="html": saved.append((body, reason, ext)))
        self.c.http.last_text = "<title>Captcha</title>api-captcha"
        page = SimpleNamespace(content=lambda: "<html>browser-dom</html>", url="https://krym.cian.ru/cat.php")
        fake_b = SimpleNamespace(page=page, raw_html=lambda: "<html>raw-ssr</html>")

        def get_fake_browser():
            self.c.browser = fake_b
            return fake_b

        self.c.get_browser = get_fake_browser

        def bad_browser(b):
            self.c.snapshot_last("нет состояния страницы")
            raise IOError("fail")

        with self.assertRaises(IOError):
            self.c.fetch(lambda: (_ for _ in ()).throw(Blocked("api")), bad_browser)
        self.assertEqual(self.c.last_mode, "browser")
        self.assertEqual(saved[-1], ("<html>browser-dom</html>", "нет состояния страницы", "html"))

        # Если page.content() упал — снимок берёт raw_html() из ответа браузера
        page.content = lambda: (_ for _ in ()).throw(RuntimeError("navigating"))
        self.c.snapshot_last("пусто")
        self.assertEqual(saved[-1][0], "<html>raw-ssr</html>")

    def test_browser_raw_html_same_host_after_replace_state(self):
        class Page:
            url = "https://krym.cian.ru/cat.php?deal_type=sale&p=1"

        b = Browser.__new__(Browser)
        page = Page()
        b.page = page
        b._docs = {page: SimpleNamespace(status=200, url="https://krym.cian.ru/cat.php?p=1", text=lambda: "<html>ok</html>")}
        self.assertEqual(b.raw_html(), "<html>ok</html>")
        page.url = "https://other.example/cat.php"
        self.assertIsNone(b.raw_html())

    @needs_browser
    def test_servicepipe_on_http_switches_to_real_browser_and_parses_multi_state(self):
        """Реальный сценарий прогона №158: API отдаёт SmartCaptcha, cat.php по HTTP отдаёт 2 КБ ServicePipe
        (<js-challenge-loader>) или пустую оболочку без состояния — парсер обязан поднять Blocked, переключиться
        на настоящий Chromium и прочитать второй initialState через _JS_STATE."""
        fast_timing(self)
        ssr_bytes = self._ssr_html(aggregated=42, total=95).encode("utf-8")
        sp_bytes = (
            '<!DOCTYPE html><html><body><div id="id_spinner"><js-challenge-loader class="spinner-loader">'
            '</js-challenge-loader></div><div id="id_captcha_frame_div" style="display: none;"></div></body></html>'
        ).encode("utf-8")
        state = {"cat_gets": 0, "mode": "servicepipe"}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = b'<html><head><title>Captcha - CIAN</title></head><body><form id="form_captcha"></form></body></html>'
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                state["cat_gets"] += 1
                # Первый GET — от быстрого HTTP-клиента; в режиме browser_soft_challenge и первый запрос Chromium получает заглушку
                if state["cat_gets"] == 1:
                    body = sp_bytes if state["mode"] != "empty_shell" else b"<html><body>empty shell</body></html>"
                elif state["cat_gets"] == 2 and state["mode"] == "browser_soft_challenge":
                    body = sp_bytes
                else:
                    body = ssr_bytes
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        base_url = f"http://127.0.0.1:{srv.server_port}"

        orig_api = cian_mod.API
        cian_mod.API = f"{base_url}/api"
        self.addCleanup(setattr, cian_mod, "API", orig_api)

        for mode, expected_gets in (("servicepipe", 2), ("empty_shell", 2), ("browser_soft_challenge", 3)):
            with self.subTest(mode=mode):
                state["cat_gets"] = 0
                state["mode"] = mode
                c = Cian(settings.load(), self.log.append, None, lambda **k: None)
                c._page_url = lambda page, repair=None: f"{base_url}/cat.php?p={page}"
                lb = LocalBrowser("cian", c.is_blocked, self.log.append, None, is_ready=c.is_ready, label="ЦИАН")
                try:
                    def get_lb():
                        c.browser = lb
                        return lb

                    c.get_browser = get_lb
                    offers, count = c._load(1)
                    self.assertTrue(c.use_browser, f"{mode}: после блокировки HTTP должен включиться браузер")
                    self.assertEqual(c.last_mode, "browser")
                    self.assertEqual((offers, count), ([self.offer], 42))
                    self.assertEqual(state["cat_gets"], expected_gets)
                finally:
                    lb.close()

    @needs_browser
    def test_browser_retries_transient_http_500_and_reports_persistent_500(self):
        """Реальный сценарий прогона №165: при HTTP 500 («Ошибка 500 - Циан») в браузере
        выполняются повторы с паузой; при восстановлении сбор идёт без снимка, а при стойком 500
        поднимается IOError('HTTP 500 ...') со снимком http 500, а не 'не найдено состояние страницы'."""
        fast_timing(self)
        err500 = (
            '<!DOCTYPE html><html><head><title>Ошибка 500 - Циан</title></head>'
            '<body><h1>Страница не загрузилась</h1><p>Попробуйте ещё раз, уже должно работать</p></body></html>'
        ).encode("utf-8")
        ssr_bytes = self._ssr_html(aggregated=42, total=95).encode("utf-8")
        state = {"gets": 0, "persistent": False}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                state["gets"] += 1
                is_err = state["persistent"] or state["gets"] == 1
                body = err500 if is_err else ssr_bytes
                self.send_response(500 if is_err else 200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base_url = f"http://127.0.0.1:{srv.server_port}"

        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                self.log.clear()
                state["gets"] = 0
                state["persistent"] = persistent
                snaps = []
                c = Cian(settings.load(), self.log.append, None, lambda **k: None)
                c.use_browser = True
                c._page_url = lambda page, repair=None: f"{base_url}/cat.php?p={page}"
                lb = LocalBrowser("cian", c.is_blocked, self.log.append, None, is_ready=c.is_ready, label="ЦИАН")
                lb.keep = lambda body, reason, ext="html": snaps.append((reason, ext))
                try:
                    c.get_browser = lambda lb=lb: (setattr(c, "browser", lb), lb)[1]
                    if not persistent:
                        offers, count = c._load(2)
                        self.assertEqual((offers, count), ([self.offer], 42))
                        self.assertEqual(state["gets"], 2)
                        self.assertEqual(snaps, [])
                        self.assertTrue(any("ошибка сервера (HTTP 500) — повторяю" in m for m in self.log), self.log)
                    else:
                        with self.assertRaises(IOError) as ctx:
                            c._load(3)
                        self.assertIn("HTTP 500", str(ctx.exception))
                        self.assertNotIn("не найдено состояние страницы", str(ctx.exception))
                        self.assertEqual(state["gets"], 1 + len(LocalBrowser.SERVER_RETRY_WAITS))
                        self.assertEqual(snaps, [("http 500", "html")])
                finally:
                    lb.close()

    def test_http_retries_transient_502_and_browser_retries_empty_state(self):
        """HTTP повторяет временный 502 без снимков и перехода в браузер; в браузере страница 200
        без _cianConfig перегружается один раз перед ошибкой состояния."""
        fast_timing(self)
        posts = {"n": 0}
        ssr_json = json.dumps({"data": {"offersSerialized": [self.offer], "aggregatedCount": 11}}).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                posts["n"] += 1
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                code = 502 if posts["n"] == 1 else 200
                body = b"<html>502 Bad Gateway</html>" if code == 502 else ssr_json
                self.send_response(code)
                self.send_header("Content-Type", "application/json" if code == 200 else "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        orig_api = cian_mod.API
        cian_mod.API = f"http://127.0.0.1:{srv.server_port}/api"
        self.addCleanup(setattr, cian_mod, "API", orig_api)

        snaps = []
        c = Cian(settings.load(), self.log.append, None, lambda **k: None)
        c.runlog = SimpleNamespace(request=lambda *a: None, line=lambda *a: None,
                                   snapshot=lambda body, reason, ext="html": snaps.append((reason, ext)))
        offers, count = c._load(1)
        self.assertEqual((offers, count), ([self.offer], 11))
        self.assertFalse(c.use_browser)
        self.assertEqual(c.last_mode, "http")
        self.assertEqual(posts["n"], 2)
        self.assertEqual(snaps, [])

        ssr_html = self._ssr_html(aggregated=7, total=15)
        b_calls = {"n": 0}
        c2 = Cian(settings.load(), self.log.append, None, lambda **k: None)
        c2.use_browser = True
        c2.wait = lambda *a, **k: None

        def fake_goto(u):
            b_calls["n"] += 1
            return "<html></html>" if b_calls["n"] == 1 else ssr_html

        fake_b = SimpleNamespace(goto=fake_goto, evaluate=lambda js: None, raw_html=lambda: None)
        c2.get_browser = lambda: fake_b
        self.assertEqual(c2._load(1), ([self.offer], 7))
        self.assertEqual(b_calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
