"""ЦИАН: вид ремонта узнаётся отдельными выдачами по каждому виду (в самой выдаче поля о ремонте нет),
основная выдача идёт без фильтра по ремонту и с сортировкой по цене. Данные синтетические, к сайту
не обращаемся."""
import unittest

from app import rules, settings
from app.scrapers.cian import Cian, SORT
from tests.support import cian_offer, isolate, listings

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


if __name__ == "__main__":
    unittest.main()
