"""Выгрузка избранного одним HTML-файлом:
- в файле только избранные объекты, по карточке на объект, со всеми ссылками на объявления;
- файл автономный: ни одной внешней ссылки, шрифты и фотографии зашиты внутрь;
- без JavaScript остаются все плитки и ссылки, пропадают только фильтры, сортировка и таблица;
- данные для сортировок подборки и всех колонок таблицы стоят в разметке.
База, фото и настройки — синтетические, во временной папке."""
import re
import unittest
from pathlib import Path

from app import db, dedupe, images, share
from tests.support import Case, con

PHOTO = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")  # минимальный JPEG-«файл»


def item(source, sid, **kw):
    """Объявление для базы: нужные поля заданы, остальные — как у сайта без страницы объявления."""
    base = {
        "source": source, "source_id": sid, "url": f"https://{source}.test/{sid}",
        "title": "2-к. квартира, 60 м²", "price": 10_000_000, "rooms": 2, "area": 60.0, "floor": 3, "floors": 9,
        "address": "Ялта, ул. Тестовая, 1", "lat": 44.4838, "lon": 34.1433, "zone": "A",
        "renovation": "euro", "description": "Квартира с евроремонтом, вид на море",
    }
    base.update(kw)
    return base


class LikedPage(Case):
    def setUp(self):
        super().setUp()
        c = con()
        # пять объектов: два в избранном (один из них на двух сайтах), остальные — нет
        db.upsert(c, item("cian", "1", price=12_000_000, area=70.0, address="Ялта, ул. Морская, 5"))
        db.upsert(c, item("avito", "2", price=11_500_000, area=70.0, floor=3, floors=9,
                          address="Ялта, ул. Морская, 5, кв. 12", lat=44.4838, lon=34.1433))
        db.upsert(c, item("cian", "3", price=8_000_000, area=45.0, rooms=1, zone="B", renovation="old",
                          address="Ялта, ул. Дальняя, 7", lat=44.496, lon=34.17))
        db.upsert(c, item("cian", "4", price=30_000_000, area=120.0, rooms=3, excluded="дороже максимума"))
        db.upsert(c, item("yandex", "5", price=9_000_000, area=50.0, address="Ялта, ул. Тихая, 2", lat=44.487, lon=34.149))
        c.commit()
        dedupe.rebuild(c)
        # «Морская, 5» — одна квартира на двух сайтах: сердечко стоит на объявлении ЦИАН
        self.obj2 = c.execute("SELECT object_id FROM listings WHERE source='cian' AND source_id='1'").fetchone()[0]
        for source, sid in (("cian", "1"), ("cian", "3")):
            c.execute("INSERT INTO likes VALUES(?,?,?)", (source, sid, db.now()))
        # у «Дальней, 7» цена снижалась, а объявление снято с продажи
        c.execute("INSERT INTO price_history VALUES('cian','3',?,9500000)", (db.now(),))
        c.execute("UPDATE listings SET active=0, removed_at=?, removed_reason='нет на сайте' WHERE source='cian' AND source_id='3'",
                  (db.now(),))
        c.commit()
        c.close()
        (images.IMAGES / "cian").mkdir(parents=True, exist_ok=True)
        (images.IMAGES / "cian" / "1.jpg").write_bytes(PHOTO)
        self.page = share.page(con())

    def test_only_liked_objects(self):
        objs = share.liked_objects(con())
        self.assertEqual(len(objs), 2, "в выгрузку попали только объекты с сердечком")
        self.assertEqual(self.page.count('<article class="card'), 2, "по карточке на объект")
        for url in ("https://cian.test/1", "https://avito.test/2", "https://cian.test/3"):
            self.assertIn(url, self.page, f"ссылка на объявление {url} в файле есть")
        for url in ("https://cian.test/4", "https://yandex.test/5"):
            self.assertNotIn(url, self.page, "объявления не из избранного в файл не попадают")
        self.assertIn("ул. Морская, 5, кв. 12", self.page, "адрес взят самый подробный из объявлений объекта")

    def test_file_is_offline(self):
        """Ни одной ссылки наружу и в папку программы: иначе на чужом компьютере файл окажется пустым."""
        for bad in ('src="/', 'href="/', "/static/", "http://127.0.0.1", "<link"):
            self.assertNotIn(bad, self.page, f"в файле осталась внешняя ссылка: {bad}")
        self.assertIn("data:font/woff2;base64,", self.page, "шрифт зашит в файл")
        self.assertIn("data:image/jpeg;base64,", self.page, "фотография зашита в файл")
        links = re.findall(r'href="(?!https://(?:cian|avito|yandex)\.test/)[^"]+"', self.page)
        self.assertEqual(links, [], f"в файле есть ссылки, кроме объявлений: {links[:3]}")

    def test_readable_without_js(self):
        """Скрипт только прячет и переставляет готовые узлы, поэтому без него остаётся вся суть."""
        self.assertIn('<html lang="ru" class="nojs">', self.page)
        self.assertIn(".nojs .share-tools", self.page, "без JavaScript панель фильтров скрыта")
        self.assertIn("<noscript>", self.page)
        self.assertIn("JavaScript отключён", self.page, "в noscript объяснено, что не работает")
        body = self.page.split("<script>", 2)[-1]  # скрипт идёт последним, после всей разметки
        self.assertNotIn("<article", body, "карточки не рисуются скриптом — они уже в разметке")
        self.assertEqual(self.page.count("<tr data-i="), 2, "строки таблицы тоже готовы в разметке")
        self.assertIn("евроремонт", self.page)
        self.assertIn("снято с продажи", self.page, "снятое с продажи объявление помечено")

    def test_sort_and_filter_data(self):
        """У каждой карточки есть всё, по чему сортирует подборка и фильтрует панель."""
        card = re.search(r"<article class=\"card[^>]*>", self.page).group(0)
        for attr in ("data-i=", "data-price=", "data-ppm=", "data-area=", "data-rooms=", "data-floor=",
                     "data-zone=", "data-src=", "data-sites=", "data-first=", "data-active=", "data-reno=",
                     "data-address=", "data-text="):
            self.assertIn(attr, card, f"у карточки нет {attr}")
        for value, label in share.SORTS:
            self.assertIn(f'<option value="{value}">{label}</option>', self.page, f"нет сортировки «{label}»")
        self.assertIn('name="zone"', self.page, "фильтр по зонам есть: в избранном зоны A и B")
        self.assertIn('name="src"', self.page, "фильтр по сайтам есть")
        self.assertNotIn('value="C"', self.page, "зоны, которых нет в избранном, не предлагаются")

    def test_table_has_app_columns(self):
        head = self.page.split('<thead id="head">')[1].split("</thead>")[0]
        for key, title, _, _ in share.COLS:
            self.assertIn(f'data-k="{key}"', head, f"в таблице нет колонки {key}")
            self.assertIn(title, head)
        self.assertEqual(head.count("<th"), len(share.COLS))

    def test_price_change_shown(self):
        self.assertIn("old-price", self.page + " ", "прежняя цена зачёркнута")
        self.assertIn("delta", self.page, "значок изменения цены на месте")

    def test_export_writes_file(self):
        res = share.export(str(self.dir / "избранное"), con())
        self.assertTrue(res["path"].endswith(".html"), f"расширение дописано: {res['path']}")
        self.assertEqual(res["objects"], 2)
        self.assertGreater(len(Path(res["path"]).read_text(encoding="utf-8")), 10_000, "файл не пустой")


class NothingLiked(Case):
    def test_empty_page_explains(self):
        c = con()
        db.upsert(c, item("cian", "9"))
        c.commit()
        c.close()
        self.assertFalse(share.has_liked(con()))
        page = share.page(con())
        self.assertIn("В избранном пусто", page)
        self.assertNotIn('<article class="card', page)
        self.assertEqual(page.count("<script>"), 1, "на пустой странице скрипту нечего делать — остаётся только снятие класса nojs")
        self.assertNotIn("https://cian.test/9", page, "без сердечек в файл не попадает ничего")


if __name__ == "__main__":
    unittest.main()
