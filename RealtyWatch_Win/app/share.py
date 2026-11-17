"""Избранное одним файлом: плитки как в «Подборке», переключение в таблицу, сортировки и фильтры.

Файл полностью автономный — стили, шрифты, фотографии и скрипт лежат внутри, поэтому его можно
отправить кому угодно и открыть без интернета. Разметка готова сразу: все объекты, ссылки и фото
стоят в HTML, а скрипт только прячет и переставляет уже готовые узлы. Поэтому при отключённом
JavaScript страница остаётся читаемой: пропадают фильтры, сортировка и таблица, всё остальное на месте.

Вид берётся из web/app.css — классы карточек и панели фильтров те же, что в программе, так что
выгрузка выглядит как «Подборка» и так же понимает тёмную тему системы.
"""
import base64
import datetime
import html
import json
from pathlib import Path

from . import images
from .paths import WEB

SRC = {"cian": "ЦИАН", "avito": "Авито", "yandex": "Яндекс"}
ZONE = {"A": "Красный круг", "B": "Остальная Ялта", "C": "Посёлки Большой Ялты"}
ZONE_SHORT = {"A": "Круг", "B": "Ялта", "C": "Посёлок"}
RENO = {"design": "дизайнерский ремонт", "euro": "евроремонт", "cosmetic": "косметический ремонт",
        "old": "старый ремонт", "finish": "чистовая отделка", "none": "без ремонта", "any": "с ремонтом"}
RENO_RANK = {"design": 6, "euro": 5, "cosmetic": 4, "finish": 3, "old": 2, "any": 1}
SELLER = {"owner": "собственник", "agent": "агент", "developer": "застройщик"}
MIME = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".avif": "image/avif"}
MONTHS = ("янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.")
NBSP = " "

# колонки таблицы: ключ для сортировки, заголовок, что в клетке, числовая ли
COLS = [
    ("sites", "Сайты", lambda o: "<br>".join(
        f'<a href="{e(i["url"])}" target="_blank" rel="noopener">'
        f'<i class="sdot {i["source"]}"></i> {SRC.get(i["source"], i["source"])}</a>' for i in o["items"]), 0),
    ("zone", "Зона", lambda o: f'<i class="zdot {o["zone"]}"></i> {ZONE_SHORT.get(o["zone"], o["zone"])}', 0),
    ("price", "Цена", lambda o: fmt(o["price"]) + (f'<br><small>до {fmt(o["price_max"])}</small>'
                                                   if (o["price_max"] or 0) > (o["price"] or 0) else ""), 1),
    ("ppm", "₽/м²", lambda o: fmt(o["price_m2"]), 1),
    ("rooms", "Комн.", lambda o: "" if o["rooms"] is None else o["rooms"], 1),
    ("area", "м²", lambda o: o["area"] if o["area"] is not None else "", 1),
    ("floor", "Этаж", lambda o: f'{o["floor"] if o["floor"] is not None else "?"}/{o["floors"] if o["floors"] is not None else "?"}', 1),
    ("reno", "Ремонт", lambda o: RENO.get(o["renovation"], ""), 0),
    ("address", "Адрес", lambda o: e(o["address"]), 0),
    ("note", "Заметка", lambda o: e(o.get("note") or ""), 0),
    ("excluded", "Отсев", lambda o: e(o["excluded"] or ""), 0),
    ("first", "Найдено", lambda o: dt(o["first_seen"]), 0),
]
SORTS = [("new", "сначала новые"), ("price", "дешевле"), ("-price", "дороже"), ("ppm", "₽/м² ↑"), ("-area", "площадь ↓")]


def e(s):
    return html.escape("" if s is None else str(s), quote=True)


def fmt(n):
    """Целое число с неразрывными пробелами — как toLocaleString('ru-RU') в программе."""
    return "—" if n is None else f"{round(n):,}".replace(",", NBSP)


