"""Общая логика парсеров: выбор способа загрузки (HTTP / браузер / авто с откатом)
и автоматическая реакция на ограничение IP (пауза + рост задержки)."""
from datetime import datetime, timedelta, timezone

from ..net import Blocked, Browser, Http, IpBanned, pause
from ..runlog import NullRunLog

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
    # Режим «авто» сразу через браузер, без попытки быстрого HTTP (сайт всё равно его блокирует,
    # а отказ HTTP-клиенту с cookies браузера помечает и сессию браузера)
    AUTO_BROWSER = False
    # Условие на JS «страницу закрывает окно проверки» (капча поверх обычной страницы) или None
    BLOCKED_JS = None

    def __init__(self, cfg, log, stop, progress):
        self.cfg = cfg
        self.site = cfg["sites"][self.name]
        self.mode = self.site.get("mode", "auto")
        self.log = log
        self.stop = stop
        self.progress = progress
        self.delay = tuple(self.site.get("delay", (3, 7)))
        self.runlog = NullRunLog()  # подробный журнал прогона (назначает RunManager)
        self.http = Http(self.name, self.is_blocked)
        self.http.trace = self._trace
        self.browser = None
        self.use_browser = self.mode == "browser" or (self.mode == "auto" and self.AUTO_BROWSER)
        self.last_mode = None
        # True — выдача прочитана полностью и достоверно; только тогда отсутствующие объявления проверяются на снятие
        self.complete = False
        self.incomplete_reason = "прогон не дошёл до конца выдачи"
        self.declared = None  # сколько объявлений заявил сайт
        self.bans = 0         # ограничений IP за прогон

    # --- переопределяется в наследниках ---
    def is_blocked(self, html, url):
        return False

    def is_ready(self, html, url):
        """На странице нужное содержимое (а не промежуточная заглушка после капчи)."""
        return True

    def is_ip_banned(self, html, url):
        """Страница «ограничение по IP за частые запросы» (отличается от капчи)."""
        return False

    # Доля объявлений от заявленного сайтом числа, которую надо собрать, чтобы выдача считалась полной
    # (None — у сайта счётчик неточный, см. Авито)
    MIN_COVERAGE = 0.95

    def verify(self, row):
        """Проверка объявления, пропавшего из поиска, по его странице на сайте.
        Возвращает (state, reason, price): state — 'active' | 'removed' | None (не удалось узнать).
        'removed' — только по явному признаку: страница не найдена (404/410, редирект в каталог)
        или сайт показывает статус «снято/закрыто». Любой сбой загрузки — None, такое объявление не снимается.
        Капча и ограничение IP пробрасываются (Blocked/IpBanned): проверка остальных прекращается."""
        return None, None, None

    @property
    def captchas(self):
        return self.browser.captchas if self.browser else 0

    @property
    def solved(self):
        """Капч, пройденных автоматически."""
        return self.browser.solved if self.browser else 0

    @property
    def mode_label(self):
        return f"{self.mode} (через браузер)" if self.mode == "auto" and self.AUTO_BROWSER else self.mode

    def captcha_solver(self):
        """Автоматическое прохождение капчи этого сайта (app.captcha) или None — капчу проходит человек."""
        return None

    def _trace(self, kind, method, url, status=None, ms=None, size=None, note="", body=None, reason=None):
        self.runlog.request(kind, method, url, status, ms, size, note)
        if body is not None and reason:
            self.runlog.snapshot(body, reason, "json" if body.lstrip()[:1] in "{[" else "html")

    def snapshot_last(self, reason):
        """Сохраняет последнюю полученную страницу (при пустой или странной странице выдачи)."""
        body = None
        try:
            if self.last_mode == "browser" and self.browser and self.browser.page:
                try:
                    body = self.browser.page.content()
                except Exception:
                    body = None
                if not body:
                    body = self.browser.raw_html()
            else:
                body = self.http.last_text
        except Exception:
            pass
        if body:
            self.runlog.snapshot(body, reason, "json" if body.lstrip()[:1] in "{[" else "html")

    def finish(self, page, pages, limit, found, declared):
        """Решает, можно ли доверять выдаче настолько, чтобы искать по ней снятые объявления."""
        self.declared = declared
        self.runlog.line(f"выдача: прочитано страниц {page - 1} из {pages}, заявлено сайтом "
                         f"{'—' if declared is None else declared}, собрано уникальных {found}")
        why = None
        if page <= pages:
            why = f"прочитано {page - 1} из {pages} страниц" + (f" (ограничение {limit} страниц в настройках)" if pages > limit else "")
        elif declared is None:
            why = "сайт не сообщил число объявлений в выдаче"
        elif found == 0 and declared > 0:
            why = f"сайт заявил {declared} объявлений, а получено 0"
        elif self.MIN_COVERAGE and declared and found < self.MIN_COVERAGE * declared:
            why = f"получено {found} из {declared} заявленных сайтом (меньше {self.MIN_COVERAGE:.0%})"
        self.complete = why is None
        self.incomplete_reason = why or ""
        if why:
            self.log(f"⚠ выдача неполная: {why} — снятие объявлений не проверяется")

    def empty_page(self, page, pages):
        """Пустая страница посреди выдачи — сбой (капча, ограничение), а не конец списка."""
        self.snapshot_last(f"пустая страница {page}")
        raise IOError(f"страница {page} из {pages} пришла пустой — прогон неполный, снятие объявлений не выполняется")

    def listings(self):
        """Генератор нормализованных объявлений (dict)."""
        raise NotImplementedError

    # --- общая инфраструктура ---
    def get_browser(self):
        if self.browser is None:
            b = self.cfg.get("browser", {})
            self.browser = Browser(self.name, self.is_blocked, self.log, self.stop,
                                   window=b.get("window", "hidden"), captcha_wait_min=b.get("captcha_wait_min", 10),
                                   is_ip_banned=self.is_ip_banned, is_ready=self.is_ready, label=self.label,
                                   solver=self.captcha_solver(), blocked_js=self.BLOCKED_JS)
            self.browser.trace = self._trace
            self.browser.note = lambda msg: self.runlog.line(msg)
            self.browser.keep = lambda body, reason, ext="html": self.runlog.snapshot(body, reason, ext)
        return self.browser

    def fetch(self, via_http, via_browser):
        """Загружает данные; при ограничении IP ждёт, увеличивает задержку и повторяет."""
        for attempt in range(len(BAN_COOLDOWNS_MIN) + 1):
            try:
                return self._fetch_once(via_http, via_browser)
            except IpBanned:
                self.bans += 1
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
        site = cfg["sites"][self.name]
        # Заданная пауза запоминается до первого роста — к ней пауза вернётся после чистых сборов
        site.setdefault("delay_base", list(site.get("delay") or self.delay))
        site["delay"] = list(delay)
        site["clean_runs"] = 0
        settings.save(cfg)

    def _fetch_once(self, via_http, via_browser):
        """Загружает данные выбранным способом. В режиме auto при блокировке HTTP переходит на браузер."""
        if not self.use_browser:
            try:
                self.last_mode = "http"
                self.progress(mode="http")
                return via_http()
            except Blocked as e:
                if self.mode == "http":
                    raise
                self.log(f"HTTP заблокирован ({e}) — переключаюсь на браузер")
                self.use_browser = True
        b = self.get_browser()
        self.last_mode = "browser"
        self.progress(mode="browser")
        return via_browser(b)

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
