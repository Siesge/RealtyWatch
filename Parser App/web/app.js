"use strict";
const SRC = { cian: "ЦИАН", avito: "Авито", yandex: "Яндекс" };
const ZONE = { A: "Красный круг", B: "Остальная Ялта", C: "Посёлки Большой Ялты" };
const ZONE_SHORT = { A: "Круг", B: "Ялта", C: "Посёлок" };
const RENO = { design: "дизайнерский ремонт", euro: "евроремонт", cosmetic: "косметический ремонт", old: "старый ремонт",
  finish: "чистовая отделка", none: "без ремонта", any: "с ремонтом" };
const RENO_RANK = { design: 6, euro: 5, cosmetic: 4, finish: 3, old: 2, any: 1 };
const SELLER = { owner: "собственник", agent: "агент", developer: "застройщик" };
const STATE = { running: "идёт сбор", paused: "пауза", done: "готово", error: "ошибка", blocked: "блокировка", stopped: "остановлен" };
const VIEW_TITLE = { dashboard: "Дашборд", cards: "Подборка", table: "Таблица", changes: "Изменения", map: "Карта", runs: "Прогоны", settings: "Настройки" };
const ICON = {
  pin: '<svg viewBox="0 0 24 24"><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/></svg>',
  ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  map: '<svg viewBox="0 0 24 24"><circle cx="12" cy="10" r="2.5"/><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/></svg>',
  heart: '<svg viewBox="0 0 24 24"><path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2v.1"/></svg>',
  ok: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 4v12M7 11l5 5 5-5M5 20h14"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M5 20h14"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3.5 6.5a1.5 1.5 0 0 1 1.5-1.5h4.5l2 2.5H19a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/></svg>',
  chart: '<svg viewBox="0 0 24 24"><path d="M4 19h16M6 15l4-4 3 3 5-6"/></svg>',
  img: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-4.5 4 3.5 3-2.5 4 3.5"/></svg>',
};

let LASTRUN = {}, ALL = [], OBJS = [], SETTINGS = null, view = "cards", jAfter = 0, tableSort = null, lastStates = {};
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = n => n == null || !isFinite(n) ? "—" : Math.round(n).toLocaleString("ru-RU");
const mln = n => n == null || !isFinite(n) ? "—" : (n / 1e6).toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `Ошибка ${r.status}`);
  return j;
};
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const dt = s => s ? new Date(s).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "";
const today = () => new Date().toISOString().slice(0, 10);
const key = r => `${r.source}:${r.source_id}`;
const desktop = () => window.pywebview && window.pywebview.api;
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* хранилище недоступно */ } },
};
const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many; };

// ---------- данные: объявления → объекты ----------
async function loadData() {
  ALL = await api("/api/listings");
  buildObjects();
  render();
  if (view === "dashboard") renderDashboard();
  if (view === "changes") renderChanges();
}
async function loadSettings() { SETTINGS = await api("/api/settings"); }

function buildObjects() {
  const groups = new Map();
  for (const r of ALL) {
    if (!groups.has(r.object_id)) groups.set(r.object_id, []);
    groups.get(r.object_id).push(r);
  }
  const rank = r => (r.excluded ? 0 : 8) + (r.active ? 4 : 0) + (r.has_img ? 2 : 0) + (r.lat != null ? 1 : 0);
  OBJS = [...groups.values()].map(items => {
    items.sort((a, b) => rank(b) - rank(a) || (a.first_seen > b.first_seen ? 1 : -1));
    const p = items[0];
    const live = items.filter(i => i.active);
    const prices = (live.length ? live : items).map(i => i.price).filter(Boolean);
    const reno = items.map(i => i.renovation).filter(Boolean).sort((a, b) => (RENO_RANK[b] || 0) - (RENO_RANK[a] || 0));
    const flags = {};
    items.forEach(i => Object.assign(flags, i.flags || {}));
    return {
      id: p.object_id, items, p, flags,
      img: items.find(i => i.has_img) || items.find(i => i.photo),
      sources: [...new Set(items.map(i => i.source))],
      price: prices.length ? Math.min(...prices) : null, priceMax: prices.length ? Math.max(...prices) : null,
      liked: items.some(i => i.liked), active: items.some(i => i.active),
      excluded: items.every(i => i.excluded) ? p.excluded : null,
      renovation: p.excluded || !reno.length ? p.renovation : reno[0],
      first_seen: items.reduce((m, i) => (i.first_seen < m ? i.first_seen : m), p.first_seen),
      zone: p.zone, rooms: p.rooms, area: p.area, floor: p.floor, floors: p.floors, lat: p.lat, lon: p.lon,
      address: items.map(i => i.address).filter(Boolean).sort((a, b) => b.length - a.length)[0] || "",
      text: items.map(i => `${i.title} ${i.address} ${i.description}`).join(" ").toLowerCase(),
    };
  });
  OBJS.forEach(o => { o.price_m2 = o.price && o.area ? o.price / o.area : null; });
}

// ---------- фильтр просмотра ----------
const checked = name => $$(`input[name=${name}]:checked`).map(i => i.value);

function filtered(ignoreZone = false) {
  const src = checked("src"), zones = checked("zone"), rooms = checked("rooms").map(Number);
  const pmin = parseFloat($("#fPmin").value) * 1e6, pmax = parseFloat($("#fPmax").value) * 1e6;
  const amin = parseFloat($("#fAmin").value), reno = $("#fReno").value, nb = $("#fNew").value;
  const st = $("#fState").value, active = $("#fActive").checked, noCoords = $("#fNoCoords").checked, liked = $("#fLiked").checked;
  const text = $("#fText").value.trim().toLowerCase();
  let rows = OBJS.filter(o =>
    o.sources.some(s => src.includes(s)) && (ignoreZone || zones.includes(o.zone)) &&
    (o.rooms == null || rooms.includes(o.rooms)) &&
    !(pmin && o.price < pmin) && !(pmax && o.price > pmax) && !(amin && o.area < amin) &&
    (!reno || (reno === "known" ? o.renovation && o.renovation !== "any" : o.renovation === reno)) &&
    (nb === "" || String(o.p.is_new_building ?? 0) === nb) &&
    (st === "all" || (st === "pass" ? !o.excluded : !!o.excluded)) &&
    (!active || o.active) && (!noCoords || o.lat == null) && (!liked || o.liked) &&
    (!text || o.text.includes(text)));
  if (ignoreZone) return rows;
  const s = $("#fSort").value;
  const k = { new: o => o.first_seen, price: o => o.price, "-price": o => o.price, ppm: o => o.price_m2, "-area": o => o.area }[s];
  const dir = s === "new" || s.startsWith("-") ? -1 : 1;
  rows.sort((a, b) => ((k(a) ?? 0) > (k(b) ?? 0) ? 1 : (k(a) ?? 0) < (k(b) ?? 0) ? -1 : 0) * dir);
  return rows;
}

