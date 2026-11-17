"""Операции с данными: очистка за период, по отдельным прогонам и полностью, резервные копии
(список, удаление, восстановление), новый круг с пересчётом зон и истории, «последнее обновление»
на экране изменений, срез «внутри круга» в снимках рынка, звук."""
import json
from pathlib import Path

from app import alerts, db, dedupe, history, images, paths, purge, runlog, server, transfer, trash
from app.scrapers.cian import Cian
from tests.support import Case, cian_offer, con, counts, listings, run

GOOD, BAD = "2026-10-20T10:00:00", "2026-10-22T10:00:00"


def cian(rows, verify=None):
    class C(Cian):
        def _load(self, page, repair=None):
            if repair:  # выдачи по виду ремонта в этих тестах пустые
                return [], 0
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
        self.assertEqual(c.execute("SELECT COUNT(*) FROM listings WHERE excluded IS NOT NULL").fetchone()[0], 0,
                         "отсев пересчитан: с возвращённой ценой объявления снова проходят")
        self.assertTrue(all(json.loads(r[0])["city"]["count"] == 120 for r in c.execute("SELECT data FROM snapshots")),
                        "в точках графика — весь рынок, а не только непострадавшие объявления")

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


class PickRuns(Case):
    """Удаление отдельных прогонов: за день могло быть пять сборов, из них четыре неудачных."""

    def setUp(self):
        super().setUp()
        self.rows = listings("cian", 120)
        run("cian", cian(self.rows))                     # хороший сбор 20 октября
        stamp(GOOD, "2026-11")
        self.bogus = listings("cian", 10, seed=7)        # неудачный сбор 22 октября
        for i, r in enumerate(self.bogus):
            r["source_id"] = str(90_000_000 + i)
        bad = [dict(r, price=r["price"] // 2) if i < 5 else r for i, r in enumerate(self.rows[:-5])] + self.bogus
        gone = {r["source_id"] for r in self.rows[-5:]}
        run("cian", cian(bad, verify=lambda r: ("removed", "тест", None) if r["source_id"] in gone else (None, None, None)))
        stamp(BAD, "2026-11")
        c = con()
        # в том же запуске отработал Яндекс — и остановился на полпути, своих объявлений не записав
        self.yandex = c.execute("INSERT INTO runs(source, mode, started, finished, status, found, message) "
                                "VALUES('yandex','http',?,?,'stopped',0,'остановлено пользователем')", (BAD, BAD)).lastrowid
        c.commit()
        self.good, self.bad = 1, 2

    def test_launches_group_sites_and_mark_failures(self):
        groups = purge.launches(con())
        self.assertEqual([len(g["runs"]) for g in groups], [2, 1], "новые запуски сверху, сайты запуска вместе")
        self.assertTrue(groups[0]["bad"], "в запуске есть остановленный прогон — он неудачный")
        self.assertFalse(groups[1]["bad"])
        self.assertEqual(sorted(r["source"] for r in groups[0]["runs"]), ["cian", "yandex"])
        self.assertEqual(groups[1]["collected"], 120, "сколько собрал каждый сайт — из самого прогона")
        self.assertEqual(purge.launches(con(), "2026-10-21", None)[0]["started"], BAD, "период отбирает запуски")

    def test_delete_bad_run_restores_good_state_and_keeps_point(self):
        before = {r["source_id"]: r["price"] for r in self.rows}
        prev = purge.runs_preview(con(), [self.bad])
        self.assertEqual((prev["runs"], prev["listings"], prev["unremoved"]), (1, 10, 5))
        self.assertEqual(prev["snapshots"], 0, "от запуска остаётся прогон Яндекса — точка графика никуда не денется")
        res = purge.purge_runs([self.bad])
        self.assertTrue(res["backup"].endswith(".zip"))
        c = con()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM listings").fetchone()[0], 120, "чужие объявления удалены")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM listings WHERE active=0").fetchone()[0], 0, "снятия отменены")
        self.assertEqual({r["source_id"]: r["price"] for r in c.execute("SELECT source_id, price FROM listings")}, before)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (self.bad,)).fetchone()[0], 0)
        self.assertEqual([r["id"] for r in c.execute("SELECT id FROM runs ORDER BY id")], [self.good, self.yandex])
        self.assertEqual([r["run_id"] for r in c.execute("SELECT run_id FROM snapshots ORDER BY ts")],
                         [self.good, self.yandex], "точка запуска переехала на оставшийся прогон")
        self.assertTrue(all(json.loads(r[0])["city"]["count"] == 120 for r in c.execute("SELECT data FROM snapshots")),
                        "точки пересчитаны по базе после удаления")

    def test_delete_whole_launch_removes_its_point(self):
        prev = purge.runs_preview(con(), [self.bad, self.yandex])
        self.assertEqual(prev["snapshots"], 1, "удаляют весь запуск — точка графика исчезает")
        purge.purge_runs([self.bad, self.yandex])
        c = con()
        self.assertEqual([r["id"] for r in c.execute("SELECT id FROM runs")], [self.good])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)

    def test_run_log_goes_with_the_run(self):
        self.assertIsNotNone(runlog.run_log_path(self.bad), "журнал неудачного прогона записан")
        res = purge.purge_runs([self.bad])
        self.assertGreaterEqual(res["logs"], 1)
        self.assertIsNone(runlog.run_log_path(self.bad), "подробный журнал удалён вместе с прогоном")
        self.assertIsNotNone(runlog.run_log_path(self.good), "журнал хорошего прогона остался")

    def test_api_checks_input(self):
        self.assertEqual(server.db_runs_delete({"ids": [self.bad], "dry_run": True})["listings"], 10)
        self.assertEqual(con().execute("SELECT COUNT(*) FROM listings").fetchone()[0], 130, "примерка ничего не меняет")
        for body in ({"ids": []}, {"ids": ["не число"]}, {"ids": [99999]}):
            with self.assertRaises(Exception):
                server.db_runs_delete(body)


