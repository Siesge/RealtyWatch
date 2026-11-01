"""Автообновление: занятый сайт пропускает запуск, остальные стартуют по расписанию; уведомления не ждут
медленный сайт; предупреждение, если сайт раз за разом не укладывается в шаг."""
import os
import threading
import time
import unittest
from datetime import datetime

os.environ["RW_NO_SCHEDULER"] = "1"

from app import runner, scheduler  # noqa: E402
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
                starts[s].append(round(time.time() - t0))
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

        # быстрые сайты — каждый шаг; Авито — только когда закончил прошлый сбор
        self.assertGreaterEqual(len(starts["cian"]), 8)
        self.assertTrue(all(b - a <= 2 for a, b in zip(starts["cian"], starts["cian"][1:])))
        self.assertLessEqual(len(starts["avito"]), 4)
        self.assertTrue(all(b - a >= 3 for a, b in zip(starts["avito"], starts["avito"][1:])), starts["avito"])
        # быстрые сайты уведомляют без Авито
        self.assertTrue(any(s == ["cian", "yandex"] for _, s in notes), notes)
        self.assertTrue(any(s == ["avito"] for _, s in notes), notes)
        # пропуски видны, после трёх неуложившихся сборов — предупреждение
        self.assertTrue(any("ещё собирают с прошлого раза — Авито" in j for j in journal))
        self.assertTrue(any(j.startswith("⚠ Авито: 3 сбора подряд не уложились в шаг") for j in journal), journal)
        self.assertIn("avito", sc.get()["overrun"])
