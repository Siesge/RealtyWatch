"""Распознавание статуса объявления по его странице (синтетические страницы с теми же признаками, что у сайтов)."""
import json
import unittest

from app import settings
from app.net import Blocked
from app.scrapers.avito import Avito
from app.scrapers.cian import Cian
from app.scrapers.yandex import Yandex
from tests.support import isolate

ROW = {"source_id": "8283902753", "url": "https://www.avito.ru/yalta/kvartiry/1-k._kvartira_38_m_15_et._8283902753"}


def avito_item(closed):
    return (f'<html><body><h1 data-marker="item-view/title-info">1-к. квартира</h1>'
            f'<span data-marker="item-view/item-price">8 500 000 ₽</span><span data-marker="item-view/item-id">№ 8283902753</span>'
            f'<script>window.__state__ = "{{\\"closedItem\\":{"true" if closed else "false"}}}"</script></body></html>')


class Resp:
    def __init__(self, text):
        self.text = text


def scraper(cls):
    s = cls(settings.load(), lambda m: None, None, lambda **k: None)
    return s


class AvitoVerify(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.a = scraper(Avito)

    def check(self, html, url, status):
        self.a._fetch_once = lambda h, b: (html, url, status)
        return self.a.verify(ROW)[0]

    def test_open(self):
        self.a._fetch_once = lambda h, b: (avito_item(False), ROW["url"], 200)
        state, _, price = self.a.verify(ROW)
        self.assertEqual((state, price), ("active", 8_500_000))

    def test_closed(self):
        self.assertEqual(self.check(avito_item(True), ROW["url"], 200), "removed")

    def test_404(self):
        self.assertEqual(self.check("<html></html>", ROW["url"], 404), "removed")

    def test_redirect_to_catalog(self):
        cat = '<html><span data-marker="page-title/count">2 754</span><div data-marker="item"></div></html>'
        self.assertEqual(self.check(cat, "https://www.avito.ru/yalta/kvartiry/prodam/1-komnatnye", 200), "removed")

    def test_unknown_pages_are_not_removed(self):
        self.assertIsNone(self.check("<html><body>Что-то пошло не так</body></html>", ROW["url"], 200))
        self.assertIsNone(self.check("<html><body>Вход</body></html>", "https://www.avito.ru/login", 200))
        self.assertIsNone(self.check(avito_item(False).replace("8283902753", "1111111111"), ROW["url"], 200))

    def test_captcha_aborts_checking(self):
        def raise_blocked(h, b):
            raise Blocked("капча")
        self.a._fetch_once = raise_blocked
        with self.assertRaises(Blocked):
            self.a.verify(ROW)

    def test_repeated_unknown_pages_save_one_snapshot_and_abort(self):
        snaps = []
        self.a.runlog = type("RL", (), {"snapshot": lambda _s, body, reason, ext="html": snaps.append((body, reason))})()
        stub = "<html><body><div>Заглушка без карточки</div></body></html>"
        for _ in range(3):
            self.assertIsNone(self.check(stub, ROW["url"], 200))
        self.assertEqual(len(snaps), 1, "диагностический снимок пишется один раз за прогон")
        # Успешная проверка сбрасывает счётчик неузнанных страниц
        self.assertEqual(self.check(avito_item(False), ROW["url"], 200), "active")
        for _ in range(3):
            self.assertIsNone(self.check(stub, ROW["url"], 200))
        with self.assertRaises(Blocked):
            self.check(stub, ROW["url"], 200)
        self.assertEqual(len(snaps), 1)


class CianVerify(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.c = scraper(Cian)
        self.row = {"source_id": "324868665", "url": "https://krym.cian.ru/sale/flat/324868665/"}

    def page(self, status, archived=False):
        return (f'{{"offer":{{"category":"flatSale","status":"{status}","cianId":324868665,"isArchived":{"true" if archived else "false"},'
                f'"bargainTerms":{{"price":9450000,"priceType":"all","currency":"rur","saleType":"free"}}}}}}')

    def verify(self, text=None, error=None):
        def get(url, **kw):
            if error:
                raise error
            return Resp(text)
        self.c.http.get = get
        return self.c.verify(self.row)

    def test_published(self):
        self.assertEqual(self.verify(self.page("published"))[::2], ("active", 9_450_000))

    def test_removed_statuses(self):
        for st in ("deactivated", "removed", "sold", "draft", "blocked", "refused"):
            self.assertEqual(self.verify(self.page(st))[0], "removed", st)
        self.assertEqual(self.verify(self.page("published", archived=True))[0], "removed")

    def test_moderation_and_unknown_are_not_removed(self):
        self.assertIsNone(self.verify(self.page("moderate"))[0])
        self.assertIsNone(self.verify(self.page("somethingNew"))[0])
        self.assertIsNone(self.verify("<html>заглушка</html>")[0])

    def test_404_and_network_errors(self):
        self.assertEqual(self.verify(error=IOError("HTTP 404 x"))[0], "removed")
        self.assertIsNone(self.verify(error=IOError("HTTP 503 x"))[0])
        self.assertIsNone(self.verify(error=IOError("timeout"))[0])
        with self.assertRaises(Blocked):
            self.verify(error=Blocked("капча"))


class YandexVerify(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.y = scraper(Yandex)
        self.row = {"source_id": "1", "url": "https://realty.yandex.ru/offer/1/"}

    def verify(self, card):
        st = {"offerCard": {"card": card}} if card is not None else {}
        self.y.http.get = lambda url, **kw: Resp(f"<script>window.INITIAL_STATE = {json.dumps(st)};</script>")
        return self.y.verify(self.row)[0]

    def test_states(self):
        self.assertEqual(self.verify({"active": True, "price": {"value": 1}}), "active")
        self.assertEqual(self.verify({"active": False}), "removed")
        self.assertEqual(self.verify({"active": True, "obsolete": True}), "removed")
        self.assertIsNone(self.verify(None))
