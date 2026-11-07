"""Нажатие по ссылке, когда страница перекладывается под курсором (локальная имитация сайта,
настоящий Chromium без окна).

Место ссылки измеряется до того, как рука к ней пойдёт, а идёт она почти секунду. За это время страница
успевает переложиться — дозагрузились картинки карточек, довернулась прокрутка, — и ссылка уезжает из-под
курсора. Здесь проверяется, что нажатие всё равно попадает по ссылке: переход идёт кликом (у запроса есть
Referer и Sec-Fetch-User), а не набором адреса.

Подвох имитации: Chromium сам шлёт странице mousemove, когда она прокручивается под курсором. Поэтому
«страница переложится при первом же движении руки» включается не в самой странице, а из теста — после того
как прокрутка к ссылке уже сделана. Иначе страница перекладывается ещё при поиске ссылки, до нажатия,
и проверять оказывается нечего.
"""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from app import human
from tests.support import Case, LocalBrowser, fast_timing, has_browser

PAD = 1200           # высота блока объявлений над ссылкой
SHIFT = 260          # на столько ссылка уезжает вниз, когда страница перекладывается
LINK = 'a[data-marker="pagination-button/nextPage"]'
ARRIVED = "() => !!document.querySelector('[data-marker=\"on-page-2\"]')"
# страница переложится при первом же движении курсора — то есть пока рука идёт к ссылке
ARM = ("() => addEventListener('mousemove', () => "
       f"document.getElementById('pad').style.height = {PAD + SHIFT} + 'px', {{once: true}})")
MISSED = "ссылка уехала, пока курсор шёл к ней — прицеливаюсь заново"
GROW = f"() => document.getElementById('pad').style.height = {PAD + SHIFT} + 'px'"

PAGE_1 = ('<html><head><title>Выдача</title></head><body>'
          f'<div id="pad" style="height:{PAD}px">Объявления</div>'
          '<div style="padding:30px"><a data-marker="pagination-button/nextPage" href="/p2">Следующая страница</a></div>'
          '<div style="height:400px">Популярные запросы</div></body></html>')
PAGE_2 = '<html><body><div data-marker="on-page-2">Страница 2</div></body></html>'


class Site(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        Site.hits.append((path, {k: self.headers.get(k) or "" for k in ("Referer", "Sec-Fetch-Site", "Sec-Fetch-User")}))
        data = (PAGE_1 if path == "/" else PAGE_2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)


@unittest.skipUnless(has_browser(), "Chromium не установлен (bin/bootstrap.sh)")
class ClickAim(Case):
    def setUp(self):
        super().setUp()
        fast_timing(self)
        # рука идёт к ссылке как в настоящем прогоне: именно за это время страница и перекладывается
        human.TIME_SCALE = 1.0
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_port}"
        Site.hits = []
        self.log = []
        self.b = LocalBrowser("test", lambda html, url: False, self.log.append, None,
                              is_ready=lambda html, url: True, label="Тест")
        self.addCleanup(self.b.close)

    def _open(self, arm=True):
        """Первая страница, ссылка найдена и прокрутка к ней сделана. arm — страница переложится в пути."""
        self.b.goto(self.base + "/", settle=(0, 0))
        was = self.b.box(LINK)
        if arm:
            self.b.page.evaluate(ARM)
        return was

    def _opened_p2(self):
        return [h for path, h in Site.hits if path == "/p2"]

    def _count_clicks(self):
        """Список нажатий, которые сделала программа (поведение не меняется)."""
        clicks, orig = [], human.Mouse.click

        def counted(mouse, box, recheck=None):
            clicks.append(box)
            return orig(mouse, box, recheck)
        human.Mouse.click = counted
        self.addCleanup(setattr, human.Mouse, "click", orig)
        return clicks

    def _click(self):
        self.b.click_through(LINK, self.base + "/p2", ARRIVED, settle=(0, 0))

    def test_moving_link_is_still_clicked(self):
        was = self._open()
        self._click()
        got = self._opened_p2()
        self.assertEqual(len(got), 1, "страница 2 открыта один раз")
        self.assertEqual(got[0]["Referer"], self.base + "/", "переход с первой страницы: у запроса есть Referer")
        self.assertEqual(got[0]["Sec-Fetch-Site"], "same-origin", "переход кликом по ссылке, а не по адресу")
        self.assertEqual(got[0]["Sec-Fetch-User"], "?1", "переход по действию человека")
        self.assertNotIn("переход по ссылке не открыл нужную страницу — открываю её по адресу", self.log)
        self.assertGreater(was["y"], 0, "ссылка была найдена до нажатия")

    def test_still_page_is_clicked_as_before(self):
        """Страница, которая не перекладывается: переход по-прежнему кликом, лишних нажатий нет."""
        self._open(arm=False)
        clicks = self._count_clicks()
        self._click()
        got = self._opened_p2()
        self.assertEqual(len(got), 1, "страница 2 открыта один раз")
        self.assertEqual(got[0]["Sec-Fetch-Site"], "same-origin")
        self.assertEqual(len(clicks), 1, "попали с первого раза — второго нажатия не было")
        self.assertNotIn(MISSED, self.log)

    def test_aim_follows_the_link(self):
        """Прицел видит новое место ссылки, а не то, которое измерили до движения руки."""
        was = self._open(arm=False)
        aim = self.b._aim(LINK)
        self.b.page.evaluate(GROW)
        now = aim()
        self.assertIsNotNone(now)
        self.assertAlmostEqual(now["y"] - was["y"], SHIFT, delta=2, msg="ссылка уехала вниз, прицел это видит")
        self.assertFalse(human._inside(now, *human.point_in(was)), "прежняя точка нажатия уже вне ссылки")

    def test_missed_click_is_repeated(self):
        """Без сверки нажатие уходит в пустоту — и тогда программа нажимает ещё раз, а не идёт по адресу.
        Первое нажатие делается по-старому (без прицела), остальные — как сейчас."""
        self._open()
        self.b.CLICK_REACT_S = 2.0
        clicks, orig = [], human.Mouse.click

        def blind(mouse, box, recheck=None):
            clicks.append(box)
            return orig(mouse, box, None if len(clicks) == 1 else recheck)
        human.Mouse.click = blind
        self.addCleanup(setattr, human.Mouse, "click", orig)
        self._click()
        self.assertEqual(len(clicks), 2, "первое нажатие ушло в пустоту — программа нажимает второй раз")
        self.assertNotAlmostEqual(clicks[1]["y"], clicks[0]["y"], delta=10, msg="второе нажатие — по новому месту ссылки")
        self.assertIn(MISSED, self.log, "в журнале видно, почему понадобилось второе нажатие")
        got = self._opened_p2()
        self.assertEqual(len(got), 1, "страница 2 открыта один раз")
        self.assertEqual(got[0]["Sec-Fetch-Site"], "same-origin", "и открыта всё-таки кликом")
        self.assertNotIn("переход по ссылке не открыл нужную страницу — открываю её по адресу", self.log)


if __name__ == "__main__":
    unittest.main()
