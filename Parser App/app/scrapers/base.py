"""Общая логика парсеров: выбор способа загрузки (HTTP / браузер / авто с откатом)
и автоматическая реакция на ограничение IP (пауза + рост задержки)."""
from datetime import datetime, timedelta, timezone

from ..net import Blocked, Browser, Http, IpBanned, pause

# Ограничение IP: паузы перед повтором (мин) — растут с каждой блокировкой подряд в одном прогоне
BAN_COOLDOWNS_MIN = [10, 20, 40, 60]
DELAY_GROWTH = 1.5
DELAY_CAP = (40.0, 90.0)


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

    def is_ip_banned(self, html, url):
        """Страница «ограничение по IP за частые запросы» (отличается от капчи)."""
        return False

    def listings(self):
        """Генератор нормализованных объявлений (dict)."""
        raise NotImplementedError

    # --- общая инфраструктура ---
    def get_browser(self):
        if self.browser is None:
            b = self.cfg.get("browser", {})
            self.browser = Browser(self.name, self.is_blocked, self.log, self.stop,
                                   window=b.get("window", "hidden"), captcha_wait_min=b.get("captcha_wait_min", 10),
                                   is_ip_banned=self.is_ip_banned)
        return self.browser

    def fetch(self, via_http, via_browser):
        """Загружает данные; при ограничении IP ждёт, увеличивает задержку и повторяет."""
        for attempt in range(len(BAN_COOLDOWNS_MIN) + 1):
            try:
                return self._fetch_once(via_http, via_browser)
            except IpBanned:
                if attempt == len(BAN_COOLDOWNS_MIN):
                    raise Blocked(f"IP ограничен, {attempt} пауз(ы) не помогли — попробуйте позже")
                self._on_ip_ban(BAN_COOLDOWNS_MIN[attempt])

    def _on_ip_ban(self, minutes):
        old = self.delay
        new = (min(round(old[0] * DELAY_GROWTH + 1), DELAY_CAP[0]), min(round(old[1] * DELAY_GROWTH + 2), DELAY_CAP[1]))
        self.delay = new
        self._save_delay(new)
        until = datetime.now() + timedelta(minutes=minutes)
        self.log(f"⛔ {self.label} ограничил доступ по IP: задержка {old[0]:g}–{old[1]:g} с ведёт к блокировке. "
                 f"Задержка увеличена до {new[0]:g}–{new[1]:g} с. Пауза {minutes} мин, продолжу в {until:%H:%M}")
        self.progress(paused_until=until.isoformat(timespec="seconds"), delay=list(new))
        pause((minutes * 60, minutes * 60), self.stop)
        self.progress(paused_until=None)
        self.log("пауза окончена — продолжаю с той же страницы")

    def _save_delay(self, delay):
        """Новая задержка сохраняется в настройках — следующие прогоны начинают уже с неё."""
        from .. import settings

        cfg = settings.load()
        cfg["sites"][self.name]["delay"] = list(delay)
        settings.save(cfg)

    def _fetch_once(self, via_http, via_browser):
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