def mln(n):
    if n is None:
        return "—"
    s = f"{n / 1e6:.2f}".rstrip("0")
    return (s + "0" if s.endswith(".") else s).replace(".", ",")


def dt(ts):
    if not ts:
        return ""
    d = datetime.datetime.fromisoformat(str(ts)[:19])
    return f"{d.day} {MONTHS[d.month - 1]}"


def plural(n, one, few, many):
    m10, m100 = n % 10, n % 100
    return one if m10 == 1 and m100 != 11 else few if 2 <= m10 <= 4 and not 10 <= m100 < 20 else many


def default_name():
    return f"realtywatch-избранное-{datetime.date.today().isoformat()}.html"


def _last_change(item):
    """Последнее изменение цены объявления: (прежняя, нынешняя, когда) или None."""
    h = item["history"]
    for k in range(len(h) - 1, 0, -1):
        if h[k][1] != h[k - 1][1]:
            return h[k - 1][1], h[k][1], h[k][0]
    return None


def _relisted(items, price):
    live = [i for i in items if i["active"] and i["price"]]
    gone = [i for i in items if not i["active"] and i["price"]]
    if not live or not gone or not price:
        return None
    best = [i for i in live if i["price"] == price]
    if not best:
        return None

    def earlier(g):
        gf = g.get("first_seen") or ""
        for l in best:
            lf = l.get("first_seen") or ""
            if gf >= lf:
                return False
            try:
                if (datetime.datetime.fromisoformat(lf[:19]) - datetime.datetime.fromisoformat(gf[:19])).total_seconds() < 6 * 3600:
                    return False
            except ValueError:
                pass
        return True

    before = [g for g in gone if earlier(g)]
    if not before:
        return None
    prev = max(before, key=lambda g: g.get("removed_at") or g.get("last_seen") or "")
    diff = price - prev["price"]
    if abs(diff) < 50_000:
        return None
    return {"old": prev["price"], "now": price, "diff": diff, "old_src": prev["source"]}


def liked_objects(con):
    """Объекты, у которых хотя бы одно объявление в избранном, — со всеми объявлениями объекта.

    Собирается так же, как buildObjects в web/app.js: главным выбирается самое «полное» объявление,
    цена — минимальная среди активных, ремонт — лучший из указанных.
    """
    liked = {(r["source"], r["source_id"]) for r in con.execute("SELECT source, source_id FROM likes")}
    if not liked:
        return []
    rows = [dict(r) for r in con.execute("SELECT * FROM listings")]
    for r in rows:
        r["object_id"] = r["object_id"] or f"{r['source']}:{r['source_id']}"
    ids = {r["object_id"] for r in rows if (r["source"], r["source_id"]) in liked}
    rows = [r for r in rows if r["object_id"] in ids]
    hist = {}
    for h in con.execute("SELECT source, source_id, ts, price FROM price_history ORDER BY ts"):
        hist.setdefault((h["source"], h["source_id"]), []).append((h["ts"], h["price"]))
    notes = {(r["source"], r["source_id"]): (r["text"] or "", r["ts"] or "")
             for r in con.execute("SELECT source, source_id, text, ts FROM notes")}
    groups = {}
    for r in rows:
        r["flags"] = json.loads(r["flags"]) if r["flags"] else {}
        r["raw"] = json.loads(r["raw"]) if r["raw"] else {}
        r["history"] = hist.get((r["source"], r["source_id"]), [])
        r["note"], r["note_ts"] = notes.get((r["source"], r["source_id"]), ("", ""))
        r["file"] = images.local_path(r["source"], r["source_id"])
        groups.setdefault(r["object_id"], []).append(r)
    objs = []
    for oid, items in groups.items():
        items.sort(key=lambda i: (-((0 if i["excluded"] else 8) + (4 if i["active"] else 0)
                                    + (2 if i["file"] else 0) + (1 if i["lat"] is not None else 0)), i["first_seen"]))
        p = items[0]
        live = [i for i in items if i["active"]]
        prices = [i["price"] for i in (live or items) if i["price"]]
        reno = sorted((i["renovation"] for i in items if i["renovation"]), key=lambda r: -RENO_RANK.get(r, 0))
        flags = {}
        for i in items:
            flags.update(i["flags"] or {})
        note = max(((i["note_ts"], i["note"]) for i in items if i["note"]), default=("", ""))[1]
        area = p["area"]
        price = min(prices) if prices else None
        objs.append({
            "id": oid, "items": items, "p": p, "flags": flags,
            "img": next((i for i in items if i["file"]), None),
            "sources": sorted({i["source"] for i in items}),
            "price": price, "price_max": max(prices) if prices else None,
            "price_m2": price / area if price and area else None,
            "active": any(i["active"] for i in items),
            "excluded": p["excluded"] if all(i["excluded"] for i in items) else None,
            "renovation": p["renovation"] if (p["excluded"] or not reno) else reno[0],
            "relisted": _relisted(items, price),
            "note": note,
            "first_seen": min(i["first_seen"] for i in items),
            "zone": p["zone"], "rooms": p["rooms"], "area": area, "floor": p["floor"], "floors": p["floors"],
            "address": sorted((i["address"] for i in items if i["address"]), key=len, reverse=True)[0] if any(i["address"] for i in items) else "",
            "text": " ".join(
                [*(f'{i["title"] or ""} {i["address"] or ""} {i["description"] or ""}' for i in items), note]
            ).lower(),
        })
    objs.sort(key=lambda o: o["first_seen"], reverse=True)
    return objs


