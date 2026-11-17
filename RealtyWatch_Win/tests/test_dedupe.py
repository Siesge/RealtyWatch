"""Объединение объявлений одной квартиры в карточку. Строгие правила прежние; мягкое совпадение — для
вторичного жилья на разных сайтах, которые расходятся в площади (с балконом и без) или в этажности дома.
База — во временной папке, данные синтетические (по образцу квартиры на Украинской, 1 из базы владельца)."""
from app import db, dedupe, geo
from tests.support import Case

N = [0]


def add(source, area, price, floors=4, floor=3, rooms=2, lat=44.48835, lon=34.13875, desc="Квартира", year=1969,
        new=0, first_seen=None):
    N[0] += 1
    sid = str(1000 + N[0])
    con = db.connect()
    con.execute("INSERT INTO listings(source, source_id, url, title, price, rooms, area, floor, floors, lat, lon, "
                "description, build_year, is_new_building, first_seen, last_seen, active, missed) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)",
                (source, sid, f"https://example.test/{sid}", "кв", price, rooms, area, floor, floors, lat, lon, desc,
                 year, new, first_seen or f"2026-11-01T10:{N[0]:02d}:00", db.now()))
    con.commit()
    con.close()
    return f"{source}:{sid}"


def objects():
    con = db.connect()
    g = {}
    dedupe.rebuild(con)
    for r in con.execute("SELECT source, source_id, object_id FROM listings"):
        g.setdefault(r["object_id"], set()).add(f"{r['source']}:{r['source_id']}")
    con.close()
    return sorted(sorted(v) for v in g.values())


def together(*keys):
    return any(set(keys) <= set(g) for g in objects())


class SoftMatch(Case):
    def test_flat_from_owners_example_is_one_card(self):
        """Украинская, 1: ЦИАН и Яндекс — 47,7 м², Авито — 45 м² (без балкона), у одного объявления ЦИАН
        этажность 5 вместо 4; старое объявление Авито — 46 м² и 10 млн, до перевыставления. Одна квартира."""
        y = add("yandex", 47.7, 10_500_000)
        c1 = add("cian", 47.7, 10_500_000)
        c2 = add("cian", 47.7, 10_500_000, floors=5)
        a1 = add("avito", 46.0, 10_000_000)
        a2 = add("avito", 45.0, 10_500_000)
        self.assertTrue(together(y, c1, c2, a1, a2), objects())

    def test_area_differs_within_3_m2_across_sites(self):
        a, b = add("cian", 55.1, 12_500_000), add("avito", 57.0, 12_500_000)
        self.assertTrue(together(a, b))

    def test_area_too_far(self):
        a, b = add("cian", 47.0, 12_500_000), add("avito", 51.0, 12_500_000)
        self.assertFalse(together(a, b), "4 м² — уже другая квартира")

    def test_price_must_be_close_for_soft_area(self):
        a, b = add("cian", 55.0, 12_000_000), add("avito", 57.0, 12_500_000)
        self.assertFalse(together(a, b), "разница в площади и цене 4% — мягкое совпадение не годится")

    def test_floors_differ_with_exact_area(self):
        a, b = add("yandex", 48.0, 10_600_000, floors=2, floor=1), add("cian", 48.0, 10_600_000, floors=3, floor=1)
        self.assertTrue(together(a, b), "этажность 2 и 3 при той же площади и цене — сайты расходятся в данных")

    def test_floors_and_area_both_differ(self):
        a, b = add("yandex", 48.0, 10_600_000, floors=2, floor=1), add("cian", 50.0, 10_600_000, floors=3, floor=1)
        self.assertFalse(together(a, b), "два расхождения сразу — не склеиваем")

    def test_not_in_new_buildings(self):
        """В новостройке на одном этаже бывают одинаковые квартиры по одной цене — там только строгие правила."""
        for kw in ({"new": 1}, {"year": 2023}, {"desc": "ЖК у моря, предчистовая отделка"}):
            with self.subTest(**kw):
                a, b = add("cian", 40.0, 11_500_000, **kw), add("avito", 37.9, 11_500_000)
                self.assertFalse(together(a, b))

    def test_not_on_the_same_site(self):
        a, b = add("avito", 55.1, 12_500_000), add("avito", 57.0, 12_500_000)
        self.assertFalse(together(a, b), "на одном сайте разная площадь — скорее разные квартиры")

    def test_other_house(self):
        a = add("cian", 55.1, 12_500_000)
        b = add("avito", 57.0, 12_500_000, lat=44.48835 + 0.0009)  # ~100 м
        self.assertFalse(together(a, b), "мягкое совпадение — только тот же дом (до 60 м)")

    def test_strict_twin_is_preferred(self):
        """Объявление, у которого есть точный дубль, не уходит от него в другую карточку по мягкому совпадению."""
        y = add("yandex", 48.0, 10_600_000, floors=2, floor=1, year=2008)    # по году — «новый» дом
        add("yandex", 48.0, 10_600_000, floors=3, floor=1)
        add("cian", 48.0, 10_600_000, floors=3, floor=1)
        a = add("avito", 48.0, 10_600_000, floors=2, floor=1, first_seen="2026-11-02T10:00:00")
        self.assertTrue(together(y, a), objects())

    def test_rebuild_is_deterministic(self):
        for k in range(4):
            add("cian" if k % 2 else "avito", 47.4 + k, 12_500_000)
        first = objects()
        self.assertEqual(objects(), first)


