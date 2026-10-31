"use strict";
const SRC = { cian: "ЦИАН", avito: "Авито", yandex: "Яндекс" };
const ZONE = { A: "Зона A — красный круг", B: "Зона B — остальная Ялта", C: "Зона C — посёлки Большой Ялты" };
const RENO = { design: "дизайнерский", euro: "евроремонт", cosmetic: "косметический", old: "старый ремонт",
  finish: "чистовая", none: "без ремонта", any: "с ремонтом (фильтр сайта)" };
const SELLER = { owner: "собственник", agent: "агент", developer: "застройщик" };
const STATE = { running: "идёт", done: "готово", error: "ошибка", blocked: "блокировка", stopped: "остановлен" };

let ALL = [], SETTINGS = null, view = "cards", jAfter = 0, map = null, mapLayer = null, zoneLayer = null, tableSort = null;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = n => n == null ? "—" : Math.round(n).toLocaleString("ru-RU");
const mln = n => n == null ? "—" : (n / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 2 }) + " млн";
const api = async (url, opts) => (await fetch(url, opts)).json();
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const dt = s => s ? new Date(s).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "2-digit" }) : "";

// ---------- загрузка ----------
async function loadData() {
  ALL = await api("/api/listings");
  render();
}

async function loadSettings() {
  SETTINGS = await api("/api/settings");
}

// ---------- фильтр просмотра ----------
function checked(name) { return $$(`input[name=${name}]:checked`).map(i => i.value); }

function filtered() {
  const src = checked("src"), zones = checked("zone"), rooms = checked("rooms").map(Number);
  const pmin = parseFloat($("#fPmin").value) * 1e6, pmax = parseFloat($("#fPmax").value) * 1e6;
  const amin = parseFloat($("#fAmin").value), reno = $("#fReno").value, nb = $("#fNew").value;
  const st = $("#fState").value, active = $("#fActive").checked, noCoords = $("#fNoCoords").checked;
  const text = $("#fText").value.trim().toLowerCase();
  let rows = ALL.filter(r =>
    src.includes(r.source) && zones.includes(r.zone) &&
    (r.rooms == null || rooms.includes(r.rooms)) &&
    !(pmin && r.price < pmin) && !(pmax && r.price > pmax) && !(amin && r.area < amin) &&
    (!reno || (reno === "known" ? r.renovation && r.renovation !== "any" : r.renovation === reno)) &&
    (nb === "" || String(r.is_new_building ?? 0) === nb) &&
    (st === "all" || (st === "pass" ? !r.excluded : !!r.excluded)) &&
    (!active || r.active) && (!noCoords || r.lat == null) &&
    (!text || `${r.title} ${r.address} ${r.description}`.toLowerCase().includes(text)));
  const s = $("#fSort").value;
  const key = { new: r => r.first_seen, price: r => r.price, "-price": r => r.price, ppm: r => r.price_m2, "-area": r => r.area }[s];
  const dir = s === "new" || s.startsWith("-") ? -1 : 1;
  rows.sort((a, b) => ((key(a) ?? 0) > (key(b) ?? 0) ? 1 : (key(a) ?? 0) < (key(b) ?? 0) ? -1 : 0) * dir);
  return rows;
}

function median(a) { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }

function renderSummary(rows) {
  const by = k => rows.reduce((m, r) => (m[r[k]] = (m[r[k]] || 0) + 1, m), {});
  const bs = by("source"), bz = by("zone");
  const ppm = median(rows.map(r => r.price_m2).filter(Boolean));
  $("#summary").innerHTML =
    `<span class="chip">Показано <b>${rows.length}</b> из ${ALL.length}</span>` +
    Object.keys(SRC).map(k => `<span class="chip">${SRC[k]}: <b>${bs[k] || 0}</b></span>`).join("") +
    Object.keys(ZONE).map(k => `<span class="chip">Зона ${k}: <b>${bz[k] || 0}</b></span>`).join("") +
    `<span class="chip">Медиана за м²: <b>${fmt(ppm)} ₽</b></span>`;
}

function render() {
  const rows = filtered();
  renderSummary(rows);
  if (view === "cards") renderCards(rows);
  if (view === "table") renderTable(rows);
  if (view === "map") renderMap(rows);
}

// ---------- карточки ----------
function priceDelta(r) {
  const h = r.history || [];
  if (h.length < 2) return "";
  const d = h[h.length - 1][1] - h[0][1];
  if (!d) return "";
  return `<span class="${d < 0 ? "delta-down" : "delta-up"}">${d < 0 ? "▼" : "▲"} ${fmt(Math.abs(d))} ₽</span>`;
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
  if (!r.active) t.push(`<span class="tag bad">снято</span>`);
  return t.join("");
}

