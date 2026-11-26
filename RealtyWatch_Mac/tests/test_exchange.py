"""Тесты Этапа 1: формат .rwcards.zip, слой «Импорт», свежесть (stale),
дедупликация повторов, многопользовательские заметки и изоляция imported_only."""
import asyncio
import json
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app import db, dedupe, exchange, history, images, main, offsale, paths, purge, server, settings, transfer
from app.scrapers.cian import Cian
from tests.support import Case, cian_offer, con, listings, run


def _fake_cian(rows):
    class C(Cian):
        def _load(self, page, repair=None):
            if repair:
                return [], 0
            return [cian_offer(r) for r in rows[(page - 1) * 28: page * 28]], len(rows)

        def verify(self, row):
            return (None, None, None)

    return C


def _insert_listing(
    c,
    source,
    source_id,
    *,
    rooms=2,
    area=52.0,
    floor=4,
    floors=9,
    price=10_500_000,
    lat=44.4838,
    lon=34.1433,
    zone="A",
    address="Ялта, ул. Чехова, 10",
    title=None,
    description="Квартира с евроремонтом",
    renovation="euro",
    active=1,
    first_seen="2026-11-18T10:00:00",
    last_seen="2026-11-18T10:00:00",
    removed_at=None,
    removed_reason=None,
    raw=None,
    flags=None,
    excluded=None,
    imported_only=0,
    cluster_key=None,
):
    c.execute(
        """INSERT INTO listings(
               source, source_id, url, title, price, price_m2, rooms, area, floor, floors,
               address, lat, lon, zone, renovation, description, flags, excluded,
               cluster_key, raw, first_seen, last_seen, active, removed_at, removed_reason, imported_only
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            source,
            str(source_id),
            f"https://example.test/{source}/{source_id}",
            title or f"{rooms}-к. квартира, {area:g} м², {floor}/{floors} эт.",
            price,
            round(price / area) if price and area else None,
            rooms,
            area,
            floor,
            floors,
            address,
            lat,
            lon,
            zone,
            renovation,
            description,
            json.dumps(flags or {}, ensure_ascii=False),
            excluded,
            cluster_key,
            json.dumps(raw or {}, ensure_ascii=False),
            first_seen,
            last_seen,
            active,
            removed_at,
            removed_reason,
            imported_only,
        ),
    )
    if price:
        c.execute("INSERT INTO price_history VALUES(?,?,?,?)", (source, str(source_id), first_seen, price))


class ExchangeTestBase(Case):
    """Позволяет в одном тесте переключать изолированные виртуальные компьютеры (A, B, C, D)."""

    def setUp(self):
        super().setUp()
        self._devices = {}

    @contextmanager
    def device(self, name, *, device_name=None, filters=None, zone=None):
        d = self.dir / f"dev_{name}"
        d.mkdir(exist_ok=True)
        prev = (
            db.DB_FILE,
            settings.SETTINGS_FILE,
            images.IMAGES,
            transfer.IMAGES,
            transfer.BACKUPS,
            transfer.DB_DIR,
            exchange.IMPORT_IMAGES,
        )
        db.DB_FILE = d / "realty.db"
        db.reset()
        settings.SETTINGS_FILE = d / "settings.json"
        images.IMAGES = transfer.IMAGES = d / "images"
        transfer.BACKUPS, transfer.DB_DIR = d / "backups", d
        exchange.IMPORT_IMAGES = None
        if not settings.SETTINGS_FILE.exists():
            cfg = settings.load()
            if device_name:
                cfg["lan"]["device_name"] = device_name
            if filters:
                cfg["filters"].update(filters)
            if zone:
                cfg["zone"].update(zone)
            settings.save(cfg)
        elif device_name or filters or zone:
            cfg = settings.load()
            if device_name:
                cfg["lan"]["device_name"] = device_name
            if filters:
                cfg["filters"].update(filters)
            if zone:
                cfg["zone"].update(zone)
            settings.save(cfg)
        try:
            yield d
        finally:
            (
                db.DB_FILE,
                settings.SETTINGS_FILE,
                images.IMAGES,
                transfer.IMAGES,
                transfer.BACKUPS,
                transfer.DB_DIR,
                exchange.IMPORT_IMAGES,
            ) = prev
            db.reset()


class DeviceIdentityAndExport(ExchangeTestBase):
    def test_device_identity_persistence_and_settings_guard(self):
        with self.device("A"):
            c = con()
            ident1 = settings.ensure_device_identity(c)
            self.assertTrue(ident1["device_id"])
            self.assertTrue(ident1["device_name"])
            meta_id = c.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()[0]
            c.close()
            self.assertEqual(meta_id, ident1["device_id"])

            # При повторном вызове идентификатор не меняется
            ident2 = settings.ensure_device_identity()
            self.assertEqual(ident2["device_id"], ident1["device_id"])

            # Клиентский POST /api/settings не должен затирать UUID пустой строкой
            saved = server.set_settings({"lan": {"enabled": True, "device_name": "Ноут Риелтора", "device_id": ""}})
            self.assertEqual(saved["lan"]["device_id"], ident1["device_id"])
            self.assertEqual(saved["lan"]["device_name"], "Ноут Риелтора")
            self.assertTrue(saved["lan"]["enabled"])

        with self.device("B"):
            ident_b = settings.ensure_device_identity()
            self.assertNotEqual(ident_b["device_id"], ident1["device_id"])

    def test_export_selected_and_favorites_bundle_structure(self):
        bundle_fav = self.dir / "fav.rwcards.zip"
        bundle_sel = self.dir / "sel.rwcards.zip"
        with self.device("A", device_name="Комп А"):
            c = con()
            with self.assertRaises(ValueError):
                exchange.export_cards_zip(bundle_fav, liked_only=True, con=c)
            with self.assertRaises(ValueError):
                exchange.export_cards_zip(bundle_fav, object_ids=[], con=c)

            _insert_listing(
                c, "cian", "1001", price=10_000_000, raw={"apartments": True, "huge_dump": "x" * 5000},
                flags={"encumbrance": ["залог"]},
            )
            _insert_listing(
                c, "avito", "2001", price=10_200_000, address="Ялта, ул. Морская, 5",
                first_seen="2026-11-18T11:00:00", last_seen="2026-11-18T11:00:00",
            )
            _insert_listing(c, "yandex", "3001", rooms=3, area=70.0, floor=7, price=12_000_000)
            c.execute("INSERT INTO manual_merges VALUES('cian','1001','g1',1,'2026-11-18T12:00:00')")
            c.execute("INSERT INTO manual_merges VALUES('avito','2001','g1',0,'2026-11-18T12:00:00')")
            c.execute("INSERT INTO likes VALUES('cian','1001','2026-11-18T12:05:00')")
            c.execute("INSERT INTO notes VALUES('cian','1001','Светлая, балкон на море','2026-11-18T12:06:00')")
            c.commit()
            dedupe.rebuild(c)
            merged_oid = c.execute("SELECT object_id FROM listings WHERE source='cian' AND source_id='1001'").fetchone()[0]
            other_oid = c.execute("SELECT object_id FROM listings WHERE source='yandex' AND source_id='3001'").fetchone()[0]
            c.close()

            img_file = images.IMAGES / "cian" / "1001.jpg"
            img_file.parent.mkdir(parents=True, exist_ok=True)
            img_file.write_bytes(b"FAKEJPEG" * 300)

            res = exchange.export_cards_zip(bundle_fav, liked_only=True, kind="favorites")
            self.assertEqual((res["cards"], res["listings"], res["images"]), (1, 2, 1))
            with zipfile.ZipFile(bundle_fav) as z:
                names = set(z.namelist())
                self.assertIn("manifest.json", names)
                self.assertIn("cards.json", names)
                self.assertIn("images/cian/1001.jpg", names)
                manifest = json.loads(z.read("manifest.json").decode("utf-8"))
                self.assertEqual(manifest["format"], exchange.FORMAT)
                self.assertEqual(manifest["version"], 1)
                self.assertEqual(manifest["sender"]["device_name"], "Комп А")
                cards = json.loads(z.read("cards.json").decode("utf-8"))["cards"]
                self.assertEqual(len(cards), 1)
                c0 = cards[0]
                self.assertEqual(c0["note"], "Светлая, балкон на море")
                self.assertTrue(c0["manual_merged"])
                self.assertEqual(len(c0["manual_merges_rows"]), 2)
                self.assertTrue(c0["listing_rows"][0]["apartments"])
                self.assertNotIn("raw", c0["listing_rows"][0])
                self.assertEqual(len(c0["price_history_rows"]), 2)

            res2 = exchange.export_cards_zip(bundle_sel, object_ids=[other_oid])
            self.assertEqual(res2["cards"], 1)
            self.assertNotEqual(merged_oid, other_oid)


class ImportStaleAndDedupe(ExchangeTestBase):
    def test_import_into_empty_db_reclassifies_zone_and_excluded_and_merges(self):
        bundle = self.dir / "export_a.rwcards.zip"
        with self.device("A", device_name="Отправитель А", filters={"area_min": 30, "price_max": 20_000_000}):
            c = con()
            _insert_listing(c, "cian", "11", rooms=1, area=35.0, floor=2, price=14_500_000, lat=44.4838, lon=34.1433, zone="A")
            _insert_listing(c, "cian", "12", rooms=2, area=55.0, floor=5, price=11_000_000, lat=44.4838, lon=34.1433, zone="A")
            _insert_listing(c, "avito", "13", rooms=2, area=55.0, floor=6, price=11_200_000, address="Ялта, ул. Игнатенко, 3", zone="A")
            c.execute("INSERT INTO manual_merges VALUES('cian','12','m1',1,'2026-11-18T10:00:00')")
            c.execute("INSERT INTO manual_merges VALUES('avito','13','m1',0,'2026-11-18T10:00:00')")
            c.commit()
            dedupe.rebuild(c)
            c.close()
            (images.IMAGES / "cian").mkdir(parents=True, exist_ok=True)
            (images.IMAGES / "cian" / "12.webp").write_bytes(b"WEBP" * 400)
            exchange.export_cards_zip(bundle)

        # На компьютере B круг сдвинут в сторону (Ливадия/Гаспра), а фильтры жёстче (от 40 м² до 13 млн)
        with self.device("B", device_name="Получатель B", zone={"center": [44.4500, 34.1200], "radius_m": 400}):
            res = exchange.import_cards_zip(bundle, likes_mode="all")
            self.assertFalse(res["skipped_empty"])
            self.assertEqual(res["new_cards"], 2)
            self.assertIsNotNone(images.local_path("cian", "12"))

            c = con()
            rows = {r["source_id"]: dict(r) for r in c.execute("SELECT * FROM listings")}
            self.assertEqual(all(r["imported_only"] == 1 for r in rows.values()), True)
            # Точка 44.4838, 34.1433 далеко от круга B, но в Ялте → зона B
            self.assertEqual(rows["12"]["zone"], "B")
            # 11-е объявление 35 м² и 14.5 млн отсеивается фильтрами получателя
            self.assertIn("площадь", rows["11"]["excluded"])
            self.assertIn("цена", rows["11"]["excluded"])
            self.assertIsNone(rows["12"]["excluded"])
            # Ручное объединение перенеслось
            self.assertEqual(rows["12"]["object_id"], rows["13"]["object_id"])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM manual_merges").fetchone()[0], 2)
            c.close()

    def test_all_three_stale_cases_boundaries_and_multisite(self):
        bundle = self.dir / "stale_mix.rwcards.zip"
        with self.device("A", device_name="Комп А", filters={"area_min": 30, "price_max": 25_000_000}):
            c = con()
            # Каждая отдельная квартира имеет уникальный (floor, area, rooms), чтобы авто-склейка не смешала тесты
            # Case 1: будет снят в базе B позже (15:00 > 10:00)
            _insert_listing(c, "cian", "101", rooms=1, area=41.0, floor=1, price=9_000_000, last_seen="2026-11-18T10:00:00")
            # Case 1 inverse: на B снято в 10:00, но у A свежее (18:00) и живо
            _insert_listing(c, "cian", "102", rooms=2, area=46.0, floor=2, price=9_500_000, last_seen="2026-11-18T18:00:00")
            # Case 2: нет у B, подходит под фильтры B, у B будет свежий полный прогон ЦИАН в 14:00
            _insert_listing(c, "cian", "201", rooms=2, area=54.0, floor=4, price=11_000_000, last_seen="2026-11-18T10:00:00")
            # Вне фильтров B (16 млн > 13 млн) — не должен стать stale по Case 2
            _insert_listing(c, "cian", "202", rooms=3, area=72.0, floor=5, price=16_000_000, last_seen="2026-11-18T10:00:00")
            # Низкое покрытие Яндекс у B (50/100 < 95%) — не должен стать stale
            _insert_listing(c, "yandex", "203", rooms=2, area=58.0, floor=6, price=11_200_000, last_seen="2026-11-18T10:00:00")
            # Case 3: уже снято у отправителя A
            _insert_listing(c, "avito", "301", rooms=1, area=43.0, floor=7, price=8_800_000, active=0, removed_at="2026-11-18T09:00:00", removed_reason="снято продавцом", last_seen="2026-11-18T09:00:00")
            # Мультисайтовая частично снятая (ЦИАН жив и свежее прогона B 14:00, Авито снято) -> карточка актуальна!
            _insert_listing(c, "cian", "401", rooms=3, area=68.0, floor=3, price=11_500_000, cluster_key="ck1", last_seen="2026-11-18T18:00:00")
            _insert_listing(c, "avito", "402", rooms=3, area=68.0, floor=3, price=11_700_000, active=0, removed_reason="ушло", last_seen="2026-11-18T09:00:00")
            c.execute("INSERT INTO manual_merges VALUES('cian','401','g_multi',1,'2026-11-18T10:00:00')")
            c.execute("INSERT INTO manual_merges VALUES('avito','402','g_multi',0,'2026-11-18T10:00:00')")
            # Мультисайтовая полностью устаревшая (оба сайта неактуальны)
            _insert_listing(c, "cian", "403", rooms=1, area=49.0, floor=8, price=9_500_000, last_seen="2026-11-18T10:00:00")
            _insert_listing(c, "avito", "404", rooms=1, area=49.0, floor=8, price=9_500_000, active=0, last_seen="2026-11-18T09:00:00")
            c.execute("INSERT INTO manual_merges VALUES('cian','403','g_dead',1,'2026-11-18T10:00:00')")
            c.execute("INSERT INTO manual_merges VALUES('avito','404','g_dead',0,'2026-11-18T10:00:00')")
            c.execute("INSERT INTO notes VALUES('cian','201','Отличная планировка','2026-11-18T10:30:00')")
            for sid in ("101", "102", "201", "202", "203", "401", "403"):
                c.execute("INSERT INTO likes VALUES(?,?,?)", ("yandex" if sid == "203" else "cian", sid, "2026-11-18T10:30:00"))
            c.execute("INSERT INTO likes VALUES('avito','301','2026-11-18T10:30:00')")
            c.commit()
            dedupe.rebuild(c)
            c.close()

            (images.IMAGES / "cian").mkdir(parents=True, exist_ok=True)
            (images.IMAGES / "cian" / "201.jpg").write_bytes(b"STALEIMG" * 200)
            exchange.export_cards_zip(bundle)

        with self.device("B", device_name="Комп B"):
            c = con()
            _insert_listing(c, "cian", "101", rooms=1, area=41.0, floor=1, price=9_000_000, active=0, removed_at="2026-11-18T15:00:00", removed_reason="продана", last_seen="2026-11-18T12:00:00")
            _insert_listing(c, "cian", "102", rooms=2, area=46.0, floor=2, price=9_500_000, active=0, removed_at="2026-11-18T10:00:00", removed_reason="временно снято", last_seen="2026-11-18T09:00:00")
            # Свежий качественный прогон ЦИАН в 14:00 (95/100 >= 85%)
            c.execute(
                "INSERT INTO runs(source,mode,started,finished,status,declared,collected) VALUES('cian','fast','2026-11-18T13:50:00','2026-11-18T14:00:00','done',100,95)"
            )
            # Просевший прогон Яндекс в 14:00 (50/100 < 95%)
            c.execute(
                "INSERT INTO runs(source,mode,started,finished,status,declared,collected) VALUES('yandex','fast','2026-11-18T13:50:00','2026-11-18T14:00:00','done',100,50)"
            )
            c.commit()
            c.close()

            prev = exchange.preview_cards_zip(bundle)
            self.assertEqual(prev["stale_cards"], 4)   # 101, 201, 301, (403+404)
            self.assertEqual(prev["new_cards"], 3)     # 202 (дорогая), 203 (слабый прогон), (401+402 мультисайт)
            self.assertEqual(prev["matched_cards"], 1) # 102 (оживилось более свежей датой)

            res = exchange.import_cards_zip(bundle, likes_mode="all")
            self.assertEqual(res["stale_cards"], prev["stale_cards"])
            self.assertEqual(res["new_cards"], prev["new_cards"])
            self.assertEqual(res["matched_cards"], prev["matched_cards"])

            c = con()
            sids = {r["source_id"]: dict(r) for r in c.execute("SELECT * FROM listings")}
            # Case 2 и Case 3 не попали в listings
            self.assertNotIn("201", sids)
            self.assertNotIn("301", sids)
            self.assertNotIn("403", sids)
            # Case 1 остался active=0 и не получил лайк
            self.assertEqual(sids["101"]["active"], 0)
            # Case 1 inverse ожил
            self.assertEqual(sids["102"]["active"], 1)
            # 202 (вне лимита цены), 203 (ненадёжный прогон Яндекса), 401 (мультисайт с живым ЦИАН) добавились
            self.assertIn("202", sids)
            self.assertIn("203", sids)
            self.assertEqual(sids["401"]["active"], 1)
            self.assertEqual(sids["402"]["active"], 0)

            liked_ids = {r["source_id"] for r in c.execute("SELECT source_id FROM likes")}
            self.assertTrue({"102", "202", "203", "401"}.issubset(liked_ids))
            self.assertTrue({"101", "201", "301", "403"}.isdisjoint(liked_ids))

            # Проверка import_images, get_batch_detail и /img/import/{source}/{source_id}
            self.assertIsNotNone(exchange.import_image_path("cian", "201"))
            detail = exchange.get_batch_detail(c, res["batch_id"])
            self.assertEqual(detail["counts"]["stale"], 4)
            self.assertEqual(detail["counts"]["relevant"], 4)
            c201 = next(x for x in detail["cards"] if x["export_card_id"] == "cian:201")
            self.assertTrue(c201["is_stale"])
            self.assertEqual(c201["items"][0]["photo_url"], "/img/import/cian/201")
            self.assertEqual(c201["imported_notes"][0]["text"], "Отличная планировка")
            c.close()

            resp = server.import_image("cian", "201")
            self.assertEqual(Path(resp.path).name, "201.jpg")
            self.assertEqual(server.import_image("cian", "999999").status_code, 404)

    def test_repeat_import_30_to_40_and_notes_updates_and_100pct_duplicate(self):
        b1 = self.dir / "b30.rwcards.zip"
        b2 = self.dir / "b40.rwcards.zip"
        b3 = self.dir / "b40_notes.rwcards.zip"
        b4 = self.dir / "b40_clear.rwcards.zip"

        with self.device("A", device_name="Комп А"):
            c = con()
            for i in range(30):
                sid = str(1000 + i)
                _insert_listing(c, "cian", sid, rooms=1 + i % 3, area=42.0 + i, price=9_000_000 + i * 50_000)
                c.execute("INSERT INTO likes VALUES('cian', ?, '2026-11-18T10:00:00')", (sid,))
            c.commit()
            dedupe.rebuild(c)
            c.close()
            exchange.export_cards_zip(b1, liked_only=True)

        with self.device("B", device_name="Комп B"):
            r1 = exchange.import_cards_zip(b1, likes_mode="all")
            self.assertEqual(r1["new_cards"], 30)
            self.assertEqual(r1["duplicate_cards"], 0)
            self.assertEqual(r1["updated_notes"], 0)

        with self.device("A"):
            c = con()
            for i in range(30, 40):
                sid = str(1000 + i)
                _insert_listing(c, "cian", sid, rooms=1 + i % 3, area=42.0 + i, price=9_000_000 + i * 50_000)
                c.execute("INSERT INTO likes VALUES('cian', ?, '2026-11-18T12:00:00')", (sid,))
            c.commit()
            dedupe.rebuild(c)
            c.close()
            exchange.export_cards_zip(b2, liked_only=True)

        with self.device("B"):
            p2 = exchange.preview_cards_zip(b2)
            self.assertEqual((p2["total_cards"], p2["duplicate_cards"], p2["new_cards"]), (40, 30, 10))
            r2 = exchange.import_cards_zip(b2, likes_mode="all")
            self.assertEqual((r2["total_cards"], r2["duplicate_cards"], r2["new_cards"]), (40, 30, 10))

            # Повторный импорт тех же 40 карточек -> 100% дубль, пустой батч НЕ создаётся
            p_dup = exchange.preview_cards_zip(b2)
            self.assertTrue(p_dup["all_duplicates"])
            r_dup = exchange.import_cards_zip(b2, likes_mode="all")
            self.assertTrue(r_dup["skipped_empty"])
            self.assertIsNone(r_dup["batch_id"])
            c = con()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0], 2)
            c.close()

        # Изменяем/добавляем заметки на 2 карточках из 40 на А
        with self.device("A"):
            c = con()
            c.execute("INSERT INTO notes VALUES('cian','1000','Смотреть первым','2026-11-18T14:00:00')")
            c.execute("INSERT INTO notes VALUES('cian','1001','Южная сторона','2026-11-18T14:00:00')")
            c.commit()
            c.close()
            exchange.export_cards_zip(b3, liked_only=True)

        with self.device("B"):
            r3 = exchange.import_cards_zip(b3, likes_mode="all")
            self.assertFalse(r3["skipped_empty"])
            self.assertEqual((r3["duplicate_cards"], r3["new_cards"], r3["updated_notes"]), (38, 0, 2))
            c = con()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0], 3)
            self.assertEqual(c.execute("SELECT text FROM imported_notes WHERE source='cian' AND source_id='1000'").fetchone()[0], "Смотреть первым")
            c.close()

            # Повторный импорт b3 снова пропускается как 100%-дубль
            self.assertTrue(exchange.import_cards_zip(b3)["skipped_empty"])

        # Очищаем одну заметку на А и переэкспортируем -> заметка удаляется у B
        with self.device("A"):
            c = con()
            c.execute("DELETE FROM notes WHERE source='cian' AND source_id='1000'")
            c.commit()
            c.close()
            exchange.export_cards_zip(b4, liked_only=True)

        with self.device("B"):
            r4 = exchange.import_cards_zip(b4, likes_mode="all")
            self.assertEqual(r4["updated_notes"], 1)
            c = con()
            self.assertIsNone(c.execute("SELECT 1 FROM imported_notes WHERE source='cian' AND source_id='1000'").fetchone())
            self.assertEqual(c.execute("SELECT text FROM imported_notes WHERE source='cian' AND source_id='1001'").fetchone()[0], "Южная сторона")
            c.close()


class MultiuserNotesLikesIsolationAndApi(ExchangeTestBase):
    def test_multiuser_notes_api_forwarding_and_transfer_merge(self):
        bundle_a = self.dir / "from_a.rwcards.zip"
        bundle_c = self.dir / "from_c.rwcards.zip"
        bundle_b_fwd = self.dir / "from_b.rwcards.zip"
        full_zip = self.dir / "full_b.zip"

        with self.device("A", device_name="Комп А"):
            c = con()
            _insert_listing(c, "cian", "555")
            c.execute("INSERT INTO likes VALUES('cian','555','2026-11-18T10:00:00')")
            c.execute("INSERT INTO notes VALUES('cian','555','Заметка А: хороший вид','2026-11-18T10:05:00')")
            c.commit()
            dedupe.rebuild(c)
            c.close()
            exchange.export_cards_zip(bundle_a, liked_only=True)

        with self.device("C", device_name="Комп В"):
            c = con()
            _insert_listing(c, "cian", "555")
            c.execute("INSERT INTO likes VALUES('cian','555','2026-11-18T11:00:00')")
            c.execute("INSERT INTO notes VALUES('cian','555','Заметка В: торг до 10 млн','2026-11-18T11:05:00')")
            c.commit()
            dedupe.rebuild(c)
            c.close()
            exchange.export_cards_zip(bundle_c, liked_only=True)

        with self.device("B", device_name="Комп Б"):
            c = con()
            _insert_listing(c, "cian", "555")
            c.execute("INSERT INTO notes VALUES('cian','555','Моя личная заметка','2026-11-18T09:00:00')")
            c.commit()
            dedupe.rebuild(c)
            c.close()

            exchange.import_cards_zip(bundle_a)
            exchange.import_cards_zip(bundle_c)

            items = server.listings()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["note"], "Моя личная заметка")
            imp = items[0]["imported_notes"]
            self.assertEqual({n["origin_name"]: n["text"] for n in imp}, {
                "Комп А": "Заметка А: хороший вид",
                "Комп В": "Заметка В: торг до 10 млн",
            })
            c_chk = con()
            by_keys = server._listings_for_keys(c_chk, [("cian", "555")])
            c_chk.close()
            self.assertEqual(len(by_keys), 1)
            self.assertEqual(len(by_keys[0]["imported_notes"]), 2)

            # Пересылка с Компа Б на Комп Д переносит личную заметку Б + цепочку imported_notes
            exchange.export_cards_zip(bundle_b_fwd)
            transfer.export_zip(full_zip)

        with self.device("D", device_name="Комп Д"):
            exchange.import_cards_zip(bundle_b_fwd)
            c = con()
            names_on_d = {r["origin_name"]: r["text"] for r in c.execute("SELECT origin_name, text FROM imported_notes")}
            c.close()
            self.assertEqual(names_on_d, {
                "Комп Б": "Моя личная заметка",
                "Комп А": "Заметка А: хороший вид",
                "Комп В": "Заметка В: торг до 10 млн",
            })

        with self.device("E", device_name="Комп Е"):
            c = con()
            _insert_listing(c, "cian", "555", imported_only=0)
            c.commit()
            dedupe.rebuild(c)
            c.close()
            transfer.import_zip(full_zip, mode="merge")
            c = con()
            self.assertEqual(c.execute("SELECT imported_only FROM listings WHERE source_id='555'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM imported_notes").fetchone()[0], 2)
            c.close()

    def test_likes_modes_all_preserve_none(self):
        bundle = self.dir / "likes.rwcards.zip"
        with self.device("A", device_name="Комп А"):
            c = con()
            _insert_listing(c, "cian", "1", rooms=1, area=42.0, floor=2)
            _insert_listing(c, "cian", "2", rooms=2, area=55.0, floor=5)
            _insert_listing(c, "avito", "3", rooms=3, area=70.0, floor=8, active=0, removed_reason="ушло")
            c.execute("INSERT INTO likes VALUES('cian','1','2026-11-18T10:00:00')")
            c.commit()
            dedupe.rebuild(c)
            c.close()
            exchange.export_cards_zip(bundle)

        for mode, expected in (
            ("all", {"90", "1", "2"}),
            ("preserve", {"90", "1"}),
            ("none", {"90"}),
        ):
            with self.device(f"B_{mode}"):
                c = con()
                _insert_listing(c, "cian", "90", rooms=2, area=60.0, floor=9)
                _insert_listing(c, "cian", "1", rooms=1, area=42.0, floor=2)
                c.execute("INSERT INTO likes VALUES('cian','90','2026-11-18T08:00:00')")
                c.execute("INSERT INTO dislikes VALUES('cian','1','2026-11-18T08:00:00')")
                c.commit()
                c.close()
                exchange.import_cards_zip(bundle, likes_mode=mode)
                c = con()
                got = {r["source_id"] for r in c.execute("SELECT source_id FROM likes")}
                c.close()
                self.assertEqual(got, expected, mode)

    def test_imported_only_isolation_and_api_lifecycle(self):
        bundle = self.dir / "isolated.rwcards.zip"
        with self.device("A", device_name="Комп А", filters={"price_max": 30_000_000}):
            c = con()
            # Два разных объекта по ценам-выбросам 25 млн и 24 млн — не должны искажать медиану на B
            _insert_listing(c, "cian", "8000001", rooms=2, area=50.0, floor=4, price=25_000_000)
            _insert_listing(c, "cian", "8000002", rooms=3, area=75.0, floor=7, price=24_000_000)
            c.commit()
            dedupe.rebuild(c)
            c.close()
            res_exp = server.cards_export({"path": str(bundle)})
            self.assertEqual(res_exp["cards"], 2)

        with self.device("B", device_name="Комп B"):
            base_rows = listings("cian", 120)
            run("cian", _fake_cian(base_rows))
            c = con()
            snap_before = json.loads(c.execute("SELECT data FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()[0])
            med_before = snap_before["city"]["median_price"]
            c.close()

            prev = server.imports_preview({"path": str(bundle)})
            self.assertEqual(prev["new_cards"], 2)
            imp = server.imports_apply({"path": str(bundle), "likes_mode": "preserve"})
            self.assertEqual(imp["new_cards"], 2)

            # Второй полный прогон с 120 базовыми: imported_only не учитываются в mass_limit / db.missing / offsale
            log = run("cian", _fake_cian(base_rows))
            self.assertFalse(any("массов" in m for m in log), log)
            c = con()
            rows_imp = {r["source_id"]: dict(r) for r in c.execute("SELECT * FROM listings WHERE source_id IN ('8000001','8000002')")}
            self.assertEqual(rows_imp["8000001"]["active"], 1)
            self.assertEqual(rows_imp["8000001"]["imported_only"], 1)
            self.assertEqual(rows_imp["8000002"]["active"], 1)
            self.assertEqual(rows_imp["8000002"]["imported_only"], 1)

            # История и дашборд игнорируют imported_only=1
            history.rebuild_snapshots(c)
            snap_after = json.loads(c.execute("SELECT data FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()[0])
            self.assertEqual(snap_after["city"]["count"], 120)
            self.assertEqual(snap_after["city"]["median_price"], med_before)
            dash = history.dashboard(c)
            self.assertEqual(dash["sources"]["cian"]["total"], 120)
            c.close()

            # Очистка периода НЕ удаляет imported_only=1
            purge.purge("2020-01-01", "2030-01-01")
            c = con()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM listings WHERE imported_only=1").fetchone()[0], 2)

            # Локальный скрапер находит 8000001 -> imported_only сбрасывается в 0
            c.execute("UPDATE listings SET last_seen='2026-11-18T09:00:00' WHERE source_id='8000001'")
            db.upsert(c, {**base_rows[0], "source": "cian", "source_id": "8000001", "price": 11_000_000})
            c.commit()
            self.assertEqual(c.execute("SELECT imported_only FROM listings WHERE source_id='8000001'").fetchone()[0], 0)

            # Батчи и delete_batch через API
            batches = server.imports_list()
            self.assertEqual(len(batches), 1)
            detail = server.imports_detail(batches[0]["id"])
            self.assertEqual(len(detail["cards"]), 2)
            server.imports_delete(batches[0]["id"])
            self.assertEqual(server.imports_list(), [])
            with self.assertRaises(HTTPException) as ctx:
                server.imports_detail(batches[0]["id"])
            self.assertEqual(ctx.exception.status_code, 404)
            # После delete_batch объявление осталось в listings
            self.assertIsNotNone(c.execute("SELECT 1 FROM listings WHERE source_id='8000002'").fetchone())

            # clear_all очищает журналы импорта и import_images, сохраняя sync_peers
            c.execute("INSERT INTO sync_peers(device_id,name) VALUES('peer-1','Mac')")
            c.commit()
            c.close()
            (exchange.import_images_dir() / "cian").mkdir(parents=True, exist_ok=True)
            (exchange.import_images_dir() / "cian" / "stale.jpg").write_bytes(b"123")
            purge.clear_all()
            c = con()
            self.assertEqual(c.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM import_items").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM imported_notes").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sync_peers").fetchone()[0], 1)
            c.close()
            self.assertFalse((exchange.import_images_dir() / "cian" / "stale.jpg").exists())

            # При активном сборе /api/imports/apply возвращает 409, а неверный ZIP -> 400
            bad_zip = self.dir / "bad.zip"
            bad_zip.write_text("not a zip", encoding="utf-8")
            with self.assertRaises(HTTPException) as ctx400:
                server.imports_preview({"path": str(bad_zip)})
            self.assertEqual(ctx400.exception.status_code, 400)
            with patch.object(server.manager, "any_running", return_value=True):
                with self.assertRaises(HTTPException) as ctx409:
                    server.imports_apply({"path": str(bundle)})
                self.assertEqual(ctx409.exception.status_code, 409)

            schema = server.app.openapi()
            for ep in ("/api/cards/export", "/api/exports/download", "/api/imports/upload",
                       "/api/imports/preview", "/api/imports/apply",
                       "/api/imports", "/api/imports/{batch_id}", "/api/imports/{batch_id}/delete",
                       "/img/import/{source}/{source_id}"):
                self.assertIn(ep, schema["paths"])


class Stage2UiAndUploadTests(ExchangeTestBase):
    def test_stage2_ui_wiring_and_acts_order(self):
        html = (paths.WEB / "index.html").read_text(encoding="utf-8")
        js = (paths.WEB / "app.js").read_text(encoding="utf-8")
        dash = (paths.WEB / "dash.js").read_text(encoding="utf-8")
        css = (paths.WEB / "app.css").read_text(encoding="utf-8")

        # Навигация и экран «Импорт»
        self.assertIn('data-view="imports"', html)
        self.assertIn('id="impBadge"', html)
        self.assertIn('id="view-imports"', html)
        self.assertLess(html.index('data-view="runs"'), html.index('data-view="imports"'))
        self.assertLess(html.index('data-view="imports"'), html.index('data-view="settings"'))

        # Меню «Экспорт» и выделение карточек (порядок кнопок в .acts сохранён)
        self.assertIn('id="btnShare"', html)
        self.assertIn('id="expMenu"', html)
        self.assertIn('data-exp="file"', html)
        self.assertIn('data-exp="lan"', html)
        self.assertIn('data-exp="html"', html)
        acts = js.split('<span class="acts">')[1].split("</span>")[0]
        self.assertLess(acts.index("export-btn"), acts.index("unmerge-btn"))
        self.assertLess(acts.index("unmerge-btn"), acts.index("merge-btn"))
        self.assertLess(acts.index("merge-btn"), acts.index("note-btn"))

        self.assertIn("EXPORT_PICK", js)
        self.assertIn("syncExportPickUI", js)
        self.assertIn("exportCardsToLan", js)
        self.assertIn("cardImpNotesHtml", js)
        self.assertIn('class="card-imp-notes"', js)
        self.assertIn(".export-bar", css)
        self.assertIn("data-export=", dash)
        self.assertIn("m-imp-notes", dash)
        self.assertIn("clearExportPick()", dash)

    def test_browser_upload_staging_download_and_desktop_api(self):
        bundle = self.dir / "stage2_b.rwcards.zip"
        with self.device("A", device_name="Комп А"):
            c = con()
            _insert_listing(c, "cian", "701", rooms=2, area=55.0, floor=4, price=10_500_000)
            c.execute("INSERT INTO likes VALUES('cian','701','2026-11-18T10:00:00')")
            c.execute("INSERT INTO notes VALUES('cian','701','Тестовая заметка А','2026-11-18T10:05:00')")
            c.commit()
            dedupe.rebuild(c)
            c.close()

            # DesktopApi.export_cards с мокнутым системным диалогом
            api_desk = main.DesktopApi()
            out_path = self.dir / "desk_out"
            with patch.object(main.DesktopApi, "_dialog", return_value=str(out_path)):
                dres = api_desk.export_cards(liked_only=True, kind="favorites")
            self.assertEqual(dres["cards"], 1)
            self.assertTrue(Path(dres["path"]).is_file())
            self.assertTrue(dres["path"].endswith(".rwcards.zip"))
            bundle = Path(dres["path"])

            with patch.object(main.DesktopApi, "_dialog", return_value=None):
                self.assertTrue(api_desk.export_cards(liked_only=True)["cancelled"])
            with patch.object(main.DesktopApi, "_dialog", return_value=str(bundle)):
                self.assertEqual(api_desk.pick_cards_import()["path"], str(bundle))

        with self.device("B", device_name="Комп B") as dev_b:
            raw = bundle.read_bytes()

            class FakeReq:
                def __init__(self, payload, hdrs=None):
                    self._payload = payload
                    self.headers = hdrs or {}

                async def body(self):
                    return self._payload

            up = asyncio.run(server.imports_upload(FakeReq(raw), name="test_cards.rwcards.zip"))
            self.assertTrue(Path(up["path"]).is_file())
            self.assertEqual(up["preview"]["new_cards"], 1)
            self.assertEqual(up["preview"]["origin_name"], "Комп А")

            # Невалидный ZIP -> 400 без мусора в staging
            before_files = set(exchange.import_staging_dir().iterdir())
            with self.assertRaises(HTTPException) as ctx400:
                asyncio.run(server.imports_upload(FakeReq(b"bad"), name="broken.rwcards.zip"))
            self.assertEqual(ctx400.exception.status_code, 400)
            self.assertEqual(set(exchange.import_staging_dir().iterdir()), before_files)

            # Проверка exports_download (в изолированной exports-папке dev_b)
            exp_dir = dev_b / "exports"
            exp_dir.mkdir(exist_ok=True)
            (exp_dir / "sample.rwcards.zip").write_bytes(raw)
            with patch.object(paths, "EXPORTS", exp_dir):
                resp = server.exports_download("sample.rwcards.zip")
                self.assertEqual(Path(resp.path).name, "sample.rwcards.zip")
                with self.assertRaises(HTTPException) as ctx_bad:
                    server.exports_download("../secret.zip")
                self.assertEqual(ctx_bad.exception.status_code, 400)
                with self.assertRaises(HTTPException) as ctx_miss:
                    server.exports_download("missing.rwcards.zip")
                self.assertEqual(ctx_miss.exception.status_code, 404)