def _b64(path):
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _css():
    """Стили программы с зашитыми внутрь шрифтами — файлу не нужен ни интернет, ни папка web."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    for name in ("manrope-cyrillic", "manrope-latin"):
        f = WEB / "vendor" / "fonts" / f"{name}.woff2"
        if f.exists():
            css = css.replace(f'url("/static/vendor/fonts/{name}.woff2")', f'url("data:font/woff2;base64,{_b64(f)}")')
    return css


def _media(o):
    f = o["img"]["file"] if o["img"] else None
    if f is None or not f.exists():
        return '<div class="media noimg">'
    return f'<div class="media"><img src="data:{MIME.get(f.suffix, "image/jpeg")};base64,{_b64(f)}" alt="" loading="lazy">'


def _tags(o):
    t = []
    if o["renovation"]:
        good = "bad" if o["renovation"] == "none" else "good"
        t.append(f'<span class="tag {good}">{RENO.get(o["renovation"], o["renovation"])}</span>')
    else:
        t.append('<span class="tag warn">ремонт не указан</span>')
    if o["p"]["is_new_building"]:
        t.append('<span class="tag">новостройка</span>')
    if any((i["raw"] or {}).get("apartments") or (i["raw"] or {}).get("isApartments") for i in o["items"]):
        t.append('<span class="tag warn">апартаменты</span>')
    rl = o.get("relisted")
    if rl:
        t.append(f'<span class="tag {"good" if rl["diff"] < 0 else "warn"}">↻ перевыставлено {"↓" if rl["diff"] < 0 else "↑"} {mln(abs(rl["diff"]))} млн</span>')
    for s in sorted({i["seller_type"] for i in o["items"] if i["seller_type"]}):
        t.append(f'<span class="tag">{SELLER.get(s, e(s))}</span>')
    for k, lbl in (("encumbrance", "обременение?"), ("share", "доля?"), ("auction", "торги?")):
        if o["flags"].get(k):
            t.append(f'<span class="tag warn">{lbl}</span>')
    if o["excluded"]:
        t.append(f'<span class="tag bad">✕ {e(o["excluded"])}</span>')
    if not o["active"]:
        t.append('<span class="tag bad">снято с продажи</span>')
    return "".join(t)


def _offers(o):
    best = o["price"] if len(o["items"]) > 1 and o["price"] != o["price_max"] else None
    items = sorted(o["items"], key=lambda i: (not i["active"], i["price"] or 0))
    out = []
    for i in items:
        ch = _last_change(i) if i["active"] else None
        cheaper = '<span class="best">дешевле</span> ' if best and i["price"] == best and i["active"] else ""
        old = f'<s class="old-mini">{mln(ch[0])}</s> ' if ch else ""
        out.append(f'<a class="offer {"" if i["active"] else "gone"}" href="{e(i["url"])}" target="_blank" rel="noopener">'
                   f'<i class="sdot {i["source"]}"></i><span>{SRC.get(i["source"], e(i["source"]))}</span>'
                   f'<span>{cheaper}{old}<b>{fmt(i["price"])} ₽</b></span></a>')
    return f'<div class="offers">{"".join(out)}</div>'


def _delta(o):
    """Значок «цена снижена/повышена» — как на карточке в программе."""
    with_change = [i for i in o["items"] if i["active"] and _last_change(i)]
    i = next((x for x in with_change if x["price"] == o["price"]), None) or (with_change[0] if with_change else None)
    if i is not None:
        old, now, _ = _last_change(i)
        d = now - old
        return f'<span class="delta {"down" if d < 0 else "up"}">{"↓" if d < 0 else "↑"} {mln(abs(d))} млн</span>'
    rl = o.get("relisted")
    if rl:
        d = rl["diff"]
        return f'<span class="delta {"down" if d < 0 else "up"}">↻ {"↓" if d < 0 else "↑"} {mln(abs(d))} млн</span>'
    return ""


def _old_price(o):
    i = next((x for x in o["items"] if x["active"] and x["price"] == o["price"] and _last_change(x)), None)
    return f'<s class="old-price">{fmt(_last_change(i)[0])} ₽</s>' if i else ""


def _data(o, n):
    """Значения для фильтров и сортировки — скрипт читает только их, своей копии данных у него нет."""
    return (f'data-i="{n}" data-price="{o["price"] or ""}" data-ppm="{round(o["price_m2"]) if o["price_m2"] else ""}" '
            f'data-area="{o["area"] if o["area"] is not None else ""}" data-rooms="{o["rooms"] if o["rooms"] is not None else ""}" '
            f'data-floor="{o["floor"] if o["floor"] is not None else ""}" data-zone="{o["zone"]}" '
            f'data-src="{" ".join(o["sources"])}" data-sites="{len(o["sources"])}" data-first="{e(o["first_seen"])}" '
            f'data-active="{1 if o["active"] else 0}" data-reno="{o["renovation"] or ""}" '
            f'data-address="{e(o["address"]).lower()}" data-excluded="{e(o["excluded"] or "")}" '
            f'data-text="{e(o["text"])}"')


def _card(o, n):
    meta = " · ".join(x for x in (f'жилая {o["p"]["living_area"]} м²' if o["p"]["living_area"] else "",
                                  f'кухня {o["p"]["kitchen_area"]} м²' if o["p"]["kitchen_area"] else "",
                                  f'{o["p"]["build_year"]} г.' if o["p"]["build_year"] else "") if x)
    rooms = "студия" if o["rooms"] == 0 else "—" if o["rooms"] is None else o["rooms"]
    n_items = len(o["items"])
    sites = f'<span class="pill multi">{len(o["sources"])} {plural(len(o["sources"]), "сайт", "сайта", "сайтов")}</span>' if len(o["sources"]) > 1 else ""
    return f"""<article class="card {"excl" if o["excluded"] else ""} {"" if o["active"] else "inactive"}" {_data(o, n)}>
    {_media(o)}
      <div class="media-top"><span class="left"><span class="pill zone-{o["zone"]}">{ZONE_SHORT.get(o["zone"], o["zone"])}</span>{sites}</span></div>
      <div class="media-bottom"><div><div class="price">{fmt(o["price"])} ₽ {_old_price(o)}</div>
        <div class="ppm">{fmt(o["price_m2"])} ₽/м²{f' · до {mln(o["price_max"])} млн' if (o["price_max"] or 0) > (o["price"] or 0) else ""}</div></div>{_delta(o)}</div>
    </div>
    <div class="body">
      <div class="facts">
        <div class="fact"><b>{rooms}</b><span>{"" if o["rooms"] == 0 else "комнат"}</span></div>
        <div class="fact"><b>{o["area"] if o["area"] is not None else "—"}</b><span>м² общая</span></div>
        <div class="fact"><b>{o["floor"] if o["floor"] is not None else "?"}<small>/{o["floors"] if o["floors"] is not None else "?"}</small></b><span>этаж</span></div>
      </div>
      <div class="addr"><span>{e(o["address"]) or "адрес не указан"}</span></div>
      {f'<div class="meta">{e(meta)}</div>' if meta else ""}
      <div class="tags">{_tags(o)}</div>
      {f'<div class="card-note">✎ {e(o["note"])}</div>' if o.get("note") else ""}
      {_offers(o)}
      <p class="desc">{e((o["p"]["description"] or "")[:320])}</p>
    </div>
    <div class="card-foot"><span>с {dt(o["first_seen"])} · {n_items} {plural(n_items, "объявление", "объявления", "объявлений")}</span></div>
  </article>"""


def _seg(name, values, labels, small=True):
    """Отрезок переключателей для фильтра — только те значения, которые есть в выгрузке."""
    if len(values) < 2:
        return ""
    inner = "".join(f'<label><input type="checkbox" name="{name}" value="{e(v)}" checked>'
                    f'<span>{labels(v)}</span></label>' for v in values)
    return f'<div class="seg{" small" if small else ""}">{inner}</div>'


def _tools(objs):
    zones = [z for z in ZONE if any(o["zone"] == z for o in objs)]
    srcs = [s for s in SRC if any(s in o["sources"] for o in objs)]
    rooms = sorted({o["rooms"] for o in objs if o["rooms"] is not None})
    renos = [r for r in RENO if any(o["renovation"] == r for o in objs)]
    reno_sel = ("" if len(renos) < 2 else
                '<label class="sel"><span>Ремонт</span><select id="reno"><option value="">любой</option>'
                + "".join(f'<option value="{r}">{RENO[r]}</option>' for r in renos) + "</select></label>")
    return f"""<div class="toolbar share-tools">
      <div class="tb-row">
        <label class="search"><input type="search" id="q" placeholder="Улица, ЖК, слово из описания"></label>
        {_seg("zone", zones, lambda z: f'<i class="zdot {z}"></i>{ZONE_SHORT[z]}')}
        {_seg("rooms", rooms, lambda r: "студия" if r == 0 else f"{r}{NBSP}к")}
        {_seg("src", srcs, lambda s: f'<i class="sdot {s}"></i>{SRC[s]}')}
      </div>
      <div class="tb-row">
        <div class="range"><span>Цена, млн</span><input type="number" id="pmin" step="0.1" placeholder="от"><i>—</i><input type="number" id="pmax" step="0.1" placeholder="до"></div>
        <div class="range"><span>Площадь</span><input type="number" id="amin" placeholder="от, м²"></div>
        {reno_sel}
        <label class="sel"><span>Сортировка</span><select id="sort">{
            "".join(f'<option value="{v}">{t}</option>' for v, t in SORTS)}</select></label>
        <label class="check"><input type="checkbox" id="live"><span>только в продаже</span></label>
        <button id="reset" class="link">Сбросить</button>
        <span class="tb-right"><span class="share-count" id="count"></span></span>
      </div>
    </div>"""


SCRIPT = """
(() => {
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const cards = $$("#cards > .card"), rows = $$("#rows > tr"), D = cards.map(c => c.dataset);
  const NUM = new Set(["price", "ppm", "rooms", "area", "floor", "sites"]);
  const val = (i, k) => { const x = D[i][k] || ""; return NUM.has(k) ? (x === "" ? 0 : +x) : String(x); };
  const on = n => $$(`input[name=${n}]`).filter(i => i.checked).map(i => i.value);
  const has = n => $$(`input[name=${n}]`).length > 0;
  let tsort = ["first", -1];

  function pass(d) {
    const q = $("#q").value.trim().toLowerCase();
    const pmin = parseFloat($("#pmin").value) * 1e6, pmax = parseFloat($("#pmax").value) * 1e6;
    const amin = parseFloat($("#amin").value), reno = $("#reno") ? $("#reno").value : "";
    const price = +d.price || 0, area = +d.area || 0;
    return (!q || d.text.includes(q))
      && !(pmin && price < pmin) && !(pmax && price > pmax) && !(amin && area < amin)
      && (!reno || d.reno === reno)
      && (!has("zone") || on("zone").includes(d.zone))
      && (!has("rooms") || d.rooms === "" || on("rooms").includes(d.rooms))
      && (!has("src") || d.src.split(" ").some(s => on("src").includes(s)))
      && (!$("#live").checked || d.active === "1");
  }

  function place(nodes, box, key, dir) {
    [...nodes].sort((a, b) => {
      const x = val(+a.dataset.i, key), y = val(+b.dataset.i, key);
      return (x > y ? 1 : x < y ? -1 : 0) * dir;
    }).forEach(n => box.appendChild(n));
  }

  const SORT = { new: ["first", -1], price: ["price", 1], "-price": ["price", -1], ppm: ["ppm", 1], "-area": ["area", -1] };

  function apply() {
    let n = 0;
    cards.forEach((c, i) => {
      const ok = pass(D[i]);
      c.hidden = !ok;
      if (rows[i]) rows[i].hidden = !ok;
      if (ok) n++;
    });
    $("#count").textContent = n === cards.length ? "" : `показано ${n} из ${cards.length}`;
    const [k, d] = SORT[$("#sort").value] || SORT.new;
    place(cards, $("#cards"), k, d);
    place(rows, $("#rows"), tsort[0], tsort[1]);
    $$("#head th").forEach(th => th.textContent = th.dataset.t + (tsort[0] === th.dataset.k ? (tsort[1] > 0 ? " ↑" : " ↓") : ""));
  }

  $$(".share-tools input, .share-tools select").forEach(el => el.addEventListener("input", apply));
  $("#reset").onclick = () => {
    $$(".share-tools input[type=number], .share-tools input[type=search]").forEach(i => i.value = "");
    $$(".share-tools input[type=checkbox]").forEach(i => { i.checked = !!i.name; });  // зоны, комнаты, сайты — все, «только в продаже» — нет
    if ($("#reno")) $("#reno").value = "";
    $("#sort").value = "new";
    apply();
  };
  $$("#head th").forEach(th => th.onclick = () => {
    tsort = tsort[0] === th.dataset.k ? [th.dataset.k, -tsort[1]] : [th.dataset.k, 1];
    apply();
  });
  $$(".share-views button").forEach(b => b.onclick = () => {
    document.body.dataset.v = b.dataset.v;
    $$(".share-views button").forEach(x => x.classList.toggle("active", x === b));
  });
  apply();
})();
"""

SHARE_CSS = """
/* страница-выгрузка: та же вёрстка карточек, но обычный документ со прокруткой, без боковой панели */
html, body { height: auto; }
[hidden] { display: none !important; }
.share-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; padding: 30px 32px 16px; }
.share-head h1 { margin: 0; font-size: 24px; font-weight: 800; letter-spacing: -.02em; }
.share-head p { margin: 5px 0 0; color: var(--muted); font-size: 13px; }
.share-views { display: flex; gap: 8px; flex: none; }
.share-count { color: var(--muted); font-size: 12.5px; font-weight: 600; align-self: center; }
.share-foot { padding: 26px 32px 60px; color: var(--muted); font-size: 12px; line-height: 1.6; }
.share-note { margin: 0 32px 16px; }
.share-note p { margin: 0 0 6px; }
#tableWrap { display: none; }
body[data-v="table"] #cards { display: none; }
body[data-v="table"] #tableWrap { display: block; }
table.grid th { cursor: pointer; }
table.grid tr:hover td { background: var(--surface-2); }
.card:hover { transform: none; box-shadow: var(--shadow-1); border-color: var(--line); }
.offer { cursor: pointer; }
/* без JavaScript фильтры, сортировка и таблица не работают — их не показываем, плитки остаются */
.nojs .share-tools, .nojs .share-views { display: none; }
"""


def has_liked(con):
    return con.execute("SELECT COUNT(*) FROM likes").fetchone()[0] > 0


def page(con, objs=None):
    """Весь файл одной строкой: стили, шрифты, фото, разметка и скрипт внутри."""
    objs = liked_objects(con) if objs is None else objs
    today = datetime.date.today()
    cards = "".join(_card(o, n) for n, o in enumerate(objs))
    head = "".join(f'<th data-k="{k}" data-t="{t}" class="{"num" if num else ""}">{t}</th>' for k, t, _, num in COLS)
    rows = "".join(f'<tr data-i="{n}" class="{"excl" if o["excluded"] else ""}">'
                   + "".join(f'<td class="{"num" if num else ""}">{cell(o)}</td>' for _, _, cell, num in COLS)
                   + "</tr>" for n, o in enumerate(objs))
    empty = ('<div class="empty"><b>В избранном пусто</b>Отметьте квартиры сердечком в программе и выгрузите снова</div>'
             if not objs else "")
    nojs = (f'<div class="about share-note"><b>JavaScript отключён</b><p>Фильтры, сортировка и таблица не работают, '
            f'но {len(objs)} {plural(len(objs), "объект", "объекта", "объектов")} ниже со всеми ссылками и фотографиями на месте.</p></div>')
    return f"""<!doctype html>
