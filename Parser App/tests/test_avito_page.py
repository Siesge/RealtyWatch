"""Выдача Авито: все объявления страницы берутся из данных для поисковиков, даже если браузер дорисовал
только часть карточек; карусель рекомендаций не собирается; у объявления без карточки адрес остаётся из базы."""
from app.scrapers.avito import Avito
from tests.support import Case, avito_page, con, last_run, listings, run


def avito(pages_fn):
    class A(Avito):
        def _get_search_html(self, url):
            return pages_fn(int(url.split("&p=")[1]) if "&p=" in url else 1)

        def enrich(self, item):
            return item
    return A


class AvitoPage(Case):
    def setUp(self):
        super().setUp()
        self.rows = listings("avito", 100)
        self.reco = listings("avito", 5, seed=9)
        for i, r in enumerate(self.reco):
            r["source_id"] = str(5_000_000_000 + i)

    def page(self, p, drawn=None):
        return avito_page(self.rows[(p - 1) * 50: p * 50], len(self.rows), ld=True, drawn=drawn, carousel=self.reco)

    def ids(self):
        return {r[0] for r in con().execute("SELECT source_id FROM listings WHERE source='avito'")}

    def test_all_listings_even_if_cards_not_drawn(self):
        run("avito", avito(lambda p: self.page(p, drawn=12)))
        self.assertEqual(self.ids(), {r["source_id"] for r in self.rows})
        r = last_run()
        self.assertEqual((r["status"], r["collected"]), ("done", 100))

    def test_carousel_is_not_collected(self):
        run("avito", avito(lambda p: self.page(p)))
        self.assertFalse(self.ids() & {r["source_id"] for r in self.reco})

    def test_address_kept_when_card_not_drawn(self):
        run("avito", avito(lambda p: self.page(p)))           # все карточки — адреса записаны
        run("avito", avito(lambda p: self.page(p, drawn=0)))  # ни одной карточки — только данные для поисковиков
        c = con()
        missing = c.execute("SELECT COUNT(*) FROM listings WHERE source='avito' AND (address IS NULL OR address='')").fetchone()[0]
        self.assertEqual(missing, 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM listings WHERE source='avito' AND active=0").fetchone()[0], 0)

    def test_fallback_without_schema_data(self):
        run("avito", avito(lambda p: avito_page(self.rows[(p - 1) * 50: p * 50], len(self.rows), carousel=self.reco)))
        self.assertEqual(self.ids(), {r["source_id"] for r in self.rows})
