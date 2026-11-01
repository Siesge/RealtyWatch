"""Авито: разбор HTML выдачи + страницы объявлений (координаты, ремонт, полное описание).
Страницы объявлений запрашиваются только для новых подходящих объявлений и кэшируются в базе.

Список объявлений страницы выдачи берётся из данных для поисковиков (schema.org, <script type="application/ld+json">):
сервер отдаёт их целиком — все 50 объявлений страницы. Карточки на самой странице браузер дорисовывает по мере
прокрутки, и без прокрутки их было 12–50 из 50 (терялась треть выдачи). Карточки страницы дополняют данные
(адрес, описание, фото); карусель рекомендаций в результаты поиска не входит и не собирается."""
import json
import math
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

import lxml.html

from .. import rules
from ..net import Blocked, IpBanned
from .base import Scraper, to_float, to_int

BASE = "https://www.avito.ru"

RENOVATION = {
    "дизайнерский": "design", "евро": "euro", "косметический": "cosmetic", "требует ремонта": "none",
    "без отделки": "none", "черновая": "none", "предчистовая": "none", "чистовая": "finish", "под ключ": "finish",
}

# Состояние страницы объявления: closedItem=true — объявление закрыто (снято с публикации, продано, истёк срок)
_CLOSED = re.compile(r'closedItem\\?"\s*:\s*(true|false)')

_LD_ID = re.compile(r"_(\d+)/?$")
# Карточки вне результатов поиска: карусели рекомендаций («Вам может понравиться» и т.п.)
_SERP_CARDS = '//*[@data-marker="item"][not(ancestor::*[contains(@data-marker, "arousel")])]'

_TITLE = re.compile(r"(?:(\d+)-к\.|(студия)|(своб\. планировка))[^,]*,\s*([\d\s,\.]+)\s*м²(?:,\s*(\d+)/(\d+)\s*эт)?", re.I)


def _txt(el):
    return re.sub(r"\s+", " ", el.text_content()).strip() if el is not None else ""


def _photo_from_list(it):
    """Фото подгружаются лениво: у дальних карточек нет <img src>, но адрес есть в data-marker слайдера."""
    for m in it.xpath('.//*[starts-with(@data-marker, "slider-image/image-")]/@data-marker'):
        url = m[len("slider-image/image-"):]
        if url.startswith("http"):
            return url
    for src in it.xpath(".//img/@src | .//img/@data-src"):
        if src.startswith("http"):
            return src
    return None


def ld_offers(doc):
    """Объявления страницы выдачи из данных для поисковиков (schema.org Product → offers) или None."""
    for s in doc.xpath('//script[@type="application/ld+json"]'):
        try:
            j = json.loads(s.text or "")
        except ValueError:
            continue
        nodes = (j.get("@graph") or [j]) if isinstance(j, dict) else (j if isinstance(j, list) else [])
        for node in nodes:
            offers = node.get("offers") if isinstance(node, dict) else None
            if isinstance(offers, dict):
                offers = offers.get("offers")
            if isinstance(offers, list) and offers and isinstance(offers[0], dict) and "url" in offers[0]:
                return offers
    return None


def _clean_address(s):
    return re.sub(r"\s*·\s*\d+\s+отзыв\w*.*$", "", s or "").strip()


def parse_title(title):
    t = rules.fix_homoglyphs(title or "").replace("\xa0", " ")
    out = {"is_share": t.lower().startswith("доля"), "is_apartments": "апартамент" in t.lower()}
    m = _TITLE.search(t)
    if m:
        out["rooms"] = int(m.group(1)) if m.group(1) else (0 if m.group(2) else None)
        out["area"] = to_float(m.group(4))
        out["floor"] = to_int(m.group(5))
        out["floors"] = to_int(m.group(6))
    return out


