"""Яндекс Недвижимость: window.INITIAL_STATE из HTML выдачи (одинаково для HTTP и браузера)."""
import json
from urllib.parse import urlencode

from .. import rules
from .base import Scraper, to_float, to_int

YALTA_CITY_RGID = "682310"

RENOVATION = {
    "DESIGNER_RENOVATION": "design", "PRIME_RENOVATION": "design",
    "EURO": "euro",
    "COSMETIC_DONE": "cosmetic", "NON_GRANDMOTHER": "cosmetic",
    "GRANDMOTHER": "old",
    "TURNKEY": "finish", "CLEAN": "finish",
    "NEEDS_RENOVATION": "none", "COSMETIC_REQUIRED": "none", "NO_RENOVATION": "none",
    "ROUGH": "none", "PRE_CLEAN": "none", "BEFORE_CLEAN": "none", "WHITE_BOX": "none",
}
SELLER = {"OWNER": "owner", "AGENCY": "agent", "AGENT": "agent", "DEVELOPER": "developer"}


def parse_state(html):
    a = html.find("window.INITIAL_STATE = ")
    if a < 0:
        return None
    a += len("window.INITIAL_STATE = ")
    b = html.find("</script>", a)
    return json.loads(html[a:b].strip().rstrip(";"))


def _abs(u):
    return ("https:" + u) if u and u.startswith("//") else u


class Yandex(Scraper):
    name = "yandex"
    label = "Яндекс"

    def is_blocked(self, html, url):
        return "showcaptcha" in url or "smartcaptcha" in (html or "")[:20000].lower()

    def _page_url(self, index):
        f = self.cfg["filters"]
        p = [("priceMin", f["price_min"]), ("priceMax", f["price_max"]), ("areaMin", f["area_min"])]
        p += [("roomsTotal", r) for r in f["rooms"]]
        # Сортировка по дате обязательна: в выдаче «по релевантности» платные объявления повторяются
        # на каждой странице (одно и то же до 14 раз) и вытесняют ~15% обычных — они терялись и считались снятыми
        p.append(("sort", "DATE_DESC"))
        if index > 0:
            p.append(("page", index + 1))  # в URL нумерация с 1, page=2 — вторая страница
        return f"https://realty.yandex.ru/{self.site['region_path']}/kupit/kvartira/?" + urlencode(p)

    def _load(self, index):
        url = self._page_url(index)
        html = self.fetch(lambda: self.http.get(url).text, lambda b: b.goto(url))
        st = parse_state(html)
        if not st:
            raise IOError("Яндекс: нет INITIAL_STATE на странице")
        so = st["search"]["offers"]
        return so.get("entities") or [], so.get("pager") or {}

    def listings(self):
        index, pages, limit = 0, 1, self.site.get("max_pages", 40)
        while index < pages and index < limit:
            offers, pager = self._load(index)
            pages = pager.get("totalPages") or 1
            if not offers and index + 1 < pages:
                self.log(f"страница {index + 1}/{pages} пустая — повторяю")
                self.wait()
                offers, pager = self._load(index)
                if not offers:
                    self.empty_page(index + 1, pages)
            self.progress(page=index + 1, pages=min(pages, limit), total=pager.get("totalItems"))
            self.log(f"страница {index + 1}/{pages}: {len(offers)} объявлений")
            for o in offers:
                if o.get("offerType") == "SELL" and o.get("offerCategory") in (None, "APARTMENT"):
                    yield self._normalize(o)
            index += 1
            if index < pages and index < limit:
                self.wait()
        self.complete = index >= pages

    def verify(self, row):
        """Страница объявления: offerCard.card.active / obsolete."""
        try:
            st = parse_state(self.http.get(row["url"]).text)
        except IOError as e:
            if "HTTP 404" in str(e) or "HTTP 410" in str(e):
                return "removed", "на Яндексе страница объявления не найдена (404)", None
            return None, None, None
        except Exception:
            return None, None, None
        card = ((st or {}).get("offerCard") or {}).get("card") or {}
        if "active" not in card:
            return None, None, None
        price = to_int((card.get("price") or {}).get("value"))
        if card.get("active") and not card.get("obsolete"):
            return "active", "на Яндексе объявление активно, но не попало в поиск (цена или параметры вне фильтра)", price
        return "removed", "на Яндексе объявление снято с публикации", price

    def _normalize(self, o):
        loc = o.get("location") or {}
        pt = loc.get("point") or {}
        b = o.get("building") or {}
        ap = o.get("apartment") or {}
        price = to_int((o.get("price") or {}).get("value"))
        area = to_float((o.get("area") or {}).get("value"))
        rt = o.get("roomsTotal")
        rooms = 0 if str(rt).upper() == "STUDIO" or o.get("studio") else to_int(rt)
        raw_reno = ap.get("renovation") or o.get("decoration")
        desc = o.get("description") or ""
        reno = RENOVATION.get(raw_reno) or rules.renovation_from_text(desc)
        floors_offered = o.get("floorsOffered") or []
        settlement = str(loc.get("settlementRgid") or "")
        images = o.get("mainImages") or o.get("fullImages") or []
        author = o.get("author") or {}
        return {
            "source": self.name,
            "source_id": str(o["offerId"]),
            "url": o.get("shareUrl") or f"https://realty.yandex.ru/offer/{o['offerId']}",
            "title": f"{'Студия' if rooms == 0 else f'{rooms}-к. квартира'}, {area} м², "
                     f"{floors_offered[0] if floors_offered else '?'}/{o.get('floorsTotal') or '?'} эт.",
            "price": price,
            "price_m2": round(price / area) if price and area else None,
            "rooms": rooms,
            "area": area,
            "living_area": to_float((o.get("livingSpace") or {}).get("value")),
            "kitchen_area": to_float((o.get("kitchenSpace") or {}).get("value")),
            "floor": floors_offered[0] if floors_offered else None,
            "floors": o.get("floorsTotal"),
            "address": loc.get("geocoderAddress") or loc.get("address"),
            "lat": pt.get("latitude"),
            "lon": pt.get("longitude"),
            "settlement_outside": bool(settlement) and settlement != YALTA_CITY_RGID,
            "renovation": reno,
            "renovation_raw": raw_reno,
            "building_type": b.get("buildingType"),
            "build_year": b.get("builtYear"),
            "is_new_building": int(bool(o.get("newBuilding") or o.get("flatType") == "NEW_FLAT")),
            "seller_type": SELLER.get(author.get("category")),
            "description": desc,
            "photo": _abs(images[0]) if images else None,
            "published_at": o.get("creationDate"),
            "fetch_mode": self.last_mode,
            "cluster_key": o.get("clusterId"),
            "raw": {"flatType": o.get("flatType"), "clusterId": o.get("clusterId"), "clusterSize": o.get("clusterSize"),
                    "updateDate": o.get("updateDate"), "predictedPrice": ((o.get("predictions") or {}).get("predictedPrice") or {}).get("value"),
                    "partnerName": o.get("partnerName"), "apartments": o.get("apartments")},
        }
