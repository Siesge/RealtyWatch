"""ЦИАН: JSON API поиска (HTTP) или состояние страницы выдачи (браузер)."""
import json
import math
import re
from urllib.parse import urlencode

from .. import rules
from ..net import Blocked, IpBanned
from .base import Scraper, to_float, to_int, ts_to_iso

API = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"
PAGE_SIZE = 28

DECORATION = {"without": "none", "rough": "none", "preFine": "none", "fine": "finish"}
# Вид ремонта из раздела «О квартире». В выдаче поиска ЦИАН его нет (проверено на ответе API 11 ноября 2026:
# ни repairType, ни другого поля о ремонте), зато поиск умеет по нему фильтровать. Поэтому вид ремонта
# узнаётся отдельными выдачами «только без ремонта», «только косметический» и т. д.: всё, что пришло
# в такой выдаче, этот вид ремонта и имеет. Код фильтра ЦИАН → (код программы, как написано на сайте).
# Код 1 = «без ремонта» проверен: в этой выдаче была квартира 336007473, у которой на сайте «Без ремонта».
REPAIR = {1: ("none", "без ремонта"), 2: ("cosmetic", "косметический"),
          3: ("euro", "евроремонт"), 4: ("design", "дизайнерский")}
# Порядок выдачи. Без явной сортировки ЦИАН ранжирует объявления моделью и между запросами страниц
# переставляет их: одни приходят дважды, другие ни разу (11 ноября: заявлено 418, собрано 371).
# По цене порядок для всех страниц один и тот же.
SORT = "price_object_order"
# Статусы страницы объявления, которые однозначно означают «не в продаже»
STATUS = {"deactivated": "снято с публикации", "removed": "удалено", "draft": "черновик", "blocked": "заблокировано",
          "refused": "отклонено модерацией", "sold": "продано"}
# Временные статусы: объявление может вернуться — это не снятие
STATUS_PENDING = {"moderate", "onModeration"}

_CONFIG_RE = re.compile(
    r"_cianConfig\[(['\"])(?P<key>[^'\"]+)\1\]"
    r"(?:\s*=\s*\(?\s*(?:window\.)?_cianConfig\[[^\]]+\]\s*\|\|\s*\[\]\s*\)?)?"
    r"\s*(?:\.concat\s*\(|=\s*)"
)


def _find_results(obj, depth=0):
    """Ищет словарь results со списком объявлений offers в состоянии страницы ЦИАН."""
    if depth > 4:
        return None
    if isinstance(obj, dict):
        res = obj.get("results")
        if isinstance(res, dict) and isinstance(res.get("offers"), list):
            return res
        for v in obj.values():
            hit = _find_results(v, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for item in obj:
            hit = _find_results(item, depth + 1)
            if hit is not None:
                return hit
    return None


def _decode_config_arg(html, pos, dec):
    n = len(html)
    while pos < n and html[pos].isspace():
        pos += 1
    if html.startswith(("[", "{"), pos):
        data, _ = dec.raw_decode(html, pos)
        return data
    if html.startswith("JSON.parse(", pos):
        p2 = pos + len("JSON.parse(")
        while p2 < n and html[p2].isspace():
            p2 += 1
        if p2 < n and html[p2] == '"':
            raw_str, _ = dec.raw_decode(html, p2)
            return json.loads(raw_str)
    return None


def parse_state(html):
    """Извлекает offers / total / aggregated из HTML страницы поиска ЦИАН или None."""
    if not html:
        return None
    dec = json.JSONDecoder()
    hits = []
    for m in _CONFIG_RE.finditer(html):
        try:
            data = _decode_config_arg(html, m.end(), dec)
        except Exception:
            continue
        res = _find_results(data) if data is not None else None
        if res is not None:
            prio = 0 if m.group("key") == "frontend-serp" else 1
            hits.append((prio, res))
    if not hits:
        nm = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.I | re.S)
        if nm:
            try:
                res = _find_results(json.loads(nm.group(1)))
                if res is not None:
                    hits.append((2, res))
            except Exception:
                pass
    if not hits:
        for m in re.finditer(r'"results"\s*:\s*\{', html):
            try:
                res, _ = dec.raw_decode(html, m.end() - 1)
            except Exception:
                continue
            if isinstance(res, dict) and isinstance(res.get("offers"), list):
                hits.append((3, res))
                break
    if not hits:
        return None
    hits.sort(key=lambda h: (h[0], not bool(h[1].get("offers")),
                             "totalOffers" not in h[1] and "aggregatedOffers" not in h[1]))
    res = hits[0][1]
    return {"offers": res["offers"], "total": res.get("totalOffers"), "aggregated": res.get("aggregatedOffers")}


