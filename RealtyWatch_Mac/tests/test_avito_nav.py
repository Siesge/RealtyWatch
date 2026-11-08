"""Авито через браузер, как человек (локальная имитация сайта, настоящий Chromium без окна):
- режим «авто» не делает ни одного HTTP-запроса — сразу браузер;
- выдача листается кнопкой «Следующая страница»: у запроса есть Referer прошлой страницы и
  Sec-Fetch-Site: same-origin, как при клике человека, а не при наборе адреса;
- объявления открываются в новой вкладке кликом по карточке; выдача в первой вкладке остаётся открытой;
- карточки, которые скрипт страницы убрал после загрузки, берутся из ответа сервера;
- первая страница с недобором карточек (меньше 50, не последняя) сохраняется для разбора."""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from app import net, runlog, settings
from app.scrapers import avito
from tests.support import Case, LocalBrowser, con, fast_timing, listings, needs_browser, run

COUNT = 190                     # заявлено сайтом → 4 страницы по 50
SIZES = {1: 50, 2: 50, 3: 30, 4: 40}  # на 3-й сервер прислал меньше 50
REMOVED_ON_2 = 20               # скрипт 2-й страницы убирает 20 карточек после загрузки
ROWS = listings("avito", sum(SIZES.values()))


def _rows(p):
    start = sum(SIZES[i] for i in range(1, p))
    return ROWS[start:start + SIZES[p]]


def _link(p):
    return f"/yalta/kvartiry/prodam-TEST?f=abc&p={p}&s=104"


def search_page(p):
    cards = "".join(
        f'<div data-marker="item" data-item-id="{r["source_id"]}" style="height:170px;border-bottom:1px solid #ddd">'
        f'<a data-marker="item-title" target="_blank" rel="noopener" href="/yalta/kvartiry/x_{r["source_id"]}?context=abc">{r["title"]}</a>'
        f'<meta itemprop="price" content="{r["price"]}"><p>{r["description"]}</p><span data-marker="item-address">{r["address"]}</span></div>'
        for r in _rows(p))
    pager = "".join(f'<span data-marker="pagination-button/page({n})">{n}</span>' if n == p else
                    f'<a data-marker="pagination-button/page({n})" href="{_link(n)}">{n}</a>' for n in SIZES)
    if p < len(SIZES):
        pager += f'<a data-marker="pagination-button/nextPage" href="{_link(p + 1)}">Следующая страница</a>'
    script = (f"<script>[...document.querySelectorAll('[data-marker=item]')].slice({SIZES[2] - REMOVED_ON_2}).forEach(el => el.remove())</script>"
              if p == 2 else "")
    return (f'<html><head><title>Квартиры в Ялте</title></head><body><h1>Квартиры <span data-marker="page-title/count">{COUNT}</span></h1>'
            f'<div data-marker="catalog-serp">{cards}</div><div data-marker="pagination-button" style="padding:30px">{pager}</div>'
            f'<div style="height:400px">Популярные запросы</div>{script}</body></html>')


def item_page(sid):
    return (f'<html><body><div data-marker="item-view/item-id">№ {sid}</div><div data-map-lat="44.4841" data-map-lon="34.1442"></div>'
            f'<div data-marker="item-view/item-description">Квартира с евроремонтом, вид на море</div>'
            '<script>window.__s = {"closedItem":false}</script></body></html>')


class Site(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlsplit(self.path)
        h = {k: self.headers.get(k) or "" for k in ("Referer", "Sec-Fetch-Site", "Sec-Fetch-User")}
        if u.path.startswith("/yalta/kvartiry/prodam-TEST"):
            p = int(parse_qs(u.query).get("p", ["1"])[0])
            Site.hits.append(("search", p, h))
            body = search_page(p)
        elif u.path.startswith("/yalta/kvartiry/x_"):
            sid = u.path.rsplit("_", 1)[1]
            Site.hits.append(("item", sid, h))
            body = item_page(sid)
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)


@needs_browser
class AvitoLikeHuman(Case):
    def setUp(self):
        super().setUp()
        fast_timing(self)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_port}"
        orig_base = avito.BASE
        avito.BASE = base
        self.addCleanup(setattr, avito, "BASE", orig_base)
        cfg = settings.load()
        cfg["sites"]["avito"].update(search_url=base + "/yalta/kvartiry/prodam-TEST", max_details=4)
        settings.save(cfg)
        self.http_calls = []
        orig_req = net.Http.request
        net.Http.request = lambda s, method, url, **kw: self.http_calls.append(url)
        self.addCleanup(setattr, net.Http, "request", orig_req)
        Site.hits = []

    def test_reads_avito_like_a_person(self):
        browsers = []

        class A(avito.Avito):
            def get_browser(self):
                if self.browser is None:
                    self.browser = LocalBrowser(self.name, self.is_blocked, self.log, self.stop, is_ip_banned=self.is_ip_banned,
                                                is_ready=self.is_ready, label=self.label, solver=self.captcha_solver())
                    self.browser.trace = self._trace
                    browsers.append(self.browser)
                return self.browser
        try:
            log = run("avito", A)
        finally:
            for b in browsers:
                b.close()
        ids = {r[0] for r in con().execute("SELECT source_id FROM listings WHERE source='avito'")}
        self.assertEqual(ids, {r["source_id"] for r in ROWS}, "собраны все объявления, в том числе убранные скриптом страницы")
        self.assertEqual(self.http_calls, [], "в режиме «авто» Авито не получает быстрых HTTP-запросов")
        self.assertIn("старт, режим auto (через браузер)", log)
        self.assertTrue(any(f"страница 2/4: {SIZES[2]} объявлений (в ответе сервера {SIZES[2]}, на странице {SIZES[2] - REMOVED_ON_2})" in m
                            for m in log), "2-я страница: карточки из ответа сервера дополняют страницу")

        searches = {p: h for kind, p, h in Site.hits if kind == "search"}
        self.assertEqual(sorted(searches), [1, 2, 3, 4], "каждая страница выдачи открыта один раз")
        for p in (2, 3, 4):
            self.assertIn(f"p={p - 1}" if p > 2 else "prodam-TEST", searches[p]["Referer"], f"стр. {p}: переход со страницы {p - 1}; журнал: {log}")
            self.assertEqual(searches[p]["Sec-Fetch-Site"], "same-origin", f"стр. {p}: переход кликом, а не по адресу")
            self.assertEqual(searches[p]["Sec-Fetch-User"], "?1", f"стр. {p}: переход по действию человека")

        items = [h for kind, _, h in Site.hits if kind == "item"]
        self.assertEqual(len(items), 4, "открыто столько объявлений, сколько разрешено настройкой")
        for h in items:
            self.assertIn("prodam-TEST", h["Referer"], "объявление открыто из выдачи")
            self.assertEqual(h["Sec-Fetch-Site"], "same-origin", "объявление открыто кликом по карточке")
        fetched = con().execute("SELECT COUNT(*) FROM listings WHERE source='avito' AND details_fetched=1 AND lat IS NOT NULL").fetchone()[0]
        self.assertEqual(fetched, 4, "со страниц объявлений взяты координаты")

        snaps = [p.name for p in runlog.SNAP_DIR.glob("*.html")]
        self.assertTrue(any("страница_3" in n for n in snaps), f"страница с недобором карточек сохранена для разбора: {snaps}")
        self.assertFalse(any("страница_2" in n or "страница_4" in n for n in snaps), "сохраняется только первая такая страница")
