"""Сбой выгрузки не должен снимать объявления: снятие — только по явному признаку на странице объявления."""
from datetime import datetime, timedelta

from app.net import Blocked
from app.scrapers.avito import Avito
from app.scrapers.cian import Cian
from app.scrapers.yandex import Yandex
from tests.support import Case, avito_page, cian_offer, con, counts, last_run, listings, run, yandex_offer

UNKNOWN = (None, None, None)


def cian(rows, total=None, fail_page=None, verify=None, per_page=28):
    class C(Cian):
        def _load(self, page):
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
        def _get_html(self, url):
            html = pages_fn(int(url.split("&p=")[1]) if "&p=" in url else 1)
            self.http.last_text = html
            return html

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

    def test_coverage_below_95_percent_is_partial(self):
        run("cian", cian(self.rows, per_page=24, verify=lambda r: ("removed", "не должно вызываться", None)))
        self.assertEqual(counts("cian")["removed"], 0)
        r = last_run()
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["declared"], len(self.rows))
        self.assertLess(r["collected"], 0.95 * len(self.rows))

    def test_only_confirmed_are_removed(self):
        miss = self.rows[-10:]
        verdict = dict(zip([r["source_id"] for r in miss],
                           [("removed", "статус снято", None)] * 3 + [("active", "опубликовано", None)] * 3 + [UNKNOWN] * 4))
        run("cian", cian(self.rows[:-10], verify=lambda r: verdict[r["source_id"]]))
        self.assertEqual(counts("cian")["removed"], 3)
        c = con()
        unknown = [tuple(c.execute("SELECT active, missed FROM listings WHERE source_id=?", (r["source_id"],)).fetchone()) for r in miss[6:]]
        self.assertEqual(unknown, [(1, 1)] * 4)
        # второй раз не удалось проверить — всё равно в продаже
        run("cian", cian(self.rows[:-10], verify=lambda r: UNKNOWN))
        unknown = [tuple(c.execute("SELECT active, missed FROM listings WHERE source_id=?", (r["source_id"],)).fetchone()) for r in miss[6:]]
        self.assertEqual(unknown, [(1, 2)] * 4)
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

    def test_unconfirmed_stay_on_sale_and_closed_are_removed(self):
        sub = self.rows[:-30]
        page = lambda p: avito_page(sub[(p - 1) * 50: p * 50], len(sub))  # noqa: E731
        run("avito", avito(page, verify=blocked))
        run("avito", avito(page))
        self.assertEqual(counts("avito")["removed"], 0)
        gone = {r["source_id"] for r in self.rows[-30:][:5]}  # в пределах лимита проверок Авито за сбор (20)
        run("avito", avito(page, verify=lambda r: ("removed", "closedItem", None) if r["source_id"] in gone else ("active", "x", None)))
        self.assertEqual(counts("avito")["removed"], len(gone))

    def test_sudden_drop_is_failure(self):
        run("avito", avito(lambda p: avito_page(self.rows[(p - 1) * 50: p * 50], 100) if p <= 2 else avito_page([], 100),
                           verify=lambda r: ("removed", "не должно вызываться", None)))
        self.assertEqual(counts("avito")["removed"], 0)
        self.assertEqual(last_run()["status"], "partial")
