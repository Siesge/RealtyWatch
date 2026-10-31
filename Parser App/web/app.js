"use strict";
const SRC = { cian: "ЦИАН", avito: "Авито", yandex: "Яндекс" };
const ZONE = { A: "Красный круг", B: "Остальная Ялта", C: "Посёлки Большой Ялты" };
const ZONE_SHORT = { A: "Круг", B: "Ялта", C: "Посёлок" };
const RENO = { design: "дизайнерский ремонт", euro: "евроремонт", cosmetic: "косметический ремонт", old: "старый ремонт",
  finish: "чистовая отделка", none: "без ремонта", any: "с ремонтом" };
const SELLER = { owner: "собственник", agent: "агент", developer: "застройщик" };
const STATE = { running: "идёт сбор", done: "готово", error: "ошибка", blocked: "блокировка", stopped: "остановлен" };
const VIEW_TITLE = { cards: "Подборка", table: "Таблица", map: "Карта", runs: "Прогоны", settings: "Настройки" };
const ICON = {
  pin: '<svg viewBox="0 0 24 24"><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/></svg>',
  ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  map: '<svg viewBox="0 0 24 24"><circle cx="12" cy="10" r="2.5"/><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2v.1"/></svg>',
  ok: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
};

let ALL = [], SETTINGS = null, view = "cards", jAfter = 0, tableSort = null, lastStates = {};
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = n => n == null ? "—" : Math.round(n).toLocaleString("ru-RU");
const mln = n => n == null ? "—" : (n / 1e6).toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const api = async (url, opts) => (await fetch(url, opts)).json();
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const dt = s => s ? new Date(s).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) : "";
const today = () => new Date().toISOString().slice(0, 10);
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* хранилище недоступно */ } },
};

// ---------- данные ----------
async function loadData() { ALL = await api("/api/listings"); render(); }
async function loadSettings() { SETTINGS = await api("/api/settings"); }

// ---------- фильтр просмотра ----------
const checked = name => $$(`input[name=${name}]:checked`).map(i => i.value);

function filtered(ignoreZone = false) {
  const src = checked("src"), zones = checked("zone"), rooms = checked("rooms").map(Number);
  const pmin = parseFloat($("#fPmin").value) * 1e6, pmax = parseFloat($("#fPmax").value) * 1e6;
  const amin = parseFloat($("#fAmin").value), reno = $("#fReno").value, nb = $("#fNew").value;
  const st = $("#fState").value, active = $("#fActive").checked, noCoords = $("#fNoCoords").checked;
  const text = $("#fText").value.trim().toLowerCase();
  let rows = ALL.filter(r =>
    src.includes(r.source) && (ignoreZone || zones.includes(r.zone)) &&
    (r.rooms == null || rooms.includes(r.rooms)) &&
    !(pmin && r.price < pmin) && !(pmax && r.price > pmax) && !(amin && r.area < amin) &&
    (!reno || (reno === "known" ? r.renovation && r.renovation !== "any" : r.renovation === reno)) &&
    (nb === "" || String(r.is_new_building ?? 0) === nb) &&
    (st === "all" || (st === "pass" ? !r.excluded : !!r.excluded)) &&
    (!active || r.active) && (!noCoords || r.lat == null) &&
    (!text || `${r.title} ${r.address} ${r.description}`.toLowerCase().includes(text)));
  if (ignoreZone) return rows;
  const s = $("#fSort").value;
  const key = { new: r => r.first_seen, price: r => r.price, "-price": r => r.price, ppm: r => r.price_m2, "-area": r => r.area }[s];
  const dir = s === "new" || s.startsWith("-") ? -1 : 1;
  rows.sort((a, b) => ((key(a) ?? 0) > (key(b) ?? 0) ? 1 : (key(a) ?? 0) < (key(b) ?? 0) ? -1 : 0) * dir);
  return rows;
}

const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many; };