class Backups(Case):
    """Резервные копии: сколько их, удаление в корзину и восстановление."""

    def setUp(self):
        super().setUp()
        real = trash._to_trash                  # в тестах — обычное удаление: корзину хозяина не трогаем
        trash._to_trash = lambda files: False
        self.addCleanup(setattr, trash, "_to_trash", real)
        run("cian", cian(listings("cian", 20)))

    def test_list_counts_and_contents(self):
        img = self.dir / "images" / "cian"
        img.mkdir(parents=True, exist_ok=True)
        (img / "1.webp").write_bytes(b"\x00" * 1024)
        transfer.backup("перед_очисткой")
        transfer.backup("перед_полной_очисткой", with_images=True)
        b = transfer.backups()
        self.assertEqual(b["count"], 2)
        self.assertEqual(b["items"][0]["label"], "перед полной очисткой", "в имени — повод и дата")
        self.assertTrue(all(i["ok"] for i in b["items"]), "в копии есть база — восстановить можно")
        self.assertEqual([i["images"] for i in b["items"]], [True, False])
        self.assertGreater(b["bytes"], 0)
        self.assertEqual(b["folder"], str(transfer.BACKUPS))

    def test_delete_all_and_one(self):
        first = transfer.backup("перед_очисткой")
        transfer.backup("перед_импортом")
        res = transfer.delete_backups([Path(first).name])
        self.assertEqual((res["deleted"], res["left"], res["trash"]), (1, 1, False))
        self.assertFalse(Path(first).exists())
        res = transfer.delete_backups()
        self.assertEqual((res["deleted"], res["left"]), (1, 0))
        self.assertEqual(transfer.delete_backups()["deleted"], 0, "удалять больше нечего")

    def test_only_own_backups(self):
        for name in ("../realty.db", "нет_такой.zip", "перед_очисткой_2026-01-01_1200.txt"):
            with self.assertRaises(ValueError):
                transfer.backup_path(name)

    def test_restore_undoes_a_purge(self):
        c = con()
        c.execute("INSERT INTO runs(source, mode, started, finished, status) VALUES('cian','http',?,?,'done')", (BAD, BAD))
        c.commit()
        res = purge.purge(BAD[:10], BAD[:10])             # очистка сама делает копию «перед очисткой»
        self.assertEqual(res["runs"], 1)
        name = Path(res["backup"]).name
        back = transfer.restore(name)
        self.assertEqual(back["restored"], name)
        self.assertEqual(back["added"], 20, "объявления из копии вернулись")
        self.assertEqual(con().execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2, "и удалённый прогон тоже")
        self.assertTrue(any(i["label"] == "перед восстановлением" for i in transfer.backups()["items"]),
                        "нынешнее состояние перед заменой тоже ушло в копию")

    def test_restore_rejects_foreign_name(self):
        with self.assertRaises(ValueError):
            transfer.restore("нет_такой.zip")