function renderHeader(rows) {
  const zr = filtered(true);
  for (const z of ["A", "B", "C"]) $(`#zc-${z}`).textContent = zr.filter(o => o.zone === z).length;
  const likes = OBJS.filter(o => o.liked).length;
  $("#likeCount").textContent = likes || "";
  const ppm = median(rows.map(o => o.price_m2).filter(Boolean));
  const pr = median(rows.map(o => o.price).filter(Boolean));
  const multi = rows.filter(o => o.sources.length > 1).length;
  $("#kpis").innerHTML = ["runs", "settings", "dashboard", "changes"].includes(view) ? "" : `
    <div class="kpi" title="Объектов по текущим фильтрам. Объект — одна квартира; её объявления с разных сайтов объединены"><b>${rows.length}</b><span>${plural(rows.length, "объект", "объекта", "объектов")}</span></div>
    <div class="kpi gold" title="Медиана цены за м² по показанным объектам: половина дешевле, половина дороже"><b>${fmt(ppm)}</b><span>медиана, ₽/м²</span></div>
    <div class="kpi" title="Медианная цена показанных объектов"><b>${mln(pr)}</b><span>медиана, млн ₽</span></div>
    <div class="kpi" title="Сколько показанных объектов найдено сразу на двух или трёх сайтах"><b>${multi}</b><span>на 2+ сайтах</span></div>`;
  const by = ALL.reduce((m, r) => (m[r.source] = (m[r.source] || 0) + 1, m), {});
  $("#viewSub").textContent = view === "runs" ? "История запусков парсеров" : view === "settings" ? "Параметры поиска, геозона, сбор данных и база"
    : view === "dashboard" ? `Сводка рынка Ялты · в базе ${OBJS.length} ${plural(OBJS.length, "объект", "объекта", "объектов")} из ${ALL.length} объявлений`
    : view === "changes" ? "Новые, снятые, изменения цен — по каждому сайту. Первичная загрузка сайтов изменениями не считается"
    : `В базе ${OBJS.length} ${plural(OBJS.length, "объект", "объекта", "объектов")} из ${ALL.length} объявлений · ` + Object.keys(SRC).map(k => `${SRC[k]} ${by[k] || 0}`).join(" · ");
}

function render() {
  const rows = filtered();
  renderHeader(rows);
  if (view === "cards") renderCards(rows);
  if (view === "table") renderTable(rows);
  if (view === "map") renderMap(rows);
}

// ---------- карточки ----------
const dtt = s => s ? new Date(s).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
const ZONE_TIP = {
  A: "Зона A — внутри красного круга с GeoZone_v1.jpg (радиус ~1 км)",
  B: "Зона B — город Ялта вне красного круга (по официальной границе города)",
  C: "Зона C — посёлок Большой Ялты (Ливадия, Массандра, Кореиз, Гурзуф и др.)",
};
const SELLER_TIP = { owner: "Продаёт собственник — по отметке на сайте", agent: "Продаёт агент или агентство недвижимости",
  developer: "Продаёт застройщик (новостройка, договор с застройщиком)" };

// Последнее изменение цены объявления: [старая, новая, когда]
function lastChange(i) {
  const h = i.history || [];
  for (let k = h.length - 1; k > 0; k--) if (h[k][1] !== h[k - 1][1]) return [h[k - 1][1], h[k][1], h[k][0]];
  return null;
}

function priceTip(o) {
  const lines = o.items.filter(i => i.active).map(i => {
    const ch = lastChange(i);
    return `${SRC[i.source]}: ${fmt(i.price)} ₽` + (ch ? ` (было ${fmt(ch[0])} ₽, изменено ${dtt(ch[2])})` : "");
  });
  return "Минимальная цена среди активных объявлений объекта.\n" + lines.join("\n") + "\nКлик по карточке — график цен.";
}

function delta(o) {
  const i = o.items.find(x => x.active && x.price === o.price && lastChange(x)) || o.items.find(x => x.active && lastChange(x));
  if (!i) return "";
  const [old, now, ts] = lastChange(i), d = now - old;
  return `<span class="delta ${d < 0 ? "down" : "up"}" title="${SRC[i.source]}: цена ${d < 0 ? "снижена" : "повышена"} ${dtt(ts)} — с ${fmt(old)} до ${fmt(now)} ₽ (${d > 0 ? "+" : "−"}${fmt(Math.abs(d))} ₽, ${(100 * d / old).toFixed(1)}%)">${d < 0 ? "↓" : "↑"} ${mln(Math.abs(d))} млн</span>`;
}

function oldPrice(o) {
  const i = o.items.find(x => x.active && x.price === o.price && lastChange(x));
  if (!i) return "";
  const [old, , ts] = lastChange(i);
  return `<s class="old-price" title="Прежняя цена на ${SRC[i.source]} до ${dtt(ts)}. Клик по карточке — вся история цен">${fmt(old)} ₽</s>`;
}

function renoTip(o) {
  const r = o.p;
  if (o.renovation === "any") return "ЦИАН отнёс объявление к «с ремонтом», но вид ремонта не указан";
  if (!o.renovation) return "Ни сайт, ни описание не указывают вид ремонта. Объявление не отсеяно — проверьте по фото";
  return `Ремонт: ${RENO[o.renovation]}. ` + (r.renovation_raw ? `На сайте указано «${r.renovation_raw}»` : "Определено по тексту описания");
}

function goneTip(i) {
  return `снято ${dtt(i.removed_at) || "—"}. ${i.removed_reason || "Не найдено в поиске сайта"}. Последний раз в поиске: ${dtt(i.last_seen)}`;
}

function tags(o) {
  const t = [], r = o.p;
  if (o.renovation) t.push(`<span class="tag ${o.renovation === "none" ? "bad" : "good"}" title="${esc(renoTip(o))}">${RENO[o.renovation] || o.renovation}</span>`);
  else t.push(`<span class="tag warn" title="${esc(renoTip(o))}">ремонт не указан</span>`);
  if (r.is_new_building) t.push(`<span class="tag" title="Квартира в новостройке (по данным сайта)">новостройка</span>`);
  if (o.items.some(i => i.raw && i.raw.apartments)) t.push(`<span class="tag warn" title="Юридически апартаменты, а не квартира: нельзя прописаться, выше налог и тарифы ЖКХ">апартаменты</span>`);
  const sellers = [...new Set(o.items.map(i => i.seller_type).filter(Boolean))];
  sellers.forEach(s => t.push(`<span class="tag" title="${esc(SELLER_TIP[s] || s)}">${SELLER[s] || s}</span>`));
  if (o.lat == null) t.push(`<span class="tag warn" title="Сайт не дал координаты: зона определена по адресу, на карте объекта нет">нет координат</span>`);
  for (const [k, lbl, what] of [["encumbrance", "обременение?", "обременение"], ["share", "доля?", "продажа доли"], ["auction", "торги?", "торги / аукцион"]])
    if (o.flags[k]) t.push(`<span class="tag warn" title="${esc(`Возможно ${what}. В тексте найдено: «${o.flags[k].join("» … «")}». Проверьте вручную`)}">${lbl}</span>`);
  if (o.excluded) t.push(`<span class="tag bad" title="${esc(`Не прошло отсев по параметрам поиска: ${o.excluded}. Параметры — в «Настройках»`)}">✕ ${esc(o.excluded)}</span>`);
  if (!o.active) t.push(`<span class="tag bad" title="${esc("Снято со всех сайтов:\n" + o.items.map(i => `${SRC[i.source]}: ${goneTip(i)}`).join("\n"))}">снято с продажи</span>`);
  return t.join("");
}