<html lang="ru" class="nojs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Избранное · RealtyWatch</title>
<style>{_css()}</style>
<style>{SHARE_CSS}</style>
<script>document.documentElement.classList.remove("nojs");</script>
</head>
<body class="share" data-v="cards">
<header class="share-head">
  <div><h1>Избранные квартиры в Ялте</h1>
    <p>{len(objs)} {plural(len(objs), "объект", "объекта", "объектов")} · выгружено {today.day} {MONTHS[today.month - 1]} {today.year} · RealtyWatch</p></div>
  <div class="share-views"><button class="toggle active" data-v="cards">Плитки</button><button class="toggle" data-v="table">Таблица</button></div>
</header>
<noscript>{nojs}</noscript>
{_tools(objs) if objs else ""}
<main class="view">
  {empty}
  <div id="cards" class="cards flat">{cards}</div>
  <div id="tableWrap" class="panel"><table class="grid"><thead id="head"><tr>{head}</tr></thead><tbody id="rows">{rows}</tbody></table></div>
</main>
<footer class="share-foot">
  <p>Файл автономный: стили, шрифты и фотографии внутри, интернет нужен только чтобы открыть ссылки на объявления.
  Цены и данные — на момент выгрузки, {today.day} {MONTHS[today.month - 1]} {today.year}; источники — ЦИАН, Авито, Яндекс Недвижимость.</p>
  <p>Собрано программой RealtyWatch.</p>
</footer>
{f"<script>{SCRIPT}</script>" if objs else ""}
</body>
</html>
"""


def export(path, con):
    """Пишет страницу в файл; возвращает путь, число объектов и размер в МБ."""
    # имя дописываем, а не заменяем расширение: «избранное v1.0» не должно стать «избранное v1.html»
    p = Path(path if str(path).lower().endswith((".html", ".htm")) else f"{path}.html")
    objs = liked_objects(con)
    p.write_text(page(con, objs), encoding="utf-8")
    return {"path": str(p), "objects": len(objs), "mb": round(p.stat().st_size / 1048576, 1)}
