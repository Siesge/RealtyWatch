"""Запуск парсеров в фоновых потоках, прогресс и журнал для интерфейса."""
import logging
import threading
import traceback
from collections import deque
from datetime import datetime

from . import db, geo, rules, settings
from .net import Blocked, Stopped
from .paths import LOGS
from .scrapers import SCRAPERS

logging.basicConfig(
    filename=LOGS / "app.log", level=logging.INFO, encoding="utf-8",
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("realtywatch")


class RunManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = {}
        self.threads = {}
        self.stops = {}
        self.journal = deque(maxlen=600)
        self._seq = 0

    def _log(self, source, msg):
        with self.lock:
            self._seq += 1
            self.journal.append({"n": self._seq, "t": datetime.now().strftime("%H:%M:%S"), "src": source, "msg": msg})
        log.info("[%s] %s", source, msg)

    def _progress(self, source, **kw):
        with self.lock:
            self.status.setdefault(source, {}).update(kw)

    def running(self, source):
        t = self.threads.get(source)
        return t is not None and t.is_alive()

    def start(self, sources):
        started = []
        for s in sources:
            if s in SCRAPERS and not self.running(s):
                self.stops[s] = threading.Event()
                t = threading.Thread(target=self._run, args=(s,), daemon=True, name=f"run-{s}")
                self.threads[s] = t
                t.start()
                started.append(s)
        return started

    def stop(self, sources=None):
        for s, ev in self.stops.items():
            if sources is None or s in sources:
                ev.set()

    def snapshot(self, after=0):
        with self.lock:
            return {
                "status": {k: dict(v, running=self.running(k)) for k, v in self.status.items()},
                "journal": [j for j in self.journal if j["n"] > after],
            }

    def _run(self, source):
        cfg = settings.load()
        stop = self.stops[source]
        lg = lambda m: self._log(source, m)
        pr = lambda **kw: self._progress(source, **kw)
        started = db.now()
        with self.lock:
            self.status[source] = {"state": "running", "started": started, "page": 0, "pages": 0, "found": 0,
                                   "new": 0, "changed": 0, "excluded": 0, "details": 0,
                                   "mode": cfg["sites"][source].get("mode"), "message": ""}
        con = db.connect()
        run_id = con.execute("INSERT INTO runs(source, mode, started, status) VALUES(?,?,?,?)",
                             (source, cfg["sites"][source].get("mode"), started, "running")).lastrowid
        con.commit()
        scraper = SCRAPERS[source](cfg, lg, stop, pr)
        counts = {"found": 0, "new": 0, "changed": 0, "excluded": 0, "details": 0}
        state, message, removed = "done", "", 0
        lg(f"старт, режим {scraper.mode}")
        try:
            for item in scraper.listings():
                self._process(con, scraper, cfg, item, counts)
                pr(**counts)
            removed = db.mark_removed(con, source, started)
            message = f"найдено {counts['found']}, новых {counts['new']}, изменилось {counts['changed']}, снято {removed}"
        except Stopped:
            state, message = "stopped", "остановлено пользователем"
        except Blocked as e:
            state, message = "blocked", f"блокировка: {e}"
        except Exception as e:
            state, message = "error", f"{type(e).__name__}: {e}"
            log.error(traceback.format_exc())
        finally:
            scraper.close()
            con.execute("UPDATE runs SET finished=?, status=?, pages=?, found=?, new=?, changed=?, removed=?, message=? WHERE id=?",
                        (db.now(), state, self.status[source].get("page"), counts["found"], counts["new"],
                         counts["changed"], removed, message, run_id))
            con.commit()
            con.close()
            pr(state=state, message=message, finished=db.now())
            lg(f"завершено: {state} — {message}")

    def _process(self, con, scraper, cfg, item, counts):
        f = cfg["filters"]
        existing = db.get(con, item["source"], item["source_id"])
        # Данные со страницы объявления уже есть в базе — берём их, не запрашивая повторно
        if existing and existing.get("details_fetched"):
            for k in scraper.detail_fields:
                if existing.get(k) not in (None, ""):
                    item[k] = existing[k]
        if hasattr(scraper, "enrich") and not item.get("details_fetched") and scraper.site.get("details", True):
            pre = [r for r in rules.evaluate(dict(item), dict(f, renovation="off"))]
            if not pre and counts["details"] < scraper.site.get("max_details", 400):
                scraper.wait()
                scraper.enrich(item)
                counts["details"] += 1
        item["zone"] = geo.classify(item, cfg["zone"])
        item["excluded"] = rules.evaluate(item, f)
        res = db.upsert(con, item)
        con.commit()
        counts["found"] += 1
        if item["excluded"]:
            counts["excluded"] += 1
        if res == "new":
            counts["new"] += 1
        elif res == "changed":
            counts["changed"] += 1


manager = RunManager()