class RulesVersion(Case):
    def test_startup_rebuilds_when_rules_change(self):
        from app import main
        a, b = add("cian", 55.1, 12_500_000), add("avito", 57.0, 12_500_000)
        con = db.connect()
        con.execute("UPDATE listings SET object_id = source || ':' || source_id")
        con.execute("INSERT OR REPLACE INTO meta VALUES('dedupe_rules', '1')")
        con.commit()
        con.close()
        main._startup_maintenance()
        con = db.connect()
        self.assertEqual(con.execute("SELECT value FROM meta WHERE key='dedupe_rules'").fetchone()[0],
                         str(dedupe.RULES_VERSION))
        self.assertEqual(con.execute("SELECT COUNT(DISTINCT object_id) FROM listings").fetchone()[0], 1,
                         "объекты пересобраны по новым правилам")
        con.close()

    def test_fast_rebuild_matches_sequential_assign(self):
        """Быстрый пересчёт в памяти даёт точно те же object_id, что и последовательный assign()."""
        for i in range(15):
            base_lat, base_lon = 44.4883 + i * 0.003, 34.1387 + (i % 4) * 0.003
            add("cian", 50.0 + i, 10_000_000 + i * 100_000, floors=5, floor=2, rooms=2, lat=base_lat, lon=base_lon)
            add("avito", 51.8 + i, 10_050_000 + i * 100_000, floors=5, floor=2, rooms=2, lat=base_lat, lon=base_lon)
            add("yandex", 50.0 + i, 10_000_000 + i * 100_000, floors=6, floor=2, rooms=2, lat=base_lat, lon=base_lon)
            add("avito", 40.0 + i, 9_000_000, floors=9, floor=1, rooms=1, new=1, year=2022, lat=base_lat, lon=base_lon)
        con = db.connect()
        con.execute("UPDATE listings SET object_id=NULL")
        for r in con.execute("SELECT source, source_id FROM listings ORDER BY first_seen, source, source_id").fetchall():
            dedupe.assign(con, r["source"], r["source_id"])
        con.commit()
        seq = {(r["source"], r["source_id"]): r["object_id"]
               for r in con.execute("SELECT source, source_id, object_id FROM listings")}
        n_obj = dedupe.rebuild(con)
        fast = {(r["source"], r["source_id"]): r["object_id"]
                for r in con.execute("SELECT source, source_id, object_id FROM listings")}
        con.close()
        self.assertEqual(fast, seq)
        self.assertEqual(n_obj, len(set(fast.values())))

    def test_geo_polygon_bbox_matches_full_raycast(self):
        """Быстрое отсечение по ограничивающему прямоугольнику не меняет результат классификации полигона."""
        poly = geo._polygon("yalta_city.json")
        self.assertIsNotNone(poly)
        pts = [(44.495, 34.165), (44.45, 34.12), (44.40, 33.90), (45.0, 35.0), (44.51, 34.20)]
        pb = geo._polygon_with_bbox("yalta_city.json")
        for lat, lon in pts:
            self.assertEqual(geo._in_polygon(lat, lon, poly), geo._in_polygon(lat, lon, *pb))
