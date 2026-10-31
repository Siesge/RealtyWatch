"use strict";
// Дашборд, экран «Изменения», окно объекта с графиком цен, автообновление, всплывающие подсказки.
// Использует общие функции и данные из app.js (ALL, OBJS, SRC, fmt, mln, esc, api, post, …).

const KIND = {
  new: { label: "новое", cls: "k-new", tip: "Объявление появилось на сайте" },
  removed: { label: "снято", cls: "k-removed", tip: "Объявление снято с продажи" },
  returned: { label: "вернулось", cls: "k-ret", tip: "Объявление снова в продаже" },
  down: { label: "подешевело", cls: "k-down", tip: "Цена снижена" },
  up: { label: "подорожало", cls: "k-up", tip: "Цена повышена" },
};
const isDark = () => document.documentElement.dataset.theme === "dark" ||
  (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
// Категориальная палитра (порядок слотов фиксирован, проверена на различимость при нарушениях цветового зрения)
const SERIES = () => isDark() ? ["#3987e5", "#d95926", "#199e70", "#c98500"] : ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"];
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const charts = {};

function chartBase() {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { position: "top", align: "start", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, color: css("--ink-2"), font: { family: "Manrope", weight: 600 } } },
      tooltip: { backgroundColor: css("--surface"), titleColor: css("--ink"), bodyColor: css("--ink-2"), borderColor: css("--line-2"), borderWidth: 1,
        padding: 10, titleFont: { family: "Manrope", weight: 700 }, bodyFont: { family: "Manrope" }, usePointStyle: true },
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: css("--muted"), font: { family: "Manrope" }, maxRotation: 0, autoSkipPadding: 16 }, border: { color: css("--line-2") } },
      y: { grid: { color: css("--line") }, ticks: { color: css("--muted"), font: { family: "Manrope" } }, border: { display: false } },
    },
  };
}

// Табличный вид графика: те же значения цифрами (обязателен, т.к. часть цветов ниже 3:1 на светлом фоне)
function chartTable(id, labels, series, yFmt) {
  const box = document.getElementById(`${id}-tbl`);
  if (!box) return;
  box.innerHTML = `<table class="grid compact"><thead><tr><th>Проверка</th>${series.map(s => `<th class="num">${esc(s.label)}</th>`).join("")}</tr></thead>
    <tbody>${labels.map((l, k) => `<tr><td>${esc(l)}</td>${series.map(s => `<td class="num">${s.data[k] == null ? "—" : yFmt(s.data[k])}</td>`).join("")}</tr>`).reverse().join("")}</tbody></table>`;
}

function lineChart(id, labels, series, yFmt, extra = {}) {
  chartTable(id, labels, series, yFmt);
  if (charts[id]) charts[id].destroy();
  const el = document.getElementById(id);
  if (!el) return;
  const colors = SERIES();
  const opt = chartBase();
  opt.scales.y.ticks.callback = yFmt;
  opt.plugins.tooltip.callbacks = { label: c => ` ${c.dataset.label}: ${yFmt(c.parsed.y)}` };
  Object.assign(opt, extra);
  charts[id] = new Chart(el, {
    type: "line",
    data: { labels, datasets: series.map((s, i) => ({ label: s.label, data: s.data, borderColor: s.color || colors[i], backgroundColor: s.color || colors[i],
      borderWidth: 2, pointRadius: labels.length < 12 ? 3 : 0, pointHoverRadius: 5, tension: .25, spanGaps: true, ...(s.ds || {}) })) },
    options: opt,
  });
}

function barChart(id, labels, series, yFmt) {
  chartTable(id, labels, series, yFmt);
  if (charts[id]) charts[id].destroy();
  const el = document.getElementById(id);
  if (!el) return;
  const colors = SERIES();
  const opt = chartBase();
  opt.scales.y.ticks.callback = yFmt;
  opt.scales.y.ticks.precision = 0;
  charts[id] = new Chart(el, {
    type: "bar",
    data: { labels, datasets: series.map((s, i) => ({ label: s.label, data: s.data, backgroundColor: colors[i], borderRadius: 4, maxBarThickness: 18,
      borderColor: css("--surface"), borderWidth: 2, borderSkipped: false })) },
    options: opt,
  });
}

