"""Автообновление: занятый сайт пропускает запуск, остальные стартуют по расписанию; уведомления не ждут
медленный сайт; предупреждение, если сайт раз за разом не укладывается в шаг. Режим «по расписанию»:
время с привязкой к дням недели и пропущенные запуски."""
import os
import threading
import time
import unittest
from datetime import datetime, timedelta

os.environ["RW_NO_SCHEDULER"] = "1"

from app import db, runner, scheduler  # noqa: E402
from app.scrapers.base import Scraper  # noqa: E402
from tests.support import isolate  # noqa: E402

STEP = 1.0                              # шаг расписания, с
DUR = {"cian": 0.2, "yandex": 0.2, "avito": 2.4}  # Авито не укладывается в шаг


def fake(name):
    class F(Scraper):
        def listings(self):
            time.sleep(DUR[self.name])
            return iter(())

        def close(self):
            pass
    F.name = name
    return F


class Schedule(unittest.TestCase):
    def test_one_snapshot_per_launch(self):
        """Запуск — одна точка графика, сколько бы сайтов в нём ни было."""
        isolate(self)
        for s in DUR:
            runner.SCRAPERS[s] = fake(s)
        m = runner.RunManager()
        m._log = m.log = lambda src, msg: None
        for _ in range(2):
            m.start(list(DUR))
            while m.any_running():
                time.sleep(0.05)
        c = db.connect()
        self.assertEqual(c.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 6)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 2,
                         "два запуска по три сайта — две точки, а не шесть")

    def test_cadence_skip_notify_overrun(self):
        d = isolate(self)
        scheduler.FILE = d / "schedule.json"
        for s in DUR:
            runner.SCRAPERS[s] = fake(s)
        m = runner.RunManager()
        journal, notes, starts = [], [], {s: [] for s in DUR}
        m._log = lambda src, msg: journal.append(msg)
        m.log = lambda src, msg: journal.append(msg)
        m.on_launch_done.append(lambda run_ids, sources, auto: notes.append((round(time.time() - t0, 1), sorted(sources))))
        orig = m.start

        def rec(sources, auto=False):
            got = orig(sources, auto)
            for s in got:
                starts[s].append(time.time() - t0)
            return got
        m.start = rec
        sc = scheduler.Scheduler(m)
        sc.update({"enabled": True, "sources": list(DUR)})
        sc.cfg["hours"] = STEP / 3600
        t0 = time.time()
        sc.cfg["next_run"] = datetime.now().isoformat(timespec="seconds")
        while time.time() - t0 < 9.5:
            sc._tick()
            time.sleep(0.1)
        while m.any_running():
            time.sleep(0.1)

        def at(s):
            """Времена запусков с сотыми — в сообщение об ошибке."""
            return [round(x, 2) for x in starts[s]]

        # Быстрые сайты — каждый шаг; Авито — только когда закончил прошлый сбор. Времена точные:
        # округление до секунд превращало верный промежуток 2.5 в «2» и роняло тест на правильном поведении.
        self.assertGreaterEqual(len(starts["cian"]), 8)
        self.assertTrue(all(b - a <= 2 * STEP + 0.5 for a, b in zip(starts["cian"], starts["cian"][1:])), at("cian"))
        self.assertLessEqual(len(starts["avito"]), 4)
        self.assertTrue(all(b - a >= DUR["avito"] for a, b in zip(starts["avito"], starts["avito"][1:])), at("avito"))
        # быстрые сайты уведомляют без Авито
        self.assertTrue(any(s == ["cian", "yandex"] for _, s in notes), notes)
        self.assertTrue(any(s == ["avito"] for _, s in notes), notes)
        # пропуски видны, после трёх неуложившихся сборов — предупреждение
        self.assertTrue(any("ещё собирают с прошлого раза — Авито" in j for j in journal))
        self.assertTrue(any(j.startswith("⚠ Авито: 3 сбора подряд не уложились в шаг") for j in journal), journal)
        self.assertIn("avito", sc.get()["overrun"])


class FakeManager:
    """Планировщику нужен только запуск сборов и журнал — настоящий RunManager здесь ни к чему."""

    def __init__(self):
        self.started, self.journal, self.status = [], [], {}

    def start(self, sources, auto=False):
        self.started.append(list(sources))
        return list(sources)

    def running(self, source):
        return False

    def flush_partial(self):
        pass

    def log(self, src, msg):
        self.journal.append(msg)


WORK = {"time": "09:00", "days": [1, 2, 3, 4, 5]}   # по будням в 9:00
REST = {"time": "11:00", "days": [6, 7]}            # по выходным в 11:00
MON = datetime(2026, 11, 9)                         # понедельник
FRI, SAT, SUN = datetime(2026, 11, 13), datetime(2026, 11, 14), datetime(2026, 11, 15)


