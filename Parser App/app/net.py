"""Сетевой слой: быстрый HTTP (curl_cffi с отпечатком Chrome) и стелс-браузер (patchright)
с общим набором cookies в profiles/<сайт>/."""
import json
import random
import time

from curl_cffi import requests as cffi

from .paths import PROFILES


class Blocked(Exception):
    """Сайт показал капчу / запрет доступа."""


class Stopped(Exception):
    """Пользователь остановил прогон."""


def pause(delay, stop=None):
    t = random.uniform(*delay)
    end = time.time() + t
    while time.time() < end:
        if stop is not None and stop.is_set():
            raise Stopped()
        time.sleep(0.2)


class Http:
    def __init__(self, site, is_blocked):
        self.site = site
        self.is_blocked = is_blocked
        self.cookie_file = PROFILES / site / "http_cookies.json"
        self.s = cffi.Session(impersonate="chrome", timeout=40)
        self.s.headers.update({"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.6"})
        self._load_cookies()

    def _load_cookies(self):
        if self.cookie_file.exists():
            try:
                for c in json.loads(self.cookie_file.read_text(encoding="utf-8")):
                    self.s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
            except (OSError, ValueError, KeyError):
                pass

    def save_cookies(self):
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        out = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self.s.cookies.jar]
        self.cookie_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    def import_browser_cookies(self, cookies):
        for c in cookies:
            self.s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        self.save_cookies()

    def request(self, method, url, **kw):
        try:
            r = self.s.request(method, url, **kw)
        except Exception as e:  # сетевые ошибки curl
            raise IOError(f"{type(e).__name__}: {e}") from e
        if r.status_code in (401, 403, 429) or self.is_blocked(r.text, str(r.url)):
            raise Blocked(f"HTTP {r.status_code} {r.url}")
        if r.status_code >= 400:
            raise IOError(f"HTTP {r.status_code} {url}")
        return r

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


class Browser:
    """Видимое окно Chromium с постоянным профилем. При капче ждёт, пока её пройдёт человек."""

    def __init__(self, site, is_blocked, log, stop, headless=False, captcha_wait_min=10):
        self.site = site
        self.is_blocked = is_blocked
        self.log = log
        self.stop = stop
        self.headless = headless
        self.captcha_wait = captcha_wait_min * 60
        self._pw = None
        self.ctx = None
        self.page = None

    def start(self):
        if self.ctx:
            return
        from patchright.sync_api import sync_playwright

        self.log("запускаю браузер")
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILES / self.site / "browser"),
            headless=self.headless,
            no_viewport=True,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            args=["--start-maximized"],
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def _humanize(self):
        try:
            for _ in range(random.randint(2, 4)):
                self.page.mouse.move(random.randint(200, 1100), random.randint(150, 700), steps=random.randint(8, 20))
                self.page.mouse.wheel(0, random.randint(250, 700))
                time.sleep(random.uniform(0.4, 1.1))
        except Exception:
            pass

    def goto(self, url, settle=(2.5, 5.0)):
        self.start()
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        pause(settle, self.stop)
        self._humanize()
        html = self.page.content()
        if self.is_blocked(html, self.page.url):
            # Иногда это «мягкая» проверка, которая проходит сама после перезагрузки
            self.log("проверка безопасности — пробую перезагрузить")
            pause((8, 14), self.stop)
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            pause(settle, self.stop)
            html = self.page.content()
        if self.is_blocked(html, self.page.url):
            self.log(f"⚠ НУЖНА РУЧНАЯ ПРОВЕРКА: пройдите капчу в окне браузера ({self.captcha_wait // 60} мин)")
            end = time.time() + self.captcha_wait
            while time.time() < end:
                pause((3, 3), self.stop)
                try:
                    html = self.page.content()
                except Exception:
                    continue
                if not self.is_blocked(html, self.page.url):
                    self.log("проверка пройдена")
                    if self.page.url.rstrip("/") != url.rstrip("/"):
                        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        pause(settle, self.stop)
                        html = self.page.content()
                    break
            else:
                raise Blocked("капча не пройдена вовремя")
        return html

    def evaluate(self, js):
        return self.page.evaluate(js)

    def cookies(self):
        return self.ctx.cookies() if self.ctx else []

    def close(self):
        try:
            if self.ctx:
                self.ctx.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self.ctx = self._pw = self.page = None
