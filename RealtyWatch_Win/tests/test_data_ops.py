"""Операции с данными: очистка за период и полностью, новый круг с пересчётом зон и истории,
«последнее обновление» на экране изменений, срез «внутри круга» в снимках рынка, звук."""
import json

from app import alerts, db, history, purge, server
from app.scrapers.cian import Cian
from tests.support import Case, cian_offer, con, counts, listings, run

GOOD, BAD = "2026-10-20T10:00:00", "2026-10-22T10:00:00"


def cian(rows, verify=None):
    class C(Cian):
        def _load(self, page):
            return [cian_offer(r) for r in rows[(page - 1) * 28: page * 28]], len(rows)

        def verify(self, row):
            return verify(row) if verify else (None, None, None)
    return C


def stamp(ts, since):
    """Всё, что записано после since, «случилось» в момент ts (имитация сбора в другой день)."""
    c = con()
    c.execute("UPDATE listings SET first_seen=? WHERE first_seen>=?", (ts, since))
    c.execute("UPDATE listings SET last_seen=? WHERE last_seen>=?", (ts, since))
    c.execute("UPDATE listings SET removed_at=? WHERE removed_at>=?", (ts, since))
    for table, col in (("price_history", "ts"), ("events", "ts"), ("snapshots", "ts"), ("runs", "started"), ("runs", "finished")):
        c.execute(f"UPDATE {table} SET {col}=? WHERE {col}>=?", (ts, since))
    c.commit()


