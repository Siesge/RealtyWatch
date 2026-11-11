"""ЦИАН: вид ремонта берётся из поля «Ремонт» объявления (раздел «О квартире»), а не из описания,
и на стороне ЦИАН по ремонту ничего не фильтруется. Данные синтетические, к сайту не обращаемся."""
import unittest

from app import rules, settings
from app.scrapers.cian import Cian
from tests.support import cian_offer, isolate, listings

# Описание объявления 333647311 по сути: ремонт сделан только в санузлах, а поле «Ремонт» — «без ремонта»
BATHROOMS_ONLY = "Вид неописуемый. Внутри сделан хороший ремонт в санузлах (ванная и туалет полностью готовы)."


class CianRepair(unittest.TestCase):
    def setUp(self):
        isolate(self)
        self.log = []
        self.c = Cian(settings.load(), self.log.append, None, lambda **k: None)
        self.f = settings.load()["filters"]

    def offer(self, repair=None, decoration=None, description="Квартира в центре"):
        o = cian_offer(listings("cian", 1)[0])
        o["description"] = description
        o["decoration"] = decoration
        if repair is not None:
            o["repairType"] = repair
        return o

    def test_field_wins_over_description(self):
        """«Хороший ремонт в санузлах» в описании не делает квартиру отремонтированной, если в «О квартире»
        написано «без ремонта»: такая квартира отсеивается."""
        item = self.c._normalize(self.offer("no", description=BATHROOMS_ONLY))
        self.assertEqual(item["renovation"], "none")
        self.assertEqual(item["renovation_raw"], "без ремонта", "на карточке видно, что указано на сайте")
        self.assertIn("без ремонта", rules.evaluate(item, self.f))

    def test_field_values(self):
        for repair, code in (("cosmetic", "cosmetic"), ("euro", "euro"), ("design", "design"), ("no", "none")):
            with self.subTest(repair=repair):
                self.assertEqual(self.c._normalize(self.offer(repair, description="требует ремонта"))["renovation"], code,
                                 "поле сайта важнее описания и в обратную сторону")

    def test_without_field_old_rules(self):
        """Поля нет — как раньше: отделка новостройки, затем описание."""
        self.assertEqual(self.c._normalize(self.offer(decoration="fine"))["renovation"], "finish")
        self.assertEqual(self.c._normalize(self.offer(decoration="fine", description="Квартира требует ремонта"))["renovation"], "none")
        self.assertEqual(self.c._normalize(self.offer(description="Сделан евроремонт"))["renovation"], "euro")

    def test_no_guess_without_data(self):
        """Ни поля, ни описания — ремонт не указан. Раньше такие объявления помечались «с ремонтом» в расчёте
        на фильтр ЦИАН, а он пропускал и квартиры без ремонта."""
        self.assertNotEqual(self.f.get("renovation"), "off", "фильтр по ремонту включён — ровно тот случай")
        item = self.c._normalize(self.offer())
        self.assertIsNone(item["renovation"])
        self.assertIsNone(item["renovation_raw"])

    def test_no_repair_filter_on_site(self):
        """На сайт ремонт не передаётся: ни в API, ни в адресе страницы выдачи."""
        self.assertNotIn("repair", self.c._json_query(1))
        self.assertNotIn("repair", self.c._page_url(1))
        self.assertNotIn("repair", self.c._page_url(2))

    def run_listings(self, offers):
        self.c._load = lambda page: (offers, len(offers))
        return list(self.c.listings())

    def test_run_log_counts_the_field(self):
        """В журнал прогона пишется, сколько объявлений пришло с каким значением поля — по нему видно, что
        ЦИАН поле действительно присылает."""
        offers = [self.offer("no"), self.offer("cosmetic"), self.offer("cosmetic"), self.offer()]
        for k, o in enumerate(offers):
            o["cianId"] = 1000 + k
        self.run_listings(offers)
        self.assertIn("поле «Ремонт»: без ремонта 1, косметический 2, не указан 1", self.log)

    def test_run_log_warns_when_field_never_comes(self):
        offers = [self.offer(), self.offer()]
        offers[1]["cianId"] = 2001
        self.run_listings(offers)
        self.assertTrue(any(m.startswith("⚠ поле «Ремонт» ЦИАН не пришло ни в одном из 2 объявлений") for m in self.log), self.log)


if __name__ == "__main__":
    unittest.main()