function renderHeader(rows) {
  const zr = filtered(true);
  for (const z of ["A", "B", "C"]) $(`#zc-${z}`).textContent = zr.filter(r => r.zone === z).length;
  const ppm = median(rows.map(r => r.price_m2).filter(Boolean));
  const pr = median(rows.map(r => r.price).filter(Boolean));
  const fresh = rows.filter(r => (r.first_seen || "").startsWith(today())).length;
  $("#kpis").innerHTML = view === "runs" || view === "settings" ? "" : `
    <div class="kpi"><b>${rows.length}</b><span>${plural(rows.length, "объект", "объекта", "объектов")}</span></div>
    <div class="kpi gold"><b>${fmt(ppm)}</b><span>медиана, ₽/м²</span></div>
    <div class="kpi"><b>${mln(pr)}</b><span>медиана, млн ₽</span></div>
    <div class="kpi"><b>${fresh}</b><span>новых сегодня</span></div>`;
  const by = rows.reduce((m, r) => (m[r.source] = (m[r.source] || 0) + 1, m), {});
  $("#viewSub").textContent = view === "runs" ? "История запусков парсеров" : view === "settings" ? "Параметры поиска, геозона и сбор данных"
    : `Всего в базе ${ALL.length} · ` + Object.keys(SRC).map(k => `${SRC[k]} ${by[k] || 0}`).join(" · ");
}

function render() {
  const rows = filtered();
  renderHeader(rows);
  if (view === "cards") renderCards(rows);
  if (view === "table") renderTable(rows);
  if (view === "map") renderMap(rows);
}

// ---------- карточки ----------
function delta(r) {
  const h = r.history || [];
  if (h.length < 2) return "";
  const d = h[h.length - 1][1] - h[0][1];
  return d ? `<span class="delta ${d < 0 ? "down" : "up"}">${d < 0 ? "↓" : "↑"} ${mln(Math.abs(d))} млн</span>` : "";
}

function tags(r) {
  const t = [];
  if (r.renovation) t.push(`<span class="tag ${r.renovation === "none" ? "bad" : "good"}" title="${esc(r.renovation_raw || "")}">${RENO[r.renovation] || r.renovation}</span>`);
  else t.push(`<span class="tag warn">ремонт не указан</span>`);
  if (r.is_new_building) t.push(`<span class="tag">новостройка</span>`);
  if (r.raw && r.raw.apartments) t.push(`<span class="tag warn">апартаменты</span>`);
  if (r.seller_type) t.push(`<span class="tag">${SELLER[r.seller_type] || r.seller_type}</span>`);
  if (r.lat == null) t.push(`<span class="tag warn">нет координат</span>`);
  for (const [k, lbl] of [["encumbrance", "обременение?"], ["share", "доля?"], ["auction", "торги?"]])
    if (r.flags && r.flags[k]) t.push(`<span class="tag warn" title="${esc(r.flags[k].join(" … "))}">${lbl}</span>`);
  if (r.excluded) t.push(`<span class="tag bad" title="причина отсева">✕ ${esc(r.excluded)}</span>`);
  if (!r.active) t.push(`<span class="tag bad">снято с продажи</span>`);
  return t.join("");
}