const tsLabel = t => new Date(t).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const pct = (a, b) => (a && b ? (100 * (a - b) / b) : null);
const signed = (v, d = 1) => v == null ? "" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`;

// ---------- дашборд ----------
async function renderDashboard() {
  const el = $("#view-dashboard");
  let d;
  try { d = await api("/api/dashboard"); } catch (e) { el.innerHTML = `<div class="empty"><b>Не удалось загрузить сводку</b>${esc(e.message)}</div>`; return; }
  if (view !== "dashboard") return;
  const S = d.snapshots, last = S[S.length - 1], prev = S[S.length - 2];
  const city = last ? last.city : {}, pcity = prev ? prev.city : {};
  const e24 = d.events_24h, e7 = d.events_7d;
  const kpi = (val, label, tip, delta, cls = "") => `<div class="kpi ${cls}" title="${esc(tip)}"><b>${val}</b><span>${label}</span>${delta ? `<em class="${delta.startsWith("−") ? "neg" : "pos"}">${delta}</em>` : ""}</div>`;
  const delta = (a, b) => (a != null && b ? signed(pct(a, b)) : "");
  const srcRows = ["cian", "avito", "yandex"].map(s => {
    const x = d.sources[s] || {}, r = d.last_runs[s] || {};
    return `<tr title="${esc(`${SRC[s]}: последний сбор ${r.started ? tsLabel(r.started) : "—"} — ${r.message || "ещё не было"}`)}">
      <td><i class="sdot ${s}"></i> ${SRC[s]}</td><td class="num">${x.active ?? 0}</td><td class="num">${x.passing ?? 0}</td>
      <td class="num">${last && last.sources ? last.sources[s] : "—"}</td><td class="num">${x.removed ?? 0}</td>
      <td><span class="st-badge ${r.status || ""}">${STATE[r.status] || r.status || "—"}</span> <small>${r.finished ? tsLabel(r.finished) : ""}</small></td></tr>`;
  }).join("");
  const rz = (z, k) => (last && last.zones && last.zones[z]) ? last.zones[z][k] : null;
  const rr = (n, k) => (last && last.rooms && last.rooms[n]) ? last.rooms[n][k] : null;
  el.innerHTML = `
    <div class="dash-grid">
      <div class="kpis wide">
        ${kpi(city.count ?? "—", "объектов в Ялте", "Квартир в зонах A и B, подходящих под параметры поиска (дубли с разных сайтов объединены). Изменение — к предыдущей проверке", delta(city.count, pcity.count))}
        ${kpi(mln(city.median_price), "медиана цены, млн ₽", "Медианная цена: половина квартир дешевле, половина дороже. Изменение — к предыдущей проверке", delta(city.median_price, pcity.median_price))}
        ${kpi(fmt(city.median_ppm), "медиана, ₽/м²", "Медианная цена квадратного метра общей площади", delta(city.median_ppm, pcity.median_ppm), "gold")}
        ${kpi(`+${e24.new}`, "новых за сутки", `Новых объявлений за последние 24 часа (за 7 дней: ${e7.new}). Первичная загрузка сайта не считается`)}
        ${kpi(e7.removed, "снято за неделю", "Объявлений, снятых с продажи за 7 дней (проверено на странице сайта или пропало в двух полных сборах подряд)")}
        ${kpi(`↓${e7.price_down} ↑${e7.price_up}`, "цены за неделю", "Сколько объявлений подешевело (↓) и подорожало (↑) за 7 дней")}
        ${kpi(last ? last.multi : "—", "на 2+ сайтах", "Объектов в Ялте, найденных сразу на нескольких сайтах")}
      </div>

      <div class="panel dash-card span2"><div class="dc-head"><h3>Медианная цена по комнатности</h3><span class="meta">Ялта (зоны A+B), каждая точка — одна проверка рынка <button class="link sm" data-tbl="chRooms" title="Показать значения таблицей">таблица</button></span></div>
        <div class="chart-box"><canvas id="chRooms"></canvas></div><div class="chart-tbl hidden" id="chRooms-tbl"></div></div>
      <div class="panel dash-card"><div class="dc-head"><h3>Цена м² по зонам</h3><span class="meta">медиана, ₽/м² <button class="link sm" data-tbl="chPpm" title="Показать значения таблицей">таблица</button></span></div>
        <div class="chart-box"><canvas id="chPpm"></canvas></div><div class="chart-tbl hidden" id="chPpm-tbl"></div></div>
      <div class="panel dash-card"><div class="dc-head"><h3>Движение рынка</h3><span class="meta">событий между проверками <button class="link sm" data-tbl="chEvents" title="Показать значения таблицей">таблица</button></span></div>
        <div class="chart-box"><canvas id="chEvents"></canvas></div><div class="chart-tbl hidden" id="chEvents-tbl"></div></div>
      <div class="panel dash-card"><div class="dc-head"><h3>Предложение</h3><span class="meta">объектов в продаже <button class="link sm" data-tbl="chCount" title="Показать значения таблицей">таблица</button></span></div>
        <div class="chart-box"><canvas id="chCount"></canvas></div><div class="chart-tbl hidden" id="chCount-tbl"></div></div>

      <div class="panel dash-card span2"><div class="dc-head"><h3>Срез рынка сейчас</h3><span class="meta">медианы по подходящим объектам; «все» — зоны A+B</span></div>
        <table class="grid compact"><thead><tr><th title="Число комнат">Комнаты</th><th class="num" title="Объектов в продаже">Объектов</th>
          <th class="num" title="Медианная цена">Цена, млн</th><th class="num" title="Медианная цена за м²">₽/м²</th><th class="num" title="Медианная общая площадь">м²</th>
          <th class="num" title="Медиана ₽/м² в красном круге (зона A)">₽/м² круг</th><th class="num" title="Медиана ₽/м² в остальной Ялте (зона B)">₽/м² Ялта</th></tr></thead><tbody>
          ${["1", "2", "3"].map(n => `<tr><td>${n}-комнатные</td><td class="num">${rr(n, "count") ?? "—"}</td><td class="num">${mln(rr(n, "median_price"))}</td>
            <td class="num">${fmt(rr(n, "median_ppm"))}</td><td class="num">${rr(n, "median_area") ?? "—"}</td>
            <td class="num">${fmt(roomZonePpm(n, "A"))}</td><td class="num">${fmt(roomZonePpm(n, "B"))}</td></tr>`).join("")}
          <tr class="total"><td>Все</td><td class="num">${city.count ?? "—"}</td><td class="num">${mln(city.median_price)}</td><td class="num">${fmt(city.median_ppm)}</td>
            <td class="num">${city.median_area ?? "—"}</td><td class="num">${fmt(rz("A", "median_ppm"))}</td><td class="num">${fmt(rz("B", "median_ppm"))}</td></tr>
        </tbody></table></div>
      <div class="panel dash-card"><div class="dc-head"><h3>Источники</h3><span class="meta">объявлений по сайтам</span></div>
        <table class="grid compact"><thead><tr><th>Сайт</th><th class="num" title="Активных объявлений">Активно</th><th class="num" title="Подходят под параметры поиска">Подходят</th>
          <th class="num" title="Объектов в Ялте, у которых есть объявление на этом сайте">В Ялте</th><th class="num" title="Снято с продажи за всё время">Снято</th><th>Последний сбор</th></tr></thead>
          <tbody>${srcRows}</tbody></table></div>

      <div class="panel dash-card span3"><div class="dc-head"><h3>Самые выгодные сейчас</h3><span class="meta">цена м² ниже медианы для той же комнатности и зоны</span></div>
        ${dealsTable()}</div>

      <div class="panel dash-card span3 likes-card"><div class="dc-head"><h3>♥ Избранное</h3><span class="meta">отмеченные квартиры: цена, динамика, сравнение с рынком</span></div>
        ${likesBlock()}</div>
    </div>`;
  el.querySelectorAll("[data-open]").forEach(a => a.onclick = () => openObject(a.dataset.open));
  el.querySelectorAll("[data-card]").forEach(a => a.onclick = () => goToCard(a.dataset.card));
  el.querySelectorAll("[data-tbl]").forEach(b => b.onclick = () => {
    const t = $(`#${b.dataset.tbl}-tbl`), on = t.classList.toggle("hidden");
    b.textContent = on ? "таблица" : "график";
    $(`#${b.dataset.tbl}`).parentNode.classList.toggle("hidden", !on);
  });
  if (!S.length) return;
  const labels = S.map(s => tsLabel(s.ts));
  lineChart("chRooms", labels, [
    { label: "Все", data: S.map(s => s.city.median_price) },
    { label: "1-комн.", data: S.map(s => s.rooms["1"].median_price) },
    { label: "2-комн.", data: S.map(s => s.rooms["2"].median_price) },
    { label: "3-комн.", data: S.map(s => s.rooms["3"].median_price) },
  ], v => `${mln(v)} млн`);
  lineChart("chPpm", labels, [
    { label: "Ялта A+B", data: S.map(s => s.city.median_ppm) },
    { label: "Круг (A)", data: S.map(s => s.zones.A.median_ppm) },
    { label: "Ялта (B)", data: S.map(s => s.zones.B.median_ppm) },
  ], v => fmt(v));
  barChart("chEvents", labels, [
    { label: "новые", data: S.map(s => (s.events || {}).new || 0) },
    { label: "снятые", data: S.map(s => (s.events || {}).removed || 0) },
    { label: "смена цены", data: S.map(s => ((s.events || {}).price_down || 0) + ((s.events || {}).price_up || 0)) },
  ], v => fmt(v));
  lineChart("chCount", labels, [
    { label: "Ялта A+B", data: S.map(s => s.city.count) },
    { label: "Круг (A)", data: S.map(s => s.zones.A.count) },
  ], v => fmt(v));
}