function card(r) {
  const meta = [r.living_area && `жилая ${r.living_area}`, r.kitchen_area && `кухня ${r.kitchen_area}`, r.build_year && `${r.build_year} г.`]
    .filter(Boolean).join(" · ");
  return `<div class="card ${r.excluded ? "excl" : ""} ${r.active ? "" : "inactive"}">
    <div class="ph" style="background-image:url('${esc(r.photo || "")}')">
      <span class="src ${r.source}">${SRC[r.source]}</span><span class="zn ${r.zone}">${r.zone}</span>
    </div>
    <div class="cb">
      <div class="price">${fmt(r.price)} ₽<small>${fmt(r.price_m2)} ₽/м²</small> ${priceDelta(r)}</div>
      <div class="ttl">${esc(r.title)}</div>
      <div class="addr">${esc(r.address || "")}</div>
      ${meta ? `<div class="addr">${esc(meta)}</div>` : ""}
      <div class="tags">${tags(r)}</div>
      <div class="desc">${esc((r.description || "").slice(0, 300))}</div>
    </div>
    <div class="cf"><span>впервые ${dt(r.first_seen)} · ${r.fetch_mode || ""}</span><a href="${esc(r.url)}" target="_blank" rel="noopener">Открыть ↗</a></div>
  </div>`;
}

function renderCards(rows) {
  const el = $("#view-cards");
  if (!rows.length) { el.innerHTML = `<div class="empty">Нет объявлений по текущему фильтру. Запустите парсеры или ослабьте фильтр.</div>`; return; }
  const LIMIT = 240;
  el.innerHTML = Object.keys(ZONE).map(z => {
    const zr = rows.filter(r => r.zone === z);
    if (!zr.length) return "";
    const ppm = median(zr.map(r => r.price_m2).filter(Boolean));
    return `<div class="zone-h"><span class="dot" style="background:var(--zone${z})"></span>${ZONE[z]}
      <small>${zr.length} шт. · медиана ${fmt(ppm)} ₽/м²${zr.length > LIMIT ? ` · показаны первые ${LIMIT}` : ""}</small></div>
      <div class="cards">${zr.slice(0, LIMIT).map(card).join("")}</div>`;
  }).join("");
}

// ---------- таблица ----------
const COLS = [
  ["source", "Сайт", r => SRC[r.source]], ["zone", "Зона", r => r.zone], ["price", "Цена", r => fmt(r.price), 1],
  ["price_m2", "₽/м²", r => fmt(r.price_m2), 1], ["rooms", "Комн.", r => r.rooms ?? "", 1], ["area", "м²", r => r.area ?? "", 1],
  ["floor", "Этаж", r => `${r.floor ?? "?"}/${r.floors ?? "?"}`, 1], ["renovation", "Ремонт", r => RENO[r.renovation] || ""],
  ["address", "Адрес", r => esc(r.address || "")], ["excluded", "Отсев", r => esc(r.excluded || "")],
  ["first_seen", "Найдено", r => dt(r.first_seen)], ["url", "", r => `<a href="${esc(r.url)}" target="_blank">↗</a>`],
];

