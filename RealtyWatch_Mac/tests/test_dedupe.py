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


class ManualMerge(Case):
    """Ручное объединение несовместимых карточек, мульти-слияние и устойчивость при assign/rebuild."""

    def test_manual_merge_same_and_cross_site_survives_rebuild_and_assign(self):
        a = add("cian", 42.0, 9_000_000, floors=5, floor=2, rooms=1, desc="Первая")
        b = add("cian", 68.0, 14_000_000, floors=12, floor=7, rooms=3, desc="Вторая")
        c = add("avito", 68.2, 14_100_000, floors=12, floor=7, rooms=3, desc="Автодубль второй")
        self.assertFalse(together(a, b))

        con = db.connect()
        dedupe.rebuild(con)
        res = dedupe.merge_objects(con, a, b)
        self.assertEqual(res["manual_merges"], 1)
        self.assertEqual(res["items"], 3)

        by_group, primaries = dedupe.active_manual_merges(con)
        self.assertEqual(len(by_group), 1)
        self.assertEqual(primaries, {tuple(a.split(":"))})

        # Пересборка по правилам НЕ разрушает ручное объединение
        self.assertEqual(dedupe.rebuild(con), 1)
        rows = {(r["source"], r["source_id"]): r["object_id"]
                for r in con.execute("SELECT source, source_id, object_id FROM listings")}
        self.assertEqual(len(set(rows.values())), 1)

        # Новое авто-совпадение со второй веткой (b/c) при assign() присоединяется к той же группе и совпадает с rebuild()
        d = add("yandex", 68.1, 14_050_000, floors=12, floor=7, rooms=3, desc="Новый автодубль b")
        con.execute("UPDATE listings SET object_id=NULL WHERE source=? AND source_id=?", tuple(d.split(":")))
        dedupe.assign(con, *d.split(":"))
        con.commit()
        oid_after_assign = {r["object_id"] for r in con.execute("SELECT object_id FROM listings")}
        self.assertEqual(len(oid_after_assign), 1)

        # Последовательный assign() с нуля даёт ровно те же object_id, что rebuild()
        con.execute("UPDATE listings SET object_id=NULL")
        for r in con.execute("SELECT source, source_id FROM listings ORDER BY first_seen, source, source_id").fetchall():
            dedupe.assign(con, r["source"], r["source_id"])
        con.commit()
        seq = {(r["source"], r["source_id"]): r["object_id"]
               for r in con.execute("SELECT source, source_id, object_id FROM listings")}
        dedupe.rebuild(con)
        fast = {(r["source"], r["source_id"]): r["object_id"]
                for r in con.execute("SELECT source, source_id, object_id FROM listings")}
        self.assertEqual(seq, fast)
        con.close()

    def test_multi_merge_primary_order_and_unmerge_all(self):
        a = add("cian", 40.0, 8_000_000, floors=5, floor=1, rooms=1, desc="A")
        b = add("avito", 55.0, 11_000_000, floors=9, floor=4, rooms=2, desc="B")
        c = add("yandex", 75.0, 15_000_000, floors=12, floor=8, rooms=3, desc="C")
        d = add("cian", 90.0, 19_000_000, floors=16, floor=10, rooms=4, desc="D")

        con = db.connect()
        dedupe.rebuild(con)
        # (A + B) + C -> первенство остаётся за A
        r1 = dedupe.merge_objects(con, a, b)
        r2 = dedupe.merge_objects(con, r1["object_id"], c)
        self.assertEqual(r2["manual_merges"], 1)
        self.assertEqual(dedupe.active_manual_merges(con)[1], {tuple(a.split(":"))})

        # D + (A + B + C) -> D становится первой и главной
        r3 = dedupe.merge_objects(con, d, r2["object_id"])
        self.assertEqual(r3["manual_merges"], 1)
        self.assertEqual(r3["items"], 4)
        self.assertEqual(dedupe.active_manual_merges(con)[1], {tuple(d.split(":"))})
        self.assertEqual(con.execute("SELECT COUNT(DISTINCT object_id) FROM listings").fetchone()[0], 1)

        # Полный сброс возвращает все 4 несовместимые квартиры в отдельные карточки
        un = dedupe.unmerge_all(con)
        self.assertEqual(un["unmerged"], 1)
        self.assertEqual(un["objects"], 4)
        self.assertEqual(con.execute("SELECT COUNT(DISTINCT object_id) FROM listings").fetchone()[0], 4)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM manual_merges").fetchone()[0], 0)
        con.close()

    def test_unmerge_single_object_keeps_other_merges(self):
        a = add("cian", 40.0, 8_000_000, floors=5, floor=1, rooms=1, desc="A")
        b = add("avito", 55.0, 11_000_000, floors=9, floor=4, rooms=2, desc="B")
        c = add("yandex", 75.0, 15_000_000, floors=12, floor=8, rooms=3, desc="C")
        d = add("cian", 90.0, 19_000_000, floors=16, floor=10, rooms=4, desc="D")

        con = db.connect()
        dedupe.rebuild(con)
        r_ab = dedupe.merge_objects(con, a, b)
        r_cd = dedupe.merge_objects(con, c, d)
        self.assertEqual((r_cd["objects"], r_cd["manual_merges"]), (2, 2))

        res = dedupe.unmerge_object(con, r_ab["object_id"])
        self.assertEqual((res["parts"], res["objects"], res["manual_merges"]), (2, 3, 1))
        self.assertFalse(together(a, b), "первая связка разошлась на исходные карточки")
        self.assertTrue(together(c, d), "вторая ручная связка осталась нетронутой")
        con.close()

    def test_exclude_auto_group_keeps_remaining_merged_and_reassigns_primary(self):
        # Группа A — две авто-склеенные ссылки (a1 + a2), где a1 является главной (is_primary=1)
        a1 = add("cian", 42.0, 9_000_000, floors=5, floor=2, rooms=1, desc="A1")
        a2 = add("avito", 42.1, 9_050_000, floors=5, floor=2, rooms=1, desc="A2")
        b = add("yandex", 64.0, 13_000_000, floors=9, floor=5, rooms=2, desc="B")
        c = add("cian", 85.0, 17_000_000, floors=12, floor=8, rooms=3, desc="C")

        con = db.connect()
        dedupe.rebuild(con)
        self.assertTrue(together(a1, a2))

        r1 = dedupe.merge_objects(con, a1, b)
        r2 = dedupe.merge_objects(con, r1["object_id"], c)
        self.assertEqual((r2["objects"], r2["manual_merges"]), (1, 1))
        self.assertEqual(dedupe.active_manual_merges(con)[1], {tuple(a1.split(":"))})

        # Исключаем a2: отделяется ВСЯ авто-группа A (a1 + a2 вместе), B + C остаются объединены вручную
        ex1 = dedupe.exclude_from_merge(con, r2["object_id"], *a2.split(":"))
        self.assertTrue(ex1["still_merged"])
        self.assertEqual(ex1["excluded_count"], 2)
        self.assertEqual(set(ex1["excluded_keys"]), {a1, a2})
        self.assertEqual((ex1["objects"], ex1["manual_merges"]), (2, 1))
        self.assertTrue(together(a1, a2) and together(b, c) and not together(a1, b))

        _, primaries = dedupe.active_manual_merges(con)
        self.assertTrue(primaries <= {tuple(b.split(":")), tuple(c.split(":"))})
        self.assertEqual(len(primaries), 1, "оставшейся связке B+C автоматически назначена новая главная ссылка")

        # Исключаем C из оставшейся пары B+C: остаётся 1 исходная группа B -> ручное объединение снимается полностью
        ex2 = dedupe.exclude_from_merge(con, ex1["remaining_object_id"], *c.split(":"))
        self.assertFalse(ex2["still_merged"])
        self.assertEqual((ex2["objects"], ex2["manual_merges"]), (3, 0))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM manual_merges").fetchone()[0], 0)
        con.close()

    def test_targeted_ops_match_full_rebuild_and_scoped_auto_group_map(self):
        """Точечные merge/exclude/unmerge и scoped auto_group_map полностью совпадают с полным rebuild(con)."""
        a1 = add("cian", 42.0, 9_000_000, floors=5, floor=2, rooms=1, desc="A1")
        a2 = add("avito", 42.2, 9_050_000, floors=5, floor=2, rooms=1, desc="A2")
        # Несвязанный сосед на том же (rooms=1, floor=2) с другой площадью — не должен затрагиваться или меняться
        nbr = add("yandex", 58.0, 12_000_000, floors=5, floor=2, rooms=1, desc="Сосед")
        b1 = add("yandex", 64.0, 13_000_000, floors=9, floor=5, rooms=2, desc="B1")
        c1 = add("cian", 64.1, 13_100_000, floors=9, floor=5, rooms=2, lat=44.499, lon=34.180, desc="C1 другой адрес")
        d1 = add("cian", 85.0, 17_000_000, floors=12, floor=8, rooms=3, desc="D1")

        con = db.connect()
        dedupe.rebuild(con)

        def snapshot_oids():
            return {(r["source"], r["source_id"]): r["object_id"]
                    for r in con.execute("SELECT source, source_id, object_id FROM listings")}

        r1 = dedupe.merge_objects(con, a1, b1)
        r2 = dedupe.merge_objects(con, r1["object_id"], c1)
        r3 = dedupe.merge_objects(con, r2["object_id"], d1)
        after_merge = snapshot_oids()
        dedupe.rebuild(con)
        self.assertEqual(after_merge, snapshot_oids())

        full_ag = dedupe.auto_group_map(con)
        subset = {tuple(a1.split(":")), tuple(a2.split(":")), tuple(c1.split(":")), tuple(d1.split(":"))}
        scoped_ag = dedupe.auto_group_map(con, target_keys=subset)
        self.assertEqual(scoped_ag, {k: full_ag[k] for k in subset})

        ex = dedupe.exclude_from_merge(con, r3["object_id"], *a2.split(":"))
        after_excl = snapshot_oids()
        dedupe.rebuild(con)
        self.assertEqual(after_excl, snapshot_oids())
        self.assertEqual(after_excl[tuple(nbr.split(":"))], nbr)

        dedupe.unmerge_object(con, ex["remaining_object_id"])
        after_unm = snapshot_oids()
        dedupe.rebuild(con)
        self.assertEqual(after_unm, snapshot_oids())
        con.close()