function cityObjs() {
  return OBJS.filter(o => o.active && !o.excluded && (o.zone === "A" || o.zone === "B") && o.price_m2);
}

function roomZonePpm(n, z) {
  return median(cityObjs().filter(o => String(o.rooms) === n && o.zone === z).map(o => o.price_m2));
}

function marketPpm(o) {
  const peers = OBJS.filter(x => x.active && !x.excluded && x.zone === o.zone && x.rooms === o.rooms && x.price_m2).map(x => x.price_m2);
  return peers.length >= 3 ? median(peers) : null;
}

function dealsTable() {
  const rows = cityObjs().map(o => ({ o, m: marketPpm(o) })).filter(x => x.m).map(x => ({ ...x, d: pct(x.o.price_m2, x.m) }))
    .filter(x => x.d < 0).sort((a, b) => a.d - b.d).slice(0, 10);
  if (!rows.length) return `<div class="empty small">Пока недостаточно данных</div>`;
  return `<table class="grid compact clickable"><thead><tr><th></th><th>Квартира</th><th>Адрес</th><th>Зона</th><th class="num">Цена</th><th class="num">₽/м²</th>
    <th class="num" title="Насколько цена м² ниже медианы для такой же комнатности в той же зоне">к рынку</th><th>Сайты</th></tr></thead><tbody>
    ${rows.map(({ o, d }) => `<tr data-card="${esc(o.id)}" title="Клик — открыть карточку">
      <td>${o.liked ? '<span class="lk">♥</span>' : ""}</td><td>${o.rooms}-к, ${o.area} м², ${o.floor ?? "?"}/${o.floors ?? "?"} эт.</td>
      <td class="addr-cell">${esc(o.address)}</td><td><i class="zdot ${o.zone}"></i> ${ZONE_SHORT[o.zone]}</td>
      <td class="num">${mln(o.price)} млн</td><td class="num">${fmt(o.price_m2)}</td><td class="num good">${signed(d)}</td>
      <td>${o.sources.map(s => `<i class="sdot ${s}" title="${SRC[s]}"></i>`).join(" ")}</td></tr>`).join("")}</tbody></table>`;
}

