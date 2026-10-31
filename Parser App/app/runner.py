"""Запуск парсеров в фоновых потоках, прогресс и журнал для интерфейса."""
import logging
import threading
import traceback
from collections import deque
from datetime import datetime

from . import db, dedupe, geo, history, images, rules, settings
from .net import Blocked, Stopped, pause
from .paths import LOGS
from .scrapers import SCRAPERS
from .scrapers.base import Scraper

MISSED_TO_REMOVE = 2  # без проверки по странице объявление снимается после стольких пропусков подряд
SOURCE_NAME = {"cian": "ЦИАН", "avito": "Авито", "yandex": "Яндекса"}

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
        self._active = 0              # прогонов в работе (для сигнала «пачка завершена»)
        self.batch_started = None     # начало текущей пачки прогонов
        self.batch_auto = False       # пачка запущена расписанием
        self.on_batch_done = []       # обработчики: f(batch_started, auto)

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

    def start(self, sources, auto=False):
        started = []
        for s in sources:
            if s in SCRAPERS and not self.running(s):
                with self.lock:
                    if self._active == 0:
                        self.batch_started, self.batch_auto = db.now(), auto
                    self._active += 1
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

    def any_running(self):
        return any(self.running(s) for s in list(self.threads))

    def log(self, source, msg):
        self._log(source, msg)

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
        # Пока у сайта не было ни одного полного прогона, «новые» — это первичная загрузка, а не новинки рынка
        baseline = int(not con.execute("SELECT 1 FROM runs WHERE source=? AND status='done' AND id<?", (source, run_id)).fetchone())
        con.commit()
        scraper = SCRAPERS[source](cfg, lg, stop, pr)
        counts = {"found": 0, "new": 0, "changed": 0, "excluded": 0, "details": 0}
        state, message, removed = "done", "", 0
        lg(f"старт, режим {scraper.mode}")
        try:
            for item in scraper.listings():
                self._process(con, scraper, cfg, item, counts, run_id, baseline)
                pr(**counts)
            if scraper.complete:
                removed = self._handle_missing(con, scraper, started, run_id, lg, stop)
                note = ""
            else:
                note = " (выдача пройдена не полностью — снятие не проверялось)"
            message = f"найдено {counts['found']}, новых {counts['new']}, изменилось {counts['changed']}, снято {removed}{note}"
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
        if state != "stopped":
            images.repair(db.connect, sources=[source], log=lg, stop=stop)
        try:
            c2 = db.connect()
            history.snapshot(c2, run_id)
            c2.close()
        except Exception:
            log.error(traceback.format_exc())
        with self.lock:
            self._active -= 1
            last = self._active == 0
        if last:
            for hook in self.on_batch_done:
                try:
                    hook(self.batch_started, self.batch_auto)
                except Exception:
                    log.error(traceback.format_exc())

    def _handle_missing(self, con, scraper, started, run_id, lg, stop):
        """Объявления, пропавшие из полной выдачи: проверка на сайте или два пропуска подряд."""
        cand = db.missing(con, scraper.name, started)
        if not cand:
            return 0
        active = con.execute("SELECT COUNT(*) FROM listings WHERE source=? AND active=1", (scraper.name,)).fetchone()[0]
        can_verify = type(scraper).verify is not Scraper.verify
        if not can_verify and len(cand) > max(20, 0.3 * active):
            lg(f"⚠ из поиска пропало подозрительно много объявлений ({len(cand)} из {active}) — снятие отложено до следующего прогона")
            return 0
        lg(f"не найдено в поиске: {len(cand)} — " + ("проверяю на сайте" if can_verify else "считаю пропуски"))
        removed = 0
        for row in cand:
            state, reason, price = scraper.verify(row) if can_verify else (None, None, None)
            if can_verify:
                pause((1.0, 2.5), stop)
            if state == "active":
                con.execute("UPDATE listings SET missed=0 WHERE source=? AND source_id=?", (row["source"], row["source_id"]))
                if price:
                    db.set_price(con, row["source"], row["source_id"], price, "цена со страницы объявления (в поиске его не было)", run_id)
            elif state == "removed":
                db.remove(con, row["source"], row["source_id"], reason, run_id)
                removed += 1
            else:
                missed = (row.get("missed") or 0) + 1
                con.execute("UPDATE listings SET missed=? WHERE source=? AND source_id=?", (missed, row["source"], row["source_id"]))
                if missed >= MISSED_TO_REMOVE:
                    db.remove(con, row["source"], row["source_id"],
                              f"не найдено в поиске {SOURCE_NAME[scraper.name]} {missed} полных прогона подряд"
                              + ("; страницу объявления проверить не удалось" if can_verify else ""), run_id)
                    removed += 1
            con.commit()
        return removed

    def _process(self, con, scraper, cfg, item, counts, run_id=None, baseline=0):
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
        res = db.upsert(con, item, run_id, baseline)
        dedupe.assign(con, item["source"], item["source_id"])  # объединение с дублями на лету
        con.commit()
        counts["found"] += 1
        if item["excluded"]:
            counts["excluded"] += 1
        if res == "new":
            counts["new"] += 1
        elif res == "changed":
            counts["changed"] += 1


manager = RunManager()
