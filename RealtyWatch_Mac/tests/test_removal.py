"""Сбой выгрузки не должен снимать объявления: снятие — по явному признаку на странице объявления
или потому, что объявления не осталось ни на одном сайте."""
from datetime import datetime, timedelta

from app import offsale
from app.net import Blocked
from app.scrapers.avito import Avito
from app.scrapers.cian import Cian
from app.scrapers.yandex import Yandex
from tests.support import Case, avito_page, cian_offer, con, counts, last_run, listings, run, yandex_offer

UNKNOWN = (None, None, None)


def cian(rows, total=None, fail_page=None, verify=None, per_page=28):
    class C(Cian):
        def _load(self, page, repair=None):
            if repair:  # выдачи по виду ремонта в этих тестах пустые
                return [], 0
            if fail_page == page:
                raise IOError("timeout: сервер не ответил")
            chunk = rows[(page - 1) * 28: page * 28][:per_page]
            self.http.last_text = '{"data": {"offersSerialized": []}}'
            return [cian_offer(r) for r in chunk], (len(rows) if total is None else total)

        def verify(self, row):
            return verify(row) if verify else UNKNOWN
    return C


def avito(pages_fn, verify=None):
    class A(Avito):
        def _get_search_html(self, url, page=1):
            html = pages_fn(int(url.split("&p=")[1]) if "&p=" in url else 1)
            self.http.last_text = html
            return html

        def enrich(self, item):  # страница объявления в тестах не открывается
            return item

        def verify(self, row):
            return verify(row) if verify else UNKNOWN
    return A


def blocked(row):
    raise Blocked("капча")


class CianRemoval(Case):
    def setUp(self):
        super().setUp()
        self.rows = listings("cian")
        run("cian", cian(self.rows))  # первичная загрузка
        self.base = counts("cian")
        self.assertEqual(self.base["total"], len(self.rows))

    def test_server_timeout_mid_run(self):
        run("cian", cian(self.rows, fail_page=3))
        self.assertEqual(counts("cian"), self.base)
        self.assertEqual(last_run()["status"], "error")

    def test_empty_page_mid_run(self):
        run("cian", cian(self.rows[:-40], total=len(self.rows)))
        self.assertEqual(counts("cian"), self.base)
        self.assertEqual(last_run()["status"], "error")

    def test_coverage_below_85_percent_is_partial(self):
        # 21*4 + 8 = 92 из 120 (76.7% < 85%) — неполный прогон, снятие не проверяется
        run("cian", cian(self.rows, per_page=21, verify=lambda r: ("removed", "не должно вызываться", None)))
        self.assertEqual(counts("cian")["removed"], 0)
        r = last_run()
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["declared"], len(self.rows))
        self.assertLess(r["collected"], Cian.MIN_COVERAGE * len(self.rows))
        # 24*4 + 8 = 104 из 120 (86.7% >= 85%) — типичное схлопывание дублей в основной выдаче ЦИАН, прогон полный
        run("cian", cian(self.rows, per_page=24, verify=lambda r: ("active", "опубликовано", None)))
        r2 = last_run()
        self.assertEqual(r2["status"], "done")
        self.assertEqual(r2["collected"], 104)
        self.assertGreaterEqual(r2["collected"], Cian.MIN_COVERAGE * len(self.rows))

    def test_confirmed_at_once_and_lost_after_two_runs(self):
        miss = self.rows[-10:]
        verdict = dict(zip([r["source_id"] for r in miss],
                           [("removed", "статус снято", None)] * 3 + [("active", "опубликовано", None)] * 3 + [UNKNOWN] * 4))
        run("cian", cian(self.rows[:-10], verify=lambda r: verdict[r["source_id"]]))
        self.assertEqual(counts("cian")["removed"], 3, "сразу снимаются только те, кого сайт показал снятыми")
        c = con()
        state = lambda rows: [tuple(c.execute("SELECT active, missed FROM listings WHERE source_id=?",  # noqa: E731
                                              (r["source_id"],)).fetchone()) for r in rows]
        self.assertEqual(state(miss[6:]), [(1, 1)] * 4, "страницу не открыть — после первого сбора остаются в продаже")
        # второй сбор подряд без них: на ЦИАНе их больше нет, а других сайтов у этих квартир нет — снято
        run("cian", cian(self.rows[:-10], verify=lambda r: UNKNOWN))
        self.assertEqual(state(miss[6:]), [(0, 2)] * 4)
        self.assertEqual(state(miss[3:6]), [(1, 1)] * 3, "а эти сайт только что показывал живыми — счётчик с нуля")
        self.assertEqual(counts("cian")["removed"], 7)
        self.assertEqual(last_run()["status"], "done")

    def test_captcha_during_verify_removes_nothing(self):
        run("cian", cian(self.rows[:-10], verify=blocked))
        self.assertEqual(counts("cian")["removed"], 0)

    def test_mass_disappearance_after_short_pause(self):
        run("cian", cian(self.rows[:60], verify=lambda r: ("removed", "не должно вызываться", None)))
        self.assertEqual(counts("cian")["removed"], 0)
        self.assertEqual(last_run()["status"], "partial")

    def test_mass_disappearance_after_long_pause_is_checked(self):
        old = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")
        c = con()
        c.execute("UPDATE listings SET last_seen=? WHERE source='cian'", (old,))
        c.commit()
        checked = []
        run("cian", cian(self.rows[:60], verify=lambda r: (checked.append(r["source_id"]), UNKNOWN)[1]))
        self.assertEqual(len(checked), 60)
        self.assertEqual(last_run()["status"], "done")