function likesBlock() {
  const L = OBJS.filter(o => o.liked);
  if (!L.length) return `<div class="empty small"><b>Пока пусто</b>Отмечайте квартиры сердечком на карточке — здесь появится их сводка</div>`;
  const rows = L.map(o => {
    const firsts = o.items.map(i => (i.history || [])[0]).filter(Boolean).map(h => h[1]);
    const start = firsts.length ? Math.min(...firsts) : o.price;
    const m = marketPpm(o);
    return { o, start, ch: pct(o.price, start), mk: m ? pct(o.price_m2, m) : null };
  });
  const active = rows.filter(r => r.o.active);
  const avgMk = active.filter(r => r.mk != null);
  const sum = `<div class="likes-sum">
    <div title="Всего в избранном"><b>${L.length}</b><span>в избранном</span></div>
    <div title="Из них в продаже хотя бы на одном сайте"><b>${active.length}</b><span>в продаже</span></div>
    <div title="Сняты со всех сайтов"><b>${rows.length - active.length}</b><span>сняты</span></div>
    <div title="Сколько избранных подешевело с момента появления в базе"><b>${rows.filter(r => r.ch < 0).length}</b><span>подешевели</span></div>
    <div title="Средняя разница цены м² избранных с медианой рынка той же комнатности и зоны"><b>${avgMk.length ? signed(avgMk.reduce((a, r) => a + r.mk, 0) / avgMk.length) : "—"}</b><span>к рынку, ₽/м²</span></div>
  </div>`;
  return sum + `<table class="grid compact clickable"><thead><tr><th>Квартира</th><th>Адрес</th><th>Зона</th><th class="num">Цена</th>
    <th class="num" title="Изменение цены с момента первого появления в базе">с начала</th><th class="num">₽/м²</th>
    <th class="num" title="Цена м² относительно медианы рынка той же комнатности и зоны">к рынку</th><th>Статус</th><th></th></tr></thead><tbody>
    ${rows.sort((a, b) => (b.o.active - a.o.active) || (a.mk ?? 99) - (b.mk ?? 99)).map(({ o, ch, mk }) => `<tr>
      <td data-card="${esc(o.id)}" class="lnk" title="Открыть карточку в подборке">${o.rooms}-к, ${o.area} м², ${o.floor ?? "?"}/${o.floors ?? "?"} эт.</td>
      <td class="addr-cell">${esc(o.address)}</td><td><i class="zdot ${o.zone}"></i> ${ZONE_SHORT[o.zone]}</td>
      <td class="num">${mln(o.price)} млн</td><td class="num ${ch < 0 ? "good" : ch > 0 ? "bad" : ""}">${ch ? signed(ch) : "без изменений"}</td>
      <td class="num">${fmt(o.price_m2)}</td><td class="num ${mk < 0 ? "good" : mk > 0 ? "bad" : ""}">${mk == null ? "—" : signed(mk)}</td>
      <td>${o.active ? `в продаже · ${o.sources.map(s => `<i class="sdot ${s}" title="${SRC[s]}"></i>`).join(" ")}` : `<span class="tag bad" title="${esc(o.items.map(i => `${SRC[i.source]}: ${goneTip(i)}`).join("\n"))}">снято</span>`}</td>
      <td><button class="icon-btn" data-open="${esc(o.id)}" title="История цен">${ICON.chart}</button></td></tr>`).join("")}</tbody></table>`;
}

