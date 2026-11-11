"""ЦИАН: JSON API поиска (HTTP) или состояние страницы выдачи (браузер)."""
import math
import re
from urllib.parse import urlencode

from .. import rules
from ..net import Blocked, IpBanned
from .base import Scraper, to_float, to_int, ts_to_iso

API = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"
PAGE_SIZE = 28

DECORATION = {"without": "none", "rough": "none", "preFine": "none", "fine": "finish"}
# Поле «Ремонт» в разделе «О квартире» на странице объявления: ему доверяем больше, чем описанию.
# Значение → (код программы, как написано на сайте)
REPAIR = {"no": ("none", "без ремонта"), "cosmetic": ("cosmetic", "косметический"),
          "euro": ("euro", "евроремонт"), "design": ("design", "дизайнерский")}
# Статусы страницы объявления, которые однозначно означают «не в продаже»
STATUS = {"deactivated": "снято с публикации", "removed": "удалено", "draft": "черновик", "blocked": "заблокировано",
          "refused": "отклонено модерацией", "sold": "продано"}
# Временные статусы: объявление может вернуться — это не снятие
STATUS_PENDING = {"moderate", "onModeration"}

_JS_STATE = """() => {
  const cfg = window._cianConfig && window._cianConfig['frontend-serp'];
  const st = Array.isArray(cfg) ? (cfg.find(x => x.key === 'initialState') || {}).value : null;
  if (!st || !st.results) return null;
  return {offers: st.results.offers, total: st.results.totalOffers, aggregated: st.results.aggregatedOffers, jsonQuery: st.results.jsonQuery};
}"""