_JS_STATE = """() => {
  const cfg = window._cianConfig;
  if (!cfg) return null;
  const findResults = (obj, depth = 0) => {
    if (!obj || typeof obj !== 'object' || depth > 4) return null;
    if (obj.results && Array.isArray(obj.results.offers)) return obj.results;
    for (const v of (Array.isArray(obj) ? obj : Object.values(obj))) {
      const hit = findResults(v, depth + 1);
      if (hit) return hit;
    }
    return null;
  };
  const bundles = cfg['frontend-serp'] ? [cfg['frontend-serp'], ...Object.values(cfg)] : Object.values(cfg);
  for (const b of bundles) {
    const res = findResults(b);
    if (res) return {offers: res.offers, total: res.totalOffers, aggregated: res.aggregatedOffers};
  }
  return null;
}"""


class Cian(Scraper):
    name = "cian"
    label = "ЦИАН"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._api_blocked = False

    def is_blocked(self, html, url):
        if "captcha" in (url or "").lower():
            return True
        head = (html or "")[:16000]
        m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
        if m and ("captcha" in m.group(1).lower() or "вы не робот" in m.group(1).lower()):
            return True
        return 'id="form_captcha"' in head or 'id="captcha-container"' in head

    def is_ready(self, html, url):
        return not self.is_blocked(html, url) and parse_state(html) is not None

    def _json_query(self, page, repair=None):
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
            "sort": {"type": "term", "value": SORT},
        }
        if repair:
            q["repair"] = {"type": "terms", "value": [repair]}
        # Основная выдача идёт без фильтра по ремонту: иначе пропали бы квартиры, где вид ремонта не указан.
        # Раньше здесь стоял постоянный фильтр repair [1, 2, 3] в расчёте на «косметический, евро,
        # дизайнерский», а код 1 оказался «без ремонта», дизайнерский (4) же не запрашивался вовсе.
        return q

    def _page_url(self, page, repair=None):
        f = self.cfg["filters"]
        p = [("deal_type", "sale"), ("engine_version", 2), ("offer_type", "flat"), ("region", self.site["region"]),
             ("minprice", f["price_min"]), ("maxprice", f["price_max"]), ("mintarea", f["area_min"])]
        p += [(f"room{r}", 1) for r in f["rooms"]]
        p.append(("sort", SORT))
        if repair:
            p.append(("repair[0]", repair))
        if page > 1:
            p.append(("p", page))
        return "https://www.cian.ru/cat.php?" + urlencode(p)

    def _load(self, page, repair=None):
        def via_http():
            if not self._api_blocked:
                try:
                    r = self.http.post(API, json={"jsonQuery": self._json_query(page, repair)},
                                       headers={"Origin": "https://www.cian.ru", "Referer": "https://www.cian.ru/"})
                    d = r.json().get("data") or {}
                    if "offersSerialized" not in d:
                        self.snapshot_last("нет списка в ответе API")
                        raise IOError("ЦИАН: в ответе API нет списка объявлений")
                    # aggregatedCount — уникальные объекты (offerCount включает дубли от разных агентов)
                    return d["offersSerialized"], d["aggregatedCount"] if "aggregatedCount" in d else d["offerCount"]
                except Blocked as e:
                    self._api_blocked = True
                    self.log(f"API поиска ЦИАН заблокирован ({e}) — пробую страницу поиска по HTTP")
            r = self.http.get(self._page_url(page, repair), headers={"Referer": "https://www.cian.ru/"})
            st = parse_state(r.text)
            if not st:
                self.snapshot_last("нет состояния страницы")
                raise IOError("ЦИАН: не найдено состояние страницы")
            agg, tot = st.get("aggregated"), st.get("total")
            return st["offers"], agg if agg is not None else tot

        def via_browser(b):
            html = b.goto(self._page_url(page, repair))
            st = b.evaluate(_JS_STATE)
            if not st:
                st = parse_state(b.raw_html()) or parse_state(html)
            if not st:
                self.snapshot_last("нет состояния страницы")
                raise IOError("ЦИАН: не найдено состояние страницы")
            agg, tot = st.get("aggregated"), st.get("total")
            return st["offers"], agg if agg is not None else tot

        return self.fetch(via_http, via_browser)

    def _pages(self, repair, label, first):
        """Все страницы одной выдачи по очереди: (страница, всего страниц, заявлено сайтом, объявления).
        first — уже загруженная первая страница (объявления, заявлено сайтом). После последней страницы
        в self.last_pages — (следующая страница, всего страниц, ограничение) для finish()."""
        page, limit = 1, self.site.get("max_pages", 40)
        offers, total = first
        pages = self._count(total)
        while True:
            if page > 1:
                offers, total = self._load(page, repair)
                pages = self._count(total)
            if not offers and page < pages:
                self.log(f"{label}страница {page}/{pages} пустая — повторяю")
                self.wait()
                offers, total = self._load(page, repair)
                if not offers:
                    self.empty_page(page, pages)
            self.log(f"{label}страница {page}/{pages}: {len(offers)} объявлений")
            self.step()
            yield page, pages, total, offers
            page += 1
            if page > pages or page > limit:
                break
            self.wait()
        self.last_pages = (page, pages, limit)

    @staticmethod
    def _count(total):
        return max(1, math.ceil((total or 0) / PAGE_SIZE))

    def step(self):
        """Одна прочитанная страница — шаг общего прогресса по всем выдачам сбора."""
        self.done += 1
        self.progress(page=min(self.done, self.planned), pages=self.planned, total=self.declared_main)

    def listings(self):
        limit = self.site.get("max_pages", 40)
        queries = [(code, f"ремонт «{name}», ") for code, (_, name) in REPAIR.items()] + [(None, "")]
        # 0. Первые страницы всех выдач — по ним известно, сколько страниц всего, и полоса прогресса
        # идёт одна на весь сбор, а не заново для каждой выдачи
        first = {}
        for n, (code, _) in enumerate(queries):
            first[code] = self._load(1, code)
            if n < len(queries) - 1:
                self.wait()
        self.declared_main = first[None][1]
        self.planned = sum(min(self._count(t), limit) for _, t in first.values())
        self.done = 0
        self.log(f"выдачи по виду ремонта и основная: страниц всего {self.planned}")
        self.progress(page=0, pages=self.planned, total=self.declared_main)
        # 1. Вид ремонта — по выдаче на каждый вид; запоминается, у какого объявления какой
        self.repairs = {}   # id → код ремонта ЦИАН
        extra = {}          # id → объявление из этих выдач, если основной выдаче оно вдруг не встретится
        for code, label in queries[:-1]:
            for _page, _pages, _total, offers in self._pages(code, label, first[code]):
                for o in offers:
                    sid = str(o.get("cianId") or o.get("id"))
                    self.repairs[sid] = code
                    extra[sid] = o
        # 2. Основная выдача — все квартиры, в том числе те, где вид ремонта не указан
        ids, total = set(), None
        for _page, _pages, total, offers in self._pages(None, "", first[None]):
            for o in offers:
                item = self._normalize(o)
                if item["source_id"] not in ids:
                    ids.add(item["source_id"])
                    yield item
        last = self.last_pages
        # Пришло в выдаче по ремонту, но не в основной: в поиске объявление есть, терять его незачем
        for sid, o in extra.items():
            if sid not in ids:
                ids.add(sid)
                yield self._normalize(o)
        self._log_repairs(ids)
        self.finish(*last, len(ids), total)

    def _log_repairs(self, ids):
        """Итог по виду ремонта — в журнал прогона: по нему видно, что выдачи по ремонту сработали."""
        counts = {}
        for sid in ids:
            code = self.repairs.get(sid)
            counts[code] = counts.get(code, 0) + 1
        if not ids:
            return
        if set(counts) == {None}:
            self.log(f"⚠ вид ремонта ЦИАН не определён ни у одного из {len(ids)} объявлений — выдачи по ремонту "
                     "пришли пустыми, ремонт берётся только из описания")
            return
        parts = [f"{name} {counts[code]}" for code, (_, name) in REPAIR.items() if counts.get(code)]
        if counts.get(None):
            parts.append(f"не указан {counts[None]}")
        self.log(f"вид ремонта: {', '.join(parts)}")

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
        sid = str(o.get("cianId") or o.get("id"))
        repair = (getattr(self, "repairs", None) or {}).get(sid)
        if repair in REPAIR:
            # Вид ремонта из «О квартире» — его указывает продавец, и он точнее описания:
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
            "source_id": sid,
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
                    "repair": repair,
                    "isApartments": o.get("isApartments"), "creationDate": o.get("creationDate")},
        }