function card(r) {
  const meta = [r.living_area && `жилая ${r.living_area} м²`, r.kitchen_area && `кухня ${r.kitchen_area} м²`, r.build_year && `${r.build_year} г.`].filter(Boolean).join(" · ");
  const rooms = r.rooms === 0 ? "студия" : r.rooms ?? "—";
  return `<article class="card ${r.excluded ? "excl" : ""} ${r.active ? "" : "inactive"}" data-id="${esc(rid(r))}">
    <div class="media" style="background-image:url('${esc(r.photo || "")}')">
      <div class="media-top"><span class="pill"><i class="sdot ${r.source}"></i>${SRC[r.source]}</span><span class="pill zone-${r.zone}">${ZONE_SHORT[r.zone] || r.zone}</span></div>
      <div class="media-bottom"><div><div class="price">${fmt(r.price)} ₽</div><div class="ppm">${fmt(r.price_m2)} ₽/м²</div></div>${delta(r)}</div>
    </div>
    <div class="body">
      <div class="facts">
        <div class="fact"><b>${rooms}</b><span>${r.rooms === 0 ? "" : "комнат"}</span></div>
        <div class="fact"><b>${r.area ?? "—"}</b><span>м² общая</span></div>
        <div class="fact"><b>${r.floor ?? "?"}<small>/${r.floors ?? "?"}</small></b><span>этаж</span></div>
      </div>
      <div class="addr">${ICON.pin}<span>${esc(r.address || "адрес не указан")}</span></div>
      ${meta ? `<div class="meta">${esc(meta)}</div>` : ""}
      <div class="tags">${tags(r)}</div>
      <p class="desc">${esc((r.description || "").slice(0, 320))}</p>
    </div>
    <div class="card-foot"><span>с ${dt(r.first_seen)} · ${r.fetch_mode === "browser" ? "браузер" : "HTTP"}</span>
      <span class="acts">${r.lat != null ? `<button class="icon-btn pin" title="Показать на карте">${ICON.map}</button>` : ""}
      <a class="open-btn" href="${esc(r.url)}" target="_blank" rel="noopener">Открыть ${ICON.ext}</a></span></div>
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
    el.innerHTML = `<div class="empty"><b>${ALL.length ? "Ничего не найдено" : "База пока пуста"}</b>${ALL.length ? "Ослабьте фильтры или включите другие зоны" : "Нажмите «Запустить» слева, чтобы собрать объявления"}</div>`;
    return;
  }
  const LIMIT = 240;
  el.innerHTML = Object.keys(ZONE).map(z => {
    const zr = rows.filter(r => r.zone === z);
    if (!zr.length) return "";
    const ppm = median(zr.map(r => r.price_m2).filter(Boolean)), pr = median(zr.map(r => r.price).filter(Boolean));
    return `<section class="zone"><div class="zone-head"><div class="zone-mark ${z}">${z}</div>
      <div><h2>${ZONE[z]}</h2><p>${zr.length} ${plural(zr.length, "объект", "объекта", "объектов")} · медиана ${fmt(ppm)} ₽/м² · ${mln(pr)} млн ₽${zr.length > LIMIT ? ` · показаны первые ${LIMIT}` : ""}</p></div>
      <div class="rule"></div></div>
      <div class="cards">${zr.slice(0, LIMIT).map(card).join("")}</div></section>`;
  }).join("");
}

// ---------- таблица ----------
const COLS = [
  ["source", "Сайт", r => `<i class="sdot ${r.source}"></i> ${SRC[r.source]}`], ["zone", "Зона", r => `<i class="zdot ${r.zone}"></i> ${ZONE_SHORT[r.zone]}`],
  ["price", "Цена", r => fmt(r.price), 1], ["price_m2", "₽/м²", r => fmt(r.price_m2), 1], ["rooms", "Комн.", r => r.rooms ?? "", 1],
  ["area", "м²", r => r.area ?? "", 1], ["floor", "Этаж", r => `${r.floor ?? "?"}/${r.floors ?? "?"}`, 1],
  ["renovation", "Ремонт", r => RENO[r.renovation] || ""], ["address", "Адрес", r => esc(r.address || "")],
  ["excluded", "Отсев", r => esc(r.excluded || "")], ["first_seen", "Найдено", r => dt(r.first_seen)],
  ["url", "", r => `<a href="${esc(r.url)}" target="_blank">Открыть</a>`],
];

function renderTable(rows) {
  if (tableSort) {
    const [k, d] = tableSort;
    rows = [...rows].sort((a, b) => ((a[k] ?? "") > (b[k] ?? "") ? 1 : (a[k] ?? "") < (b[k] ?? "") ? -1 : 0) * d);
  }
  $("#view-table").innerHTML = `<div class="panel"><table class="grid"><thead><tr>${COLS.map(c => `<th data-k="${c[0]}">${c[1]}${tableSort && tableSort[0] === c[0] ? (tableSort[1] > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(0, 1500).map(r => `<tr class="${r.excluded ? "excl" : ""}">${COLS.map(c => `<td class="${c[3] ? "num" : ""}">${c[2](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  $$("#view-table th").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    tableSort = tableSort && tableSort[0] === k ? [k, -tableSort[1]] : [k, 1];
    render();
  });
}

// ---------- карты ----------
const COLOR = { cian: "#3E7BFA", avito: "#1FAF6A", yandex: "#F2552F" };
const rid = r => `${r.source}:${r.source_id}`;
let MAIN = null, SIDE = null, hl = null;

function makeMap(el) {
  const m = L.map(el, { zoomControl: true, attributionControl: true }).setView([44.49, 34.155], 13);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(m);
  return { map: m, layer: L.layerGroup().addTo(m), zone: null, markers: {} };
}

function markerStyle(r, on) {
  return { radius: on ? 11 : 6.5, color: on ? "#fff" : "rgba(255,255,255,.9)", weight: on ? 3 : 1.5, fillColor: COLOR[r.source], fillOpacity: r.excluded ? .35 : .95 };
}

function drawMap(M, rows) {
  setTimeout(() => M.map.invalidateSize(), 60);
  if (M.zone) M.zone.remove();
  const z = SETTINGS.zone, gold = getComputedStyle(document.documentElement).getPropertyValue("--gold").trim() || "#B4873B";
  const style = { color: gold, weight: 2.5, dashArray: "7 6", fillColor: gold, fillOpacity: .10 };
  M.zone = (z.polygon ? L.polygon(z.polygon, style) : L.circle(z.center, { radius: z.radius_m, ...style })).addTo(M.map);
  M.layer.clearLayers();
  M.markers = {};
  rows.filter(r => r.lat != null).forEach(r => {
    const mk = L.circleMarker([r.lat, r.lon], markerStyle(r, false))
      .bindPopup(`<b>${fmt(r.price)} ₽</b><br>${esc(r.title)}<br><span style="opacity:.7">${esc(r.address || "")}</span><br>${SRC[r.source]} · ${ZONE[r.zone]}<br><a href="${esc(r.url)}" target="_blank">Открыть объявление →</a>`)
      .addTo(M.layer);
    mk.row = r;
    mk.on("click", () => focusCard(rid(r)));
    M.markers[rid(r)] = mk;
  });
}

function renderMap(rows) { if (!MAIN) MAIN = makeMap("map"); drawMap(MAIN, rows); }

function highlight(id, fly) {
  if (!SIDE || !sideOn()) return;
  if (hl && SIDE.markers[hl]) SIDE.markers[hl].setStyle(markerStyle(SIDE.markers[hl].row, false));
  hl = id;
  const mk = SIDE.markers[id];
  if (!mk) return;
  mk.setStyle(markerStyle(mk.row, true)).bringToFront();
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

// ---------- настройки ----------
function renderSettings() {
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
      <div class="field"><label>Окно браузера</label><select id="sWin">
        ${[["hidden", "скрытое"], ["visible", "видимое"], ["headless", "без окна (headless)"]]
          .map(([v, t]) => `<option value="${v}" ${(b.window || "hidden") === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    </div>
    <div class="s-actions"><button class="btn-primary" id="btnSave">Сохранить и пересчитать</button><span class="meta">Зоны и отсев пересчитаются сразу, параметры поиска — при следующем сборе.</span></div>
  </div>`;
  $("#btnSave").onclick = saveSettings;
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
  SETTINGS.browser = Object.assign(SETTINGS.browser || {}, { window: $("#sWin").value });
  SETTINGS = await post("/api/settings", SETTINGS);
  const r = await post("/api/reclassify");
  await loadData();
  toast("ok", "Настройки сохранены", `Пересчитано объявлений: ${r.updated}`);
}

// ---------- уведомления ----------
function toast(kind, title, text) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<div class="ic">${kind === "warn" ? ICON.warn : ICON.ok}</div><div><b>${esc(title)}</b><p>${esc(text || "")}</p></div>`;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), kind === "warn" ? 15000 : 5000);
}

// ---------- статус сбора и журнал ----------
function renderSources(status) {
  $("#sources").innerHTML = Object.keys(SRC).map(k => {
    const v = status[k], on = $(`.src-run[value=${k}]`)?.checked ?? true;
    const state = v ? (v.running ? "running" : v.state) : "";
    const pct = v && v.pages ? Math.round(100 * v.page / v.pages) : 0;
    const meta = !v ? "ещё не запускался"
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
        const warn = j.msg.includes("⚠");
        d.className = "jl" + (warn ? " warn" : "");
        d.innerHTML = `<span class="t">${j.t}</span><span class="s">${SRC[j.src] || j.src}</span>${esc(j.msg)}`;
        body.appendChild(d);
        jAfter = j.n;
        if (warn) { toast("warn", `${SRC[j.src]}: нужна проверка`, "Пройдите капчу в появившемся окне браузера — сбор продолжится сам."); $("#jDot").classList.remove("hidden"); }
        if (j.msg.startsWith("завершено")) loadData();
      });
      body.scrollTop = body.scrollHeight;
      const last = s.journal[s.journal.length - 1];
      $("#jLast").textContent = `${last.t} · ${SRC[last.src] || ""}: ${last.msg}`;
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
  $$("#filters input[type=checkbox]").forEach(i => i.checked = !(i.value === "C" || i.id === "fNoCoords"));
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
  if (!e.target.closest(".pin")) return;
  const id = e.target.closest(".card").dataset.id;
  if (!sideOn()) setSide(true);
  highlight(id, true);
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
