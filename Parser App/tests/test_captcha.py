"""Капча: после ввода сайт сам переходит на страницу — программа не должна перезагружать её в это время
(перезагрузка = новая капча) и не должна принимать промежуточную страницу за выдачу.
Имитация сайта: первая капча не проходит сама (окно ещё скрыто, программа пробует перезагрузить),
вторую «вводит человек» через 4 с, «Проверка пройдена…» переходит дальше через 5 с."""
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import net, paths

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(paths.BROWSERS))
HAS_BROWSER = paths.BROWSERS.exists() and any(paths.BROWSERS.glob("chromium-*"))

STATS = {"captchas": 0, "premature": 0}
CAPTCHA = """<html><body><div id="firewall-container">Доступ ограничен: подтвердите, что вы не робот</div>
<script>setTimeout(() => { document.cookie = "st=pending; path=/"; location.href = "/passed"; }, 4000);</script></body></html>"""
PASSED = """<html><body>Проверка пройдена, перенаправление…
<script>setTimeout(() => { document.cookie = "st=ok; path=/"; location.href = "/search"; }, 5000);</script></body></html>"""
CONTENT = '<html><body><span data-marker="page-title/count">2</span><div data-marker="item">1</div><div data-marker="item">2</div></body></html>'


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        cookie, extra = self.headers.get("Cookie") or "", []
        if self.path.startswith("/passed"):
            body = PASSED
        elif "st=ok" in cookie:
            body = CONTENT
        else:
            if "st=pending" in cookie:
                STATS["premature"] += 1  # перезагрузили, не дождавшись перехода сайта
            STATS["captchas"] += 1
            body = CAPTCHA if STATS["captchas"] > 1 else CAPTCHA.split("<script>")[0] + "</body></html>"
            extra = [("Set-Cookie", "st=none; path=/")]
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)


class HeadlessBrowser(net.Browser):
    """Тот же Browser, но без окна на экране и со временным профилем."""
    def start(self):
        if self.ctx:
            return
        from patchright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(tempfile.mkdtemp(), headless=True)
        self.page = self.ctx.pages[0]

    def _place_window(self, *a):
        pass

    def _humanize(self):
        pass


@unittest.skipUnless(HAS_BROWSER, "Chromium не установлен (setup.bat)")
class Captcha(unittest.TestCase):
    def test_waits_for_site_redirect(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        log = []
        b = HeadlessBrowser("t", lambda html, url: "firewall-container" in html, log.append, None, window="hidden",
                            captcha_wait_min=1.5, is_ready=lambda html, url: 'data-marker="item"' in html)
        b.COOLDOWN_AFTER_CAPTCHA = (0, 0)
        self.addCleanup(b.close)
        STATS.update(captchas=0, premature=0)
        html = b.goto(f"http://127.0.0.1:{srv.server_port}/search", settle=(0.5, 0.5))
        self.assertIn('data-marker="item"', html, "должна вернуться выдача, а не промежуточная страница")
        self.assertIn("проверка пройдена — страница открылась", log)
        self.assertEqual(b.captchas, 1)
        self.assertEqual(STATS["premature"], 0, "страницу перезагрузили, не дождавшись перехода сайта")