function offers(o) {
  const best = o.items.length > 1 && o.price !== o.priceMax ? o.price : null;
  const list = [...o.items].sort((a, b) => (b.active - a.active) || ((a.price || 0) - (b.price || 0)));
  return `<div class="offers">${list.map(i => {
    const ch = i.active ? lastChange(i) : null;
    const tip = i.active
      ? `Открыть объявление на ${SRC[i.source]}.\nЦена ${fmt(i.price)} ₽` + (ch ? `, до ${dtt(ch[2])} было ${fmt(ch[0])} ₽` : ", не менялась") +
        `.\nВ базе с ${dtt(i.first_seen)}, последний раз в поиске ${dtt(i.last_seen)}` + (i.seller_type ? `.\n${SELLER_TIP[i.seller_type]}` : "")
      : `Перечёркнуто — объявление считается снятым с продажи: ${goneTip(i)}.\nЕсли оно на сайте есть — «Настройки → База данных → Перепроверить снятые»`;
    return `<a class="offer ${i.active ? "" : "gone"}" href="${esc(i.url)}" target="_blank" rel="noopener" title="${esc(tip)}">
    <i class="sdot ${i.source}"></i><span>${SRC[i.source]}${i.excluded && !o.excluded ? ` <span class="tag warn" title="${esc(`Это объявление не прошло отсев (${i.excluded}), но другое объявление этой квартиры прошло`)}">!</span>` : ""}</span>
    <span>${best && i.price === best && i.active ? '<span class="best" title="Здесь цена ниже, чем на других сайтах">дешевле</span> ' : ""}${ch ? `<s class="old-mini">${mln(ch[0])}</s> ` : ""}<b>${fmt(i.price)} ₽</b></span>${ICON.ext}</a>`;
  }).join("")}</div>`;
}

function media(o) {
  if (!o.img) return `<div class="media noimg" title="Фото не найдено на сайте — повторная попытка при следующем сборе">`;
  return `<div class="media"><img src="/img/${encodeURIComponent(o.img.source)}/${encodeURIComponent(o.img.source_id)}" loading="lazy" alt=""
    onerror="this.parentNode.classList.add('noimg');this.remove()">`;
}

function card(o) {
  const meta = [o.p.living_area && `жилая ${o.p.living_area} м²`, o.p.kitchen_area && `кухня ${o.p.kitchen_area} м²`, o.p.build_year && `${o.p.build_year} г.`].filter(Boolean).join(" · ");
  const rooms = o.rooms === 0 ? "студия" : o.rooms ?? "—";
  const n = o.items.length;
  const multiTip = `Эта квартира найдена на: ${o.sources.map(s => SRC[s]).join(", ")} — объявления объединены в одну карточку.\n` +
    "Признаки одной квартиры: те же комнаты, этаж и этажность, площадь ±1 м², адрес в пределах 150 м, цена ±10%";
  return `<article class="card ${o.excluded ? "excl" : ""} ${o.active ? "" : "inactive"}" data-id="${esc(o.id)}">
    ${media(o)}
      <div class="media-top"><span class="left"><span class="pill zone-${o.zone}" title="${esc(ZONE_TIP[o.zone] || "")}">${ZONE_SHORT[o.zone] || o.zone}</span>
        ${o.sources.length > 1 ? `<span class="pill multi" title="${esc(multiTip)}">${o.sources.length} ${plural(o.sources.length, "сайт", "сайта", "сайтов")}</span>` : ""}</span>
        <button class="heart ${o.liked ? "on" : ""}" title="${o.liked ? "В избранном — нажмите, чтобы убрать" : "Добавить в избранное: фильтр «Избранное» и отдельный блок на дашборде"}">${ICON.heart}</button></div>
      <div class="media-bottom"><div><div class="price" title="${esc(priceTip(o))}">${fmt(o.price)} ₽ ${oldPrice(o)}</div>
        <div class="ppm" title="Цена за квадратный метр общей площади${o.priceMax > o.price ? `; на других сайтах цена до ${fmt(o.priceMax)} ₽` : ""}">${fmt(o.price_m2)} ₽/м²${o.priceMax > o.price ? ` · до ${mln(o.priceMax)} млн` : ""}</div></div>${delta(o)}</div>
    </div>
    <div class="body">
      <div class="facts">
        <div class="fact" title="Число комнат"><b>${rooms}</b><span>${o.rooms === 0 ? "" : "комнат"}</span></div>
        <div class="fact" title="Общая площадь квартиры"><b>${o.area ?? "—"}</b><span>м² общая</span></div>
        <div class="fact" title="Этаж квартиры / этажей в доме"><b>${o.floor ?? "?"}<small>/${o.floors ?? "?"}</small></b><span>этаж</span></div>
      </div>
      <div class="addr" title="Адрес (самый подробный из объявлений объекта)">${ICON.pin}<span>${esc(o.address || "адрес не указан")}</span></div>
      ${meta ? `<div class="meta" title="Жилая площадь, кухня, год постройки — со страниц объявлений">${esc(meta)}</div>` : ""}
      <div class="tags">${tags(o)}</div>
      ${offers(o)}
      <p class="desc" title="Начало описания; полностью — в окне карточки (клик по карточке)">${esc((o.p.description || "").slice(0, 320))}</p>
    </div>
    <div class="card-foot"><span title="Когда объект впервые появился в базе; число объявлений на всех сайтах">с ${dt(o.first_seen)} · ${n} ${plural(n, "объявление", "объявления", "объявлений")}</span>
      <span class="acts"><button class="icon-btn more" title="Подробности и график цен">${ICON.chart}</button>${o.lat != null ? `<button class="icon-btn pin" title="Показать на карте рядом">${ICON.map}</button>` : ""}</span></div>
  </article>`;
}

