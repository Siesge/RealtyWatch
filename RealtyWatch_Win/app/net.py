"""Сетевой слой: быстрый HTTP (curl_cffi с отпечатком Chrome) и стелс-браузер (patchright)
с общим набором cookies в profiles/<сайт>/."""
import json
import random
import sys
import time
from urllib.parse import urlsplit

from curl_cffi import requests as cffi

from . import alerts, human
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
        self.trace = None      # trace(kind, method, url, status, ms, size, note, body, reason) — подробный журнал
        self.last_text = None  # тело последнего ответа (для снимка страницы при сбое)

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

    def _trace(self, *a, **k):
        if self.trace:
            self.trace(*a, **k)

    def request(self, method, url, **kw):
        t0 = time.time()
        try:
            r = self.s.request(method, url, **kw)
        except Exception as e:  # сетевые ошибки curl
            self._trace("http", method, url, None, round((time.time() - t0) * 1000), None, f"{type(e).__name__}: {e}")
            raise IOError(f"{type(e).__name__}: {e}") from e
        ms, text = round((time.time() - t0) * 1000), r.text
        self.last_text = text
        if r.status_code in (401, 403, 429, 439) or self.is_blocked(text, str(r.url)):
            self._trace("http", method, url, r.status_code, ms, len(text), "блокировка/капча", text, f"http {r.status_code} блокировка")
            raise Blocked(f"HTTP {r.status_code} {r.url}")
        if r.status_code >= 400:
            self._trace("http", method, url, r.status_code, ms, len(text), "ошибка сервера", text, f"http {r.status_code}")
            raise IOError(f"HTTP {r.status_code} {url}")
        self._trace("http", method, url, r.status_code, ms, len(text), "" if str(r.url) == url else f"→ {r.url}")
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
    Мышь и прокрутка — как у человека (app.human); переходы по возможности кликом по ссылке страницы,
    страницы объявлений — в новой вкладке (open_tab), как их открывает человек.
    При капче решатель (solver, см. app.captcha) сначала пробует пройти её сам. Не вышло или такой вид капчи
    он не проходит — скрытое окно выводится на экран, звучит сигнал и приходит уведомление: капчу проходит человек."""

    # После капчи сайт сам переходит на нужную страницу — ждём его, не перезагружая (перезагрузка = новая капча)
    REDIRECT_WAIT_S = 45     # сколько ждать собственного перехода сайта после «Проверка пройдена»
    UNKNOWN_PAGE_WAIT_S = 20  # капчи уже нет, но и нужной страницы ещё нет — сколько ждать, прежде чем взять что есть
    COOLDOWN_AFTER_CAPTCHA = (25.0, 45.0)  # пауза перед следующим запросом после капчи
    AUTO_ROUNDS = 3          # капч подряд, которые решатель проходит сам (сайт иногда просит пройти ещё раз)
    AFTER_SOLVE_WAIT_S = 40  # сколько ждать, пока сайт откроет страницу после решённой капчи
    PASSED_TEXT = "Проверка пройдена"
    CLICK_REACT_S = 4.0      # сколько ждать начала перехода после нажатия по ссылке

    def __init__(self, site, is_blocked, log, stop, window="hidden", captcha_wait_min=10, is_ip_banned=None,
                 is_ready=None, label=None, solver=None, blocked_js=None):
        self.site = site
        self.label = label or site
        self.is_blocked = is_blocked
        # blocked_js: условие на JS «страницу закрывает окно проверки» — сайт может показать капчу поверх обычной
        # страницы, и по HTML это не понять (такое окно бывает заготовлено скрытым)
        self.blocked_js = blocked_js
        self.is_ip_banned = is_ip_banned or (lambda html, url: False)
        # is_ready(html, url): на странице уже нужное содержимое (выдача, объявление), а не промежуточная заглушка
        self.is_ready = is_ready or (lambda html, url: True)
        self.solver = solver  # автоматическое прохождение капчи (app.captcha) или None
        self._cooldown = False
        self.trace = None   # см. Http.trace
        self.note = lambda msg: None  # строка только в подробный журнал прогона
        self.keep = lambda body, reason, ext="html": None  # сохранить страницу или картинку в logs/snapshots
        self.captchas = 0   # капч за время жизни браузера (прогон)
        self.solved = 0     # из них пройдено автоматически
        self.log = log
        self.stop = stop
        self.window = window
        self.captcha_wait = captcha_wait_min * 60
        self._pw = None
        self.ctx = None
        self.page = None
        self.mouse = None
        self.last_status = None
        self.last_url = None  # где оказалась последняя открытая страница (в том числе во вкладке, которую уже закрыли)
        self._docs = {}     # вкладка → последний ответ сервера на загрузку её страницы
        self._navs = {}     # вкладка → запрос страницы, которым начался переход (нажатие попало по ссылке)
        self._gt = {}       # вкладка → ответы GeeTest (задание, картинки, итог) с момента загрузки её страницы

    def _nav(self, url):
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.last_status = resp.status if resp else None  # код ответа сайта — нужен для проверки «страница не найдена»

    def start(self):
        if self.ctx:
            return
        from patchright.sync_api import sync_playwright

        self.log(f"запускаю браузер ({self.window})")
        self._pw = sync_playwright().start()
        # песочница Chromium включена: без неё браузер пишет вверху «unsupported command-line flag: --no-sandbox»
        opts = dict(locale="ru-RU", timezone_id="Europe/Moscow", headless=self.window == "headless", chromium_sandbox=True)
        if self.window == "headless":
            opts["viewport"] = {"width": 1440, "height": 900}
        else:
            opts["no_viewport"] = True
            opts["args"] = (["--start-maximized"] if self.window == "visible"
                            else [f"--window-position={OFFSCREEN},{OFFSCREEN}", "--window-size=1440,900",
                                  # иначе Windows считает окно перекрытым и страница становится visibilityState=hidden
                                  "--disable-features=CalculateNativeWinOcclusion",
                                  "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"])
            # Флаг --disable-blink-features=AutomationControlled (его ставит patchright) нужен: без него
            # navigator.webdriver = true. Но Chromium показывает над страницей полосу «unsupported command-line flag»,
            # и окно становится на ~50 px ниже обычного. --test-type убирает полосу, сайту он не виден.
            opts["args"].append("--test-type")
        profile = str(PROFILES / self.site / "browser")
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(profile, **opts)
        except Exception as e:
            if "sandbox" not in str(e).lower():
                raise
            self.log("браузер не запустился с песочницей — запускаю без неё")
            opts["chromium_sandbox"] = False
            self.ctx = self._pw.chromium.launch_persistent_context(profile, **opts)
        self.ctx.on("response", self._on_response)
        self.ctx.on("request", self._on_request)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.mouse = human.Mouse(self.page, self.stop, Stopped)

    def _on_request(self, req):
        """Запрос страницы ушёл — переход начался. Ответа ещё нет, но нажатие уже сработало."""
        try:
            if req.resource_type == "document" and req.is_navigation_request() and req.frame.parent_frame is None:
                self._navs[req.frame.page] = req
        except Exception:
            pass

    def _on_response(self, resp):
        try:
            url = resp.url
            if resp.request.resource_type == "document" and resp.frame.parent_frame is None:
                self._docs[resp.frame.page] = resp
                self._gt[resp.frame.page] = []  # новая страница — прежние задания GeeTest к ней не относятся
            elif "geetest" in url or ("captcha_id=" in url and ("/load" in url or "/verify" in url)):
                # Задание GeeTest страница может получить сразу при загрузке, ещё до нажатия «Пройти проверку»,
                # поэтому ответы запоминаются всё время, а не только пока решатель работает
                got = self._gt.setdefault(resp.frame.page, [])
                got.append(resp)
                del got[:-200]
        except Exception:
            pass

    def gt_responses(self):
        """Ответы GeeTest текущей вкладки (задания, картинки, итоги проверок) с момента загрузки её страницы."""
        return self._gt.get(self.page, [])

    def overlay(self):
        """Страницу закрывает окно проверки (капча поверх обычной страницы)."""
        if not self.blocked_js or self.page is None:
            return False
        try:
            return bool(self.page.evaluate(self.blocked_js))
        except Exception:
            return False

    def _blocked(self, html, url):
        return self.is_blocked(html, url) or self.overlay()

    def clear_overlay(self, url, settle=(2.5, 5.0)):
        """Если страницу закрыло окно проверки (сайт показывает его и посреди просмотра), пройти его."""
        if self.overlay():
            self._pass_captcha(url, settle, self.page.content())
            self._cooldown = True

    def raw_html(self):
        """HTML, который прислал сервер на загрузку текущей страницы (до работы её скриптов), или None."""
        r = self._docs.get(self.page)
        if r is None or r.status != 200 or urlsplit(r.url).netloc != urlsplit(self.page.url).netloc:
            return None
        try:
            return r.text()
        except Exception:
            return None

    def _place_window(self, left, top):
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"left": left, "top": top, "width": 1366, "height": 900}})
            cdp.detach()
            if left >= 0:
                self.page.bring_to_front()
                self.raise_window(left, top)
        except Exception as e:
            self.log(f"не удалось переместить окно: {e}")

    def raise_window(self, left=80, top=60):
        """Поднять окно браузера поверх окна программы. bring_to_front() переключает только вкладку внутри
        браузера — само окно Windows оставляет сзади и лишь мигает кнопкой в панели задач. Окно ищется по
        месту, куда мы его только что поставили; если не нашли — ничего не делаем, это не повод падать."""
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            from ctypes import wintypes

            u = ctypes.windll.user32
            u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            u.SetForegroundWindow.argtypes = [wintypes.HWND]
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            def visit(hwnd, _):
                if not u.IsWindowVisible(hwnd):
                    return True
                name = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(hwnd, name, 64)
                if name.value != "Chrome_WidgetWin_1":  # окно Chromium
                    return True
                r = wintypes.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                if abs(r.left - left) <= 24 and abs(r.top - top) <= 24 and r.right - r.left > 400:
                    found.append(hwnd)
                    return False
                return True

            u.EnumWindows(visit, 0)
            if not found:
                return
            hwnd = wintypes.HWND(found[0])
            u.ShowWindow(hwnd, 9)  # SW_RESTORE — на случай свёрнутого окна
            # коротко «поверх всех» и обратно: так окно выходит вперёд, даже когда Windows не отдаёт фокус
            for order in (-1, -2):  # HWND_TOPMOST, HWND_NOTOPMOST
                u.SetWindowPos(hwnd, wintypes.HWND(order), 0, 0, 0, 0, 0x0003)  # SWP_NOMOVE | SWP_NOSIZE
            u.SetForegroundWindow(hwnd)
        except Exception as e:
            self.log(f"не удалось поднять окно браузера: {e}")

    def _humanize(self):
        """Человек на только что открытой странице: ведёт мышью, немного листает."""
        try:
            for _ in range(random.randint(1, 3)):
                self.mouse.wander()
                self.mouse.wait(random.uniform(0.2, 0.6))
            self.mouse.wheel(random.randint(1, 4))
            self.mouse.wait(random.uniform(0.4, 1.1))
        except Stopped:
            raise
        except Exception:
            pass

    def _trace(self, *a, **k):
        if self.trace:
            self.trace(*a, **k)

    def scroll_through(self, max_bursts=80):
        """Просматривает страницу до конца, как человек: серии прокрутки с паузами, иногда назад."""
        try:
            self.mouse.read_down(lambda: self.page.evaluate(
                "() => window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 80"), max_bursts)
            self.mouse.wait(random.uniform(0.8, 1.5))
        except Stopped:
            raise
        except Exception:
            pass

    def _still(self, el):
        """Где элемент, когда страница перестала ехать. Прямоугольник, снятый сразу после щелчка колёсика,
        показывает прежнее место — страница сдвигается чуть позже, и мышь бьёт мимо ссылки (на целый щелчок,
        100 px). Это ожидание страницы, а не человека, — поэтому в тестах оно не ускоряется."""
        b = el.bounding_box()
        for _ in range(10):
            self.mouse.idle(random.uniform(0.07, 0.11))
            nxt = el.bounding_box()
            if nxt is None or b is None:
                return nxt
            if abs(nxt["y"] - b["y"]) < 0.5:  # два замера на одном месте — страница доехала
                return nxt
            b = nxt
        return b

    def box(self, selector):
        """Прямоугольник элемента в окне (для мыши) или None. Если элемент за краем окна — сначала
        прокрутка к нему колёсиком, как это сделал бы человек."""
        try:
            el = self.page.query_selector(selector)
            if el is None:
                return None
            for _ in range(30):
                b = self._still(el)
                if b is None:
                    return None
                vh = self.page.evaluate("() => window.innerHeight")
                if b["y"] >= 70 and b["y"] + b["height"] <= vh - 40:
                    return b
                n = max(1, min(5, int(abs(b["y"] - vh * 0.45) / human.WHEEL_NOTCH)))
                self.mouse.wheel(n if b["y"] > vh * 0.45 else -n)
            return self._still(el)
        except Stopped:
            raise
        except Exception:
            return None

    def _aim(self, selector):
        """Прицел для human.Mouse.click: где цель сейчас, без прокрутки к ней. Прокрутка сдвинула бы цель
        ещё раз, а курсор уже рядом — нужно только поправиться на то, насколько страница переложилась."""
        def now():
            try:
                el = self.page.query_selector(selector)
                return el.bounding_box() if el is not None else None
            except Exception:
                return None  # страница как раз меняется
        return now

    def _reacted(self):
        """Начался ли переход после нажатия (ушёл запрос страницы). Не начался — нажатие ушло в пустоту:
        цель уехала уже после доводки курсора. Это ожидание страницы, а не человека, — поэтому в тестах
        оно не ускоряется. Ждём вызовом Playwright, а не сном: события (в том числе наш запрос страницы)
        он разбирает только во время своих вызовов, и на простом сне мы бы их не увидели вовсе."""
        end = time.time() + self.CLICK_REACT_S
        while self._navs.get(self.page) is None and time.time() < end:
            if self.stop is not None and self.stop.is_set():
                raise Stopped()
            try:
                self.page.wait_for_timeout(100)
            except Exception:
                time.sleep(0.1)  # страница как раз меняется
        return self._navs.get(self.page) is not None

    def _before_request(self):
        self.start()
        if self._cooldown:
            # сразу после капчи сайт следит особенно внимательно — не торопимся со следующей страницей
            self._cooldown = False
            pause(self.COOLDOWN_AFTER_CAPTCHA, self.stop)

    def goto(self, url, settle=(2.5, 5.0)):
        self._before_request()
        t0 = time.time()
        self._nav(url)
        return self._after_load(url, settle, t0)

    def click_through(self, selector, url, arrived, settle=(2.5, 5.0), timeout=40):
        """Переход, как у человека: курсор к ссылке selector и клик. arrived — условие на JS «открылась нужная
        страница» (или страница капчи). Нет ссылки или переход не случился — обычная загрузка url."""
        self._before_request()
        self.clear_overlay(self.page.url, settle)  # клик по ссылке под окном проверки попал бы в окно
        b = self.box(selector)
        if b is None:
            return self.goto(url, settle)
        t0 = time.time()
        self._docs.pop(self.page, None)
        self._navs.pop(self.page, None)
        if not self.mouse.click(b, self._aim(selector)) or not self._reacted():
            # ссылка переложилась, пока рука шла: нажатие или не состоялось, или ушло в пустоту.
            # Человек в этом случае прокрутит к ссылке и нажмёт ещё раз, а не станет набирать адрес
            self.log("ссылка уехала, пока курсор шёл к ней — прицеливаюсь заново")
            b = self.box(selector)
            if b is not None:
                self.mouse.click(b, self._aim(selector))
        if not self._wait_js(arrived, timeout):
            self.log("переход по ссылке не открыл нужную страницу — открываю её по адресу")
            return self.goto(url, settle)
        doc = self._docs.get(self.page)
        self.last_status = doc.status if doc is not None else None
        return self._after_load(url, settle, t0, "переход кликом по ссылке")

    def open_tab(self, selector, url, settle=(2.5, 5.0)):
        """Страница в новой вкладке, как у человека: клик по ссылке selector (объявления на Авито открываются
        в новой вкладке), а если ссылки нет — новая вкладка по адресу url. Потом вкладка закрывается, и первая
        вкладка (с выдачей) остаётся как была."""
        self._before_request()
        main, main_mouse = self.page, self.mouse
        if selector:
            self.clear_overlay(main.url, settle)  # клик по карточке под окном проверки попал бы в окно
        b = self.box(selector) if selector else None
        t0, tab, note = time.time(), None, "новая вкладка"
        if b is not None:
            before = main.url
            try:
                with self.ctx.expect_page(timeout=15000) as info:
                    self.mouse.click(b, self._aim(selector))
                tab, note = info.value, "клик по карточке, новая вкладка"
            except Stopped:
                raise
            except Exception:
                if main.url != before:  # открылось в этой же вкладке
                    doc = self._docs.get(main)
                    self.last_status = doc.status if doc is not None else None
                    return self._after_load(url, settle, t0, "клик по карточке")
        opened = tab is not None
        if tab is None:
            tab = self.ctx.new_page()
        self.page, self.mouse = tab, human.Mouse(tab, self.stop, Stopped)
        try:
            if opened:
                tab.wait_for_load_state("domcontentloaded", timeout=60000)
                doc = self._docs.get(tab)
                self.last_status = doc.status if doc is not None else None
            else:
                self._nav(url)
            return self._after_load(url, settle, t0, note)
        finally:
            self._docs.pop(tab, None)
            self._navs.pop(tab, None)
            try:
                tab.close()
            except Exception:
                pass
            self.page, self.mouse = main, main_mouse
            try:
                main.bring_to_front()
            except Exception:
                pass

    def _wait_js(self, js, timeout):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.page.evaluate(js):
                    return True
            except Exception:
                pass  # страница как раз меняется
            pause((0.5, 0.5), self.stop)
        return False

    def _after_load(self, url, settle, t0, how=""):
        """Общее для любой загрузки: дать странице догрузиться, проверить на капчу и ограничение IP."""
        pause(settle, self.stop)
        self._humanize()
        html = self.page.content()
        moved = "" if self.page.url == url else f"→ {self.page.url}"
        self._trace("browser", "GET", url, self.last_status, round((time.time() - t0) * 1000), len(html),
                    " · ".join(x for x in (how, moved) if x))
        if self._blocked(html, self.page.url) and self.last_status not in (403, 429, 439):
            # Иногда это «мягкая» проверка, которая проходит сама после перезагрузки (на 403/429/439 — нет: только капча)
            self.log("проверка безопасности — пробую перезагрузить")
            pause((8, 14), self.stop)
            self._nav(url)
            pause(settle, self.stop)
            html = self.page.content()
        if self.is_ip_banned(html, self.page.url) and not self.check_passed():
            # Ограничение IP за частоту запросов: человек тут не поможет, нужна пауза (её делает парсер)
            self._trace("browser", "GET", url, self.last_status, None, len(html), "ограничение IP", html, "ограничение ip")
            raise IpBanned(self.page.url)
        if self._blocked(html, self.page.url):
            html = self._pass_captcha(url, settle, html)
            self._cooldown = True
        self.last_url = self.page.url
        return html

    def check_passed(self):
        """На экране «Проверка пройдена» — сайт сейчас сам откроет страницу. Проверяется только видимый текст:
        в коде страницы Авито эта фраза заготовлена в скрипте всегда, даже пока капча не пройдена."""
        try:
            t = self.page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            return False
        return self.PASSED_TEXT in t or "перенаправление" in t.lower()

    def left_firewall(self):
        """Страница капчи сменилась другой (капча пройдена)."""
        try:
            return not self._blocked(self.page.content(), self.page.url)
        except Exception:
            return False

    def _pass_captcha(self, url, settle, html):
        """На странице капча. Решатель проходит её сам (если сайт просит снова — до AUTO_ROUNDS раз подряд);
        вид, который он не проходит, или неудача — человеку."""
        what = ""
        for _ in range(self.AUTO_ROUNDS if self.solver else 1):
            self.captchas += 1
            what = self.solver.describe(self.page) if self.solver else ""
            self._trace("browser", "GET", url, self.last_status, None, len(html),
                        "капча" + (f": {what}" if what else ""), html, "капча")
            if not self.solver:
                break
            self.log(f"капча: {what}")
            result, what = self.solver.solve(self)
            if result != "solved":
                self.log(f"капча «{what}» — " + ("такую программа сама не проходит" if result == "unsupported"
                                                 else "пройти автоматически не удалось"))
                break
            self.solved += 1
            self.log(f"капча «{what}» пройдена автоматически")
            page_html = self._after_solve(url, settle)
            if page_html is not None:
                return page_html
            html = self.page.content()
            self.log("сайт снова показал капчу")
        return self._ask_human(url, settle, what)

    def _after_solve(self, url, settle):
        """Решатель прошёл капчу: ждём, пока сайт сам откроет страницу. None — сайт снова показал капчу."""
        end = time.time() + self.AFTER_SOLVE_WAIT_S
        blocked_since = None
        while time.time() < end:
            pause((1, 1), self.stop)
            try:
                html, now_url = self.page.content(), self.page.url
            except Exception:
                continue
            if self.check_passed():
                blocked_since = None
                continue
            if self._blocked(html, now_url):
                blocked_since = blocked_since or time.time()
                if time.time() - blocked_since > 12:
                    return None
                continue
            blocked_since = None
            if self.is_ready(html, now_url):
                self.log("проверка пройдена — страница открылась")
                return html
        self.log("сайт не открыл страницу после проверки — открываю её сам")
        self._safe_nav(url, settle)
        html = self.page.content()
        return None if self._blocked(html, self.page.url) else html

    def _ask_human(self, url, settle, what=""):
        if self.window == "headless":
            raise Blocked("капча в режиме без окна — переключите «Окно браузера» на «скрытое» или «видимое»")
        if self.window == "hidden":
            self._place_window(80, 60)
        self.log(f"⚠ НУЖНА РУЧНАЯ ПРОВЕРКА: пройдите капчу в окне браузера ({self.captcha_wait // 60} мин). "
                 "После ввода окно само перейдёт на страницу — его не нужно обновлять")
        alerts.human_needed(f"{self.label}: нужна проверка",
                            f"Пройдите капчу{f' ({what})' if what else ''} в окне браузера — сбор продолжится сам.")
        html = self._wait_captcha(url, settle)
        if self.window == "hidden":
            self._place_window(OFFSCREEN, OFFSCREEN)
        return html

    def _wait_captcha(self, url, settle):
        """Ждёт, пока человек пройдёт капчу и сайт САМ откроет нужную страницу.
        Пока на экране капча — страницу не трогаем (только читаем). После «Проверка пройдена» сайт переходит
        сам через несколько секунд: ждём до REDIRECT_WAIT_S, и только если он так и не перешёл — открываем
        страницу один раз. Любая лишняя перезагрузка на этом этапе приводит к новой капче."""
        end = time.time() + self.captcha_wait
        passed_at = unknown_at = None
        renavs = 0
        beeped = time.time()
        while time.time() < end:
            pause((2, 2), self.stop)
            try:
                html, now_url = self.page.content(), self.page.url
            except Exception:
                continue  # страница как раз переходит — читаем на следующем шаге
            if self.check_passed():
                passed_at = passed_at or time.time()
                if time.time() - passed_at > self.REDIRECT_WAIT_S and renavs < 1:
                    self.log("сайт не перешёл сам после «Проверка пройдена» — открываю страницу один раз")
                    renavs += 1
                    passed_at = None
                    self._safe_nav(url, settle)
                continue
            if self._blocked(html, now_url):
                passed_at = unknown_at = None
                if time.time() - beeped > alerts.REPEAT_S:  # капча всё ещё ждёт — напомнить
                    alerts.captcha()
                    self.raise_window()  # окно могло уйти за программу, пока человека не было
                    beeped = time.time()
                continue  # капча на экране — человек её вводит, не мешаем
            if self.is_ready(html, now_url):
                self.log("проверка пройдена — страница открылась")
                return html
            # Капчи нет, но и нужной страницы нет (главная, другая страница сайта) — ждём ещё немного
            unknown_at = unknown_at or time.time()
            if time.time() - unknown_at > self.UNKNOWN_PAGE_WAIT_S:
                if renavs < 1:
                    self.log("проверка пройдена, но сайт открыл другую страницу — открываю нужную")
                    renavs += 1
                    unknown_at = None
                    self._safe_nav(url, settle)
                    continue
                return html  # пусть парсер сам разберётся, что это за страница
        raise Blocked("капча не пройдена вовремя")

    def _safe_nav(self, url, settle):
        try:
            self._nav(url)
            pause(settle, self.stop)
        except Stopped:
            raise
        except Exception:
            pass

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
        self.ctx = self._pw = self.page = self.mouse = None