function renderTable(rows) {
  if (tableSort) {
    const [k, d] = tableSort;
    rows = [...rows].sort((a, b) => ((a[k] ?? "") > (b[k] ?? "") ? 1 : (a[k] ?? "") < (b[k] ?? "") ? -1 : 0) * d);
  }
  $("#view-table").innerHTML = `<div class="view-wrap"><table class="grid"><thead><tr>${COLS.map(c => `<th data-k="${c[0]}">${c[1]}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(0, 1500).map(r => `<tr class="${r.excluded ? "excl" : ""}">${COLS.map(c => `<td class="${c[3] ? "num" : ""}">${c[2](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  $$("#view-table th").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    tableSort = tableSort && tableSort[0] === k ? [k, -tableSort[1]] : [k, 1];
    render();
  });
}

// ---------- карта ----------
function renderMap(rows) {
  if (!map) {
    map = L.map("map").setView([44.49, 34.155], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
    mapLayer = L.layerGroup().addTo(map);
  }
  setTimeout(() => map.invalidateSize(), 50);
  if (zoneLayer) zoneLayer.remove();
  const z = SETTINGS.zone;
  zoneLayer = z.polygon ? L.polygon(z.polygon, { color: "#d32f2f", weight: 2, fillOpacity: .05 })
    : L.circle(z.center, { radius: z.radius_m, color: "#d32f2f", weight: 2, fillOpacity: .05 });
  zoneLayer.addTo(map);
  mapLayer.clearLayers();
  const color = { cian: "#1a73e8", avito: "#00a046", yandex: "#e5462e" };
  rows.filter(r => r.lat != null).forEach(r => {
    L.circleMarker([r.lat, r.lon], { radius: 6, color: "#fff", weight: 1, fillColor: color[r.source], fillOpacity: r.excluded ? .35 : .9 })
      .bindPopup(`<b>${fmt(r.price)} ₽</b> · ${SRC[r.source]} · зона ${r.zone}<br>${esc(r.title)}<br>${esc(r.address || "")}<br><a href="${esc(r.url)}" target="_blank">Открыть ↗</a>`)
      .addTo(mapLayer);
  });
}

// ---------- прогоны ----------
async function renderRuns() {
  const runs = await api("/api/runs");
  $("#view-runs").innerHTML = `<div class="view-wrap"><table class="grid"><thead><tr><th>#</th><th>Сайт</th><th>Режим</th><th>Старт</th><th>Финиш</th><th>Статус</th>
    <th>Стр.</th><th>Найдено</th><th>Новых</th><th>Изм.</th><th>Снято</th><th>Сообщение</th></tr></thead><tbody>${runs.map(r => `<tr>
    <td>${r.id}</td><td>${SRC[r.source]}</td><td>${r.mode}</td><td>${(r.started || "").replace("T", " ")}</td><td>${(r.finished || "").replace("T", " ")}</td>
    <td class="state-${r.status}">${STATE[r.status] || r.status}</td><td class="num">${r.pages ?? ""}</td><td class="num">${r.found ?? ""}</td>
    <td class="num">${r.new ?? ""}</td><td class="num">${r.changed ?? ""}</td><td class="num">${r.removed ?? ""}</td><td>${esc(r.message || "")}</td></tr>`).join("")}</tbody></table></div>`;
}

// ---------- настройки ----------
function renderSettings() {
  const f = SETTINGS.filters, z = SETTINGS.zone, s = SETTINGS.sites;
  const modeSel = k => `<select data-site="${k}">${["auto", "http", "browser"].map(m => `<option ${s[k].mode === m ? "selected" : ""}>${m}</option>`).join("")}</select>`;
  $("#view-settings").innerHTML = `<div class="settings">
    <h4>Параметры поиска на сайтах</h4>
    <div class="grid2">
      <label>Комнаты (через запятую)</label><input type="text" id="sRooms" value="${f.rooms.join(",")}">
      <label>Площадь от, м²</label><input type="number" id="sArea" value="${f.area_min}">
      <label>Цена от, ₽</label><input type="number" id="sPmin" value="${f.price_min}">
      <label>Цена до, ₽</label><input type="number" id="sPmax" value="${f.price_max}">
      <label>Исключать обременение</label><input type="checkbox" id="sEnc" ${f.exclude_encumbrance ? "checked" : ""}>
      <label>Исключать доли</label><input type="checkbox" id="sShare" ${f.exclude_share ? "checked" : ""}>
      <label>Исключать аукционы/торги</label><input type="checkbox" id="sAuc" ${f.exclude_auction ? "checked" : ""}>
      <label>Ремонт</label><select id="sReno">
        <option value="exclude_none" ${f.renovation === "exclude_none" ? "selected" : ""}>отсеивать «без ремонта», неуказанный оставлять</option>
        <option value="strict" ${f.renovation === "strict" ? "selected" : ""}>только с явно указанным ремонтом</option>
        <option value="off" ${f.renovation === "off" ? "selected" : ""}>не учитывать</option></select>
    </div>
    <h4>Геозона A (круг)</h4>
    <div class="grid2">
      <label>Центр: широта</label><input type="number" step="0.000001" id="zLat" value="${z.center[0]}">
      <label>Центр: долгота</label><input type="number" step="0.000001" id="zLon" value="${z.center[1]}">
      <label>Радиус, м</label><input type="number" id="zR" value="${z.radius_m}">
    </div>
    <div class="hint">Калибровка по GeoZone_v1.jpg: Ливадийский дворец, Белая дача, «Атлантида» (точность ~50–70 м). Проверить можно на вкладке «Карта».</div>
    <h4>Способ загрузки</h4>
    <div class="grid2">
      <label>ЦИАН</label>${modeSel("cian")}
      <label>Авито</label>${modeSel("avito")}
      <label>Яндекс</label>${modeSel("yandex")}
      <label>Авито: макс. страниц объявлений за прогон</label><input type="number" id="sAvDet" value="${s.avito.max_details}">
    </div>
    <div class="hint">auto — сначала быстрый HTTP, при блокировке автоматически браузер; http — только HTTP; browser — только браузер (видимое окно, капчу проходите вручную).</div>
    <div class="actions"><button class="primary" id="btnSave">Сохранить и пересчитать</button></div>
  </div>`;
  $("#btnSave").onclick = saveSettings;
}

async function saveSettings() {
  const f = SETTINGS.filters, z = SETTINGS.zone, s = SETTINGS.sites;
  f.rooms = $("#sRooms").value.split(",").map(x => parseInt(x)).filter(x => !isNaN(x));
  f.area_min = +$("#sArea").value; f.price_min = +$("#sPmin").value; f.price_max = +$("#sPmax").value;
  f.exclude_encumbrance = $("#sEnc").checked; f.exclude_share = $("#sShare").checked; f.exclude_auction = $("#sAuc").checked;
  f.renovation = $("#sReno").value;
  z.center = [+$("#zLat").value, +$("#zLon").value]; z.radius_m = +$("#zR").value;
  $$("#view-settings select[data-site]").forEach(el => s[el.dataset.site].mode = el.value);
  s.avito.max_details = +$("#sAvDet").value;
  SETTINGS = await post("/api/settings", SETTINGS);
  await post("/api/reclassify");
  await loadData();
  alert("Сохранено. Зоны и отсев пересчитаны. Новые параметры поиска применятся при следующем запуске.");
}

// ---------- статус и журнал ----------
async function poll() {
  try {
    const s = await api(`/api/status?after=${jAfter}`);
    const box = $("#status");
    box.innerHTML = Object.keys(SRC).map(k => {
      const v = s.status[k];
      if (!v) return `<div class="st ${k}"><div class="h">${SRC[k]}<span class="m">не запускался</span></div></div>`;
      const pct = v.pages ? Math.round(100 * v.page / v.pages) : 0;
      return `<div class="st ${k}"><div class="h">${SRC[k]}<span class="state-${v.running ? "running" : v.state}">${v.running ? "идёт" : STATE[v.state] || v.state}</span></div>
        <div class="m">стр. ${v.page || 0}/${v.pages || "?"} · найдено ${v.found} · новых ${v.new} · отсеяно ${v.excluded}${v.details ? ` · карточек ${v.details}` : ""} · ${v.mode || ""}</div>
        ${v.message ? `<div class="m">${esc(v.message)}</div>` : ""}
        <div class="bar"><i style="width:${v.running ? pct : 100}%"></i></div></div>`;
    }).join("");
    if (s.journal.length) {
      const body = $("#jBody");
      s.journal.forEach(j => {
        const d = document.createElement("div");
        d.className = "jl" + (j.msg.includes("⚠") ? " warn" : "");
        d.innerHTML = `<span class="t">${j.t}</span><span class="s">${SRC[j.src] || j.src}</span>${esc(j.msg)}`;
        body.appendChild(d);
        jAfter = j.n;
        if (j.msg.includes("⚠")) $("#journal").classList.remove("collapsed");
        if (j.msg.startsWith("завершено")) loadData();
      });
      body.scrollTop = body.scrollHeight;
      const last = s.journal[s.journal.length - 1];
      $("#jLast").textContent = `${last.t} ${SRC[last.src] || ""}: ${last.msg}`;
    }
    const anyRunning = Object.values(s.status).some(v => v.running);
    if (anyRunning && Date.now() - (poll.lastLoad || 0) > 15000) { poll.lastLoad = Date.now(); loadData(); }
  } catch (e) { /* сервер перезапускается */ }
  setTimeout(poll, 2000);
}

// ---------- события ----------
function setView(v) {
  view = v;
  $$(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach(el => el.classList.add("hidden"));
  $(`#view-${v}`).classList.remove("hidden");
  const side = v === "cards" || v === "table" || v === "map";
  $("#filters").classList.toggle("hidden", !side);
  $("#summary").classList.toggle("hidden", !side);
  if (v === "runs") renderRuns();
  else if (v === "settings") renderSettings();
  else render();
}

function resetFilters() {
  $$("#filters input[type=checkbox]").forEach(i => i.checked = !(i.value === "C" || i.id === "fNoCoords"));
  $("#fPmin").value = $("#fPmax").value = $("#fAmin").value = $("#fText").value = "";
  $("#fReno").value = $("#fNew").value = ""; $("#fState").value = "pass"; $("#fSort").value = "new";
  render();
}

$$(".tabs button").forEach(b => b.onclick = () => setView(b.dataset.view));
$("#filters").addEventListener("input", () => render());
$("#btnReset").onclick = resetFilters;
$("#btnRun").onclick = async () => {
  const r = await post("/api/run", { sources: $$(".src-run:checked").map(i => i.value) });
  if (!r.started.length) alert("Выбранные парсеры уже запущены");
};
$("#btnStop").onclick = () => post("/api/stop", {});
$("#jToggle").onclick = () => $("#journal").classList.toggle("collapsed");

(async () => { await loadSettings(); await loadData(); poll(); })();