class YandexRemoval(Case):
    def test_page_without_pager(self):
        class Y(Yandex):
            def _load(self, index):
                return [], {}
        run("yandex", Y)
        self.assertEqual(last_run()["status"], "partial")

    def test_http_503(self):
        rows = listings("yandex", 60)

        class Y(Yandex):
            def _load(self, index):
                if index == 0:
                    return [yandex_offer(r) for r in rows[:20]], {"totalPages": 3, "totalItems": 60}
                raise IOError("HTTP 503")
        run("yandex", Y)
        self.assertEqual(last_run()["status"], "error")
        self.assertEqual(counts("yandex")["removed"], 0)


class AvitoRemoval(Case):
    def setUp(self):
        super().setUp()
        self.rows = listings("avito", 200)
        n = len(self.rows)
        run("avito", avito(lambda p: avito_page(self.rows[(p - 1) * 50: p * 50], n)))
        self.base = counts("avito")
        self.assertEqual(self.base["total"], n)

    def test_captcha_interstitial_instead_of_results(self):
        run("avito", avito(lambda p: "<html><body>Проверка пройдена, перенаправление…</body></html>"))
        self.assertEqual(counts("avito"), self.base)
        self.assertEqual(last_run()["status"], "error")
        self.assertTrue(list(self.dir.glob("snapshots/*.html")), "страница сбоя должна сохраниться")

    def test_page_returns_previous_page(self):
        n = len(self.rows)
        run("avito", avito(lambda p: avito_page(self.rows[:50], n) if p in (1, 3) else avito_page(self.rows[(p - 1) * 50: p * 50], n)))
        self.assertEqual(counts("avito"), self.base)
        self.assertEqual(last_run()["status"], "error")

    def test_empty_page_in_the_middle(self):
        n = len(self.rows)
        run("avito", avito(lambda p: avito_page([], n) if p == 2 else avito_page(self.rows[(p - 1) * 50: p * 50], n)))
        self.assertEqual(counts("avito"), self.base)

    def test_unconfirmed_leave_the_site_after_two_runs(self):
        """Авито на проверках отвечает капчей, и раньше такие объявления копились в продаже вечно."""
        sub = self.rows[:-30]
        page = lambda p: avito_page(sub[(p - 1) * 50: p * 50], len(sub))  # noqa: E731
        run("avito", avito(page, verify=blocked))
        self.assertEqual(counts("avito")["removed"], 0, "после одного сбора ещё рано")
        run("avito", avito(page))
        self.assertEqual(counts("avito")["removed"], 30, "второго сбора подряд без них достаточно: на Авито их нет")
        reason = con().execute("SELECT removed_reason FROM listings WHERE source='avito' AND active=0 LIMIT 1").fetchone()[0]
        self.assertIn("нет в поиске Авито", reason)

    def test_closed_pages_are_removed_at_once(self):
        sub = self.rows[:-5]
        gone = {r["source_id"] for r in self.rows[-5:]}  # в пределах лимита проверок Авито за сбор (20)
        run("avito", avito(lambda p: avito_page(sub[(p - 1) * 50: p * 50], len(sub)),
                           verify=lambda r: ("removed", "closedItem", None) if r["source_id"] in gone else ("active", "x", None)))
        self.assertEqual(counts("avito")["removed"], len(gone))

    def test_sudden_drop_is_failure(self):
        run("avito", avito(lambda p: avito_page(self.rows[(p - 1) * 50: p * 50], 100) if p <= 2 else avito_page([], 100),
                           verify=lambda r: ("removed", "не должно вызываться", None)))
        self.assertEqual(counts("avito")["removed"], 0)
        self.assertEqual(last_run()["status"], "partial")


