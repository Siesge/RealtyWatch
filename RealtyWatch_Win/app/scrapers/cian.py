"""ЦИАН: JSON API поиска (HTTP) или состояние страницы выдачи (браузер)."""
import json
import math
import re
import sqlite3
import time
from datetime import datetime
from urllib.parse import urlencode

from .. import db, net, rules
from ..net import Blocked, IpBanned
from .base import Scraper, to_float, to_int, ts_to_iso

API = "https://api.cian.ru/search-offers/v2/search-offers-desktop/"
PAGE_SIZE = 28
HTTP_BLOCK_TTL = 12 * 3600  # сколько не повторять заведомо заблокированный HTTP-поиск в режиме auto
REPAIR_TTL = 24 * 3600      # как часто обновлять виды ремонта для всей выдачи (полный проход 1..4)

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
        u = (url or "").lower()
        if "captcha" in u or "/xpvnsulc/" in u:
            return True
        head = (html or "")[:16000]
        m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
        if m and ("captcha" in m.group(1).lower() or "вы не робот" in m.group(1).lower()):
            return True
        return ('id="form_captcha"' in head or 'id="captcha-container"' in head
                or 'id="id_captcha_frame_div"' in head or "<js-challenge-loader" in head)

    def is_ip_banned(self, html, url):
        head = (html or "")[:8000]
        return 'id="REQUEST-IP"' in head and "Forbidden" in head

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

    def _page_host(self):
        return "krym.cian.ru" if self.site.get("region") == 184739 else "www.cian.ru"

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
        return f"https://{self._page_host()}/cat.php?" + urlencode(p)

    def _load(self, page, repair=None):
        url = self._page_url(page, repair)
        ref = f"https://{self._page_host()}/"

        def via_http():
            if not self._api_blocked:
                try:
                    r = self.http.post(API, json={"jsonQuery": self._json_query(page, repair)},
                                       headers={"Origin": "https://www.cian.ru", "Referer": "https://www.cian.ru/"})
                    d = r.json().get("data") or {}
                    if "offersSerialized" not in d:
                        if self.mode == "http":
                            self.snapshot_last("нет списка в ответе API")
                        raise Blocked("ЦИАН: в ответе API нет списка объявлений")
                    self._clear_http_blocked()
                    # aggregatedCount — уникальные объекты (offerCount включает дубли от разных агентов)
                    return d["offersSerialized"], d["aggregatedCount"] if "aggregatedCount" in d else d["offerCount"]
                except Blocked as e:
                    self._api_blocked = True
                    self.log(f"API поиска ЦИАН заблокирован ({e}) — пробую страницу поиска по HTTP")
            try:
                r = self.http.get(url, headers={"Referer": ref})
            except Blocked:
                if self.mode == "auto":
                    self._mark_http_blocked()
                raise
            st = parse_state(r.text)
            if not st:
                if self.mode == "http":
                    self.snapshot_last("нет состояния страницы (HTTP)")
                else:
                    self._mark_http_blocked()
                raise Blocked(f"ЦИАН: страница поиска по HTTP без состояния ({len(r.text)} Б)")
            self._clear_http_blocked()
            agg, tot = st.get("aggregated"), st.get("total")
            return st["offers"], agg if agg is not None else tot

        def via_browser(b):
            html = b.goto(url)
            st = b.evaluate(_JS_STATE)
            if not st:
                st = parse_state(b.raw_html()) or parse_state(html)
            if not st and hasattr(b, "_wait_js"):
                if b._wait_js("() => !!(window._cianConfig && Object.keys(window._cianConfig).length)", 8):
                    cur = b.content() if hasattr(b, "content") else html
                    st = b.evaluate(_JS_STATE) or parse_state(b.raw_html()) or parse_state(cur)
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

    def _http_state_file(self):
        return net.PROFILES / self.name / "http_state.json"

    def _http_recently_blocked(self):
        try:
            d = json.loads(self._http_state_file().read_text(encoding="utf-8"))
            return (time.time() - float(d.get("blocked_at", 0))) < HTTP_BLOCK_TTL
        except (OSError, ValueError, TypeError):
            return False

    def _mark_http_blocked(self):
        try:
            p = self._http_state_file()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"blocked_at": time.time()}), encoding="utf-8")
        except OSError:
            pass

    def _clear_http_blocked(self):
        try:
            self._http_state_file().unlink(missing_ok=True)
        except OSError:
            pass

    def _known_repairs(self):
        """Виды ремонта из базы, если полное обновление проходило недавно (< REPAIR_TTL), иначе None."""
        try:
            con = db.connect()
        except sqlite3.Error:
            return None
        try:
            row = con.execute("SELECT value FROM meta WHERE key='cian_repairs_ts'").fetchone()
            ts = None
            if row:
                try:
                    ts = float(row["value"])
                except (TypeError, ValueError):
                    ts = None
            known = {}
            for r in con.execute("SELECT source_id, raw FROM listings WHERE source='cian'"):
                try:
                    raw = json.loads(r["raw"]) if r["raw"] else None
                except (TypeError, ValueError):
                    raw = None
                if isinstance(raw, dict) and "repair" in raw:
                    known[str(r["source_id"])] = raw["repair"]
            if ts is None and any(v in REPAIR for v in known.values()):
                last = con.execute("SELECT finished FROM runs WHERE source='cian' AND status='done' ORDER BY id DESC LIMIT 1").fetchone()
                if last and last["finished"]:
                    try:
                        ts = datetime.fromisoformat(last["finished"]).timestamp()
                    except ValueError:
                        ts = None
            if not known or ts is None or (time.time() - ts) >= REPAIR_TTL:
                return None
            return known
        except sqlite3.Error:
            return None
        finally:
            con.close()

    def _save_repairs_ts(self):
        try:
            con = db.connect()
            try:
                con.execute("INSERT OR REPLACE INTO meta VALUES('cian_repairs_ts', ?)", (str(time.time()),))
                con.commit()
            finally:
                con.close()
        except sqlite3.Error:
            pass

    def listings(self):
        if self.mode == "auto" and not self.use_browser and self._http_recently_blocked():
            self.use_browser = True
            self.log("HTTP ЦИАН блокировался менее 12 ч назад — сразу открываю браузер")
        limit = self.site.get("max_pages", 40)
        known = self._known_repairs()
        if known:
            yield from self._listings_incremental(limit, known)
        else:
            yield from self._listings_full(limit)

    def _listings_full(self, limit):
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
        if self.complete and self.repairs:
            self._save_repairs_ts()

    def _listings_incremental(self, limit, known):
        """Ускоренный сбор: основная выдача; выдачи по ремонту — только если встретились новые объявления."""
        first_main = self._load(1, None)
        self.declared_main = first_main[1]
        self.planned = min(self._count(self.declared_main), limit)
        self.done = 0
        self.progress(page=0, pages=self.planned, total=self.declared_main)
        main_offers, seen_ids, main_total = [], set(), None
        for _page, _pages, main_total, offers in self._pages(None, "", first_main):
            for o in offers:
                sid = str(o.get("cianId") or o.get("id"))
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    main_offers.append((sid, o))
        last = self.last_pages
        self.repairs = {sid: code for sid, code in known.items() if code in REPAIR}
        unknown = [(sid, o) for sid, o in main_offers if sid not in known]
        if not unknown:
            self.log(f"выдачи по виду ремонта пропущены: все {len(main_offers)} объявлений уже есть в базе")
        else:
            unknown_ids = {sid for sid, _ in unknown}
            prices = [to_int((o.get("bargainTerms") or {}).get("priceRur") or (o.get("bargainTerms") or {}).get("price"))
                      for _, o in unknown]
            max_price = max(prices) if prices and all(prices) else None
            self.log(f"новых объявлений ЦИАН без известного ремонта: {len(unknown_ids)} — уточняю вид ремонта")
            for code, (_, name) in REPAIR.items():
                if unknown_ids <= self.repairs.keys():
                    break
                label = f"ремонт «{name}», "
                r_page, r_pages = 1, 1
                while r_page <= r_pages:
                    self.wait()
                    offers, r_total = self._load(r_page, code)
                    r_pages = min(self._count(r_total), limit)
                    self.planned = max(self.planned, self.done + 1)
                    self.log(f"{label}страница {r_page}/{self._count(r_total)}: {len(offers)} объявлений")
                    self.step()
                    for o in offers:
                        self.repairs[str(o.get("cianId") or o.get("id"))] = code
                    if unknown_ids <= self.repairs.keys():
                        break
                    if max_price is not None and offers:
                        page_prices = [to_int((o.get("bargainTerms") or {}).get("priceRur") or (o.get("bargainTerms") or {}).get("price"))
                                       for o in offers]
                        if all(page_prices) and min(page_prices) > max_price * 1.05 + 50_000:
                            break
                    r_page += 1
        for _, o in main_offers:
            yield self._normalize(o)
        self._log_repairs(seen_ids)
        self.finish(*last, len(seen_ids), main_total)

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
                    "repair": repair, "apartments": bool(o.get("isApartments")),
                    "isApartments": o.get("isApartments"), "creationDate": o.get("creationDate")},
        }