class Avito(Scraper):
    name = "avito"
    label = "Авито"
    # Счётчик «N объявлений» у Авито включает соседние города и не совпадает с выдачей — по нему полноту не проверяем
    MIN_COVERAGE = None
    detail_fields = ("lat", "lon", "address", "description", "renovation", "renovation_raw", "building_type",
                     "build_year", "floors", "living_area", "kitchen_area", "details_fetched", "published_at")

    def is_ip_banned(self, html, url):
        # «Доступ ограничен: проблема с IP» — «с него идёт очень много запросов»
        return "проблема с IP" in (html or "")[:30000]

    def is_ready(self, html, url):
        # выдача, страница объявления или каталог (куда ведёт ссылка на удалённое объявление)
        return any(m in html for m in ('data-marker="item"', 'data-marker="page-title/count"', 'data-marker="item-view/'))

    def is_blocked(self, html, url):
        head = (html or "")[:6000]
        return "Доступ ограничен" in head or "firewall-container" in (html or "")[:50000] or "/blocked" in url

    def _page_url(self, page):
        f = self.cfg["filters"]
        parts = urlsplit(self.site["search_url"])
        q = [("pmin", f["price_min"]), ("pmax", f["price_max"]), ("s", 104)]  # s=104 — по дате
        if page > 1:
            q.append(("p", page))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))

    def _region_prefix(self):
        return "/" + urlsplit(self.site["search_url"]).path.strip("/").split("/")[0] + "/"

    def _get_html(self, url):
        return self.fetch(lambda: self.http.get(url).text, lambda b: b.goto(url))

    def _get_search_html(self, url):
        def via_browser(b):
            b.goto(url)
            b.scroll_through()  # карточки дорисовываются по мере прокрутки — листаем до конца
            return b.page.content()
        return self.fetch(lambda: self.http.get(url).text, via_browser)

    def _load_page(self, page):
        """Страница выдачи: (заявлено сайтом, [(номер, данные для поисковиков или None, карточка или None)])."""
        doc = lxml.html.fromstring(self._get_search_html(self._page_url(page)))
        count = to_int(_txt(next(iter(doc.xpath('//*[@data-marker="page-title/count"]')), None)))
        cards = {it.get("data-item-id"): it for it in doc.xpath(_SERP_CARDS) if it.get("data-item-id")}
        offers = ld_offers(doc)
        if offers is None:  # данных для поисковиков нет (сменилась вёрстка) — только карточки страницы
            return count, [(sid, None, card) for sid, card in cards.items()], len(cards)
        out = []
        for o in offers:
            m = _LD_ID.search(urlsplit(o.get("url") or "").path)
            if m:
                out.append((m.group(1), o, cards.get(m.group(1))))
        return count, out, sum(1 for _, _, c in out if c is not None)

    def listings(self):
        page, pages, limit = 1, 1, self.site.get("max_pages", 40)
        seen, declared = set(), None
        prefix = self._region_prefix()
        while page <= pages and page <= limit:
            count, entries, drawn = self._load_page(page)
            # Страница без счётчика и без объявлений — не выдача (после капчи сайт открывает другую страницу).
            # Объявления, которые все уже были на прошлых страницах, — сайт вернул не ту страницу.
            # Это сбой, а не конец списка: повторяем, а затем прерываем прогон без снятия объявлений.
            stale = entries and all(sid in seen for sid, _, _ in entries)
            if count is None and not entries or (not entries and page < pages) or (stale and page < pages):
                what = "без списка объявлений" if not entries else "с объявлениями прошлых страниц"
                self.log(f"страница {page}/{pages} пришла {what} — повторяю")
                self.snapshot_last(f"страница {page} {what}")
                self.wait()
                count, entries, drawn = self._load_page(page)
                stale = entries and all(sid in seen for sid, _, _ in entries)
                if count is None and not entries or (not entries and page < pages) or (stale and page < pages):
                    self.snapshot_last(f"страница {page} {what} повторно")
                    raise IOError(f"страница {page} выдачи пришла {what} дважды — прогон неполный, снятие объявлений не выполняется")
            if count:
                pages, declared = math.ceil(count / 50), count
            batch = []
            for sid, offer, card in entries:
                item = (self._from_list(card, prefix) if card is not None else None) or (self._from_ld(offer, prefix) if offer else None)
                if item and offer and not item.get("price"):
                    item["price"] = to_int(offer.get("price"))
                if item and item["source_id"] not in seen:
                    batch.append(item)
                seen.add(sid)
            self.progress(page=page, pages=min(pages, limit), total=count)
            self.log(f"страница {page}/{pages}: {len(entries)} объявлений (карточек на странице {drawn}), новых в прогоне {len(batch)}")
            yield from batch
            if not entries:  # пустая только последняя страница (пустая в середине — ошибка выше)
                page = pages + 1
                break
            page += 1
            if page <= pages and page <= limit:
                self.wait()
        self.finish(page, pages, limit, len(seen), declared)

    def verify(self, row):
        """Страница объявления. Снято — только по явному признаку: closedItem=true на странице,
        404/410 или редирект со страницы объявления в каталог (объявления с таким номером больше нет)."""
        sid = str(row["source_id"])

        def via_http():
            r = self.http.get(row["url"])
            return r.text, str(r.url), r.status_code

        def via_browser(b):
            html = b.goto(row["url"])
            return html, b.page.url, b.last_status

        try:
            html, url, status = self._fetch_once(via_http, via_browser)
        except (Blocked, IpBanned):
            raise
        except IOError as e:
            if "HTTP 404" in str(e) or "HTTP 410" in str(e):
                return "removed", "на Авито страница объявления не найдена (404)", None
            return None, None, None
        except Exception:
            return None, None, None
        if status in (404, 410):
            return "removed", f"на Авито страница объявления не найдена ({status})", None
        doc = lxml.html.fromstring(html)
        if sid not in url:
            # Сайт увёл со страницы объявления: в каталог — объявления больше нет; куда-то ещё — неизвестно
            if doc.xpath('//*[@data-marker="page-title/count"]') or doc.xpath('//*[@data-marker="item"]'):
                return "removed", "на Авито объявления больше нет: ссылка ведёт в каталог", None
            return None, None, None
        id_el = doc.xpath('//*[@data-marker="item-view/item-id"]')
        m = _CLOSED.search(html)
        if not id_el or sid not in _txt(id_el[0]) or not m:
            return None, None, None  # не распознана страница объявления — статус неизвестен
        if m.group(1) == "true":
            return "removed", "на Авито объявление закрыто (снято с публикации)", None
        price = to_int(re.sub(r"\D", "", _txt(next(iter(doc.xpath('//*[@data-marker="item-view/item-price"]')), None))))
        return "active", "на Авито объявление открыто, но не попало в поиск (цена или параметры вне фильтра)", price

    def _from_list(self, it, prefix):
        a = next(iter(it.xpath('.//a[@data-marker="item-title"]')), None)
        if a is None:
            return None
        href = a.get("href", "")
        if not href.startswith(prefix):  # блок «объявления в соседних городах»
            return None
        title = _txt(a)
        t = parse_title(title)
        price = to_int(next(iter(it.xpath('.//meta[@itemprop="price"]/@content')), None))
        area = t.get("area")
        paras = [_txt(p) for p in it.xpath(".//p")]
        desc = max(paras, key=len) if paras else ""
        img = _photo_from_list(it)
        address = _clean_address(_txt(next(iter(it.xpath('.//*[@data-marker="item-address"]')), None)))
        return {
            "source": self.name,
            "source_id": it.get("data-item-id"),
            "url": BASE + href.split("?")[0],
            "title": rules.fix_homoglyphs(title),
            "price": price,
            "price_m2": round(price / area) if price and area else None,
            "rooms": t.get("rooms"),
            "area": area,
            "floor": t.get("floor"),
            "floors": t.get("floors"),
            "address": address,
            "is_share": t["is_share"],
            "is_apartments_studio": t["is_apartments"] and t.get("rooms") == 0,
            "renovation": rules.renovation_from_text(desc),
            "description": rules.fix_homoglyphs(desc),
            "photo": img,
            "fetch_mode": self.last_mode,
            "raw": {"apartments": t["is_apartments"]},
        }

    def _from_ld(self, offer, prefix):
        """Объявление из данных для поисковиков, когда карточку браузер не дорисовал: название, цена, фото.
        Адрес и описание берутся из базы, если объявление уже было, иначе — со страницы объявления (enrich)."""
        path = urlsplit(offer.get("url") or "").path
        m = _LD_ID.search(path)
        if not m or not path.startswith(prefix):
            return None
        title = rules.fix_homoglyphs((offer.get("name") or "").replace("\xa0", " "))
        t = parse_title(title)
        price, area = to_int(offer.get("price")), t.get("area")
        img = offer.get("image")
        return {
            "source": self.name,
            "source_id": m.group(1),
            "url": BASE + path,
            "title": title,
            "price": price,
            "price_m2": round(price / area) if price and area else None,
            "rooms": t.get("rooms"),
            "area": area,
            "floor": t.get("floor"),
            "floors": t.get("floors"),
            "address": None,
            "is_share": t["is_share"],
            "is_apartments_studio": t["is_apartments"] and t.get("rooms") == 0,
            "renovation": None,
            "description": None,
            "photo": img if isinstance(img, str) and img.startswith("http") else None,
            "fetch_mode": self.last_mode,
            "raw": {"apartments": t["is_apartments"], "from": "schema.org"},
            # чего нет в данных для поисковиков — оставить из базы
            "_keep": ("address", "description", "renovation", "renovation_raw", "photo"),
        }

    def enrich(self, item):
        """Страница объявления: координаты, параметры квартиры и дома, полное описание."""
        html = self._get_html(item["url"])
        doc = lxml.html.fromstring(html)
        m = doc.xpath("//*[@data-map-lat]")
        if m:
            item["lat"] = to_float(m[0].get("data-map-lat"))
            item["lon"] = to_float(m[0].get("data-map-lon"))
        params = {}
        for li in doc.xpath('//*[starts-with(@data-marker, "item-view/")]//li | //*[contains(@class, "params")]//li'):
            t = _txt(li)
            if ":" in t:
                k, v = t.split(":", 1)
                params.setdefault(k.strip().lower(), v.strip())
        raw_reno = params.get("ремонт") or params.get("отделка")
        if raw_reno:
            item["renovation_raw"] = raw_reno
            item["renovation"] = RENOVATION.get(raw_reno.lower()) or rules.renovation_from_text(raw_reno)
        item["living_area"] = to_float((params.get("жилая площадь") or "").replace("м²", ""))
        item["kitchen_area"] = to_float((params.get("площадь кухни") or "").replace("м²", ""))
        item["building_type"] = params.get("тип дома")
        item["build_year"] = to_int(params.get("год постройки"))
        item["floors"] = to_int(params.get("этажей в доме")) or item.get("floors")
        if params.get("способ продажи"):
            item.setdefault("raw", {})["sale_method"] = params["способ продажи"]
        addr = doc.xpath('//h2[normalize-space(.)="Расположение"]/following::span[normalize-space(.)][1]')
        if addr and _txt(addr[0]):
            item["address"] = _clean_address(_txt(addr[0]))
        desc = doc.xpath('//*[@data-marker="item-view/item-description"]')
        if desc:
            item["description"] = rules.fix_homoglyphs(_txt(desc[0]))
            if not item.get("renovation"):
                item["renovation"] = rules.renovation_from_text(item["description"])
        if not item.get("photo"):
            og = doc.xpath('//meta[@property="og:image"]/@content')
            if og and og[0].startswith("http"):
                item["photo"] = og[0]
        date = doc.xpath('//*[@data-marker="item-view/item-date"]')
        if date:
            item.setdefault("raw", {})["date_text"] = _txt(date[0]).strip(" ·")
        item["details_fetched"] = 1
        item["fetch_mode"] = self.last_mode
        return item
