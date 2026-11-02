"""Подробный журнал прогона (настоящий HTTP-слой против локального сервера), снимки страниц при сбое,
статус «неполный», возврат паузы Авито после чистых сборов, допуск на пропадание."""
import json
import threading
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import runlog, runner, settings
from app.scrapers import cian as cian_mod
from app.scrapers.avito import Avito
from app.scrapers.cian import Cian
from tests.support import Case, avito_page, cian_offer, con, last_run, listings, run

ROWS = listings("cian", 60)
FAIL_PAGE = {"n": None}


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        page = body["jsonQuery"]["page"]["value"]
        if page == FAIL_PAGE["n"]:
            data, code = b"<html>502 Bad Gateway</html>", 502
        else:
            chunk = [cian_offer(r) for r in ROWS[(page - 1) * 28: page * 28]]
            data, code = json.dumps({"data": {"offersSerialized": chunk, "aggregatedCount": len(ROWS)}}).encode(), 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


class RunLog(Case):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Api)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.api = cian_mod.API
        cian_mod.API = f"http://127.0.0.1:{cls.srv.server_port}/api"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cian_mod.API = cls.api

    def setUp(self):
        super().setUp()
        cfg = settings.load()
        cfg["sites"]["cian"]["mode"] = "http"
        settings.save(cfg)
        FAIL_PAGE["n"] = None

    def test_requests_and_summary_are_logged(self):
        run("cian", Cian)
        r = last_run()
        self.assertEqual((r["status"], r["declared"], r["collected"], r["requests"]), ("done", 60, 60, 3))
        text = runlog.run_log_path(r["id"]).read_text(encoding="utf-8")
        self.assertEqual(text.count("→ http    POST 200"), 3)
        self.assertIn("выдача: прочитано страниц 3 из 3, заявлено сайтом 60, собрано уникальных 60", text)
        self.assertIn("итог: заявлено сайтом 60, собрано уникальных 60, запросов 3", text)

    def test_server_error_saves_page(self):
        FAIL_PAGE["n"] = 2
        run("cian", Cian)
        r = last_run()
        self.assertEqual(r["status"], "error")
        text = runlog.run_log_path(r["id"]).read_text(encoding="utf-8")
        self.assertIn("POST 502", text)
        snaps = list(runlog.SNAP_DIR.glob(f"{r['id']:06d}_cian_*"))
        self.assertEqual(len(snaps), 1)
        self.assertIn("502 Bad Gateway", snaps[0].read_text(encoding="utf-8"))


class DelayEasing(Case):
    def test_delay_returns_to_base_after_clean_runs(self):
        cfg = settings.load()
        cfg["sites"]["avito"].update(delay=[14, 23], delay_base=[6, 10])
        settings.save(cfg)
        rows = listings("avito", 40)

        class A(Avito):
            def _get_search_html(self, url, page=1):
                return avito_page(rows, len(rows))

            def enrich(self, item):
                return item
        for _ in range(3):
            run("avito", A)
        self.assertEqual(settings.load()["sites"]["avito"]["delay"], [9, 15])
        for _ in range(3):
            run("avito", A)
        self.assertEqual(settings.load()["sites"]["avito"]["delay"], [6, 10])  # не ниже заданной


class MassLimit(Case):
    def test_limit_grows_with_pause(self):
        c = con()
        t0 = datetime(2026, 11, 1, 12)
        expected = {3: 0.25, 30: 0.40, 90: 0.70, 365: 0.95}
        for days, share in expected.items():
            prev = {str(i): (t0 - timedelta(days=days)).isoformat(timespec="seconds") for i in range(400)}
            limit, _, tau, _ = runner.mass_limit(c, "cian", prev, t0.isoformat(timespec="seconds"))
            self.assertAlmostEqual(limit / 400, share, delta=0.05, msg=f"{days} дн. при τ={tau:.0f}")


if __name__ == "__main__":
    unittest.main()