// ---------- изменения ----------
let CH_DAYS = +store.get("rw.chDays", "7");
async function renderChanges() {
  const el = $("#view-changes");
  const ev = await api(`/api/changes?days=${CH_DAYS}`);
  if (view !== "changes") return;
  const kinds = JSON.parse(store.get("rw.chKinds", '["new","down","up","removed","returned"]'));
  const onlyPass = store.get("rw.chPass", "1") === "1";
  const kindOf = e => e.kind === "price" ? ((e.new_value || 0) < (e.old_value || 0) ? "down" : "up") : e.kind;
  const groups = new Map();
  for (const e of ev) {
    const k = kindOf(e);
    if (!kinds.includes(k)) continue;
    const oid = e.object_id || `${e.source}:${e.source_id}`;
    if (!groups.has(oid)) groups.set(oid, []);
    groups.get(oid).push({ ...e, k });
  }
  let tiles = [...groups.entries()].map(([oid, list]) => ({ oid, list, o: OBJS.find(x => x.id === oid) }));
  if (onlyPass) tiles = tiles.filter(t => t.o && !t.o.excluded);
  const cnt = k => ev.filter(e => kindOf(e) === k).length;
  el.innerHTML = `<div class="ch-bar panel">
      <div class="seg small" title="За какой период показывать изменения">${[1, 3, 7, 30].map(dd => `<label><input type="radio" name="chDays" value="${dd}" ${dd === CH_DAYS ? "checked" : ""}><span>${dd === 1 ? "сутки" : dd + " дн."}</span></label>`).join("")}</div>
      <div class="seg small">${Object.entries(KIND).map(([k, v]) => `<label title="${v.tip}"><input type="checkbox" name="chKind" value="${k}" ${kinds.includes(k) ? "checked" : ""}><span><i class="kdot ${v.cls}"></i>${v.label} <em>${cnt(k)}</em></span></label>`).join("")}</div>
      <label class="check" title="Показывать только квартиры, подходящие под параметры поиска"><input type="checkbox" id="chPass" ${onlyPass ? "checked" : ""}><span>только подходящие</span></label>
      <span class="meta">${tiles.length} ${plural(tiles.length, "объект", "объекта", "объектов")} с изменениями</span>
    </div>
    ${tiles.length ? `<div class="ch-grid">${tiles.map(changeTile).join("")}</div>` :
      `<div class="empty"><b>Изменений нет</b>За выбранный период по выбранным типам ничего не менялось. Первичная загрузка сайта изменениями не считается.</div>`}`;
  el.querySelectorAll("input[name=chDays]").forEach(r => r.onchange = () => { CH_DAYS = +r.value; store.set("rw.chDays", r.value); renderChanges(); });
  el.querySelectorAll("input[name=chKind]").forEach(c => c.onchange = () => {
    store.set("rw.chKinds", JSON.stringify([...el.querySelectorAll("input[name=chKind]:checked")].map(x => x.value))); renderChanges();
  });
  $("#chPass").onchange = () => { store.set("rw.chPass", $("#chPass").checked ? "1" : "0"); renderChanges(); };
  el.querySelectorAll("[data-card]").forEach(b => b.onclick = () => goToCard(b.dataset.card));
  el.querySelectorAll("[data-open]").forEach(b => b.onclick = () => openObject(b.dataset.open));
}

