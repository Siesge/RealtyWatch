"""Запуск парсеров в фоновых потоках, автоповтор с чекпоинта, продолжение незавершённых сборов и журнал."""
import json
import logging
import threading
import traceback
from collections import deque
from datetime import datetime

from . import db, dedupe, geo, history, images, offsale, rules, runlog, settings
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

# Автоматическое продолжение при ошибке провайдера: до 5 попыток подряд с сохранённой страницы/шага.
# Как только провайдер продвинулся дальше хотя бы на один шаг, счётчик снова сбрасывается до 5.
MAX_AUTO_RETRIES = 5
RETRY_DELAYS = [(4.0, 8.0), (8.0, 15.0), (15.0, 25.0), (25.0, 40.0), (40.0, 60.0)]

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
        self.max_auto_retries = MAX_AUTO_RETRIES
        self._hydrated_db = None
        # Запуск — сайты, стартовавшие одной командой (кнопкой или по расписанию). Когда закончили все сайты
        # запуска, вызываются on_launch_done: f(run_ids, sources, auto). Все донажатия «Продолжить» приписывают
        # собранные данные и итоговый снимок дашборда к тому же запуску (launch_key), пока не начат новый запуск.
        self.launches = {}            # launch_key → {"key", "left", "runs", "all_run_ids", "auto", "superseded"}
        self.run_launch = {}          # сайт → launch_key
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

    def _ensure_hydrated(self):
        """Подтягивает состояние последних прогонов из текущей БД (в т.ч. флаг can_continue после перезапуска)."""
        cur_db = str(db.DB_FILE)
        if self._hydrated_db == cur_db:
            return
        try:
            con = db.connect()
            try:
                rows = con.execute(
                    """SELECT id, source, mode, started, finished, status, pages, found, new, changed,
                              message, resume_state
                       FROM runs WHERE id IN (SELECT MAX(id) FROM runs GROUP BY source)"""
                ).fetchall()
            finally:
                con.close()
        except Exception:
            return
        with self.lock:
            self._hydrated_db = cur_db
            for r in rows:
                s = r["source"]
                if s not in self.status and not self.running(s):
                    st = r["status"] or ""
                    can_cont = bool(r["resume_state"]) and st in ("error", "blocked", "partial", "stopped")
                    self.status[s] = {
                        "state": st,
                        "run_id": r["id"],
                        "started": r["started"],
                        "finished": r["finished"],
                        "page": r["pages"] or 0,
                        "pages": r["pages"] or 0,
                        "found": r["found"] or 0,
                        "new": r["new"] or 0,
                        "changed": r["changed"] or 0,
                        "mode": r["mode"],
                        "message": r["message"] or "",
                        "can_continue": can_cont,
                        "retry": 0,
                        "retry_max": self.max_auto_retries,
                    }

    def _invalidate_previous_resumables(self):
        """Закрывает возможность продолжения старых незавершённых прогонов при старте нового общего запуска
        (вручную кнопкой «Запустить» или автоматически по таймеру / расписанию)."""
        try:
            con = db.connect()
            try:
                con.execute("UPDATE runs SET resume_state=NULL WHERE status != 'running' AND resume_state IS NOT NULL")
                con.commit()
            finally:
                con.close()
        except Exception:
            log.error(traceback.format_exc())
        with self.lock:
            for launch in self.launches.values():
                launch["superseded"] = True
            for s, st in self.status.items():
                if not self.running(s):
                    st["can_continue"] = False

    def start(self, sources, auto=False):
        """Запускает новый общий сбор для свободных сайтов; прошлые незавершённые прогоны закрываются как есть."""
        self._ensure_hydrated()
        started = [s for s in dict.fromkeys(sources) if s in SCRAPERS and not self.running(s)]
        if not started:
            return []
        self._invalidate_previous_resumables()
        now_ts = db.now()
        with self.lock:
            self._launch_seq += 1
            lkey = f"{now_ts}_{self._launch_seq}"
            self.launches[lkey] = {
                "key": lkey,
                "left": set(started),
                "runs": [],
                "all_run_ids": set(),
                "auto": auto,
                "superseded": False,
            }
            for s in started:
                self.run_launch[s] = lkey
                self.stops[s] = threading.Event()
        for s in started:
            t = threading.Thread(target=self._run, args=(s,), daemon=True, name=f"run-{s}")
            self.threads[s] = t
            t.start()
        return started

    def continue_run(self, source=None, run_id=None):
        """Продолжает незавершённый прогон (error / blocked / partial / stopped) с сохранённой точки
        и приписывает все собранные данные к его исходному запуску и тому же run_id."""
        self._ensure_hydrated()
        con = db.connect()
        try:
            if run_id is not None:
                row = con.execute("SELECT * FROM runs WHERE id=?", (int(run_id),)).fetchone()
            elif source:
                row = con.execute("SELECT * FROM runs WHERE source=? ORDER BY id DESC LIMIT 1", (str(source),)).fetchone()
            else:
                row = None
            if not row or row["source"] not in SCRAPERS:
                raise ValueError("прогон не найден")
            src = row["source"]
            rid = int(row["id"])
            if self.running(src):
                return {"started": [], "run_id": rid, "already_running": True}
            if not row["resume_state"] or row["status"] in ("done", "running"):
                raise ValueError("этот прогон уже завершён или закрыт новым запуском")
            try:
                saved = json.loads(row["resume_state"])
            except ValueError as e:
                raise ValueError("повреждены данные продолжения прогона") from e
            lkey = row["launch_key"] or saved.get("launch_key")
            all_db_ids = (
                {r[0] for r in con.execute("SELECT id FROM runs WHERE launch_key=?", (lkey,))}
                if lkey else {rid}
            )
            con.execute("UPDATE runs SET status='running' WHERE id=?", (rid,))
            con.commit()
        finally:
            con.close()

        with self.lock:
            if lkey:
                launch = self.launches.get(lkey)
                if launch is None:
                    launch = {
                        "key": lkey,
                        "left": set(),
                        "runs": [],
                        "all_run_ids": set(all_db_ids) | {rid},
                        "auto": bool(saved.get("auto")),
                        "superseded": False,
                    }
                    self.launches[lkey] = launch
                launch["left"].add(src)
                launch["all_run_ids"].update(all_db_ids)
                launch["all_run_ids"].add(rid)
                launch["runs"] = [(s, r_id) for s, r_id in launch["runs"] if s != src]
                self.run_launch[src] = lkey
            self.stops[src] = threading.Event()
            t = threading.Thread(target=self._run, args=(src, rid, True), daemon=True, name=f"run-{src}")
            self.threads[src] = t
        t.start()
        return {"started": [src], "run_id": rid}

    def stop(self, sources=None):
        for s, ev in self.stops.items():
            if sources is None or s in sources:
                ev.set()

    def any_running(self):
        return any(self.running(s) for s in list(self.threads))

    def log(self, source, msg):
        self._log(source, msg)

    def snapshot(self, after=0):
        self._ensure_hydrated()
        with self.lock:
            status_out = {}
            for k, v in self.status.items():
                is_run = self.running(k)
                d = dict(v, running=is_run)
                if is_run:
                    d["can_continue"] = False
                status_out[k] = d
            return {
                "status": status_out,
                "journal": [j for j in self.journal if j["n"] > after],
            }

    def _retry_pause(self, retry_num, stop):
        if stop is not None and stop.is_set():
            raise Stopped()
        delay = RETRY_DELAYS[min(max(0, retry_num - 1), len(RETRY_DELAYS) - 1)]
        pause(delay, stop)
        if stop is not None and stop.is_set():
            raise Stopped()

    def _run(self, source, resume_id=None, manual_resume=False):
        cfg = settings.load()
        stop = self.stops.get(source)
        if stop is None:
            stop = threading.Event()
            self.stops[source] = stop
        lg = lambda m: self._log(source, m)
        pr = lambda **kw: self._progress(source, **kw)

        with self.lock:
            lkey = self.run_launch.get(source)
            launch_auto = bool(self.launches[lkey]["auto"]) if lkey and lkey in self.launches else False

        con = db.connect()
        if resume_id is None:
            started = db.now()
            run_id = con.execute(
                "INSERT INTO runs(source, mode, started, status, launch_key) VALUES(?,?,?,?,?)",
                (source, cfg["sites"][source].get("mode"), started, "running", lkey),
            ).lastrowid
            # Пока у сайта не было ни одного полного прогона, «новые» — это первичная загрузка, а не новинки рынка
            baseline = int(not con.execute("SELECT 1 FROM runs WHERE source=? AND status='done' AND id<?", (source, run_id)).fetchone())
            con.commit()
            with self.lock:
                if lkey and lkey in self.launches:
                    self.launches[lkey]["all_run_ids"].add(run_id)
            counts = {"found": 0, "new": 0, "changed": 0, "excluded": 0, "details": 0}
            seen = set()
            prev_seen = dict(con.execute(
                "SELECT source_id, last_seen FROM listings WHERE source=? AND active=1 AND COALESCE(imported_only, 0)=0",
                (source,),
            ).fetchall())
            scraper_ckpt = {}
            base_requests = 0
            total_captchas = 0
            total_solved = 0
            total_bans = 0
            last_declared = None
            init_page = 0
            init_pages = 0
        else:
            row = con.execute("SELECT * FROM runs WHERE id=?", (int(resume_id),)).fetchone()
            saved = json.loads(row["resume_state"]) if row and row["resume_state"] else {}
            run_id = int(resume_id)
            started = saved.get("started") or (row["started"] if row else None) or db.now()
            lkey = lkey or (row["launch_key"] if row else None) or saved.get("launch_key")
            launch_auto = bool(saved.get("auto", launch_auto))
            baseline = int(saved.get("baseline", 0))
            counts = {"found": 0, "new": 0, "changed": 0, "excluded": 0, "details": 0}
            counts.update(saved.get("counts") or {})
            seen = {str(x) for x in (saved.get("seen") or ())}
            prev_seen = dict(saved.get("prev_seen") or {})
            scraper_ckpt = dict(saved.get("scraper") or {})
            base_requests = int(saved.get("requests") if saved.get("requests") is not None else ((row["requests"] if row else 0) or 0))
            total_captchas = int(saved.get("captchas") or 0)
            total_solved = int(saved.get("solved") or 0)
            total_bans = int(saved.get("bans") or 0)
            last_declared = saved.get("declared", row["declared"] if row else None)
            init_page = int(saved.get("page") or 0)
            init_pages = int(saved.get("pages") or ((row["pages"] if row else 0) or 0))
            con.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
            con.commit()

        with self.lock:
            self._hydrated_db = str(db.DB_FILE)
            self.status[source] = {
                "state": "running", "run_id": run_id, "started": started,
                "page": init_page, "pages": init_pages, **counts,
                "mode": cfg["sites"][source].get("mode"), "message": "",
                "can_continue": False, "retry": 0, "retry_max": self.max_auto_retries,
            }

        rl = runlog.RunLog(run_id, source)
        with self.lock:
            self.runlogs[source] = rl

        scraper = SCRAPERS[source](cfg, lg, stop, pr)
        mode_lbl = scraper.mode_label
        delay_lbl = f"{scraper.delay[0]:g}–{scraper.delay[1]:g}"

        if resume_id is None:
            rl.line(
                f"прогон №{run_id}, {SOURCE_LABEL.get(source, source)}, режим {mode_lbl}, пауза {delay_lbl} с, "
                f"фильтры {cfg['filters']['price_min'] / 1e6:g}–{cfg['filters']['price_max'] / 1e6:g} млн, от {cfg['filters']['area_min']} м²"
            )
            lg(f"старт, режим {mode_lbl}")
        else:
            from_where = "с начала" if (not scraper_ckpt.get("_steps") or scraper_ckpt.get("pass_finished")) else f"с шага {init_page}"
            rl.line(f"продолжение прогона №{run_id} ({SOURCE_LABEL.get(source, source)}), {from_where}, уже собрано {len(seen)}")
            lg(f"продолжение сбора ({from_where})")

        # При ручном клике «Продолжить» изначально даётся 1 попытка (retries_left = 0);
        # как только прогон реально продвигается вперёд (on_step), снова открывается пакет из 5 автопопыток.
        retries_left = 0 if manual_resume else self.max_auto_retries
        retry_num = 0
        state, message, removed = "done", "", 0

        def build_resume_json():
            st_cur = self.status.get(source) or {}
            payload = {
                "started": started,
                "launch_key": lkey,
                "auto": launch_auto,
                "baseline": baseline,
                "counts": dict(counts),
                "seen": sorted(seen),
                "prev_seen": prev_seen,
                "scraper": dict(scraper_ckpt),
                "requests": base_requests + rl.requests,
                "captchas": total_captchas,
                "solved": total_solved,
                "bans": total_bans,
                "declared": last_declared,
                "page": st_cur.get("page") or 0,
                "pages": st_cur.get("pages") or 0,
            }
            return json.dumps(payload, ensure_ascii=False)

        try:
            while True:
                if scraper is None:
                    scraper = SCRAPERS[source](cfg, lg, stop, pr)
                scraper.runlog = rl
                scraper.set_checkpoint(scraper_ckpt)

                def on_step():
                    nonlocal retries_left, retry_num, scraper_ckpt
                    scraper_ckpt = dict(scraper.checkpoint)
                    if retries_left < self.max_auto_retries or retry_num > 0:
                        retries_left = self.max_auto_retries
                        retry_num = 0
                        pr(retry=0)
                    rl.suppress_snapshots = False

                scraper.on_step = on_step
                try:
                    for item in scraper.listings():
                        sid = str(item["source_id"])
                        if sid not in seen:
                            self._process(con, scraper, cfg, item, counts, run_id, baseline)
                            seen.add(sid)
                            pr(**counts)
                    scraper_ckpt = dict(scraper.checkpoint)
                    if scraper.declared is not None:
                        last_declared = scraper.declared
                    if scraper.complete:
                        removed, notes = self._handle_missing(con, scraper, seen, prev_seen, started, run_id, lg, stop)
                        # Квартира снята, только когда её не осталось ни на одном сайте: пропажу с одного
                        # сайта считаем потерей ссылки, а не снятием с продажи
                        off = offsale.reconcile(con, run_id, lg)
                        if off:
                            removed += off
                            notes.insert(0, f"из них {off} — не осталось ни на одном сайте")
                    else:
                        notes = [f"выдача неполная: {scraper.incomplete_reason} — снятие не проверялось"]
                    state = "done" if scraper.complete else "partial"
                    note = f" ({'; '.join(notes)})" if notes else ""
                    message = f"найдено {counts['found']}, новых {counts['new']}, изменилось {counts['changed']}, снято {removed}{note}"
                    break
                except Stopped:
                    scraper_ckpt = dict(scraper.checkpoint)
                    state, message = "stopped", "остановлено пользователем"
                    break
                except (Blocked, Exception) as e:
                    scraper_ckpt = dict(scraper.checkpoint)
                    if scraper.declared is not None:
                        last_declared = scraper.declared
                    is_blocked = isinstance(e, Blocked)
                    if stop.is_set():
                        state, message = "stopped", "остановлено пользователем"
                        break
                    if retries_left > 0:
                        retries_left -= 1
                        retry_num += 1
                        total_captchas += scraper.captchas
                        total_solved += scraper.solved
                        total_bans += scraper.bans
                        scraper.close()
                        scraper = None
                        rl.suppress_snapshots = True
                        err_txt = f"блокировка: {e}" if is_blocked else f"{type(e).__name__}: {e}"
                        cur_p = (self.status.get(source) or {}).get("page") or 0
                        where = f"с сохранённой позиции (шаг {cur_p})" if scraper_ckpt.get("_steps") else "с начала"
                        lg(f"⚠ сбой ({err_txt}) — автоповтор {retry_num}/{self.max_auto_retries} {where}")
                        rl.line(f"автоповтор {retry_num}/{self.max_auto_retries} {where} после ошибки: {err_txt}")
                        pr(retry=retry_num, retry_max=self.max_auto_retries)
                        try:
                            self._retry_pause(retry_num, stop)
                        except Stopped:
                            state, message = "stopped", "остановлено пользователем"
                            break
                        continue
                    if is_blocked:
                        state, message = "blocked", f"блокировка: {e}"
                    else:
                        state, message = "error", f"{type(e).__name__}: {e}"
                        log.error(traceback.format_exc())
                    break
        finally:
            if scraper is not None:
                if scraper.declared is not None:
                    last_declared = scraper.declared
                total_captchas += scraper.captchas
                total_solved += scraper.solved
                total_bans += scraper.bans
                scraper.close()
            with self.lock:
                superseded = bool(lkey and (self.launches.get(lkey) or {}).get("superseded"))
            can_continue = state in ("error", "blocked", "partial", "stopped") and not superseded
            resume_json = build_resume_json() if can_continue else None
            total_reqs = base_requests + rl.requests
            fin_ts = db.now()
            con.execute(
                "UPDATE runs SET finished=?, status=?, pages=?, found=?, new=?, changed=?, removed=?, message=?, "
                "declared=?, collected=?, requests=?, resume_state=? WHERE id=?",
                (
                    fin_ts, state, (self.status.get(source) or {}).get("page"), counts["found"], counts["new"],
                    counts["changed"], removed, message, last_declared, len(seen), total_reqs, resume_json, run_id,
                ),
            )
            con.commit()
            con.close()
            pr(state=state, message=message, finished=fin_ts, can_continue=can_continue, run_id=run_id, retry=0)
            lg(f"завершено: {STATE_LABEL.get(state, state)} — {message}")
            rl.line(
                f"итог: заявлено сайтом {last_declared if last_declared is not None else '—'}, собрано уникальных {len(seen)}, "
                f"запросов {total_reqs}, капч {total_captchas}"
                f"{f' (пройдено автоматически {total_solved})' if total_solved else ''}, ограничений IP {total_bans}"
            )
            if state in ("done", "partial") and source == "avito":
                self._ease_delay_counts(total_captchas, total_bans, lg)

        if state != "stopped":
            images.repair(db.connect, sources=[source], log=lg, stop=stop)
        with self.lock:
            self.runlogs.pop(source, None)
        rl.close()

        with self.lock:
            cur_lkey = self.run_launch.pop(source, None) or lkey
            launch = self.launches.get(cur_lkey) if cur_lkey else None
            done_runs = None
            done_auto = False
            launch_run_ids = set()
            if launch is not None:
                launch["runs"] = [(s, r_id) for s, r_id in launch["runs"] if s != source]
                launch["runs"].append((source, run_id))
                launch["all_run_ids"].add(run_id)
                launch["left"].discard(source)
                if not launch["left"]:
                    done_runs = list(launch["runs"])
                    done_auto = launch["auto"]
                    launch_run_ids = set(launch["all_run_ids"])
                    if launch.get("superseded"):
                        self.launches.pop(cur_lkey, None)

        # Снимок рынка — строго один на общий запуск. Если внутри того же запуска провайдер был продолжен
        # кнопкой «Продолжить» (даже спустя часы), прежний промежуточный снимок этого запуска заменяется
        # обновлённым, чтобы на дашборде оставалась ровно одна полная легитимная точка.
        if done_runs is not None or launch is None:
            try:
                c2 = db.connect()
                if cur_lkey:
                    db_lids = [r[0] for r in c2.execute("SELECT id FROM runs WHERE launch_key=?", (cur_lkey,))]
                    all_lids = sorted(launch_run_ids | set(db_lids))
                    if all_lids:
                        c2.execute(f"DELETE FROM snapshots WHERE run_id IN ({','.join('?' * len(all_lids))})", all_lids)
                history.snapshot(c2, run_id)
                c2.close()
            except Exception:
                log.error(traceback.format_exc())
        if done_runs is not None:
            self._fire(done_runs, done_auto)

    def _ease_delay_counts(self, captchas, bans, lg):
        """Пауза Авито, выросшая после ограничений IP, возвращается к заданной в настройках после чистых прогонов."""
        cfg = settings.load()
        site = cfg["sites"]["avito"]
        base = site.get("delay_base") or site["delay"]
        if captchas or bans:
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
        (счётчик missed) и проверяется снова в следующем прогоне. Возвращает (снято, примечания к итогу)."""
        skip = {str(x) for x in (getattr(scraper, "skip_missing_ids", None) or ())}
        cand = [r for r in db.missing(con, scraper.name, seen) if str(r["source_id"]) not in skip]
        if not cand:
            return 0, []
        eff_prev = {k: v for k, v in prev_seen.items() if str(k) not in skip} if skip else prev_seen
        limit_n, expected, tau, gap = mass_limit(con, scraper.name, eff_prev, started)
        active = len(eff_prev)
        if len(cand) > limit_n:
            scraper.complete = False
            scraper.incomplete_reason = f"пропало {len(cand)} из {active} за {gap:g} дн. — больше допуска {limit_n:.0f}"
            lg(f"⚠ из поиска пропало {len(cand)} из {active} объявлений за {gap:g} дн. — больше, чем рынок успевает снять "
               f"(ожидаемо ~{expected:.0f}, допуск {limit_n:.0f} при сроке жизни объявления ~{tau:.0f} дн.). "
               "Похоже на сбой выдачи — снятие не проверяется")
            return 0, [f"пропало {len(cand)} из {active} за {gap:g} дн., допуск {limit_n:.0f} — похоже на сбой выдачи, снятие не проверялось"]
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
            if aborted is None and n < limit:
                con.commit()  # не держим транзакцию открытой на время сетевой паузы перед следующей проверкой
                pause((1.0, 2.5) if scraper.name != "avito" else scraper.delay, stop)
        con.commit()
        if unknown:
            lg(f"статус не подтверждён у {unknown}: проверю в следующем прогоне. Те, кого нет в поиске "
               f"{offsale.GONE_AFTER} прогона подряд, считаются потерянными на этом сайте — ссылка на него убирается, "
               "а снятыми они станут, если не найдутся и на других сайтах")
        return removed, ([f"не подтверждено {unknown} — проверю в следующем прогоне"] if unknown else [])

    def _process(self, con, scraper, cfg, item, counts, run_id=None, baseline=0):
        f = cfg["filters"]
        existing = db.get(con, item["source"], item["source_id"])
        # Поля, которых нет в этом источнике данных (например, у Авито без карточки), берутся из базы
        for k in item.pop("_keep", ()):
            if existing and item.get(k) in (None, "") and existing.get(k) not in (None, ""):
                item[k] = existing[k]
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