function renderCards(rows) {
  if (sideOn()) {
    if (!SIDE) SIDE = makeMap("sideMap");
    drawMap(SIDE, rows);
    hl = null;
  }
  const el = $("#cardsList");
  if (!rows.length) {
    const liked = $("#fLiked").checked;
    el.innerHTML = `<div class="empty"><b>${liked ? "В избранном пока пусто" : ALL.length ? "Ничего не найдено" : "База пока пуста"}</b>${
      liked ? "Нажмите ♥ на карточке, чтобы добавить объект" : ALL.length ? "Ослабьте фильтры или включите другие зоны" : "Нажмите «Запустить» слева, чтобы собрать объявления"}</div>`;
    return;
  }
  const LIMIT = 240;
  if (!groupOn()) {
    el.innerHTML = `<div class="cards flat">${rows.slice(0, LIMIT * 2).map(card).join("")}</div>`;
    return;
  }
  el.innerHTML = Object.keys(ZONE).map(z => {
    const zr = rows.filter(o => o.zone === z);
    if (!zr.length) return "";
    const ppm = median(zr.map(o => o.price_m2).filter(Boolean)), pr = median(zr.map(o => o.price).filter(Boolean));
    return `<section class="zone"><div class="zone-head" title="${esc(ZONE_TIP[z])}"><div class="zone-mark ${z}">${z}</div>
      <div><h2>${ZONE[z]}</h2><p>${zr.length} ${plural(zr.length, "объект", "объекта", "объектов")} · медиана ${fmt(ppm)} ₽/м² · ${mln(pr)} млн ₽${zr.length > LIMIT ? ` · показаны первые ${LIMIT}` : ""}</p></div>
      <div class="rule"></div></div>
      <div class="cards">${zr.slice(0, LIMIT).map(card).join("")}</div></section>`;
  }).join("");
}

async function toggleLike(id, btn) {
  const o = OBJS.find(x => x.id === id);
  if (!o) return;
  const on = !o.liked;
  o.liked = on;
  o.items.forEach(i => { i.liked = on; });
  if (btn) { btn.classList.toggle("on", on); btn.classList.add("pop"); setTimeout(() => btn.classList.remove("pop"), 400); }
  try {
    await post("/api/like", { keys: o.items.map(key), on });
  } catch (e) {
    toast("warn", "Не удалось сохранить", e.message);
  }
  if ($("#fLiked").checked && !on) render(); else renderHeader(filtered());
}

// ---------- таблица ----------
const COLS = [
  ["sources", "Сайты", o => o.items.map(i => `<a href="${esc(i.url)}" target="_blank" title="${fmt(i.price)} ₽"><i class="sdot ${i.source}"></i> ${SRC[i.source]}</a>`).join("<br>")],
  ["zone", "Зона", o => `<i class="zdot ${o.zone}"></i> ${ZONE_SHORT[o.zone]}`],
  ["price", "Цена", o => fmt(o.price) + (o.priceMax > o.price ? `<br><small>до ${fmt(o.priceMax)}</small>` : ""), 1],
  ["price_m2", "₽/м²", o => fmt(o.price_m2), 1], ["rooms", "Комн.", o => o.rooms ?? "", 1],
  ["area", "м²", o => o.area ?? "", 1], ["floor", "Этаж", o => `${o.floor ?? "?"}/${o.floors ?? "?"}`, 1],
  ["renovation", "Ремонт", o => RENO[o.renovation] || ""], ["address", "Адрес", o => esc(o.address || "")],
  ["excluded", "Отсев", o => esc(o.excluded || "")], ["first_seen", "Найдено", o => dt(o.first_seen)],
  ["liked", "♥", o => o.liked ? "♥" : ""],
];

