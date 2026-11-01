"""Запуск парсеров в фоновых потоках, прогресс и журнал для интерфейса."""
import logging
import threading
import traceback
from collections import deque
from datetime import datetime

from . import db, dedupe, geo, history, images, rules, runlog, settings
from .net import Blocked, IpBanned, Stopped, pause
from .paths import LOGS
from .scrapers import SCRAPERS

# Массовое пропадание: если из полной выдачи пропало больше, чем рынок реально успевает снять за прошедшее время,
# это сбой сайта или парсера — объявления не проверяются. Допуск = ожидаемое снятие (по сроку жизни объявлений
# и времени с прошлого раза, см. history.expected_gone) + MASS_MARGIN, но не меньше MASS_SHARE (и MASS_MIN штук)
# и не больше MASS_CAP. Пауза в несколько дней — около 25%, месяц — ~40%, три месяца — ~70%, полгода — ~95%.
MASS_SHARE, MASS_MIN, MASS_MARGIN, MASS_CAP = 0.25, 20, 0.20, 0.95
SOURCE_LABEL = {"cian": "ЦИАН", "avito": "Авито", "yandex": "Яндекс"}
STATE_LABEL = {"done": "готово", "partial": "неполный", "error": "ошибка", "blocked": "блокировка", "stopped": "остановлено"}

runlog.setup_logging()
log = logging.getLogger("realtywatch")

# Пауза Авито после ограничений IP растёт; после стольких чистых прогонов подряд (без капчи и ограничений)
# она понемногу возвращается к заданной в настройках
CLEAN_RUNS_TO_EASE = 3
EASE_FACTOR = 1.5


class RunManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = {}
        self.runlogs = {}             # сайт → подробный журнал идущего прогона
        self.threads = {}
        self.stops = {}
        self.journal = deque(maxlen=600)
        self._seq = 0
        # Запуск — сайты, стартовавшие одной командой (кнопкой или по расписанию). Когда закончили все сайты
        # запуска, вызываются on_launch_done: f(run_ids, sources, auto). Сайт, который ещё собирает с прошлого
        # запуска, в новый не входит, поэтому медленный сайт не задерживает уведомление о быстрых.
        self.launches = {}            # id → {"left": сайты в работе, "runs": [(сайт, run_id)], "auto": bool}
        self.run_launch = {}          # сайт → id запуска
        self._launch_seq = 0
        self.on_launch_done = []

    def _log(self, source, msg):
        with self.lock:
            self._seq += 1
            self.journal.append({"n": self._seq, "t": datetime.now().strftime("%H:%M:%S"), "src": source, "msg": msg})
            rl = self.runlogs.get(source)
        log.info("[%s] %s", source, msg)
        if rl:
            rl.line(msg)

    def _progress(self, source, **kw):
        with self.lock:
            self.status.setdefault(source, {}).update(kw)

    def running(self, source):
        t = self.threads.get(source)
        return t is not None and t.is_alive()

    def start(self, sources, auto=False):
        """Запускает сайты, которые сейчас не собираются; идущие сборы продолжаются. Возвращает запущенные."""
        started = [s for s in dict.fromkeys(sources) if s in SCRAPERS and not self.running(s)]
        if not started:
            return []
        with self.lock:
            self._launch_seq += 1
            self.launches[self._launch_seq] = {"left": set(started), "runs": [], "auto": auto}
            for s in started:
                self.run_launch[s] = self._launch_seq
        for s in started:
            self.stops[s] = threading.Event()
            t = threading.Thread(target=self._run, args=(s,), daemon=True, name=f"run-{s}")
            self.threads[s] = t
            t.start()
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
        rl = runlog.RunLog(run_id, source)
        with self.lock:
            self.runlogs[source] = rl
        scraper = SCRAPERS[source](cfg, lg, stop, pr)
        scraper.runlog = rl
        rl.line(f"прогон №{run_id}, {SOURCE_LABEL.get(source, source)}, режим {scraper.mode}, пауза {scraper.delay[0]:g}–{scraper.delay[1]:g} с, "
                f"фильтры {cfg['filters']['price_min'] / 1e6:g}–{cfg['filters']['price_max'] / 1e6:g} млн, от {cfg['filters']['area_min']} м²")
        counts = {"found": 0, "new": 0, "changed": 0, "excluded": 0, "details": 0}
        state, message, removed = "done", "", 0
        seen = set()  # номера объявлений, реально полученные в этом прогоне
        # когда каждое активное объявление видели в поиске до этого прогона — для допуска на массовое пропадание
        prev_seen = dict(con.execute("SELECT source_id, last_seen FROM listings WHERE source=? AND active=1", (source,)).fetchall())
        lg(f"старт, режим {scraper.mode}")
        try:
            for item in scraper.listings():
                self._process(con, scraper, cfg, item, counts, run_id, baseline)
                seen.add(str(item["source_id"]))
                pr(**counts)
            if scraper.complete:
                removed, note = self._handle_missing(con, scraper, seen, prev_seen, started, run_id, lg, stop)
            else:
                note = f" (выдача неполная: {scraper.incomplete_reason} — снятие не проверялось)"
            if not scraper.complete:
                state = "partial"  # выдаче нельзя доверять: снятые не проверялись (см. Scraper.finish и mass_limit)
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
            con.execute("UPDATE runs SET finished=?, status=?, pages=?, found=?, new=?, changed=?, removed=?, message=?, "
                        "declared=?, collected=?, requests=? WHERE id=?",
                        (db.now(), state, self.status[source].get("page"), counts["found"], counts["new"],
                         counts["changed"], removed, message, scraper.declared, len(seen), rl.requests, run_id))
            con.commit()
            con.close()
            pr(state=state, message=message, finished=db.now())
            lg(f"завершено: {STATE_LABEL.get(state, state)} — {message}")
            rl.line(f"итог: заявлено сайтом {scraper.declared if scraper.declared is not None else '—'}, собрано уникальных {len(seen)}, "
                    f"запросов {rl.requests}, капч {scraper.captchas}, ограничений IP {scraper.bans}")
            if state in ("done", "partial"):
                self._ease_delay(scraper, lg)
        if state != "stopped":
            images.repair(db.connect, sources=[source], log=lg, stop=stop)
        with self.lock:
            self.runlogs.pop(source, None)
        rl.close()
        try:
            c2 = db.connect()
            history.snapshot(c2, run_id)
            c2.close()
        except Exception:
            log.error(traceback.format_exc())
        with self.lock:
            launch = self.launches.get(self.run_launch.pop(source, None))
            done = None
            if launch:
                launch["runs"].append((source, run_id))
                launch["left"].discard(source)
                if not launch["left"]:
                    done = launch
                    self.launches = {k: v for k, v in self.launches.items() if v is not launch}
        if done:
            self._fire(done["runs"], done["auto"])

    def _ease_delay(self, scraper, lg):
        """Пауза, выросшая после ограничений IP, возвращается к заданной в настройках после чистых прогонов."""
        if scraper.name != "avito":
            return
        cfg = settings.load()
        site = cfg["sites"]["avito"]
        base = site.get("delay_base") or site["delay"]
        if scraper.captchas or scraper.bans:
            site["clean_runs"] = 0
        else:
            site["clean_runs"] = site.get("clean_runs", 0) + 1
            cur = site["delay"]
            if site["clean_runs"] >= CLEAN_RUNS_TO_EASE and (cur[0] > base[0] or cur[1] > base[1]):
                new = [max(base[0], round(cur[0] / EASE_FACTOR)), max(base[1], round(cur[1] / EASE_FACTOR))]
                lg(f"{CLEAN_RUNS_TO_EASE} прогона подряд без капчи и ограничений — пауза уменьшена "
                   f"{cur[0]:g}–{cur[1]:g} → {new[0]:g}–{new[1]:g} с (не ниже заданной {base[0]:g}–{base[1]:g} с)")
                site["delay"], site["clean_runs"] = new, 0
        settings.save(cfg)

    def flush_partial(self):
        """Уведомить о сайтах, которые уже закончили, не дожидаясь медленного сайта того же запуска
        (вызывается при следующем запуске по расписанию). Медленный сайт уведомит отдельно, когда закончит."""
        parts = []
        with self.lock:
            for launch in self.launches.values():
                if launch["runs"] and launch["left"]:
                    parts.append((launch["runs"], launch["auto"]))
                    launch["runs"] = []
        for runs, auto in parts:
            self._fire(runs, auto)

    def _fire(self, runs, auto):
        for hook in self.on_launch_done:
            try:
                hook([r for _, r in runs], [s for s, _ in runs], auto)
            except Exception:
                log.error(traceback.format_exc())

    def _handle_missing(self, con, scraper, seen, prev_seen, started, run_id, lg, stop):
        """Объявления, пропавшие из полной выдачи, проверяются на своих страницах.
        Снятым считается только то, что сайт явно показывает снятым (статус, закрыто) или не находит (404,
        редирект в каталог). Если проверить не удалось, объявление остаётся в продаже с отметкой «нет в поиске»
        (счётчик missed) и проверяется снова в следующем прогоне. Возвращает (снято, примечание)."""
        cand = db.missing(con, scraper.name, seen)
        if not cand:
            return 0, ""
        limit_n, expected, tau, gap = mass_limit(con, scraper.name, prev_seen, started)
        active = len(prev_seen)
        if len(cand) > limit_n:
            scraper.complete = False
            scraper.incomplete_reason = f"пропало {len(cand)} из {active} за {gap:g} дн. — больше допуска {limit_n:.0f}"
            lg(f"⚠ из поиска пропало {len(cand)} из {active} объявлений за {gap:g} дн. — больше, чем рынок успевает снять "
               f"(ожидаемо ~{expected:.0f}, допуск {limit_n:.0f} при сроке жизни объявления ~{tau:.0f} дн.). "
               "Похоже на сбой выдачи — снятие не проверяется")
            return 0, f" (пропало {len(cand)} из {active} за {gap:g} дн., допуск {limit_n:.0f} — похоже на сбой выдачи, снятие не проверялось)"
        limit = scraper.site.get("max_verify", 300)
        lg(f"не найдено в поиске: {len(cand)} — проверяю страницы объявлений" + (f" (не больше {limit})" if len(cand) > limit else ""))
        cand.sort(key=lambda r: -(r.get("missed") or 0))  # дольше всех без подтверждения — первыми
        removed = unknown = 0
        aborted = None
        for n, row in enumerate(cand):
            state = reason = price = None
            if aborted is None and n < limit:
                try:
                    state, reason, price = scraper.verify(row)
                except (Blocked, IpBanned) as e:
                    aborted = f"{type(e).__name__}: {e}"
                    lg(f"⚠ проверка страниц прервана — сайт ограничил доступ ({e}); остальные проверю в следующий раз")
                pause((1.0, 2.5) if scraper.name != "avito" else scraper.delay, stop)
            if state == "active":
                con.execute("UPDATE listings SET missed=0 WHERE source=? AND source_id=?", (row["source"], row["source_id"]))
                if price:
                    db.set_price(con, row["source"], row["source_id"], price, "цена со страницы объявления (в поиске его не было)", run_id)
            elif state == "removed":
                db.remove(con, row["source"], row["source_id"], reason, run_id)
                removed += 1
            else:
                unknown += 1
                con.execute("UPDATE listings SET missed=? WHERE source=? AND source_id=?",
                            ((row.get("missed") or 0) + 1, row["source"], row["source_id"]))
            con.commit()
        if unknown:
            lg(f"статус не подтверждён у {unknown}: остаются в продаже с отметкой «нет в поиске», проверю в следующем прогоне")
        return removed, (f" (не подтверждено {unknown} — оставлены в продаже)" if unknown else "")

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


def mass_limit(con, source, prev_seen, started):
    """Сколько объявлений может пропасть из полной выдачи без подозрения на сбой.
    Возвращает (допуск, ожидаемо снятых, срок жизни τ в днях, медианный промежуток в днях)."""
    t0 = datetime.fromisoformat(started)
    gaps = [(t0 - datetime.fromisoformat(ts)).total_seconds() / 86400 for ts in prev_seen.values() if ts]
    tau, _ = history.lifetime_days(con, source)
    expected = history.expected_gone(gaps, tau)
    n = len(prev_seen)
    limit_n = min(MASS_CAP * n, max(MASS_MIN, MASS_SHARE * n, expected + MASS_MARGIN * n))
    gap = round(sorted(gaps)[len(gaps) // 2], 1) if gaps else 0
    return limit_n, expected, tau, gap


manager = RunManager()
