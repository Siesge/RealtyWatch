"""Тесты автоповтора провайдеров с сохранённого чекпоинта (до 5 попыток),
кнопки «Продолжить» (1 попытка при немедленной ошибке и возврат 5 автопопыток при продвижении),
единого запуска / снимка дашборда и закрытия старых незавершённых прогонов новым общим или плановым запуском."""
import json
import socket
import threading
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import uvicorn
from fastapi import HTTPException

from app import db, lan_sync, purge, runner, scheduler, server, settings, tiles
from app.net import Blocked, Stopped
from app.scrapers.avito import Avito
from app.scrapers.cian import Cian
from app.scrapers.yandex import Yandex
from tests.support import Case, LocalBrowser, avito_page, cian_offer, con, has_browser, listings, yandex_offer


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_idle(mgr: runner.RunManager, timeout: float = 10.0):
    deadline = time.time() + timeout
    while mgr.any_running() and time.time() < deadline:
        time.sleep(0.02)
    assert not mgr.any_running(), "фоновый поток сбора не завершился вовремя"


class RetryAndResumeCoreTests(Case):
    """Проверка 5 автопопыток, чекпоинтов всех 3 сайтов, логики кнопки «Продолжить» и группировки запусков."""

    def setUp(self):
        super().setUp()
        self._orig_scrapers = dict(runner.SCRAPERS)
        self._orig_mgr = server.manager
        self.mgr = runner.RunManager()
        server.manager = self.mgr
        self.addCleanup(self._restore)

    def _restore(self):
        runner.SCRAPERS.clear()
        runner.SCRAPERS.update(self._orig_scrapers)
        server.manager = self._orig_mgr

    def test_auto_retry_from_checkpoint_across_all_three_providers(self):
        # 1) Яндекс: 6 страниц по 10 квартир, падает 3 раза на 4-й странице (index=3), затем продолжает с неё же
        y_rows = listings("yandex", 60)
        y_calls: list[int] = []
        y_fails = {"left": 3}

        class Y(Yandex):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, index):
                y_calls.append(index)
                if index == 3 and y_fails["left"] > 0:
                    y_fails["left"] -= 1
                    raise IOError("HTTP 503 temporary")
                chunk = y_rows[index * 10: (index + 1) * 10]
                return [yandex_offer(r) for r in chunk], {"totalPages": 6, "totalItems": 60}

        # 2) Авито: 4 страницы по 50 квартир, блокируется 2 раза на 3-й странице, продолжает с 3-й
        a_rows = listings("avito", 200)
        a_calls: list[int] = []
        a_fails = {"left": 2}

        class A(Avito):
            def wait(self):
                pass

            def close(self):
                pass

            def enrich(self, item):
                return item

            def _get_search_html(self, url, page=1):
                a_calls.append(page)
                if page == 3 and a_fails["left"] > 0:
                    a_fails["left"] -= 1
                    raise Blocked("временная блокировка")
                chunk = a_rows[(page - 1) * 50: page * 50]
                html = avito_page(chunk, len(a_rows))
                self.http.last_text = html
                return html

        # 3) ЦИАН (полный и затем ускоренный сбор): сбой на 2-й странице основной выдачи
        c_rows = listings("cian", 60)
        c_calls: list[tuple[int, int | None]] = []
        c_fails = {"left": 2}

        class C(Cian):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, page, repair=None):
                c_calls.append((page, repair))
                if repair is not None:
                    return ([cian_offer(c_rows[0])], 1) if repair == 1 else ([], 0)
                if page == 2 and c_fails["left"] > 0:
                    c_fails["left"] -= 1
                    raise IOError("ЦИАН: не найдено состояние страницы")
                chunk = c_rows[(page - 1) * 28: page * 28]
                return [cian_offer(r) for r in chunk], len(c_rows)

        runner.SCRAPERS.update({"yandex": Y, "avito": A, "cian": C})
        self.mgr.start(["yandex", "avito", "cian"])
        _wait_idle(self.mgr)

        # Первые страницы до места обрыва НЕ перекачивались заново
        self.assertEqual(y_calls.count(0), 1)
        self.assertEqual(y_calls.count(1), 1)
        self.assertEqual(y_calls.count(2), 1)
        self.assertEqual(y_calls.count(3), 4)  # 1 попытка + 3 автоповтора
        self.assertEqual(y_calls.count(4), 1)
        self.assertEqual(y_calls.count(5), 1)

        self.assertEqual(a_calls.count(1), 1)
        self.assertEqual(a_calls.count(2), 1)
        self.assertEqual(a_calls.count(3), 3)  # 1 попытка + 2 автоповтора
        self.assertEqual(a_calls.count(4), 1)

        self.assertEqual(c_calls.count((1, None)), 1)
        self.assertEqual(c_calls.count((2, None)), 3)  # 1 попытка + 2 автоповтора
        self.assertEqual(c_calls.count((3, None)), 1)

        c = con()
        statuses = {r["source"]: (r["status"], r["collected"], r["resume_state"]) for r in c.execute("SELECT * FROM runs")}
        snaps_n = c.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        c.close()

        self.assertEqual(statuses["yandex"], ("done", 60, None))
        self.assertEqual(statuses["avito"], ("done", 200, None))
        self.assertEqual(statuses["cian"], ("done", 60, None))
        self.assertEqual(snaps_n, 1, "весь тройной запуск успешно дошёл после автоповторов — одна точка дашборда")

        # Проверяем чекпоинт ЦИАН и в ускоренном (_listings_incremental) режиме
        c_calls.clear()
        c_fails["left"] = 4
        self.mgr.start(["cian"])
        _wait_idle(self.mgr)
        self.assertEqual(c_calls.count((1, None)), 1, "1-я страница в ускоренном режиме не перекачивалась")
        self.assertEqual(c_calls.count((2, None)), 5, "1 попытка + 4 автоповтора на 2-й странице")
        c = con()
        last_c = c.execute("SELECT status, collected FROM runs WHERE source='cian' ORDER BY id DESC LIMIT 1").fetchone()
        c.close()
        self.assertEqual((last_c["status"], last_c["collected"]), ("done", 60))

    def test_five_retries_limit_then_manual_single_shot_and_restored_five_retries(self):
        rows = listings("yandex", 100)  # 10 страниц по 10 квартир
        calls: list[int] = []
        state_ctl = {"fail_p4": True, "fail_p8_left": 3}

        class Y(Yandex):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, index):
                calls.append(index)
                if index == 3 and state_ctl["fail_p4"]:
                    raise IOError("HTTP 502 Bad Gateway")
                if index == 7 and state_ctl["fail_p8_left"] > 0:
                    state_ctl["fail_p8_left"] -= 1
                    raise IOError("timeout on page 8")
                chunk = rows[index * 10: (index + 1) * 10]
                return [yandex_offer(r) for r in chunk], {"totalPages": 10, "totalItems": 100}

        runner.SCRAPERS["yandex"] = Y
        self.mgr.start(["yandex"])
        _wait_idle(self.mgr)

        # Страницы 1..3 прочитаны по 1 разу, страница 4 (index=3) — ровно 1 + 5 = 6 раз, после чего пауза
        self.assertEqual([calls.count(i) for i in range(4)], [1, 1, 1, 6])
        snap = self.mgr.snapshot()["status"]["yandex"]
        self.assertEqual(snap["state"], "error")
        self.assertTrue(snap["can_continue"])
        rid = snap["run_id"]

        # Кликаем «Продолжить» №1, пока ошибка на стр. 4 ещё держится -> СТРОГО 1 попытка (не 6!)
        res1 = server.run_continue({"source": "yandex"})
        self.assertEqual(res1["run_id"], rid)
        _wait_idle(self.mgr)
        self.assertEqual(calls.count(3), 7, "по клику «Продолжить» при мгновенной ошибке — ровно 1 попытка")
        self.assertTrue(self.mgr.snapshot()["status"]["yandex"]["can_continue"])

        # Кликаем «Продолжить» №2, ошибка всё ещё есть -> снова ровно 1 попытка, кнопка остаётся доступной
        server.run_continue({"run_id": rid})
        _wait_idle(self.mgr)
        self.assertEqual(calls.count(3), 8)
        self.assertTrue(self.mgr.snapshot()["status"]["yandex"]["can_continue"])

        # Убираем сбой на стр. 4, но на стр. 8 (index=7) ждут 3 ошибки подряд:
        # как только стр. 4 пройдена успешно, пакет из 5 автопопыток ДОЛЖЕН восстановиться и сам пройти стр. 8!
        state_ctl["fail_p4"] = False
        server.run_continue({"source": "yandex"})
        _wait_idle(self.mgr)

        self.assertEqual(calls.count(3), 9)
        self.assertEqual(calls.count(7), 4, "после продвижения со стр. 4 пакет из 5 автоповторов восстановился на стр. 8")
        self.assertEqual(calls.count(9), 1)

        final_st = self.mgr.snapshot()["status"]["yandex"]
        self.assertEqual(final_st["state"], "done")
        self.assertFalse(final_st["can_continue"])

        c = con()
        run_rows = c.execute("SELECT id, status, collected FROM runs").fetchall()
        total_db = c.execute("SELECT COUNT(*) FROM listings WHERE source='yandex'").fetchone()[0]
        c.close()
        self.assertEqual(len(run_rows), 1, "все донажатия записаны в тот же единственный run_id")
        self.assertEqual((run_rows[0]["id"], run_rows[0]["status"], run_rows[0]["collected"]), (rid, "done", 100))
        self.assertEqual(total_db, 100)

    def test_initial_stage_failure_restarts_cleanly_via_continue(self):
        rows = listings("yandex", 20)
        fail = {"on": True}
        calls = []

        class Y(Yandex):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, index):
                calls.append(index)
                if fail["on"]:
                    raise IOError("DNS error at start")
                chunk = rows[index * 10: (index + 1) * 10]
                return [yandex_offer(r) for r in chunk], {"totalPages": 2, "totalItems": 20}

        runner.SCRAPERS["yandex"] = Y
        self.mgr.start(["yandex"])
        _wait_idle(self.mgr)
        self.assertEqual(len(calls), 6)  # 1 + 5 попыток на старте
        rid = self.mgr.snapshot()["status"]["yandex"]["run_id"]
        self.assertTrue(self.mgr.snapshot()["status"]["yandex"]["can_continue"])

        fail["on"] = False
        server.run_continue({"source": "yandex"})
        _wait_idle(self.mgr)
        c = con()
        r = c.execute("SELECT id, status, collected FROM runs WHERE id=?", (rid,)).fetchone()
        c.close()
        self.assertEqual((r["status"], r["collected"]), ("done", 20))

    def test_shared_launch_keeps_all_delayed_continues_and_single_legitimate_snapshot(self):
        y_rows = listings("yandex", 20, seed=11)
        c_rows = listings("cian", 30, seed=22)
        a_rows = listings("avito", 50, seed=33)
        # Сдвигаем координаты, чтобы 3 сайта дали 100 отдельных объектов на дашборде
        for i, r in enumerate(c_rows):
            r["address"] = f"Ялта, ул. Циановая, {i + 1}"
            r["area"] = 41.0 + i * 0.5
        for i, r in enumerate(a_rows):
            r["address"] = f"Ялта, ул. Авитовая, {i + 1}"
            r["area"] = 51.0 + i * 0.5

        ctl = {"cian_fail": True, "avito_fail": True}

        class Y(Yandex):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, index):
                return [yandex_offer(r) for r in y_rows], {"totalPages": 1, "totalItems": len(y_rows)}

        class C(Cian):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, page, repair=None):
                if repair is not None:
                    return [], 0
                if page == 2 and ctl["cian_fail"]:
                    raise IOError("ЦИАН оборвался на 2-й странице")
                chunk = c_rows[(page - 1) * 28: page * 28]
                return [cian_offer(r) for r in chunk], len(c_rows)

        class A(Avito):
            def wait(self):
                pass

            def close(self):
                pass

            def enrich(self, item):
                return item

            def _get_search_html(self, url, page=1):
                if ctl["avito_fail"]:
                    raise Blocked("Авито: капча")
                html = avito_page(a_rows, len(a_rows))
                self.http.last_text = html
                return html

        runner.SCRAPERS.update({"yandex": Y, "cian": C, "avito": A})

        # Общий старт всех трёх сайтов: Яндекс проходит, ЦИАН и Авито встают с ошибками после 5 автопопыток
        self.mgr.start(["yandex", "cian", "avito"])
        _wait_idle(self.mgr)

        groups1 = server.db_launches("", "", 50)
        self.assertEqual(len(groups1), 1, "все три провайдера в одном запуске")
        self.assertTrue(groups1[0]["bad"])
        by_src1 = {r["source"]: r for r in groups1[0]["runs"]}
        self.assertEqual(by_src1["yandex"]["status"], "done")
        self.assertFalse(by_src1["yandex"]["can_continue"])
        self.assertEqual(by_src1["cian"]["status"], "error")
        self.assertTrue(by_src1["cian"]["can_continue"])
        self.assertEqual(by_src1["avito"]["status"], "blocked")
        self.assertTrue(by_src1["avito"]["can_continue"])

        # Донажимаем «Продолжить» на ЦИАН
        ctl["cian_fail"] = False
        server.run_continue({"source": "cian"})
        _wait_idle(self.mgr)

        # Затем спустя время донажимаем «Продолжить» на Авито
        ctl["avito_fail"] = False
        server.run_continue({"run_id": by_src1["avito"]["id"]})
        _wait_idle(self.mgr)

        groups2 = server.db_launches("", "", 50)
        self.assertEqual(len(groups2), 1, "после всех донажатий в истории по-прежнему ОДИН общий запуск")
        self.assertFalse(groups2[0]["bad"], "общий запуск стал полностью успешным (готово)")
        self.assertEqual(groups2[0]["collected"], 100)
        by_src2 = {r["source"]: r for r in groups2[0]["runs"]}
        self.assertEqual({s: r["id"] for s, r in by_src2.items()}, {s: r["id"] for s, r in by_src1.items()})
        self.assertTrue(all(r["status"] == "done" and not r["can_continue"] for r in groups2[0]["runs"]))

        # Проверяем дашборд: ровно 1 снимок рынка и в нём все 100 квартир со всех 3 сайтов!
        c = con()
        snaps = c.execute("SELECT ts, run_id, data FROM snapshots").fetchall()
        c.close()
        self.assertEqual(len(snaps), 1, "промежуточные снимки заменены одним итоговым снимком запуска")
        snap_data = json.loads(snaps[0]["data"])
        self.assertEqual(snap_data["all"]["count"], 100)
        self.assertEqual(snap_data["sources"], {"cian": 30, "avito": 50, "yandex": 20})

    def test_stop_and_new_manual_or_scheduled_launch_locks_old_incomplete_runs(self):
        rows = listings("yandex", 40)
        gate = threading.Event()
        fail_cian = {"on": True}

        class YSlow(Yandex):
            def wait(self):
                gate.wait(timeout=3.0)
                if self.stop and self.stop.is_set():
                    raise Stopped()

            def close(self):
                pass

            def _load(self, index):
                chunk = rows[index * 20: (index + 1) * 20]
                return [yandex_offer(r) for r in chunk], {"totalPages": 2, "totalItems": 40}

        class CFail(Cian):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, page, repair=None):
                if fail_cian["on"]:
                    raise IOError("ЦИАН временно недоступен")
                return [], 0

        runner.SCRAPERS.update({"yandex": YSlow, "cian": CFail})
        self.mgr.start(["yandex", "cian"])
        # Ждём, пока Яндекс прочитает 1-ю страницу, а ЦИАН завершит свои 5 автопопыток ошибкой, затем жмём Stop
        deadline = time.time() + 3.0
        while ((self.mgr.status.get("yandex") or {}).get("page", 0) < 1 or self.mgr.running("cian")) and time.time() < deadline:
            time.sleep(0.02)
        server.stop({})
        gate.set()
        _wait_idle(self.mgr)

        st1 = self.mgr.snapshot()["status"]
        self.assertEqual(st1["yandex"]["state"], "stopped")
        self.assertTrue(st1["yandex"]["can_continue"], "после Stop прогон можно продолжить, пока не начат новый запуск")
        self.assertEqual(st1["cian"]["state"], "error")
        self.assertTrue(st1["cian"]["can_continue"])
        old_y_id = st1["yandex"]["run_id"]
        old_c_id = st1["cian"]["run_id"]

        # 1) Пользователь запускает НОВЫЙ общий прогон -> прошлые stopped/error остаются как есть и закрываются для «Продолжить»
        class YFast(Yandex):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, index):
                chunk = rows[index * 20: (index + 1) * 20]
                return [yandex_offer(r) for r in chunk], {"totalPages": 2, "totalItems": 40}

        runner.SCRAPERS["yandex"] = YFast
        server.run({"sources": ["yandex", "cian"]})
        _wait_idle(self.mgr)

        with self.assertRaises(HTTPException) as ctx400:
            server.run_continue({"run_id": old_y_id})
        self.assertEqual(ctx400.exception.status_code, 400)

        c = con()
        old_rows = {r["id"]: (r["status"], r["resume_state"]) for r in c.execute("SELECT id, status, resume_state FROM runs WHERE id IN (?,?)", (old_y_id, old_c_id))}
        c.close()
        self.assertEqual(old_rows[old_y_id], ("stopped", None))
        self.assertEqual(old_rows[old_c_id], ("error", None))

        # Во втором запуске ЦИАН снова упал и сейчас can_continue=True:
        c2_id = self.mgr.snapshot()["status"]["cian"]["run_id"]
        self.assertNotEqual(c2_id, old_c_id)
        self.assertTrue(self.mgr.snapshot()["status"]["cian"]["can_continue"])

        # 2) Срабатывает АВТООБНОВЛЕНИЕ по таймеру/расписанию -> прошлый незавершённый ЦИАН тоже закрывается, стартует новый запуск!
        fail_cian["on"] = False
        scheduler.FILE = self.dir / "schedule.json"
        sc = scheduler.Scheduler(self.mgr)
        sc.update({"enabled": True, "mode": "timer", "hours": 6, "sources": ["yandex", "cian"]})
        sc.cfg["next_run"] = datetime.now().isoformat(timespec="seconds")
        sc._tick()
        _wait_idle(self.mgr)

        with self.assertRaises(HTTPException):
            server.run_continue({"run_id": c2_id})

        groups = server.db_launches("", "", 50)
        self.assertEqual(len(groups), 3, "три независимых запуска: остановленный, ручной и по таймеру")
        # В старых запусках ни у одного прогона не осталось can_continue
        for g in groups[1:]:
            self.assertTrue(all(not r["can_continue"] for r in g["runs"]), g)

    def test_partial_run_can_continue_and_weekly_schedule_locks_previous(self):
        rows = listings("cian", 120)
        # Сначала сайт отдаёт урезанную выдачу (< 85% от заявленного) -> статус partial («неполный»),
        # после нажатия «Продолжить» отдаёт полную выдачу в тот же run_id -> статус done («готово»).
        mode = {"truncated": True}

        class C(Cian):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, page, repair=None):
                if repair is not None:
                    return [], 0
                per = 20 if mode["truncated"] else 28
                chunk = rows[(page - 1) * 28: page * 28][:per]
                return [cian_offer(r) for r in chunk], len(rows)

        runner.SCRAPERS["cian"] = C
        self.mgr.start(["cian"])
        _wait_idle(self.mgr)

        st = self.mgr.snapshot()["status"]["cian"]
        self.assertEqual(st["state"], "partial")
        self.assertTrue(st["can_continue"])
        rid = st["run_id"]

        mode["truncated"] = False
        server.run_continue({"source": "cian"})
        _wait_idle(self.mgr)

        st2 = self.mgr.snapshot()["status"]["cian"]
        self.assertEqual(st2["state"], "done")
        self.assertFalse(st2["can_continue"])
        c = con()
        r = c.execute("SELECT id, status, collected FROM runs WHERE id=?", (rid,)).fetchone()
        c.close()
        self.assertEqual((r["status"], r["collected"]), ("done", 120))

        # Новый запуск падает в partial, затем срабатывает недельное расписание (weekly) -> старый прогон закрывается
        mode["truncated"] = True
        self.mgr.start(["cian"])
        _wait_idle(self.mgr)
        rid_partial = self.mgr.snapshot()["status"]["cian"]["run_id"]
        self.assertTrue(self.mgr.snapshot()["status"]["cian"]["can_continue"])

        mode["truncated"] = False
        now_dt = datetime.now()
        scheduler.FILE = self.dir / "schedule_weekly.json"
        sc = scheduler.Scheduler(self.mgr)
        sc.update({
            "enabled": True,
            "mode": "weekly",
            "slots": [{"time": f"{now_dt:%H:%M}", "days": [now_dt.isoweekday()]}],
            "sources": ["cian"],
        })
        sc.cfg["next_run"] = now_dt.isoformat(timespec="seconds")
        sc._tick()
        _wait_idle(self.mgr)

        with self.assertRaises(HTTPException):
            server.run_continue({"run_id": rid_partial})


