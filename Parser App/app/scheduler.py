"""Автообновление по таймеру: раз в N часов запускает сбор выбранных сайтов.
Настройка хранится в data/schedule.json — она относится к этому компьютеру и не экспортируется с базой."""
import json
import os
import threading
import time
from datetime import datetime, timedelta

from .paths import DATA

FILE = DATA / "schedule.json"
DEFAULT = {"enabled": False, "hours": 6, "sources": ["cian", "avito", "yandex"], "next_run": None}
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

    def get(self):
        with self.lock:
            out = dict(self.cfg)
        out["now"] = datetime.now().isoformat(timespec="seconds")
        return out

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
            with self.lock:
                due = self.cfg["enabled"] and self.cfg["next_run"] and datetime.now().isoformat() >= self.cfg["next_run"]
                sources = list(self.cfg["sources"])
            if not due:
                continue
            if self.manager.any_running():
                continue  # дождёмся окончания текущего сбора
            started = self.manager.start(sources, auto=True)
            self.manager.log("db", f"автообновление: запущено ({', '.join(started) or 'нет выбранных сайтов'}), "
                                   f"следующее через {self.cfg['hours']:g} ч")
            with self.lock:
                self._plan()
                self._save()
