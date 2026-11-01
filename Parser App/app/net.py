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


class IpBanned(Exception):
    """Сайт временно ограничил IP за частые запросы — нужна пауза, а не человек."""


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
        if r.status_code in (401, 403, 429, 439) or self.is_blocked(r.text, str(r.url)):
            raise Blocked(f"HTTP {r.status_code} {r.url}")
        if r.status_code >= 400:
            raise IOError(f"HTTP {r.status_code} {url}")
        return r

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


OFFSCREEN = -32000


class Browser:
    """Chromium с постоянным профилем.
    window: hidden — полноценное окно за пределами экрана (по умолчанию: незаметно и без признаков headless),
            visible — обычное окно, headless — без окна (быстрее, но сайты чаще распознают бота).
    При капче скрытое окно выводится на экран, пока её не пройдёт человек."""

    def __init__(self, site, is_blocked, log, stop, window="hidden", captcha_wait_min=10, is_ip_banned=None):
        self.site = site
        self.is_blocked = is_blocked
        self.is_ip_banned = is_ip_banned or (lambda html, url: False)
        self.log = log
        self.stop = stop
        self.window = window
        self.captcha_wait = captcha_wait_min * 60
        self._pw = None
        self.ctx = None
        self.page = None
        self.last_status = None

    def _nav(self, url):
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.last_status = resp.status if resp else None  # код ответа сайта — нужен для проверки «страница не найдена»

    def start(self):
        if self.ctx:
            return
        from patchright.sync_api import sync_playwright

        self.log(f"запускаю браузер ({self.window})")
        self._pw = sync_playwright().start()
        opts = dict(locale="ru-RU", timezone_id="Europe/Moscow", headless=self.window == "headless")
        if self.window == "headless":
            opts["viewport"] = {"width": 1440, "height": 900}
        else:
            opts["no_viewport"] = True
            opts["args"] = (["--start-maximized"] if self.window == "visible"
                            else [f"--window-position={OFFSCREEN},{OFFSCREEN}", "--window-size=1440,900",
                                  # иначе Windows считает окно перекрытым и страница становится visibilityState=hidden
                                  "--disable-features=CalculateNativeWinOcclusion",
                                  "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"])
        self.ctx = self._pw.chromium.launch_persistent_context(str(PROFILES / self.site / "browser"), **opts)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def _place_window(self, left, top):
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"left": left, "top": top, "width": 1366, "height": 900}})
            cdp.detach()
            if left >= 0:
                self.page.bring_to_front()
        except Exception as e:
            self.log(f"не удалось переместить окно: {e}")

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
        self._nav(url)
        pause(settle, self.stop)
        self._humanize()
        html = self.page.content()
        if self.is_blocked(html, self.page.url):
            # Иногда это «мягкая» проверка, которая проходит сама после перезагрузки
            self.log("проверка безопасности — пробую перезагрузить")
            pause((8, 14), self.stop)
            self._nav(url)
            pause(settle, self.stop)
            html = self.page.content()
        if self.is_ip_banned(html, self.page.url) and "Проверка пройдена" not in html:
            # Ограничение IP за частоту запросов: человек тут не поможет, нужна пауза (её делает парсер)
            raise IpBanned(self.page.url)
        if self.is_blocked(html, self.page.url):
            if self.window == "headless":
                raise Blocked("капча в режиме без окна — переключите «Окно браузера» на «скрытое» или «видимое»")
            if self.window == "hidden":
                self._place_window(80, 60)
            self.log(f"⚠ НУЖНА РУЧНАЯ ПРОВЕРКА: пройдите капчу в окне браузера ({self.captcha_wait // 60} мин)")
            end = time.time() + self.captcha_wait
            last_retry = time.time()
            while time.time() < end:
                pause((3, 3), self.stop)
                try:
                    html = self.page.content()
                except Exception:
                    continue
                # «Проверка пройдена, перенаправление...» — сайт снял ограничение, но сам не переходит
                passed = "Проверка пройдена" in html and "captcha" not in html.lower()
                if self.is_blocked(html, self.page.url) and (passed and time.time() - last_retry > 10):
                    self.log("сайт пишет «проверка пройдена» — открываю страницу заново")
                    last_retry = time.time()
                    try:
                        self._nav(url)
                        pause(settle, self.stop)
                        html = self.page.content()
                    except Exception:
                        continue
                if not self.is_blocked(html, self.page.url):
                    self.log("проверка пройдена")
                    if self.window == "hidden":
                        self._place_window(OFFSCREEN, OFFSCREEN)
                    if self.page.url.rstrip("/") != url.rstrip("/"):
                        self._nav(url)
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