class RetryAndResumeBrowserUITests(Case):
    """Браузерная проверка кнопок «Продолжить» в боковой панели (#sources) и во вкладке «Прогоны» (#view-runs)."""

    @unittest.skipUnless(has_browser(), "Chromium не установлен (setup.bat)")
    def test_ui_continue_buttons_in_sidebar_and_runs_tab(self):
        lan_sync.stop()
        orig_scrapers = dict(runner.SCRAPERS)
        orig_mgr = server.manager
        mgr = runner.RunManager()
        server.manager = mgr

        def restore():
            runner.SCRAPERS.clear()
            runner.SCRAPERS.update(orig_scrapers)
            server.manager = orig_mgr

        self.addCleanup(restore)

        rows = listings("yandex", 30)
        ctl = {"fail_p2": True}

        class Y(Yandex):
            def wait(self):
                pass

            def close(self):
                pass

            def _load(self, index):
                if index == 1 and ctl["fail_p2"]:
                    raise IOError("HTTP 503 на 2-й странице")
                chunk = rows[index * 15: (index + 1) * 15]
                return [yandex_offer(r) for r in chunk], {"totalPages": 2, "totalItems": 30}

        runner.SCRAPERS["yandex"] = Y
        mgr.start(["yandex"])
        _wait_idle(mgr)

        port = _free_port()
        uv_srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
        patcher = patch.object(tiles, "_fetch_url", return_value=(404, b""))
        patcher.start()
        self.addCleanup(patcher.stop)

        t = threading.Thread(target=uv_srv.run, daemon=True)
        t.start()
        self.addCleanup(lambda: setattr(uv_srv, "should_exit", True) or t.join(timeout=5.0))
        deadline = time.time() + 8.0
        while not uv_srv.started and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(uv_srv.started)

        b = LocalBrowser("ui-continue-run", lambda h, u: False, lambda m: None, None, window="hidden")
        self.addCleanup(b.close)
        b.start()
        page = b.page
        js_errors: list[str] = []
        page.on("pageerror", lambda err: js_errors.append(str(err)))
        page.on("console", lambda msg: js_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        page.wait_for_selector('.src-cont[data-continue="yandex"]', timeout=10000)

        # Переходим во вкладку «Прогоны» — там тоже видна кнопка «Продолжить» у упавшего прогона Яндекса
        page.click('.nav button[data-view="runs"]')
        page.wait_for_selector('#view-runs .rp-cont[data-src="yandex"]', timeout=5000)

        # Клик по «Продолжить» в боковой панели НЕ должен снимать галочку чекбокса .src-run[value="yandex"]
        was_checked = page.is_checked('.src-run[value="yandex"]')
        self.assertTrue(was_checked)

        # Чиним сайт и жмём «Продолжить» прямо в боковой панели
        ctl["fail_p2"] = False
        page.click('.src-cont[data-continue="yandex"]')
        self.assertTrue(page.is_checked('.src-run[value="yandex"]'), "клик по «Продолжить» не переключает чекбокс сайта")

        _wait_idle(mgr)
        # Уводим мышь с #sources, чтобы очередной тик poll() обновил и боковую панель, и вкладку «Прогоны»
        page.mouse.move(600, 100)
        page.wait_for_function(
            """() => {
                const badge = document.querySelector('#view-runs .rp-r .st-badge');
                return !document.querySelector('.src-cont[data-continue="yandex"]')
                    && document.querySelectorAll('#view-runs .rp-cont').length === 0
                    && badge && badge.textContent.trim() === 'готово';
            }""",
            timeout=8000,
        )
        self.assertEqual(js_errors, [], f"ошибки JS в браузере: {js_errors}")


if __name__ == "__main__":
    unittest.main()