class Purge(Case):
    def setUp(self):
        super().setUp()
        self.rows = listings("cian", 120)
        run("cian", cian(self.rows))                     # хороший сбор 20 октября
        stamp(GOOD, "2026-11")
        # неудачный сбор 22 октября: 10 чужих объявлений, у 5 «съехала» цена, 5 «сняты»
        self.bogus = listings("cian", 10, seed=7)
        for i, r in enumerate(self.bogus):
            r["source_id"] = str(90_000_000 + i)
        bad = [dict(r, price=r["price"] // 2) if i < 5 else r for i, r in enumerate(self.rows[:-5])] + self.bogus
        gone = {r["source_id"] for r in self.rows[-5:]}
        run("cian", cian(bad, verify=lambda r: ("removed", "тест", None) if r["source_id"] in gone else (None, None, None)))
        stamp(BAD, "2026-11")

    def test_preview_counts_without_changes(self):
        c = con()
        p = purge.preview(c, "2026-10-22", "2026-10-22")
        self.assertEqual((p["listings"], p["unremoved"], p["runs"]), (10, 5, 1))
        self.assertEqual(counts("cian")["total"], 130)  # ничего не удалено

    def test_purge_bad_day_restores_good_state(self):
        before = {r["source_id"]: r["price"] for r in self.rows}
        res = purge.purge("2026-10-22", "2026-10-22")
        self.assertTrue(res["backup"].endswith(".zip"))
        c = con()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM listings").fetchone()[0], 120)        # чужие удалены
        self.assertEqual(c.execute("SELECT COUNT(*) FROM listings WHERE active=0").fetchone()[0], 0)  # снятия отменены
        now = {r["source_id"]: r["price"] for r in c.execute("SELECT source_id, price FROM listings")}
        self.assertEqual(now, before)                                                          # цены вернулись
        self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE ts=?", (BAD,)).fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        self.assertTrue(all(json.loads(r[0])["city"]["count"] for r in c.execute("SELECT data FROM snapshots")))

    def test_open_ranges_and_bad_dates(self):
        c = con()
        self.assertEqual(purge.preview(c, None, "2026-10-21")["runs"], 1)   # с начала по 21-е — хороший сбор
        self.assertEqual(purge.preview(c, "2026-10-21", None)["runs"], 1)   # с 21-го по сейчас — неудачный
        with self.assertRaises(ValueError):
            purge.preview(c, "2026-10-23", "2026-10-21")

    def test_clear_all(self):
        res = purge.clear_all()
        self.assertEqual(res["listings"], 130)
        c = con()
        for table in ("listings", "price_history", "events", "snapshots", "runs", "likes"):
            self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)


class ZoneAndChanges(Case):
    def setUp(self):
        super().setUp()
        self.rows = listings("cian", 120)
        run("cian", cian(self.rows))
        run("cian", cian([dict(r, price=r["price"] - 100_000) if i < 3 else r for i, r in enumerate(self.rows)]))

    def test_snapshot_has_zone_a_part(self):
        s = json.loads(con().execute("SELECT data FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(s["a"]["all"]["count"], s["zones"]["A"]["count"])
        self.assertIn("events_a", s)
        self.assertEqual(sum(s["a"]["rooms"][k]["count"] for k in "123"), s["a"]["all"]["count"])

    def test_last_update_and_hours(self):
        c = con()
        last = history.changes(c, last=True)
        self.assertEqual(sorted(e["kind"] for e in last), ["price"] * 3)
        self.assertEqual(len(history.changes(c, hours=6)), 3)
        self.assertEqual([r["source"] for r in history.last_update(c)], ["cian"])

    def test_new_circle_reclassifies_and_rebuilds_history(self):
        res = server.set_zone({"center": [44.4835, 34.1430], "radius_m": 60})  # крошечный круг: почти всё вне
        self.assertGreater(res["moved_out"], 100)
        c = con()
        in_a = c.execute("SELECT COUNT(*) FROM listings WHERE zone='A'").fetchone()[0]
        snap = json.loads(c.execute("SELECT data FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertLess(in_a, 20)
        self.assertEqual(snap["zones"]["A"]["count"], snap["a"]["all"]["count"])
        self.assertLessEqual(snap["a"]["all"]["count"], in_a)
        with self.assertRaises(Exception):
            server.set_zone({"center": [44.48, 34.14], "radius_m": 10})  # слишком маленький радиус


class LaunchPoints(Case):
    """Точка графика — одна на запуск. Снимок после каждого сайта давал три точки подряд, где у одного
    сайта данные уже новые, а у других ещё прошлые, и это выглядело как движение рынка."""

    def setUp(self):
        super().setUp()
        run("cian", cian(listings("cian", 20)))
        stamp("2026-10-20T10:00:00", "2026-11")   # объявления появились раньше точек ниже
        c = con()
        c.execute("DELETE FROM snapshots")        # снимки напишем сами — с временами настоящего запуска
        c.commit()

    def launch(self, started, ends):
        """Как писала прежняя версия: по снимку после каждого сайта одного запуска."""
        c = con()
        for site, end in zip(("cian", "avito", "yandex"), ends):
            rid = c.execute("INSERT INTO runs(source, mode, started, finished, status) VALUES(?,?,?,?,'done')",
                            (site, "http", started, end)).lastrowid
            c.execute("INSERT INTO snapshots(ts, run_id, data) VALUES(?,?,'{}')", (end, rid))
        c.commit()

    def test_sites_of_one_launch_become_one_point(self):
        # 5 ноября Авито закончил на полчаса позже остальных, 6 ноября — на двадцать минут
        self.launch("2026-11-05T22:20:00", ["2026-11-05T22:23:00", "2026-11-05T23:00:00", "2026-11-05T22:24:00"])
        self.launch("2026-11-06T10:20:00", ["2026-11-06T10:23:00", "2026-11-06T10:50:00", "2026-11-06T10:24:00"])
        c = con()
        self.assertEqual(history.merge_launches(c), 4, "из шести снимков остаётся по одному на запуск")
        self.assertEqual([r["ts"] for r in c.execute("SELECT ts FROM snapshots ORDER BY ts")],
                         ["2026-11-05T23:00:00", "2026-11-06T10:50:00"],
                         "точка встаёт на момент, когда отработал последний сайт запуска")
        data = json.loads(c.execute("SELECT data FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()[0])
        self.assertEqual(data["city"]["count"], 20, "в точке — весь рынок, а не часть")
        self.assertEqual(history.merge_launches(c), 0, "сводить больше нечего")


class Sound(Case):
    def test_mute_toggle(self):
        self.assertFalse(alerts.muted())
        self.assertTrue(server.sound_set({"muted": True})["muted"])
        self.assertTrue(alerts.muted())
        self.assertFalse(alerts.captcha())   # без звука сигнал не подаётся


class DashboardPoints(Case):
    def test_period_and_thinning_keep_events(self):
        c = con()
        for i in range(1200):  # 1200 проверок за 50 дней, в каждой одно новое объявление
            ts = f"2026-{9 + (i // 600):02d}-{1 + (i % 600) // 24:02d}T{i % 24:02d}:00:00"
            data = {"city": {"count": i}, "events": {"new": 1}, "events_a": {"new": 1}, "a": {"all": {}}}
            c.execute("INSERT INTO snapshots(ts, run_id, data) VALUES(?,?,?)", (ts, None, json.dumps(data)))
        c.commit()
        d = history.dashboard(c)
        self.assertLessEqual(len(d["snapshots"]), history.MAX_POINTS)
        self.assertEqual(d["checks"], 1200)
        self.assertEqual(sum(s["events"]["new"] for s in d["snapshots"]), 1200)   # события не теряются
        self.assertEqual(d["recent"][-1]["city"]["count"], 1199)                  # последняя проверка — точно
        self.assertEqual(d["snapshots"][-1]["city"]["count"], 1199)