class Cian(Scraper):
    name = "cian"
    label = "ЦИАН"

    def is_blocked(self, html, url):
        head = (html or "")[:4000].lower()
        return "/captcha" in url or ("<title" in head and "captcha" in head)

    def _json_query(self, page):
        f = self.cfg["filters"]
        q = {
            "_type": "flatsale",
            "engine_version": {"type": "term", "value": 2},
            "region": {"type": "terms", "value": [self.site["region"]]},
            "price": {"type": "range", "value": {"gte": f["price_min"], "lte": f["price_max"]}},
            "currency": {"type": "term", "value": 2},
            "room": {"type": "terms", "value": f["rooms"]},
            "total_area": {"type": "range", "value": {"gte": f["area_min"]}},
            "page": {"type": "term", "value": page},
        }
        # Ремонт на стороне ЦИАН не фильтруется. Раньше здесь стоял фильтр repair [1, 2, 3] в расчёте на
        # «косметический, евро, дизайнерский», а база показала другое: найденные с ним квартиры другой сайт
        # часто называл «без ремонта», а дизайнерских у ЦИАН было в разы меньше, чем у Авито и Яндекса, —
        # похоже, код 1 у ЦИАН и есть «без ремонта», а дизайнерский (4) не запрашивался. Чтобы не зависеть
        # от смысла кодов, приходят все квартиры, вид ремонта читается из поля «Ремонт» объявления, а отсев
        # делает программа — с причиной на карточке, как у Авито и Яндекса.
        return q

    def _page_url(self, page):
        f = self.cfg["filters"]
        p = [("deal_type", "sale"), ("engine_version", 2), ("offer_type", "flat"), ("region", self.site["region"]),
             ("minprice", f["price_min"]), ("maxprice", f["price_max"]), ("mintarea", f["area_min"])]
        p += [(f"room{r}", 1) for r in f["rooms"]]
        if page > 1:
            p.append(("p", page))
        return "https://www.cian.ru/cat.php?" + urlencode(p)

    def _load(self, page):
        def via_http():
            r = self.http.post(API, json={"jsonQuery": self._json_query(page)},
                               headers={"Origin": "https://www.cian.ru", "Referer": "https://www.cian.ru/"})
            d = r.json().get("data") or {}
            if "offersSerialized" not in d:
                self.snapshot_last("нет списка в ответе API")
                raise IOError("ЦИАН: в ответе API нет списка объявлений")
            # aggregatedCount — уникальные объекты (offerCount включает дубли от разных агентов)
            return d["offersSerialized"], d.get("aggregatedCount") or d["offerCount"]

        def via_browser(b):
            b.goto(self._page_url(page))
            st = b.evaluate(_JS_STATE)
            if not st:
                self.snapshot_last("нет состояния страницы")
                raise IOError("ЦИАН: не найдено состояние страницы")
            return st["offers"], st["aggregated"] or st["total"]

        return self.fetch(via_http, via_browser)

    def listings(self):
        page, pages, limit = 1, 1, self.site.get("max_pages", 40)
        total, ids = None, set()
        self.repairs = {}  # сколько объявлений с каким значением поля «Ремонт» — итог в журнал прогона
        while page <= pages and page <= limit:
            offers, total = self._load(page)
            pages = max(1, math.ceil((total or 0) / PAGE_SIZE))
            if not offers and page < pages:
                self.log(f"страница {page}/{pages} пустая — повторяю")
                self.wait()
                offers, total = self._load(page)
                if not offers:
                    self.empty_page(page, pages)
            self.progress(page=page, pages=min(pages, limit), total=total)
            self.log(f"страница {page}/{pages}: {len(offers)} объявлений")
            for o in offers:
                item = self._normalize(o)
                ids.add(item["source_id"])
                yield item
            page += 1
            if page <= pages and page <= limit:
                self.wait()
        self._log_repairs()
        self.finish(page, pages, limit, len(ids), total)

    def _log_repairs(self):
        """Итог по полю «Ремонт»: по нему видно, что ЦИАН это поле действительно присылает. Если не пришло
        ни у одного объявления — ремонт определяется только по описанию, и об этом надо знать."""
        n = sum(self.repairs.values())
        if not n:
            return
        known = sum(v for k, v in self.repairs.items() if k in REPAIR)
        parts = [f"{REPAIR[k][1]} {self.repairs[k]}" for k in REPAIR if self.repairs.get(k)]
        other = {k: v for k, v in self.repairs.items() if k not in REPAIR and k is not None}
        parts += [f"«{k}» {v}" for k, v in other.items()]
        if self.repairs.get(None):
            parts.append(f"не указан {self.repairs[None]}")
        msg = f"поле «Ремонт»: {', '.join(parts)}"
        if not known:
            msg = (f"⚠ поле «Ремонт» ЦИАН не пришло ни в одном из {n} объявлений — вид ремонта берётся только "
                   "из описания. Если так в каждом сборе, ЦИАН поменял формат данных")
        self.log(msg)
        self.runlog.line(msg)

    def verify(self, row):
        """Страница объявления: offer.status — published (активно), статус снятия или 404."""
        try:
            h = self.http.get(row["url"]).text
        except (Blocked, IpBanned):
            raise
        except IOError as e:
            if "HTTP 404" in str(e) or "HTTP 410" in str(e):
                return "removed", "на ЦИАН страница объявления не найдена (404)", None
            return None, None, None
        except Exception:
            return None, None, None
        m = re.search(r'"offer":\{"category":"[^"]*","status":"(\w+)"', h)
        if not m or str(row["source_id"]) not in h:
            return None, None, None  # не страница этого объявления (заглушка, редирект) — статус неизвестен
        status = m.group(1)
        if status in STATUS_PENDING:
            return None, None, None
        archived = '"isArchived":true' in h[:2_000_000]
        pm = re.search(r'"bargainTerms":\{[^{}]*?"price":(\d+)[^{}]*?"currency":"rur"', h)
        price = int(pm.group(1)) if pm else None
        if status == "published" and not archived:
            return "active", "на ЦИАН объявление опубликовано, но не попало в поиск (дубль другого агента или цена вне фильтра)", price
        if status in STATUS or archived:
            return "removed", f"на ЦИАН статус объявления «{STATUS.get(status) or 'в архиве'}»", price
        return None, None, None  # незнакомый статус — не гадаем

    def _normalize(self, o):
        bt = o.get("bargainTerms") or {}
        geo = o.get("geo") or {}
        c = geo.get("coordinates") or {}
        b = o.get("building") or {}
        desc = o.get("description") or ""
        rooms = 0 if o.get("flatType") == "studio" else o.get("roomsCount")
        area = to_float(o.get("totalArea"))
        price = to_int(bt.get("priceRur") or bt.get("price"))
        repair = o.get("repairType")
        if hasattr(self, "repairs"):
            self.repairs[repair] = self.repairs.get(repair, 0) + 1
        if repair in REPAIR:
            # Поле «Ремонт» из «О квартире» — его заполняет продавец, и оно точнее описания:
            # «хороший ремонт в санузлах» не значит, что ремонт есть во всей квартире
            reno, reno_raw = REPAIR[repair]
        else:
            reno, reno_raw = DECORATION.get(o.get("decoration")), o.get("decoration")
            text_reno = rules.renovation_from_text(desc)
            if text_reno == "none" or not reno:
                reno = text_reno or reno
        address = geo.get("userInput") or ", ".join(a.get("fullName") or a.get("name", "") for a in geo.get("address") or [])
        user = o.get("user") or {}
        seller = "owner" if o.get("isByHomeowner") else ("developer" if o.get("fromDeveloper") else ("agent" if user.get("isAgent") else None))
        photos = o.get("photos") or []
        return {
            "source": self.name,
            "source_id": str(o.get("cianId") or o.get("id")),
            "url": (o.get("fullUrl") or "").split("?")[0],
            "title": f"{'Студия' if rooms == 0 else f'{rooms}-к. квартира'}, {area} м², {o.get('floorNumber')}/{b.get('floorsCount')} эт.",
            "price": price,
            "price_m2": round(price / area) if price and area else None,
            "rooms": rooms,
            "area": area,
            "living_area": to_float(o.get("livingArea")),
            "kitchen_area": to_float(o.get("kitchenArea")),
            "floor": o.get("floorNumber"),
            "floors": b.get("floorsCount"),
            "address": address,
            "lat": c.get("lat"),
            "lon": c.get("lng"),
            "renovation": reno,
            "renovation_raw": reno_raw,
            "building_type": b.get("materialType"),
            "build_year": b.get("buildYear"),
            "is_new_building": int(o.get("category") == "newBuildingFlatSale"),
            "is_share": o.get("category") == "flatShareSale",
            "seller_type": seller,
            "description": desc,
            "photo": (photos[0].get("fullUrl") or photos[0].get("thumbnailUrl")) if photos else None,
            "published_at": ts_to_iso(o.get("addedTimestamp")),
            "fetch_mode": self.last_mode,
            "raw": {"category": o.get("category"), "flatType": o.get("flatType"), "decoration": o.get("decoration"),
                    "repairType": repair,
                    "isApartments": o.get("isApartments"), "creationDate": o.get("creationDate")},
        }