function eventText(e) {
  if (e.k === "down" || e.k === "up") {
    const d = e.new_value - e.old_value;
    return `цена ${mln(e.old_value)} → <b>${mln(e.new_value)} млн</b> <span class="${d < 0 ? "good" : "bad"}">(${d > 0 ? "+" : "−"}${fmt(Math.abs(d))} ₽, ${signed(pct(e.new_value, e.old_value))})</span>`;
  }
  if (e.k === "new") return `новое объявление, ${fmt(e.new_value)} ₽`;
  if (e.k === "removed") return `снято с продажи: ${esc(e.note || "")}`;
  if (e.k === "returned") return `снова в продаже${e.new_value ? `, ${fmt(e.new_value)} ₽` : ""}`;
  return e.kind;
}

function changeTile(t) {
  const o = t.o, e0 = t.list[0];
  const head = o ? `${o.rooms ?? "?"}-к, ${o.area ?? "?"} м², ${o.floor ?? "?"}/${o.floors ?? "?"} эт.` : esc(e0.title || "");
  const img = o && o.img ? `<img src="/img/${encodeURIComponent(o.img.source)}/${encodeURIComponent(o.img.source_id)}" loading="lazy" alt="" onerror="this.remove()">` : "";
  const sites = [...new Set(t.list.map(e => e.source))];
  return `<article class="ch-tile ${o && o.liked ? "liked" : ""}">
    <div class="ch-img">${img}${o ? `<span class="pill zone-${o.zone}" title="${esc(ZONE_TIP[o.zone])}">${ZONE_SHORT[o.zone]}</span>` : ""}</div>
    <div class="ch-body">
      <div class="ch-title"><b>${head}</b>${o && o.liked ? ' <span class="lk" title="В избранном">♥</span>' : ""}
        <span class="ch-price" title="Текущая цена объекта (минимальная среди активных объявлений)">${o ? `${fmt(o.price)} ₽` : ""}</span></div>
      <div class="addr" title="Адрес">${ICON.pin}<span>${esc((o && o.address) || e0.address || "")}</span></div>
      <div class="ch-sum" title="На каких сайтах были изменения">${sites.length > 1 ? `изменения на ${sites.length} сайтах: ` : ""}${sites.map(s => `<i class="sdot ${s}"></i> ${SRC[s]}`).join(", ")}</div>
      <ul class="ch-list">${t.list.map(e => `<li title="${esc(KIND[e.k].tip + " — " + tsLabel(e.ts))}"><span class="kbadge ${KIND[e.k].cls}">${KIND[e.k].label}</span>
        <span class="ch-when">${tsLabel(e.ts)}</span><i class="sdot ${e.source}" title="${SRC[e.source]}"></i> <span>${SRC[e.source]}: ${eventText(e)}</span>
        <a href="${esc(e.url)}" target="_blank" rel="noopener" title="Открыть объявление на ${SRC[e.source]}">${ICON.ext}</a></li>`).join("")}</ul>
      ${o ? `<div class="ch-acts"><button class="btn-sec sm" data-card="${esc(o.id)}" title="Открыть эту квартиру в подборке">В подборке</button>
        <button class="btn-sec sm" data-open="${esc(o.id)}" title="Окно с историей цен по каждому сайту">${ICON.chart}История цен</button></div>` : ""}
    </div></article>`;
}

async function updateChangesBadge() {
  try {
    const ev = await api("/api/changes?days=30");
    const seen = store.get("rw.chSeen", "");
    const n = ev.filter(e => !seen || e.ts > seen).length;
    const b = $("#chBadge");
    b.textContent = n > 99 ? "99+" : n;
    b.classList.toggle("hidden", !n || view === "changes");
    b.title = `${n} ${plural(n, "изменение", "изменения", "изменений")} с последнего просмотра`;
  } catch (e) { /* нет данных */ }
}

