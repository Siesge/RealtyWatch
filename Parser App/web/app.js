"use strict";
const SRC = { cian: "ЦИАН", avito: "Авито", yandex: "Яндекс" };
const ZONE = { A: "Красный круг", B: "Остальная Ялта", C: "Посёлки Большой Ялты" };
const ZONE_SHORT = { A: "Круг", B: "Ялта", C: "Посёлок" };
const RENO = { design: "дизайнерский ремонт", euro: "евроремонт", cosmetic: "косметический ремонт", old: "старый ремонт",
  finish: "чистовая отделка", none: "без ремонта", any: "с ремонтом" };
const RENO_RANK = { design: 6, euro: 5, cosmetic: 4, finish: 3, old: 2, any: 1 };
const SELLER = { owner: "собственник", agent: "агент", developer: "застройщик" };
const STATE = { running: "идёт сбор", paused: "пауза", done: "готово", error: "ошибка", blocked: "блокировка", stopped: "остановлен" };
const VIEW_TITLE = { cards: "Подборка", table: "Таблица", map: "Карта", runs: "Прогоны", settings: "Настройки" };
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
  img: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-4.5 4 3.5 3-2.5 4 3.5"/></svg>',
};

let ALL = [], OBJS = [], SETTINGS = null, view = "cards", jAfter = 0, tableSort = null, lastStates = {};
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
  $("#kpis").innerHTML = view === "runs" || view === "settings" ? "" : `
    <div class="kpi"><b>${rows.length}</b><span>${plural(rows.length, "объект", "объекта", "объектов")}</span></div>
    <div class="kpi gold"><b>${fmt(ppm)}</b><span>медиана, ₽/м²</span></div>
    <div class="kpi"><b>${mln(pr)}</b><span>медиана, млн ₽</span></div>
    <div class="kpi"><b>${multi}</b><span>на 2+ сайтах</span></div>`;
  const by = ALL.reduce((m, r) => (m[r.source] = (m[r.source] || 0) + 1, m), {});
  $("#viewSub").textContent = view === "runs" ? "История запусков парсеров" : view === "settings" ? "Параметры поиска, геозона, сбор данных и база"
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
function delta(o) {
  const h = o.p.history || [];
  if (h.length < 2) return "";
  const d = h[h.length - 1][1] - h[0][1];
  return d ? `<span class="delta ${d < 0 ? "down" : "up"}">${d < 0 ? "↓" : "↑"} ${mln(Math.abs(d))} млн</span>` : "";
}

function tags(o) {
  const t = [], r = o.p;
  if (o.renovation) t.push(`<span class="tag ${o.renovation === "none" ? "bad" : "good"}" title="${esc(r.renovation_raw || "")}">${RENO[o.renovation] || o.renovation}</span>`);
  else t.push(`<span class="tag warn">ремонт не указан</span>`);
  if (r.is_new_building) t.push(`<span class="tag">новостройка</span>`);
  if (o.items.some(i => i.raw && i.raw.apartments)) t.push(`<span class="tag warn">апартаменты</span>`);
  const sellers = [...new Set(o.items.map(i => i.seller_type).filter(Boolean))];
  sellers.forEach(s => t.push(`<span class="tag">${SELLER[s] || s}</span>`));
  if (o.lat == null) t.push(`<span class="tag warn">нет координат</span>`);
  for (const [k, lbl] of [["encumbrance", "обременение?"], ["share", "доля?"], ["auction", "торги?"]])
    if (o.flags[k]) t.push(`<span class="tag warn" title="${esc(o.flags[k].join(" … "))}">${lbl}</span>`);
  if (o.excluded) t.push(`<span class="tag bad" title="причина отсева">✕ ${esc(o.excluded)}</span>`);
  if (!o.active) t.push(`<span class="tag bad">снято с продажи</span>`);
  return t.join("");
}

