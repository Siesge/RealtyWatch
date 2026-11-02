"""Автообновление по таймеру: раз в N часов запускает сбор выбранных сайтов.
Запуски идут строго по расписанию. Сайт, который ещё собирает с прошлого запуска, не перезапускается
и не прерывается — он пропускает этот запуск и стартует в первый запуск после того, как закончит.
Остальные сайты стартуют как обычно.
Настройка хранится в data/schedule.json — она относится к этому компьютеру и не экспортируется с базой."""
import json
import os
import threading
import time
from datetime import datetime, timedelta

from . import db
from .paths import DATA
from .runner import SOURCE_LABEL

FILE = DATA / "schedule.json"
DEFAULT = {"enabled": False, "hours": 6, "sources": ["cian", "avito", "yandex"], "next_run": None,
           "last_tick": None, "skipped": {},  # skipped: сайт → время пропущенного запуска
           "overrun": {}}  # overrun: сайт → {"streak": сборов подряд не уложились в шаг, "run": начало последнего из них}
OVERRUN_WARN = 3  # после стольких сборов подряд, не уложившихся в шаг, — предупреждение
CATCH_UP_DELAY_S = 90  # если время прогона прошло, пока программа была закрыта, — запуск вскоре после старта


class Scheduler:
    def __init__(self, manager):
        self.manager = manager
        self.lock = threading.Lock()
        self.cfg = dict(DEFAULT)
        try:
            self.cfg.update(json.loads(FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        if self.cfg["enabled"] and self.cfg["next_run"] and self.cfg["next_run"] < datetime.now().isoformat():
            self.cfg["next_run"] = (datetime.now() + timedelta(seconds=CATCH_UP_DELAY_S)).isoformat(timespec="seconds")
        if not os.environ.get("RW_NO_SCHEDULER"):  # отладочные запуски сервера не должны стартовать сбор
            threading.Thread(target=self._loop, daemon=True, name="scheduler").start()

    def _save(self):
        FILE.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def _plan(self):
        self.cfg["next_run"] = (datetime.now() + timedelta(hours=float(self.cfg["hours"]))).isoformat(timespec="seconds")

    def _plan_next(self):
        """Следующий запуск — через шаг от запланированного, а не от фактического: расписание не уплывает."""
        step, now = timedelta(hours=float(self.cfg["hours"])), datetime.now()
        nxt = datetime.fromisoformat(self.cfg["next_run"]) if self.cfg["next_run"] else now
        while nxt <= now:
            nxt += step
        self.cfg["next_run"] = nxt.isoformat(timespec="seconds")

    def get(self):
        with self.lock:
            out = dict(self.cfg)
        out["now"] = datetime.now().isoformat(timespec="seconds")
        # Пропустившие запуск, которые всё ещё собирают: когда начали текущий сбор
        st = self.manager.status
        out["skipped"] = {s: {"tick": t, "since": (st.get(s) or {}).get("started")}
                          for s, t in (out.get("skipped") or {}).items() if self.manager.running(s)}
        out["overrun"] = {s: dict(o, avg_hours=self._avg_hours(s)) for s, o in (out.get("overrun") or {}).items()
                          if o.get("streak", 0) >= OVERRUN_WARN and s in out["sources"]}
        return out

    @staticmethod
    def _avg_hours(source, n=3):
        """Средняя длительность последних законченных сборов сайта, часов."""
        try:
            con = db.connect()
            r = con.execute("""SELECT AVG(julianday(finished) - julianday(started)) * 24 FROM (
                SELECT started, finished FROM runs WHERE source=? AND status IN ('done', 'partial') AND finished IS NOT NULL
                ORDER BY id DESC LIMIT ?)""", (source, n)).fetchone()[0]
            con.close()
            return round(r, 2) if r else None
        except Exception:
            return None

    def _track_overrun(self, started, busy):
        """Считает сборы подряд, не уложившиеся в шаг. Вызывается под self.lock в момент запуска."""
        over, prev_skipped = self.cfg.setdefault("overrun", {}), self.cfg.get("skipped") or {}
        warn = []
        for s in started:
            if s not in prev_skipped:      # прошлый сбор уложился в шаг
                over.pop(s, None)
        for s in busy:
            run = (self.manager.status.get(s) or {}).get("started")
            o = over.setdefault(s, {"streak": 0, "run": None})
            if o["run"] != run:            # этот сбор не уложился в шаг впервые
                o["streak"], o["run"] = o["streak"] + 1, run
                if o["streak"] >= OVERRUN_WARN:
                    warn.append((s, o["streak"]))
        return warn

    def update(self, data):
        with self.lock:
            replan = False
            if "hours" in data:
                hours = max(0.25, min(float(data["hours"]), 24 * 14))
                replan = hours != self.cfg["hours"]
                self.cfg["hours"] = hours
            if "sources" in data:
                self.cfg["sources"] = [s for s in data["sources"] if s in ("cian", "avito", "yandex")]
            if "enabled" in data:
                replan = replan or (bool(data["enabled"]) and not self.cfg["enabled"])
                self.cfg["enabled"] = bool(data["enabled"])
            if self.cfg["enabled"] and (replan or not self.cfg["next_run"]):
                self._plan()
            if not self.cfg["enabled"]:
                self.cfg["next_run"] = None
            self._save()
        return self.get()

    def _loop(self):
        while True:
            time.sleep(10)
            self._tick()

    def _tick(self):
        with self.lock:
            due = self.cfg["enabled"] and self.cfg["next_run"] and datetime.now().isoformat() >= self.cfg["next_run"]
            sources = list(self.cfg["sources"])
        if not due:
            return
        tick = datetime.now().isoformat(timespec="seconds")
        self.manager.flush_partial()  # о закончивших с прошлого запуска — не ждать медленного сайта
        started = self.manager.start(sources, auto=True)  # идущие сборы он сам пропускает
        busy = [s for s in sources if s not in started]
        with self.lock:
            warn = self._track_overrun(started, busy)
            self.cfg["last_tick"] = tick
            self.cfg["skipped"] = {s: tick for s in busy}
            self._plan_next()
            self._save()
        names = lambda ss: ", ".join(SOURCE_LABEL.get(s, s) for s in ss)
        msg = f"автообновление: запущено — {names(started)}" if started else "автообновление: все выбранные сайты ещё собирают"
        if busy:
            since = {s: (self.manager.status.get(s) or {}).get("started", "")[11:16] for s in busy}
            msg += ("; ещё собирают с прошлого раза — " + ", ".join(f"{SOURCE_LABEL.get(s, s)} (с {since[s]})" for s in busy)
                    + " — продолжают, перезапустятся в первый запуск после окончания")
        self.manager.log("db", msg + f". Следующий запуск в {self.cfg['next_run'][11:16]}")
        for s, n in warn:
            avg = self._avg_hours(s)
            self.manager.log("db", f"⚠ {SOURCE_LABEL.get(s, s)}: {n} сбора подряд не уложились в шаг {self.cfg['hours']:g} ч"
                                   + (f" (сбор идёт в среднем {avg:g} ч)" if avg else "")
                                   + ". Данные этого сайта обновляются реже, чем задано: увеличьте шаг автообновления"
                                   + (" или уменьшите число страниц объявлений Авито за прогон" if s == "avito" else ""))