class Weekly(unittest.TestCase):
    """Автообновление по расписанию: время с привязкой к дням недели и пропущенные запуски."""

    def setUp(self):
        d = isolate(self)
        scheduler.FILE = d / "schedule.json"
        self.m = FakeManager()
        self.sc = scheduler.Scheduler(self.m)

    def plan(self, **kw):
        self.sc.update(dict({"enabled": True, "mode": "weekly", "slots": [WORK, REST], "sources": ["cian"]}, **kw))
        return self.sc

    def test_next_slot_knows_days_of_week(self):
        self.plan()
        nxt = lambda t: self.sc.next_slot(t)
        self.assertEqual(nxt(MON.replace(hour=8)), MON.replace(hour=9), "ближайшее время сегодня")
        self.assertEqual(nxt(MON.replace(hour=9)), MON.replace(hour=9) + timedelta(days=1),
                         "ровно в это время — уже следующий день, иначе запуск повторится")
        self.assertEqual(nxt(FRI.replace(hour=10)), SAT.replace(hour=11), "пятница прошла — суббота по выходному времени")
        self.assertEqual(nxt(SUN.replace(hour=12)), MON.replace(hour=9) + timedelta(days=7), "воскресенье прошло — снова будни")

    def test_every_other_day(self):
        self.sc.update({"enabled": True, "mode": "weekly", "slots": [{"time": "08:30", "days": [1, 3, 5]}]})
        self.assertEqual(self.sc.next_slot(MON.replace(hour=9)), MON.replace(hour=8, minute=30) + timedelta(days=2))

    def test_slots_are_checked(self):
        self.sc.update({"slots": [{"time": "9:5", "days": [3, 1, 1]}, {"time": "25:00", "days": [1]},
                                  {"time": "08:00", "days": []}, {"time": "нет", "days": [1]},
                                  "мусор", {"time": "07:00", "days": [1, 9]}, {"time": "09:05", "days": [1, 3]}]})
        self.assertEqual(self.sc.cfg["slots"], [{"time": "07:00", "days": [1]}, {"time": "09:05", "days": [1, 3]}],
                         "кривое отброшено, повтор убран, дни и порядок приведены в норму")
        self.sc.update({"slots": [{"time": f"{h:02d}:00", "days": [1]} for h in range(24)] * 2})
        self.assertLessEqual(len(self.sc.cfg["slots"]), scheduler.MAX_SLOTS)

    def test_enabling_plans_the_next_slot(self):
        self.plan()
        nxt = datetime.fromisoformat(self.sc.cfg["next_run"])
        self.assertGreater(nxt, datetime.now())
        self.assertIn(f"{nxt:%H:%M}", ("09:00", "11:00"))
        self.assertIn(nxt.isoweekday(), WORK["days"] if f"{nxt:%H:%M}" == "09:00" else REST["days"],
                      "время и день взяты из одного пункта расписания")

    def test_due_run_starts_and_plans_the_following(self):
        self.plan()
        self.sc.cfg["next_run"] = datetime.now().isoformat(timespec="seconds")
        self.sc._tick()
        self.assertEqual(self.m.started, [["cian"]])
        self.assertGreater(datetime.fromisoformat(self.sc.cfg["next_run"]), datetime.now())
        self.assertTrue(any("Следующий запуск" in j for j in self.m.journal), self.m.journal)

    def test_missed_slot_is_skipped_without_catch_up(self):
        self.plan(catch_up=False)
        missed = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        self.sc.cfg["next_run"] = missed
        self.sc._tick()
        self.assertEqual(self.m.started, [], "программа не работала в это время — запуск не догоняется")
        self.assertNotEqual(self.sc.cfg["next_run"], missed)
        self.assertGreater(datetime.fromisoformat(self.sc.cfg["next_run"]), datetime.now())
        self.assertTrue(any("пропущен" in j for j in self.m.journal), self.m.journal)

    def test_missed_slot_runs_with_catch_up(self):
        self.plan(catch_up=True)
        self.sc.cfg["next_run"] = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
        self.sc._tick()
        self.assertEqual(self.m.started, [["cian"]], "с галочкой пропущенный запуск догоняется")

    def test_small_delay_is_still_that_run(self):
        self.plan(catch_up=False)
        self.sc.cfg["next_run"] = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
        self.sc._tick()
        self.assertEqual(self.m.started, [["cian"]], "опоздание в пять минут — это тот же запуск, а не пропущенный")

    def test_restart_after_missed_time(self):
        self.plan(catch_up=False)
        self.sc.cfg["next_run"] = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
        self.sc._save()
        again = scheduler.Scheduler(self.m)
        self.assertGreater(datetime.fromisoformat(again.cfg["next_run"]), datetime.now() + timedelta(minutes=30),
                           "после запуска программы ждём следующего времени расписания, а не стартуем сразу")
        self.sc.cfg["catch_up"] = True
        self.sc._save()
        soon = scheduler.Scheduler(self.m)
        self.assertLess(datetime.fromisoformat(soon.cfg["next_run"]), datetime.now() + timedelta(minutes=5),
                        "с галочкой — сбор вскоре после старта")

    def test_timer_mode_keeps_its_own_behaviour(self):
        self.sc.update({"enabled": True, "mode": "timer", "hours": 6, "sources": ["cian"]})
        self.sc.cfg["next_run"] = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
        self.sc._save()
        again = scheduler.Scheduler(self.m)
        self.assertLess(datetime.fromisoformat(again.cfg["next_run"]), datetime.now() + timedelta(minutes=5),
                        "по таймеру пропущенное время по-прежнему догоняется вскоре после старта")
        again.cfg["next_run"] = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
        again._tick()
        self.assertEqual(self.m.started, [["cian"]], "и опоздавший запуск по таймеру не пропускается")

    def test_switching_modes_replans(self):
        self.plan()
        weekly_next = self.sc.cfg["next_run"]
        self.sc.update({"mode": "timer"})
        self.assertNotEqual(self.sc.cfg["next_run"], weekly_next)
        self.sc.update({"mode": "weekly"})
        self.assertEqual(self.sc.cfg["next_run"], weekly_next, "вернулись к расписанию — то же ближайшее время")
        self.sc.update({"slots": []})
        self.assertIsNone(self.sc.cfg["next_run"], "пустое расписание ничего не запускает")

    def test_when_reads_like_a_human(self):
        now = datetime.now()
        self.assertEqual(scheduler.when(now.replace(hour=21, minute=0).isoformat()), "в 21:00")
        self.assertTrue(scheduler.when((now + timedelta(days=1)).isoformat()).startswith("завтра в"))
        self.assertIsNotNone(scheduler.when(None))