function offers(o) {
  const best = o.items.length > 1 && o.price !== o.priceMax ? o.price : null;
  const list = [...o.items].sort((a, b) => (b.active - a.active) || ((a.price || 0) - (b.price || 0)));
  return `<div class="offers">${list.map(i => `<a class="offer ${i.active ? "" : "gone"}" href="${esc(i.url)}" target="_blank" rel="noopener" title="${esc(i.title)}">
    <i class="sdot ${i.source}"></i><span>${SRC[i.source]}${i.excluded && !o.excluded ? ` <span class="tag warn" title="${esc(i.excluded)}">!</span>` : ""}</span>
    <span>${best && i.price === best && i.active ? '<span class="best">дешевле</span> ' : ""}<b>${fmt(i.price)} ₽</b></span>${ICON.ext}</a>`).join("")}</div>`;
}

function media(o) {
  if (!o.img) return `<div class="media noimg">`;
  return `<div class="media"><img src="/img/${encodeURIComponent(o.img.source)}/${encodeURIComponent(o.img.source_id)}" loading="lazy" alt=""
    onerror="this.parentNode.classList.add('noimg');this.remove()">`;
}

function card(o) {
  const meta = [o.p.living_area && `жилая ${o.p.living_area} м²`, o.p.kitchen_area && `кухня ${o.p.kitchen_area} м²`, o.p.build_year && `${o.p.build_year} г.`].filter(Boolean).join(" · ");
  const rooms = o.rooms === 0 ? "студия" : o.rooms ?? "—";
  const n = o.items.length;
  return `<article class="card ${o.excluded ? "excl" : ""} ${o.active ? "" : "inactive"}" data-id="${esc(o.id)}">
    ${media(o)}
      <div class="media-top"><span class="left"><span class="pill zone-${o.zone}">${ZONE_SHORT[o.zone] || o.zone}</span>
        ${o.sources.length > 1 ? `<span class="pill multi">${o.sources.length} ${plural(o.sources.length, "сайт", "сайта", "сайтов")}</span>` : ""}</span>
        <button class="heart ${o.liked ? "on" : ""}" title="${o.liked ? "Убрать из избранного" : "В избранное"}">${ICON.heart}</button></div>
      <div class="media-bottom"><div><div class="price">${fmt(o.price)} ₽</div><div class="ppm">${fmt(o.price_m2)} ₽/м²${o.priceMax > o.price ? ` · до ${mln(o.priceMax)} млн` : ""}</div></div>${delta(o)}</div>
    </div>
    <div class="body">
      <div class="facts">
        <div class="fact"><b>${rooms}</b><span>${o.rooms === 0 ? "" : "комнат"}</span></div>
        <div class="fact"><b>${o.area ?? "—"}</b><span>м² общая</span></div>
        <div class="fact"><b>${o.floor ?? "?"}<small>/${o.floors ?? "?"}</small></b><span>этаж</span></div>
      </div>
      <div class="addr">${ICON.pin}<span>${esc(o.address || "адрес не указан")}</span></div>
      ${meta ? `<div class="meta">${esc(meta)}</div>` : ""}
      <div class="tags">${tags(o)}</div>
      ${offers(o)}
      <p class="desc">${esc((o.p.description || "").slice(0, 320))}</p>
    </div>
    <div class="card-foot"><span>с ${dt(o.first_seen)} · ${n} ${plural(n, "объявление", "объявления", "объявлений")}</span>
      <span class="acts">${o.lat != null ? `<button class="icon-btn pin" title="Показать на карте">${ICON.map}</button>` : ""}</span></div>
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
  el.innerHTML = Object.keys(ZONE).map(z => {
    const zr = rows.filter(o => o.zone === z);
    if (!zr.length) return "";
    const ppm = median(zr.map(o => o.price_m2).filter(Boolean)), pr = median(zr.map(o => o.price).filter(Boolean));
    return `<section class="zone"><div class="zone-head"><div class="zone-mark ${z}">${z}</div>
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
    <tbody>${rows.slice(0, 1500).map(o => `<tr class="${o.excluded ? "excl" : ""}">${COLS.map(c => `<td class="${c[3] ? "num" : ""}">${c[2](o)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
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
  return { map: m, layer: L.layerGroup().addTo(m), zone: null, markers: {} };
}

function markerStyle(o, on) {
  const multi = o.sources.length > 1;
  return { radius: on ? 11 : multi ? 8 : 6.5, color: on ? "#fff" : multi ? "#D8B474" : "rgba(255,255,255,.9)", weight: on ? 3 : multi ? 2.5 : 1.5,
    fillColor: o.liked ? "#E0475B" : COLOR[o.p.source], fillOpacity: o.excluded ? .35 : .95 };
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
  const modeSel = k => `<select data-site="${k}">${[["auto", "авто"], ["http", "только HTTP"], ["browser", "только браузер"]]
    .map(([v, t]) => `<option value="${v}" ${s[k].mode === v ? "selected" : ""}>${t}</option>`).join("")}</select>`;
  $("#view-settings").innerHTML = `<div class="settings">
    <div class="s-card"><h3>Параметры поиска</h3><p class="hint">Передаются сайтам при сборе и используются для отсева.</p>
      <div class="field"><label>Комнаты</label><input type="text" id="sRooms" value="${f.rooms.join(", ")}"></div>
      <div class="field"><label>Площадь от, м²</label><input type="number" id="sArea" value="${f.area_min}"></div>
      <div class="field"><label>Цена от, ₽</label><input type="number" id="sPmin" value="${f.price_min}"></div>
      <div class="field"><label>Цена до, ₽</label><input type="number" id="sPmax" value="${f.price_max}"></div>
      <div class="field"><label>Ремонт</label><select id="sReno">
        <option value="exclude_none" ${f.renovation === "exclude_none" ? "selected" : ""}>без ремонта — отсеять</option>
        <option value="strict" ${f.renovation === "strict" ? "selected" : ""}>только указанный ремонт</option>
        <option value="off" ${f.renovation === "off" ? "selected" : ""}>не учитывать</option></select></div>
      <div class="field"><label>Исключать обременение</label>${sw("sEnc", f.exclude_encumbrance)}</div>
      <div class="field"><label>Исключать доли</label>${sw("sShare", f.exclude_share)}</div>
      <div class="field"><label>Исключать аукционы и торги</label>${sw("sAuc", f.exclude_auction)}</div>
    </div>
    <div class="s-card"><h3>Геозона «Красный круг»</h3><p class="hint">Калибровка по GeoZone_v1.jpg: Ливадийский дворец, Белая дача, «Атлантида» (точность ~50–70 м).</p>
      <div class="field"><label>Центр — широта</label><input type="number" step="0.000001" id="zLat" value="${z.center[0]}"></div>
      <div class="field"><label>Центр — долгота</label><input type="number" step="0.000001" id="zLon" value="${z.center[1]}"></div>
      <div class="field"><label>Радиус, м</label><input type="number" id="zR" value="${z.radius_m}"></div>
    </div>
    <div class="s-card"><h3>Сбор данных</h3><p class="hint">В режиме «авто» сначала быстрый HTTP, при блокировке — скрытый браузер. Если сайт покажет капчу, окно браузера появится само.</p>
      <div class="field"><label>ЦИАН</label>${modeSel("cian")}</div>
      <div class="field"><label>Авито</label>${modeSel("avito")}</div>
      <div class="field"><label>Яндекс</label>${modeSel("yandex")}</div>
      <div class="field"><label>Авито: страниц объявлений за прогон</label><input type="number" id="sAvDet" value="${s.avito.max_details}"></div>
      <div class="field"><label>Авито: пауза между запросами, с<br><span class="meta">растёт сама при ограничении IP</span></label>
        <span class="pair"><input type="number" step="1" min="1" id="sAvD0" value="${s.avito.delay[0]}"><i>—</i><input type="number" step="1" min="1" id="sAvD1" value="${s.avito.delay[1]}"></span></div>
      <div class="field"><label>Окно браузера</label><select id="sWin">
        ${[["hidden", "скрытое"], ["visible", "видимое"], ["headless", "без окна (headless)"]]
          .map(([v, t]) => `<option value="${v}" ${(b.window || "hidden") === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    </div>
    <div class="s-card" id="dbCard"><h3>База данных</h3><p class="hint">Объявления, фото, избранное и настройки поиска. Перенос на другое устройство — экспорт в .zip и импорт там.</p>
      <div class="db-stats" id="dbStats"></div>
      <div class="db-path" id="dbPath"></div>
      <div class="btn-row">
        <button class="btn-sec" id="btnExport">${ICON.down}Экспорт…</button>
        <button class="btn-sec" id="btnImport">${ICON.up}Импорт…</button>
        <select id="impMode" title="Как импортировать"><option value="merge">объединить с текущей</option><option value="replace">заменить полностью</option></select>
      </div>
      <div class="btn-row" style="margin-top:8px">
        <button class="btn-sec" id="btnImages">${ICON.img}Догрузить фото</button>
        ${desktop() ? `<button class="btn-sec" id="btnFolder">${ICON.folder}Открыть папку</button>` : ""}
      </div>
    </div>
    <div class="s-actions"><button class="btn-primary" id="btnSave">Сохранить и пересчитать</button><span class="meta">Зоны, отсев и объединение пересчитаются сразу, параметры поиска — при следующем сборе.</span></div>
  </div>`;
  $("#btnSave").onclick = saveSettings;
  $("#btnExport").onclick = doExport;
  $("#btnImport").onclick = doImport;
  $("#btnImages").onclick = async () => { await post("/api/images/repair"); toast("ok", "Догружаю фото", "Прогресс — в журнале"); };
  if ($("#btnFolder")) $("#btnFolder").onclick = () => desktop().open_db_folder();
  renderDbInfo();
}

async function renderDbInfo() {
  const d = await api("/api/db/info");
  if (!$("#dbStats")) return;
  $("#dbStats").innerHTML = `<div><b>${d.objects}</b><span>объектов</span></div><div><b>${d.listings}</b><span>объявлений</span></div>
    <div><b>${d.images}</b><span>фото</span></div><div><b>${d.likes}</b><span>в избранном</span></div>
    <div><b>${d.passing}</b><span>прошли отсев</span></div><div><b>${d.size_mb} МБ</b><span>размер</span></div>`;
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
function toast(kind, title, text) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<div class="ic">${kind === "warn" ? ICON.warn : ICON.ok}</div><div><b>${esc(title)}</b><p>${esc(text || "")}</p></div>`;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "warn" ? 15000 : 6000);
}

// ---------- статус сбора и журнал ----------
function renderSources(status) {
  $("#sources").innerHTML = Object.keys(SRC).map(k => {
    const v = status[k], on = $(`.src-run[value=${k}]`)?.checked ?? true;
    const paused = v && v.running && v.paused_until;
    const state = v ? (paused ? "paused" : v.running ? "running" : v.state) : "";
    const pct = v && v.pages ? Math.round(100 * v.page / v.pages) : 0;
    const meta = !v ? "ещё не запускался"
      : paused ? `⏸ ограничение IP — пауза до ${new Date(v.paused_until).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}, задержка ${v.delay ? v.delay.join("–") + " с" : "увеличена"}`
      : v.running ? `стр. ${v.page || 0} из ${v.pages || "?"} · найдено ${v.found}${v.details ? ` · карточек ${v.details}` : ""}`
      : v.message || "";
    return `<label class="src-row ${state}"><input type="checkbox" class="src-run" value="${k}" ${on ? "checked" : ""}><span class="switch"></span>
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
        body.appendChild(d);
        jAfter = j.n;
        if (warn) { toast("warn", `${SRC[j.src]}: нужна проверка`, "Пройдите капчу в появившемся окне браузера — сбор продолжится сам."); $("#jDot").classList.remove("hidden"); }
        if (j.msg.startsWith("завершено") || j.msg.startsWith("фото: загружено") || j.msg.startsWith("объединение")) {
          loadData();
          if (view === "settings") renderDbInfo();
        }
      });
      body.scrollTop = body.scrollHeight;
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
  $("#filters").classList.toggle("hidden", !lists);
  $("#btnSide").classList.toggle("hidden", v !== "cards");
  if (v === "runs") { renderHeader([]); renderRuns(); }
  else if (v === "settings") { renderHeader([]); renderSettings(); }
  else render();
  $(".main").scrollTop = 0;
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
  }
});

(async () => {
  const on = store.get("rw.sideMap", "1") !== "0";
  $("#split").classList.toggle("with-map", on);
  $("#btnSide").classList.toggle("active", on);
  $$("#theme button").forEach(b => b.classList.toggle("on", b.dataset.t === store.get("rw.theme", "")));
  renderSources({});
  await loadSettings();
  await loadData();
  poll();
})();