class ImportAndImagesCache(Case):
    """Импорт архива сохраняет статистику прогонов и пересчитывает снимки/правила; кэш фото находит файлы."""

    def test_existing_set_matches_server_listings(self):
        run("cian", cian(listings("cian", 3)))
        rows = server.listings()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(not r["has_img"] for r in rows))
        img_dir = images.IMAGES / "cian"
        img_dir.mkdir(parents=True, exist_ok=True)
        first = rows[0]
        (img_dir / f"{first['source_id']}.webp").write_bytes(b"\x00" * 2048)
        self.assertIn((first["source"], first["source_id"]), images.existing_set())
        after = {r["source_id"]: r["has_img"] for r in server.listings()}
        self.assertTrue(after[first["source_id"]])

    def test_import_merge_keeps_run_metrics_and_rebuilds_snapshots(self):
        run("cian", cian(listings("cian", 5)))
        c = con()
        c.execute("UPDATE runs SET declared=50, collected=5, requests=3")
        c.commit()
        zip_path = self.dir / "export.zip"
        transfer.export_zip(zip_path)
        purge.clear_all()
        res = transfer.import_zip(zip_path, mode="merge")
        self.assertEqual(res["added"], 5)
        c2 = con()
        r = c2.execute("SELECT declared, collected, requests FROM runs").fetchone()
        self.assertEqual((r["declared"], r["collected"], r["requests"]), (50, 5, 3))
        meta = c2.execute("SELECT value FROM meta WHERE key='dedupe_rules'").fetchone()
        self.assertEqual(meta["value"], str(dedupe.RULES_VERSION))
        self.assertGreaterEqual(c2.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)


class ListingsPayloadAndNotes(Case):
    """Облегчённый /api/listings (apartments вместо raw) и личные заметки к квартире (API, очистка, перенос)."""

    def test_listings_drops_raw_and_exposes_apartments_and_notes(self):
        c = con()
        db.upsert(c, {
            "source": "cian", "source_id": "101", "url": "https://cian.test/101", "title": "1-к., 40 м²",
            "price": 9_000_000, "rooms": 1, "area": 40.0, "floor": 2, "floors": 5,
            "address": "Ялта, ул. Морская, 1", "raw": {"isApartments": True, "repair": 2, "huge": "x" * 500},
        })
        db.upsert(c, {
            "source": "avito", "source_id": "202", "url": "https://avito.test/202", "title": "1-к., 40 м²",
            "price": 9_100_000, "rooms": 1, "area": 40.0, "floor": 2, "floors": 5,
            "address": "Ялта, ул. Морская, 1", "raw": {"apartments": False},
        })
        c.commit()
        dedupe.rebuild(c)
        c.close()

        rows = {f"{r['source']}:{r['source_id']}": r for r in server.listings()}
        self.assertNotIn("raw", rows["cian:101"])
        self.assertTrue(rows["cian:101"]["apartments"])
        self.assertFalse(rows["avito:202"]["apartments"])

        # Сохранение и очистка заметки
        server.note_set({"keys": ["cian:101", "avito:202"], "text": "  Вид во двор, торг до 8,8  "})
        rows2 = {f"{r['source']}:{r['source_id']}": r for r in server.listings()}
        self.assertEqual(rows2["cian:101"]["note"], "Вид во двор, торг до 8,8")
        self.assertEqual(rows2["avito:202"]["note"], "Вид во двор, торг до 8,8")

        # Экспорт → полная очистка → слияние сохраняет заметку
        zip_path = self.dir / "with-note.zip"
        transfer.export_zip(zip_path, with_images=False)
        purge.clear_all()
        c = con()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 0)
        c.close()
        transfer.import_zip(zip_path, mode="merge")
        rows3 = {f"{r['source']}:{r['source_id']}": r for r in server.listings()}
        self.assertEqual(rows3["cian:101"]["note"], "Вид во двор, торг до 8,8")

        # Пустой текст удаляет заметку
        server.note_set({"keys": ["cian:101", "avito:202"], "text": ""})
        self.assertEqual([r["note"] for r in server.listings()], ["", ""])
