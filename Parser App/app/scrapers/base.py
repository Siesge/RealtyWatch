"""Общая логика парсеров: выбор способа загрузки (HTTP / браузер / авто с откатом)."""
from datetime import datetime, timezone

from ..net import Blocked, Browser, Http, pause


def to_float(v):
    try:
        return float(str(v).replace(",", ".").replace("\xa0", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def to_int(v):
    f = to_float(v)
    return int(f) if f is not None else None


def ts_to_iso(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


class Scraper:
    name = ""
    label = ""
    # Поля, которые приходят только со страницы объявления (их не затираем данными выдачи)
    detail_fields = ()

    def __init__(self, cfg, log, stop, progress):
        self.cfg = cfg
        self.site = cfg["sites"][self.name]
        self.mode = self.site.get("mode", "auto")
        self.log = log
        self.stop = stop
        self.progress = progress
        self.delay = tuple(self.site.get("delay", (3, 7)))
        self.http = Http(self.name, self.is_blocked)
        self.browser = None
        self.use_browser = self.mode == "browser"
        self.last_mode = None

    # --- переопределяется в наследниках ---
    def is_blocked(self, html, url):
        return False

    def listings(self):
        """Генератор нормализованных объявлений (dict)."""
        raise NotImplementedError

    # --- общая инфраструктура ---
    def get_browser(self):
        if self.browser is None:
            b = self.cfg.get("browser", {})
            self.browser = Browser(self.name, self.is_blocked, self.log, self.stop,
                                   headless=b.get("headless", False), captcha_wait_min=b.get("captcha_wait_min", 10))
        return self.browser

    def fetch(self, via_http, via_browser):
        """Загружает данные выбранным способом. В режиме auto при блокировке HTTP переходит на браузер."""
        if not self.use_browser:
            try:
                res = via_http()
                self.last_mode = "http"
                self.progress(mode="http")
                return res
            except Blocked as e:
                if self.mode == "http":
                    raise
                self.log(f"HTTP заблокирован ({e}) — переключаюсь на браузер")
                self.use_browser = True
        res = via_browser(self.get_browser())
        self.last_mode = "browser"
        self.progress(mode="browser")
        return res

    def wait(self):
        pause(self.delay, self.stop)

    def close(self):
        if self.browser:
            try:
                self.http.import_browser_cookies(self.browser.cookies())
            except Exception:
                pass
            self.browser.close()
        self.http.save_cookies()