function renderTable(rows) {
  if (tableSort) {
    const [k, d] = tableSort;
    const v = o => (k === "sources" ? o.sources.length : o[k]) ?? "";
    rows = [...rows].sort((a, b) => (v(a) > v(b) ? 1 : v(a) < v(b) ? -1 : 0) * d);
  }
  $("#view-table").innerHTML = `<div class="panel"><table class="grid"><thead><tr>${COLS.map(c => `<th data-k="${c[0]}">${c[1]}${tableSort && tableSort[0] === c[0] ? (tableSort[1] > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(0, 1500).map(o => `<tr class="${o.excluded ? "excl" : ""}" data-id="${esc(o.id)}" title="Клик — открыть карточку в подборке">${COLS.map(c => `<td class="${c[3] ? "num" : ""}">${c[2](o)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  $$("#view-table tbody tr").forEach(tr => tr.onclick = e => { if (!e.target.closest("a")) goToCard(tr.dataset.id); });
  $$("#view-table th").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    tableSort = tableSort && tableSort[0] === k ? [k, -tableSort[1]] : [k, 1];
    render();
  });
}

// ---------- карты ----------
const COLOR = { cian: "#3E7BFA", avito: "#1FAF6A", yandex: "#F2552F" };
let MAIN = null, SIDE = null, hl = null;

function makeMap(el) {
  const m = L.map(el, { zoomControl: true, attributionControl: true }).setView([44.49, 34.155], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(m);
  legend().addTo(m);
  return { map: m, layer: L.layerGroup().addTo(m), zone: null, markers: {} };
}

// Цвет точки — набор сайтов объекта (среди выбранных в фильтре): один сайт — его цвет, два — смешанный, три — светлое золото
const MIX = {
  "cian": "#3E7BFA", "avito": "#1FAF6A", "yandex": "#F2552F",
  "cian+yandex": "#9B5DE5", "avito+yandex": "#F2C230", "avito+cian": "#19B9C4", "avito+cian+yandex": "#F3DFA2",
};
const MIX_LABEL = {
  "cian": "только ЦИАН", "avito": "только Авито", "yandex": "только Яндекс", "cian+yandex": "ЦИАН + Яндекс",
  "avito+yandex": "Авито + Яндекс", "avito+cian": "ЦИАН + Авито", "avito+cian+yandex": "на всех трёх сайтах",
};
function markerKey(o) {
  const src = checked("src");
  const s = o.sources.filter(x => src.includes(x));
  return (s.length ? s : o.sources).slice().sort().join("+");
}

function markerStyle(o, on) {
  const k = markerKey(o), n = k.split("+").length;
  return { radius: on ? 11 : n > 1 ? 8 : 6.5, color: on ? "#111" : o.liked ? "#E0475B" : "#fff", weight: on || o.liked ? 3 : 1.5,
    fillColor: MIX[k] || COLOR[o.p.source], fillOpacity: o.excluded ? .35 : .95 };
}

function legend() {
  const c = L.control({ position: "bottomleft" });
  c.onAdd = () => {
    const d = L.DomUtil.create("div", "map-legend");
    d.innerHTML = Object.keys(MIX).map(k => `<span title="${MIX_LABEL[k]}"><i style="background:${MIX[k]}"></i>${MIX_LABEL[k]}</span>`).join("") +
      `<span title="Объект в избранном — отмечен сердечком (♥ в подписи точки)"><i class="liked"></i>♥ избранное</span>` +
      `<span title="Объявление не прошло отсев по параметрам поиска"><i class="dim"></i>отсеяно</span>` +
      `<span title="Зона A — красный круг с GeoZone_v1.jpg"><i class="zone"></i>красный круг</span>`;
    L.DomEvent.disableClickPropagation(d);
    return d;
  };
  return c;
}

function drawMap(M, rows) {
  setTimeout(() => M.map.invalidateSize(), 60);
  if (M.zone) M.zone.remove();
  const z = SETTINGS.zone, gold = getComputedStyle(document.documentElement).getPropertyValue("--gold").trim() || "#B4873B";
  const style = { color: gold, weight: 2.5, dashArray: "7 6", fillColor: gold, fillOpacity: .10 };
  M.zone = (z.polygon ? L.polygon(z.polygon, style) : L.circle(z.center, { radius: z.radius_m, ...style })).addTo(M.map);
  M.layer.clearLayers();
  M.markers = {};
  rows.filter(o => o.lat != null).forEach(o => {
    const links = o.items.map(i => `<a href="${esc(i.url)}" target="_blank">${SRC[i.source]} · ${fmt(i.price)} ₽ →</a>`).join("<br>");
    const mk = L.circleMarker([o.lat, o.lon], markerStyle(o, false))
      .bindPopup(`<b>${fmt(o.price)} ₽</b>${o.liked ? " ♥" : ""}<br>${o.rooms ?? "?"}-к, ${o.area ?? "?"} м², ${o.floor ?? "?"}/${o.floors ?? "?"} эт.<br><span style="opacity:.7">${esc(o.address)}</span><br>${links}`)
      .addTo(M.layer);
    mk.obj = o;
    mk.on("click", () => focusCard(o.id));
    M.markers[o.id] = mk;
  });
}

function renderMap(rows) { if (!MAIN) MAIN = makeMap("map"); drawMap(MAIN, rows); }

function highlight(id, fly) {
  if (!SIDE || !sideOn()) return;
  if (hl && SIDE.markers[hl]) SIDE.markers[hl].setStyle(markerStyle(SIDE.markers[hl].obj, false));
  hl = id;
  const mk = SIDE.markers[id];
  if (!mk) return;
  mk.setStyle(markerStyle(mk.obj, true)).bringToFront();
  if (fly) { SIDE.map.flyTo(mk.getLatLng(), Math.max(SIDE.map.getZoom(), 16), { duration: .6 }); mk.openPopup(); }
}

function focusCard(id) {
  if (view !== "cards") return;
  const el = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
  if (!el) return;
  el.scrollIntoView({ block: "center", behavior: "smooth" });
  el.classList.add("focus");
  setTimeout(() => el.classList.remove("focus"), 1800);
}

const groupOn = () => $("#btnGroup").classList.contains("active");
function setGroup(on) {
  $("#btnGroup").classList.toggle("active", on);
  store.set("rw.group", on ? "1" : "0");
  render();
}

// Перетаскиваемый разделитель карточек и карты; ширина карты хранится в % от ширины области
function initGutter() {
  const g = $("#gutter"), split = $("#split");
  const saved = parseFloat(store.get("rw.mapW", ""));
  if (saved) split.style.setProperty("--map-w", `${saved}%`);
  let drag = null;
  g.addEventListener("pointerdown", e => {
    drag = split.getBoundingClientRect();
    g.setPointerCapture(e.pointerId);
    split.classList.add("dragging");
    e.preventDefault();
  });
  g.addEventListener("pointermove", e => {
    if (!drag) return;
    const w = Math.max(260, Math.min(drag.right - e.clientX - 8, drag.width - 300));
    split.style.setProperty("--map-w", `${(100 * w / drag.width).toFixed(2)}%`);
    if (SIDE) SIDE.map.invalidateSize({ pan: false });
  });
  const end = () => {
    if (!drag) return;
    drag = null;
    split.classList.remove("dragging");
    store.set("rw.mapW", parseFloat(split.style.getPropertyValue("--map-w")) || "");
    if (SIDE) SIDE.map.invalidateSize();
  };
  g.addEventListener("pointerup", end);
  g.addEventListener("pointercancel", end);
  g.addEventListener("dblclick", () => {
    split.style.removeProperty("--map-w");
    store.set("rw.mapW", "");
    if (SIDE) setTimeout(() => SIDE.map.invalidateSize(), 50);
  });
}

const sideOn = () => $("#split").classList.contains("with-map");
function setSide(on) {
  $("#split").classList.toggle("with-map", on);
  $("#btnSide").classList.toggle("active", on);
  store.set("rw.sideMap", on ? "1" : "0");
  if (on) render();
}

// ---------- прогоны ----------
async function renderRuns() {
  const runs = await api("/api/runs");
  const t = s => (s || "").replace("T", " ").slice(5, 16);
  $("#view-runs").innerHTML = runs.length ? `<div class="panel"><table class="grid"><thead><tr><th>Сайт</th><th>Статус</th><th>Старт</th><th>Финиш</th><th>Режим</th>
    <th>Стр.</th><th>Найдено</th><th>Новых</th><th>Изм.</th><th>Снято</th><th>Итог</th></tr></thead><tbody>${runs.map(r => `<tr>
    <td><i class="sdot ${r.source}"></i> ${SRC[r.source]}</td><td><span class="st-badge ${r.status}">${STATE[r.status] || r.status}</span></td>
    <td>${t(r.started)}</td><td>${t(r.finished)}</td><td>${r.mode}</td><td class="num">${r.pages ?? ""}</td><td class="num">${r.found ?? ""}</td>
    <td class="num">${r.new ?? ""}</td><td class="num">${r.changed ?? ""}</td><td class="num">${r.removed ?? ""}</td><td>${esc(r.message || "")}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty"><b>Прогонов ещё не было</b>Запустите сбор данных в панели слева</div>`;
}

// ---------- настройки и база ----------
async function renderSettings() {
  await loadSettings();  // свежие значения: задержка Авито могла вырасти во время сбора
  const f = SETTINGS.filters, z = SETTINGS.zone, s = SETTINGS.sites, b = SETTINGS.browser || {};
  const sw = (id, on) => `<span class="sw"><input type="checkbox" id="${id}" ${on ? "checked" : ""}><i></i></span>`;
  const field = (label, control, hint) => `<div class="field"><label>${label}${hint ? `<span class="f-hint">${hint}</span>` : ""}</label>${control}</div>`;
  const modeSel = k => `<select data-site="${k}">${[["auto", "авто"], ["http", "только HTTP"], ["browser", "только браузер"]]
    .map(([v, t]) => `<option value="${v}" ${s[k].mode === v ? "selected" : ""}>${t}</option>`).join("")}</select>`;
  const modeHint = "<b>авто</b> — сначала быстрый HTTP-запрос с отпечатком Chrome; если сайт блокирует, сам переходит на скрытый браузер. " +
    "<b>только HTTP</b> — быстрее, но при блокировке сбор остановится. <b>только браузер</b> — медленнее, но надёжнее на строгих сайтах.";
  $("#view-settings").innerHTML = `<div class="settings">
    <div class="s-card"><h3>Параметры поиска</h3><p class="hint">Передаются сайтам при сборе и используются для отсева. После сохранения вся база пересчитывается сразу; новые границы поиска на сайтах — при следующем сборе.</p>
      ${field("Комнаты", `<input type="text" id="sRooms" value="${f.rooms.join(", ")}">`, "Через запятую. Студия — 0. Объявления с другим числом комнат отсеиваются с причиной «комнат: N».")}
      ${field("Площадь от, м²", `<input type="number" id="sArea" value="${f.area_min}">`, "Минимальная общая площадь. Меньшие — отсев «площадь».")}
      ${field("Цена от, ₽", `<input type="number" id="sPmin" value="${f.price_min}">`, "Нижняя граница цены. Передаётся сайтам; Яндекс иногда подмешивает «похожие» дешевле — они отсеиваются.")}
      ${field("Цена до, ₽", `<input type="number" id="sPmax" value="${f.price_max}">`, "Верхняя граница цены.")}
      ${field("Ремонт", `<select id="sReno">
        <option value="exclude_none" ${f.renovation === "exclude_none" ? "selected" : ""}>без ремонта — отсеять</option>
        <option value="strict" ${f.renovation === "strict" ? "selected" : ""}>только указанный ремонт</option>
        <option value="off" ${f.renovation === "off" ? "selected" : ""}>не учитывать</option></select>`,
        "<b>без ремонта — отсеять</b>: убираются «требует ремонта», «без отделки», черновая/предчистовая (по полю сайта или тексту); где ремонт не указан — остаётся с жёлтой меткой. <b>только указанный</b>: остаются лишь объявления с явно указанным ремонтом. ЦИАН дополнительно фильтруется по ремонту на своей стороне.")}
      ${field("Исключать обременение", sw("sEnc", f.exclude_encumbrance), "Ищет в тексте «обременение», «арест», «в залоге», «залог у банка». Отрицания («без обременений», «аресты сняты») не считаются. Выключено — объявление остаётся с меткой «обременение?».")}
      ${field("Исключать доли", sw("sShare", f.exclude_share), "Категория «доля» на ЦИАН, заголовок «Доля…» на Авито, в тексте «доля», «1/2 доли», «долевая собственность».")}
      ${field("Исключать аукционы и торги", sw("sAuc", f.exclude_auction), "В тексте «аукцион», «торги», «банкротство», «конкурсный управляющий». «Торг уместен» не считается.")}
    </div>
    <div class="s-card"><h3>Геозона «Красный круг»</h3><p class="hint">Зона A на карточках и карте. Калибровка по GeoZone_v1.jpg: Ливадийский дворец, Белая дача, «Атлантида» (точность ~50–70 м). Проверить — на вкладке «Карта» (золотой пунктир).</p>
      ${field("Центр — широта", `<input type="number" step="0.000001" id="zLat" value="${z.center[0]}">`, "Градусы северной широты центра круга.")}
      ${field("Центр — долгота", `<input type="number" step="0.000001" id="zLon" value="${z.center[1]}">`, "Градусы восточной долготы центра круга.")}
      ${field("Радиус, м", `<input type="number" id="zR" value="${z.radius_m}">`, "Объекты ближе этого расстояния к центру относятся к зоне A, остальные в городе — к B, в посёлках — к C.")}
    </div>
    <div class="s-card"><h3>Сбор данных</h3><p class="hint">Как программа забирает объявления. Если сайт покажет капчу, окно браузера появится само — пройдите её, сбор продолжится.</p>
      ${field("ЦИАН", modeSel("cian"), modeHint)}
      ${field("Авито", modeSel("avito"), "Авито отдаёт страницы объявлений только браузеру, поэтому он самый медленный.")}
      ${field("Яндекс", modeSel("yandex"), "Яндекс обычно стабильно отвечает на быстрые HTTP-запросы.")}
      ${field("Авито: страниц объявлений за прогон", `<input type="number" id="sAvDet" value="${s.avito.max_details}">`, "Сколько новых объявлений Авито открывать за один сбор (ради координат и вида ремонта). Остальные — в следующий раз. Больше — дольше и выше риск ограничения IP.")}
      ${field("Авито: пауза между запросами, с", `<span class="pair"><input type="number" step="1" min="1" id="sAvD0" value="${s.avito.delay[0]}"><i>—</i><input type="number" step="1" min="1" id="sAvD1" value="${s.avito.delay[1]}"></span>`,
        "Случайная пауза в этом диапазоне перед каждым запросом. При ограничении IP растёт сама в ~1,5 раза и сохраняется.")}
      ${field("Окно браузера", `<select id="sWin">
        ${[["hidden", "скрытое"], ["visible", "видимое"], ["headless", "без окна (headless)"]]
          .map(([v, t]) => `<option value="${v}" ${(b.window || "hidden") === v ? "selected" : ""}>${t}</option>`).join("")}</select>`,
        "<b>скрытое</b> — полноценное окно за пределами экрана, появляется только для капчи. <b>видимое</b> — можно наблюдать за сбором. <b>headless</b> — без окна; сайты распознают такой режим легче.")}
    </div>
    <div class="s-card" id="dbCard"><h3>База данных</h3><p class="hint">Объявления, фото, избранное, история цен и настройки поиска — в папке database. Перенос на другое устройство: «Экспорт» здесь → «Импорт» там.</p>
      <div class="db-stats" id="dbStats"></div>
      <div class="db-path" id="dbPath" title="Папка базы — её можно копировать целиком"></div>
      <div class="btn-row">
        <button class="btn-sec" id="btnExport" title="Сохранить всю базу (объявления, фото, избранное, историю, настройки поиска) в один .zip">${ICON.down}Экспорт…</button>
        <button class="btn-sec" id="btnImport" title="Загрузить базу из .zip. Перед импортом текущая база сохраняется в database\\backups">${ICON.up}Импорт…</button>
        <select id="impMode" title="«Объединить» — добавить новое и обновить более свежим; «заменить» — полностью заменить текущую базу"><option value="merge">объединить с текущей</option><option value="replace">заменить полностью</option></select>
      </div>
      <div class="btn-row" style="margin-top:8px">
        <button class="btn-sec" id="btnImages" title="Скачать фото, которые не загрузились (повторяется и само — после сбора и при запуске)">${ICON.img}Догрузить фото</button>
        <button class="btn-sec" id="btnRecheck" title="Открыть на сайтах все объявления ЦИАН и Яндекса, помеченные снятыми, и вернуть те, что на самом деле активны">${ICON.ok}Перепроверить снятые</button>
        ${desktop() ? `<button class="btn-sec" id="btnFolder" title="Открыть папку database в Проводнике">${ICON.folder}Открыть папку</button>` : ""}
      </div>
    </div>
    <div class="s-actions"><button class="btn-primary" id="btnSave" title="Сохранить настройки и сразу пересчитать зоны, отсев и объединение дублей">Сохранить и пересчитать</button><span class="meta">Зоны, отсев и объединение пересчитаются сразу, параметры поиска — при следующем сборе.</span></div>
  </div>`;
  $("#btnSave").onclick = saveSettings;
  $("#btnExport").onclick = doExport;
  $("#btnImport").onclick = doImport;
  $("#btnImages").onclick = async () => { await post("/api/images/repair"); toast("ok", "Догружаю фото", "Прогресс — в журнале"); };
  $("#btnRecheck").onclick = async () => {
    try { await post("/api/recheck_removed"); toast("ok", "Перепроверяю снятые", "Итог — в журнале; ошибочно снятые вернутся в подборку"); }
    catch (e) { toast("warn", "Не получилось", e.message); }
  };
  if ($("#btnFolder")) $("#btnFolder").onclick = () => desktop().open_db_folder();
  renderDbInfo();
}

async function renderDbInfo() {
  const d = await api("/api/db/info");
  if (!$("#dbStats")) return;
  $("#dbStats").innerHTML = `<div title="Уникальных квартир после объединения дублей"><b>${d.objects}</b><span>объектов</span></div>
    <div title="Всего объявлений со всех сайтов, включая снятые и отсеянные"><b>${d.listings}</b><span>объявлений</span></div>
    <div title="Сохранённых в базе главных фото"><b>${d.images}</b><span>фото</span></div>
    <div title="Объявлений, отмеченных сердечком"><b>${d.likes}</b><span>в избранном</span></div>
    <div title="Активных объявлений, подходящих под параметры поиска"><b>${d.passing}</b><span>прошли отсев</span></div>
    <div title="Объём базы с фото"><b>${d.size_mb} МБ</b><span>размер</span></div>`;
  $("#dbPath").textContent = d.folder;
}

async function doExport() {
  const btn = $("#btnExport");
  btn.disabled = true;
  try {
    const r = desktop() ? await desktop().export_db() : await post("/api/db/export");
    if (r && !r.cancelled) toast("ok", "База экспортирована", r.path);
  } catch (e) {
    toast("warn", "Экспорт не удался", e.message || String(e));
  } finally { btn.disabled = false; }
}

async function doImport() {
  let path;
  if (desktop()) {
    const r = await desktop().pick_import();
    if (!r || r.cancelled) return;
    path = r.path;
  } else {
    path = prompt("Путь к архиву базы (.zip)");
    if (!path) return;
  }
  const mode = $("#impMode").value;
  if (mode === "replace" && !confirm("Заменить текущую базу содержимым архива? Текущая база будет сохранена в database\\backups.")) return;
  toast("ok", "Импорт…", "Это может занять до минуты");
  try {
    const r = await post("/api/db/import", { path, mode });
    toast("ok", "Импорт завершён", `Добавлено ${r.added}, обновлено ${r.updated}, фото ${r.images}. Резервная копия: ${r.backup}`);
    await loadSettings();
    await loadData();
    renderSettings();
  } catch (e) {
    toast("warn", "Импорт не удался", e.message);
  }
}

async function saveSettings() {
  const f = SETTINGS.filters, z = SETTINGS.zone, s = SETTINGS.sites;
  f.rooms = $("#sRooms").value.split(/[,\s]+/).map(x => parseInt(x)).filter(x => !isNaN(x));
  f.area_min = +$("#sArea").value; f.price_min = +$("#sPmin").value; f.price_max = +$("#sPmax").value;
  f.exclude_encumbrance = $("#sEnc").checked; f.exclude_share = $("#sShare").checked; f.exclude_auction = $("#sAuc").checked;
  f.renovation = $("#sReno").value;
  z.center = [+$("#zLat").value, +$("#zLon").value]; z.radius_m = +$("#zR").value;
  $$("#view-settings select[data-site]").forEach(el => s[el.dataset.site].mode = el.value);
  s.avito.max_details = +$("#sAvDet").value;
  const d0 = Math.max(1, +$("#sAvD0").value), d1 = Math.max(d0, +$("#sAvD1").value);
  s.avito.delay = [d0, d1];
  SETTINGS.browser = Object.assign(SETTINGS.browser || {}, { window: $("#sWin").value });
  SETTINGS = await post("/api/settings", SETTINGS);
  const r = await post("/api/reclassify");
  await loadData();
  renderDbInfo();
  toast("ok", "Настройки сохранены", `Пересчитано объявлений: ${r.updated}, объектов: ${r.objects}`);
}

// ---------- уведомления ----------
function toast(kind, title, text, action) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<div class="ic">${kind === "warn" ? ICON.warn : ICON.ok}</div><div><b>${esc(title)}</b><p>${esc(text || "")}</p>
    ${action ? `<button class="toast-act">${esc(action.label)}</button>` : ""}</div>`;
  if (action) el.querySelector(".toast-act").onclick = () => { action.fn(); el.remove(); };
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "warn" || action ? 15000 : 6000);
}

// ---------- статус сбора и журнал ----------
function renderSources(status) {
  $("#sources").innerHTML = Object.keys(SRC).map(k => {
    const v = status[k] || LASTRUN[k], on = $(`.src-run[value=${k}]`)?.checked ?? true;
    const paused = v && v.running && v.paused_until;
    const state = v ? (paused ? "paused" : v.running ? "running" : v.state) : "";
    const pct = v && v.pages ? Math.round(100 * v.page / v.pages) : 0;
    const meta = !v ? "ещё не запускался"
      : paused ? `⏸ ограничение IP — пауза до ${new Date(v.paused_until).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}, задержка ${v.delay ? v.delay.join("–") + " с" : "увеличена"}`
      : v.running ? `стр. ${v.page || 0} из ${v.pages || "?"} · найдено ${v.found}${v.details ? ` · карточек ${v.details}` : ""}`
      : v.message || "";
    const tip = `${SRC[k]}: переключатель включает сайт в сбор — и для кнопки «Запустить», и для автообновления.` +
      (v && v.finished ? `
Последний сбор завершён ${dtt(v.finished)}: ${v.message || STATE[v.state] || ""}` : "");
    return `<label class="src-row ${state}" title="${esc(tip)}"><input type="checkbox" class="src-run" value="${k}" ${on ? "checked" : ""}><span class="switch"></span>
      <span class="src-name"><i class="sdot ${k}"></i>${SRC[k]}</span><span class="src-state ${state}">${v ? STATE[state] || state : ""}</span>
      <span class="src-meta">${esc(meta)}</span><span class="meter"><i style="width:${v ? (v.running ? Math.max(pct, 4) : 100) : 0}%"></i></span></label>`;
  }).join("");
}

async function poll() {
  try {
    const s = await api(`/api/status?after=${jAfter}`);
    if (!$("#sources").matches(":hover")) renderSources(s.status);
    for (const [k, v] of Object.entries(s.status)) {
      const st = v.running ? "running" : v.state;
      if (lastStates[k] === "running" && st !== "running")
        toast(st === "done" ? "ok" : "warn", `${SRC[k]}: ${STATE[st] || st}`, v.message || "");
      lastStates[k] = st;
    }
    if (s.journal.length) {
      const body = $("#jBody");
      s.journal.forEach(j => {
        const d = document.createElement("div");
        const warn = j.msg.includes("⚠"), ban = j.msg.includes("⛔");
        d.className = "jl" + (warn || ban ? " warn" : "");
        if (ban) {
          toast("warn", `${SRC[j.src]}: ограничение по IP`, j.msg.replace(/^⛔\s*/, ""));
          $("#jDot").classList.remove("hidden");
          loadSettings();  // задержка в настройках увеличилась
        }
        d.innerHTML = `<span class="t">${j.t}</span><span class="s">${SRC[j.src] || (j.src === "db" ? "База" : j.src)}</span>${esc(j.msg)}`;
        const keep = body.scrollTop > 4, h0 = body.scrollHeight;
        body.prepend(d);
        if (keep) body.scrollTop += body.scrollHeight - h0;
        jAfter = j.n;
        if (j.msg.startsWith("🔔")) {
          toast("ok", "Изменения на рынке", j.msg.replace(/^🔔\s*изменения на рынке:\s*/, ""), { label: "Показать", fn: () => setView("changes") });
          loadData();
          updateChangesBadge();
        }
        if (j.msg.startsWith("сбор завершён")) loadData();
        if (warn) { toast("warn", `${SRC[j.src]}: нужна проверка`, "Пройдите капчу в появившемся окне браузера — сбор продолжится сам."); $("#jDot").classList.remove("hidden"); }
        if (j.msg.startsWith("завершено") || j.msg.startsWith("фото: загружено") || j.msg.startsWith("объединение")) {
          loadData();
          if (view === "settings") renderDbInfo();
        }
      });
      const last = s.journal[s.journal.length - 1];
      $("#jLast").textContent = `${last.t} · ${SRC[last.src] || (last.src === "db" ? "База" : "")}: ${last.msg}`;
    }
    if (Object.values(s.status).some(v => v.running) && Date.now() - (poll.lastLoad || 0) > 15000) { poll.lastLoad = Date.now(); loadData(); }
  } catch (e) { /* сервер ещё стартует */ }
  setTimeout(poll, 2000);
}

// ---------- навигация и события ----------
function setView(v) {
  view = v;
  $$(".nav button").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach(el => el.classList.add("hidden"));
  $(`#view-${v}`).classList.remove("hidden");
  $("#viewTitle").textContent = VIEW_TITLE[v];
  const lists = v === "cards" || v === "table" || v === "map";
  if (v === "changes") markChangesSeen();
  $("#filters").classList.toggle("hidden", !lists);
  $("#btnSide").classList.toggle("hidden", v !== "cards");
  $("#btnGroup").classList.toggle("hidden", v !== "cards");
  $(".main").classList.toggle("inner-scroll", v === "cards");
  if (v === "dashboard") { renderHeader([]); renderDashboard(); }
  else if (v === "changes") { renderHeader([]); renderChanges(); }
  else if (v === "runs") { renderHeader([]); renderRuns(); }
  else if (v === "settings") { renderHeader([]); renderSettings(); }
  else render();
  $(".main").scrollTop = 0;
  $("#cardsList").scrollTop = 0;
}

function resetFilters() {
  $$("#filters input[type=checkbox]").forEach(i => i.checked = !(i.value === "C" || i.id === "fNoCoords" || i.id === "fLiked"));
  $("#fPmin").value = $("#fPmax").value = $("#fAmin").value = $("#fText").value = "";
  $("#fReno").value = $("#fNew").value = ""; $("#fState").value = "pass"; $("#fSort").value = "new";
  render();
}

function setTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  store.set("rw.theme", t);
  $$("#theme button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  if (view === "map" || sideOn()) render();
}

$$(".nav button").forEach(b => b.onclick = () => setView(b.dataset.view));
$("#filters").addEventListener("input", () => render());
$("#btnReset").onclick = resetFilters;
$("#btnRun").onclick = async () => {
  const sources = $$(".src-run:checked").map(i => i.value);
  if (!sources.length) return toast("warn", "Не выбраны сайты", "Включите хотя бы один источник");
  const r = await post("/api/run", { sources });
  if (r.started.length) toast("ok", "Сбор запущен", r.started.map(k => SRC[k]).join(", "));
  else toast("warn", "Уже идёт", "Выбранные сайты уже собираются");
};
$("#btnStop").onclick = () => { post("/api/stop", {}); toast("warn", "Останавливаю сбор", "Текущая страница будет дочитана"); };
$("#jToggle").onclick = () => { $("#journal").classList.toggle("collapsed"); $("#jDot").classList.add("hidden"); };
$("#jClose").onclick = () => $("#journal").classList.add("collapsed");
$("#btnSide").onclick = () => setSide(!sideOn());
$("#btnGroup").onclick = () => setGroup(!groupOn());
$$("#theme button").forEach(b => b.onclick = () => setTheme(b.dataset.t));
$("#cardsList").addEventListener("mouseover", e => {
  const c = e.target.closest(".card");
  if (c && c.dataset.id !== hl) highlight(c.dataset.id, false);
});
$("#cardsList").addEventListener("click", e => {
  const c = e.target.closest(".card");
  if (!c) return;
  if (e.target.closest(".heart")) { e.preventDefault(); toggleLike(c.dataset.id, e.target.closest(".heart")); return; }
  if (e.target.closest(".pin")) {
    if (!sideOn()) setSide(true);
    highlight(c.dataset.id, true);
    return;
  }
  if (e.target.closest("a, button, input, select")) return;
  openObject(c.dataset.id);  // окно объекта: история цен по каждому сайту
});

// Таблица и изменения → карточка в подборке: открыть, прокрутить и подсветить
function goToCard(id) {
  const o = OBJS.find(x => x.id === id);
  if (o && !$(`input[name=zone][value=${o.zone}]`).checked) $(`input[name=zone][value=${o.zone}]`).checked = true;
  if (o && o.excluded && $("#fState").value === "pass") $("#fState").value = "all";
  if (o && !o.active) $("#fActive").checked = false;
  setView("cards");
  setTimeout(() => {
    focusCard(id);
    highlight(id, false);
  }, 80);
}

// старт — после загрузки всех скриптов (dash.js подключается после app.js)
document.addEventListener("DOMContentLoaded", async () => {
  const on = store.get("rw.sideMap", "1") !== "0";
  $("#split").classList.toggle("with-map", on);
  $("#btnSide").classList.toggle("active", on);
  $("#btnGroup").classList.toggle("active", store.get("rw.group", "1") !== "0");
  initGutter();
  initTips();
  $$("#theme button").forEach(b => b.classList.toggle("on", b.dataset.t === store.get("rw.theme", "")));
  renderSources({});
  await loadSettings();
  try {
    for (const r of (await api("/api/runs")).reverse())
      LASTRUN[r.source] = { state: r.status, message: r.message, finished: r.finished, running: false };
  } catch (e) { /* нет прогонов */ }
  renderSources({});
  setView("dashboard");
  await loadData();
  initAuto();
  updateChangesBadge();
  poll();
});