def yandex(rows, verify=None, per_page=20):
    class Y(Yandex):
        def _load(self, index):
            chunk = rows[index * per_page:(index + 1) * per_page]
            return [yandex_offer(r) for r in chunk], {"totalPages": max(1, -(-len(rows) // per_page)), "totalItems": len(rows)}

        def verify(self, row):
            return verify(row) if verify else UNKNOWN
    return Y


class OffSale(Case):
    """Снятие с продажи считается по квартире целиком. Объявление пропало из поиска сайта и страницу
    не открыть — ссылка на этот сайт убирается; снято с продажи — только когда не осталось нигде."""

    def setUp(self):
        super().setUp()
        self.cr = listings("cian", 5)
        # те же пять квартир на Яндексе: те же координаты и площадь — программа сведёт их в один объект
        self.yr = [dict(r, source_id=str(20_000_000 + i), url=f"https://example.test/yandex/{20_000_000 + i}")
                   for i, r in enumerate(self.cr)]
        run("cian", cian(self.cr))
        run("yandex", yandex(self.yr))
        c = con()
        self.obj = c.execute("SELECT object_id FROM listings WHERE source='cian' AND source_id=?", (self.cr[0]["source_id"],)).fetchone()[0]
        self.assertEqual(c.execute("SELECT COUNT(*) FROM listings WHERE object_id=?", (self.obj,)).fetchone()[0], 2,
                         "квартира с двух сайтов — один объект")

    def state(self, source, sid):
        r = con().execute("SELECT active, missed, removed_reason FROM listings WHERE source=? AND source_id=?", (source, sid)).fetchone()
        return (r["active"], r["missed"], r["removed_reason"])

    def test_gone_from_one_site_keeps_the_flat_on_sale(self):
        for _ in range(2):
            run("cian", cian(self.cr[1:]))      # первой квартиры в выдаче ЦИАНа больше нет, страница не проверяется
        self.assertEqual(self.state("cian", self.cr[0]["source_id"])[:2], (1, 2),
                         "квартира продаётся на Яндексе — объявление ЦИАНа не снимается")
        self.assertTrue(offsale.off_search({"missed": 2, "active": 1}), "но ссылка на ЦИАН уже никуда не ведёт")
        self.assertEqual(self.state("yandex", self.yr[0]["source_id"])[:2], (1, 0))
        self.assertEqual(counts("cian")["removed"], 0)

    def test_gone_from_every_site_takes_the_flat_off_sale(self):
        for _ in range(2):
            run("cian", cian(self.cr[1:]))
            run("yandex", yandex(self.yr[1:]))
        c, y = self.state("cian", self.cr[0]["source_id"]), self.state("yandex", self.yr[0]["source_id"])
        self.assertEqual((c[0], y[0]), (0, 0), "не осталось ни на одном сайте — квартира снята с продажи")
        self.assertIn("нет в поиске ЦИАН 2 прогона подряд", c[2])
        self.assertIn("на других сайтах объявления тоже нет", c[2])
        ev = con().execute("SELECT kind, note FROM events WHERE source='cian' AND source_id=? ORDER BY id DESC LIMIT 1",
                           (self.cr[0]["source_id"],)).fetchone()
        self.assertEqual(ev["kind"], "removed")
        self.assertEqual(con().execute("SELECT COUNT(*) FROM listings WHERE object_id=? AND active=1", (self.obj,)).fetchone()[0], 0)

    def test_one_run_is_not_enough(self):
        run("cian", cian(self.cr[1:]))
        run("yandex", yandex(self.yr[1:]))
        self.assertEqual((self.state("cian", self.cr[0]["source_id"])[0], self.state("yandex", self.yr[0]["source_id"])[0]), (1, 1),
                         "один сбор мог и не доехать до конца — по одному прогону не снимаем")

    def test_single_site_flat_goes_off_after_two_runs(self):
        lone = listings("cian", 3, seed=5)
        for r in lone:
            r["source_id"] = str(70_000_000 + int(r["source_id"]) % 1000)
        run("cian", cian(self.cr + lone))
        for _ in range(2):
            run("cian", cian(self.cr))
        st = self.state("cian", lone[0]["source_id"])
        self.assertEqual(st[0], 0, "эта квартира была только на ЦИАНе — снята")
        self.assertNotIn("на других сайтах", st[2])

    def test_it_comes_back_when_the_site_shows_it_again(self):
        for _ in range(2):
            run("cian", cian(self.cr[1:]))
            run("yandex", yandex(self.yr[1:]))
        run("cian", cian(self.cr))
        self.assertEqual(self.state("cian", self.cr[0]["source_id"])[:2], (1, 0), "объявление вернулось в выдачу — снова в продаже")
        self.assertEqual(self.state("yandex", self.yr[0]["source_id"])[0], 0, "на Яндексе его по-прежнему нет")
        kinds = [r["kind"] for r in con().execute("SELECT kind FROM events WHERE source='cian' AND source_id=? ORDER BY id",
                                                  (self.cr[0]["source_id"],))]
        self.assertEqual(kinds[-1], "returned")

    def test_reconcile_does_not_repeat_itself(self):
        for _ in range(2):
            run("cian", cian(self.cr[1:]))
            run("yandex", yandex(self.yr[1:]))
        before = con().execute("SELECT COUNT(*) FROM events WHERE kind='removed'").fetchone()[0]
        c = con()
        self.assertEqual(offsale.reconcile(c), 0, "снимать больше нечего")
        self.assertEqual(con().execute("SELECT COUNT(*) FROM events WHERE kind='removed'").fetchone()[0], before)

    def test_verified_active_resets_the_counter(self):
        run("cian", cian(self.cr[1:], verify=lambda r: ("active", "опубликовано", None)))
        run("cian", cian(self.cr[1:], verify=lambda r: ("active", "опубликовано", None)))
        self.assertEqual(self.state("cian", self.cr[0]["source_id"])[:2], (1, 0),
                         "сайт показал страницу объявления — оно живо, счётчик сброшен")