function markChangesSeen() {
  store.set("rw.chSeen", new Date(Date.now() + 60000).toISOString().slice(0, 19));
  $("#chBadge").classList.add("hidden");
}

// ---------- окно объекта: история цен ----------
function openObject(id) {
  const o = OBJS.find(x => x.id === id);
  if (!o) return;
  const changed = o.items.some(i => lastChange(i));
  const desc = o.items.map(i => i.description || "").sort((a, b) => b.length - a.length)[0] || "";
  $("#modalBody").innerHTML = `
    <div class="m-head">
      <div class="m-img">${o.img ? `<img src="/img/${encodeURIComponent(o.img.source)}/${encodeURIComponent(o.img.source_id)}" alt="">` : ""}</div>
      <div class="m-info">
        <div class="m-title">${o.rooms === 0 ? "Студия" : `${o.rooms}-комнатная`}, ${o.area} м², этаж ${o.floor ?? "?"} из ${o.floors ?? "?"}</div>
        <div class="addr">${ICON.pin}<span>${esc(o.address)}</span></div>
        <div class="m-price" title="Минимальная цена среди активных объявлений">${fmt(o.price)} ₽ <span class="meta">${fmt(o.price_m2)} ₽/м²</span> ${oldPrice(o)}</div>
        <div class="tags">${tags(o)}</div>
        ${offers(o)}
      </div>
    </div>
    <div class="m-chart-head"><h3>История цен по сайтам</h3><span class="meta">${changed ? "ступенька — изменение цены" : "цена не менялась с момента появления в базе"}</span></div>
    <div class="chart-box tall"><canvas id="chObj"></canvas></div>
    <div class="m-hist">${priceTable(o)}</div>
    ${desc ? `<details class="m-desc"><summary>Описание</summary><p>${esc(desc)}</p></details>` : ""}`;
  $("#modal").classList.remove("hidden");
  drawObjectChart(o);
}

function priceTable(o) {
  const rows = [];
  o.items.forEach(i => {
    const h = i.history || [];
    h.forEach((p, k) => rows.push({ ts: p[0], i, price: p[1], prev: k ? h[k - 1][1] : null }));
    if (!i.active) rows.push({ ts: i.removed_at || i.last_seen, i, gone: true });
  });
  rows.sort((a, b) => (a.ts < b.ts ? 1 : -1));
  return `<table class="grid compact"><thead><tr><th>Когда</th><th>Сайт</th><th>Событие</th><th class="num">Цена</th></tr></thead><tbody>${rows.map(r => `<tr>
    <td>${tsLabel(r.ts)}</td><td><i class="sdot ${r.i.source}"></i> ${SRC[r.i.source]}</td>
    <td>${r.gone ? `<span class="kbadge k-removed">снято</span> ${esc(r.i.removed_reason || "")}` : r.prev == null ? '<span class="kbadge k-new">появилось</span>'
      : `<span class="kbadge ${r.price < r.prev ? "k-down" : "k-up"}">${r.price < r.prev ? "подешевело" : "подорожало"}</span> было ${fmt(r.prev)} ₽`}</td>
    <td class="num">${r.gone ? "" : fmt(r.price) + " ₽"}</td></tr>`).join("")}</tbody></table>`;
}

function drawObjectChart(o) {
  if (charts.chObj) charts.chObj.destroy();
  const now = Date.now();
  const per = {};
  const ds = o.items.map(i => {
    per[i.source] = (per[i.source] || 0) + 1;
    const pts = (i.history || []).map(h => ({ x: new Date(h[0]).getTime(), y: h[1] }));
    const end = i.active ? now : new Date(i.removed_at || i.last_seen).getTime();
    if (pts.length) pts.push({ x: end, y: pts[pts.length - 1].y });
    return { label: SRC[i.source] + (per[i.source] > 1 ? ` (${per[i.source]})` : "") + (i.active ? "" : " — снято"), data: pts, stepped: "before",
      borderColor: COLOR[i.source], backgroundColor: COLOR[i.source], borderWidth: 2, pointRadius: 3, pointHoverRadius: 6,
      borderDash: per[i.source] > 1 ? [6, 4] : undefined };
  });
  const xs = ds.flatMap(s => s.data.map(p => p.x));
  const short = xs.length && Math.max(...xs) - Math.min(...xs) < 3 * 864e5;  // короче трёх дней — подписи с часами
  const opt = chartBase();
  opt.scales.x = { type: "linear", grid: { display: false }, border: { color: css("--line-2") },
    ticks: { color: css("--muted"), font: { family: "Manrope" }, maxTicksLimit: 6, callback: v => short ? tsLabel(v) : new Date(v).toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) } };
  opt.scales.y.ticks.callback = v => `${mln(v)} млн`;
  opt.interaction = { mode: "nearest", intersect: false };
  opt.plugins.tooltip.callbacks = { title: c => tsLabel(c[0].parsed.x), label: c => ` ${c.dataset.label}: ${fmt(c.parsed.y)} ₽` };
  charts.chObj = new Chart($("#chObj"), { type: "line", data: { datasets: ds }, options: opt });
}

function closeModal() {
  $("#modal").classList.add("hidden");
  if (charts.chObj) { charts.chObj.destroy(); delete charts.chObj; }
}
$("#modalX").onclick = closeModal;
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });
$("#modalBody").addEventListener("click", e => {
  const h = e.target.closest(".heart");
  if (h) toggleLike(h.closest("[data-id]")?.dataset.id, h);
});

// ---------- автообновление ----------
let AUTO = null;
async function initAuto() {
  try { AUTO = await api("/api/schedule"); } catch (e) { return; }
  const sel = $("#autoHours"), custom = $("#autoCustom");
  const preset = ["1", "3", "6", "8", "12"].includes(String(AUTO.hours));
  sel.value = preset ? String(AUTO.hours) : "custom";
  custom.value = AUTO.hours;
  custom.classList.toggle("hidden", preset);
  $("#autoOn").checked = AUTO.enabled;
  $$(".src-run").forEach(c => { c.checked = AUTO.sources.includes(c.value); });
  const save = async patch => { AUTO = await post("/api/schedule", patch); tick(); };
  $("#autoOn").onchange = () => save({ enabled: $("#autoOn").checked, hours: currentHours(), sources: $$(".src-run:checked").map(i => i.value) });
  sel.onchange = () => {
    custom.classList.toggle("hidden", sel.value !== "custom");
    if (sel.value === "custom") { custom.focus(); return; }
    save({ hours: +sel.value });
  };
  custom.onchange = () => { if (+custom.value > 0) save({ hours: +custom.value }); };
  $("#sources").addEventListener("change", () => save({ sources: $$(".src-run:checked").map(i => i.value) }));
  setInterval(tick, 1000);
  tick();
}
function currentHours() { return $("#autoHours").value === "custom" ? +$("#autoCustom").value || 6 : +$("#autoHours").value; }
let autoSkew = 0;
function tick() {
  const t = $("#autoTimer");
  if (!AUTO) return;
  $("#autoRow").classList.toggle("on", AUTO.enabled);
  if (!AUTO.enabled || !AUTO.next_run) { t.textContent = "выключено"; return; }
  const left = Math.max(0, (new Date(AUTO.next_run) - new Date()) / 1000);
  const h = Math.floor(left / 3600), m = Math.floor(left % 3600 / 60), s = Math.floor(left % 60);
  t.innerHTML = left > 0 ? `следующий запуск через <b>${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}</b>` : "запускаю…";
  if (left <= 0 && Date.now() - autoSkew > 15000) { autoSkew = Date.now(); api("/api/schedule").then(a => { AUTO = a; }); }
}

// ---------- всплывающие подсказки ----------
// Любой элемент с title получает оформленную подсказку (многострочную, в стиле приложения)
function initTips() {
  const tip = $("#tip");
  let cur = null, timer = null;
  const show = (el, x, y) => {
    tip.textContent = el.dataset.tip;
    tip.classList.add("on");
    place(x, y);
  };
  const place = (x, y) => {
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 18;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > innerHeight - 8) top = y - r.height - 12;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
  };
  document.addEventListener("mouseover", e => {
    const el = e.target.closest("[title], [data-tip]");
    if (!el || el === cur) return;
    if (el.hasAttribute("title")) { el.dataset.tip = el.getAttribute("title"); el.removeAttribute("title"); }
    if (!el.dataset.tip) return;
    cur = el;
    clearTimeout(timer);
    timer = setTimeout(() => show(el, e.clientX, e.clientY), 350);
  });
  document.addEventListener("mousemove", e => { if (tip.classList.contains("on")) place(e.clientX, e.clientY); });
  document.addEventListener("mouseout", e => {
    if (cur && !cur.contains(e.relatedTarget)) { cur = null; clearTimeout(timer); tip.classList.remove("on"); }
  });
  document.addEventListener("mousedown", () => { clearTimeout(timer); tip.classList.remove("on"); });
}
