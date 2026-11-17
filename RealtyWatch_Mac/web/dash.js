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
function chartTable(id, labels, series, yFmt, head = "Проверка") {
  const box = document.getElementById(`${id}-tbl`);
  if (!box) return;
  box.innerHTML = `<table class="grid compact"><thead><tr><th>${esc(head)}</th>${series.map(s => `<th class="num">${esc(s.label)}</th>`).join("")}</tr></thead>
    <tbody>${labels.map((l, k) => `<tr><td>${esc(l)}</td>${series.map(s => `<td class="num">${s.data[k] == null ? "—" : yFmt(s.data[k])}</td>`).join("")}</tr>`).reverse().join("")}</tbody></table>`;
}

// xs — время каждой точки: ось становится настоящей шкалой времени, и проверки стоят там, когда они были
function lineChart(id, labels, series, yFmt, xs = null) {
  chartTable(id, labels, series, yFmt);
  if (charts[id]) charts[id].destroy();
  const el = document.getElementById(id);
  if (!el) return;
  const colors = SERIES();
  const opt = chartBase();
  opt.scales.y.ticks.callback = yFmt;
  opt.plugins.tooltip.callbacks = { label: c => ` ${c.dataset.label}: ${yFmt(c.parsed.y)}` };
  if (xs && xs.length) {
    const a = xs[0], b = xs[xs.length - 1], pad = Math.max((b - a) * 0.02, 9e5);  // воздух по краям: крайние точки не липнут к оси
    const tk = timeTicks(a, b);
    Object.assign(opt.scales.x, { type: "linear", min: a - pad, max: b + pad,
      afterBuildTicks: ax => { ax.ticks = tk.ticks.map(value => ({ value })); } });
    Object.assign(opt.scales.x.ticks, { callback: tk.fmt, autoSkip: false, includeBounds: false });
    opt.plugins.tooltip.callbacks.title = it => tsLabel(it[0].parsed.x);
  }
  charts[id] = new Chart(el, {
    type: "line",
    data: { labels: xs ? undefined : labels, datasets: series.map((s, i) => ({ label: s.label,
      data: xs ? s.data.map((y, k) => ({ x: xs[k], y })) : s.data, borderColor: s.color || colors[i], backgroundColor: s.color || colors[i],
      borderWidth: 2, pointRadius: labels.length < 12 ? 3 : 0, pointHoverRadius: 5, tension: .25, spanGaps: true, ...(s.ds || {}) })) },
    options: opt,
  });
}

// Столбики одной проверки стоят друг на друге: проверок бывают десятки, и рядом они выходили бы
// в пару пикселей шириной. Зазор между столбиками — за счёт ширины самого столбика (barPercentage),
// а не рамки его же цветом фона: такая рамка на узком столбике съедала всю заливку, и цвета не было видно.
function barChart(id, labels, series, yFmt, o = {}) {
  chartTable(id, labels, series, yFmt, o.head);
  if (charts[id]) charts[id].destroy();
  const el = document.getElementById(id);
  if (!el) return;
  const colors = SERIES();
  const opt = chartBase();
  opt.scales.y.ticks.callback = yFmt;
  opt.scales.y.ticks.precision = 0;
  opt.scales.x.stacked = opt.scales.y.stacked = true;
  opt.plugins.tooltip.callbacks = { label: c => ` ${c.dataset.label}: ${yFmt(c.parsed.y)}` };
  if (o.titles) opt.plugins.tooltip.callbacks.title = it => o.titles[it[0].dataIndex];
  const slot = Math.max(1, el.clientWidth - 48) / Math.max(1, labels.length);  // место под один столбик
  charts[id] = new Chart(el, {
    type: "bar",
    data: { labels, datasets: series.map((s, i) => ({ label: s.label, data: s.data, backgroundColor: colors[i],
      borderRadius: slot >= 8 ? 3 : 0, maxBarThickness: 26, categoryPercentage: 0.82, barPercentage: 0.96 })) },
    options: opt,
  });
}

const tsLabel = t => new Date(t).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const pct = (a, b) => (a && b ? (100 * (a - b) / b) : null);

// ---------- шкала времени ----------
// Время на графиках идёт равномерно: точка стоит там, когда проверка случилась на самом деле.
// Пока промежутки были равными, десять проверок за день занимали больше места, чем неделя без них.
const HOUR = 36e5, DAY = 864e5, WEEK = 7 * DAY;
const TICK_STEPS = [HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, WEEK, 2 * WEEK];
const midnight = t => { const d = new Date(t); d.setHours(0, 0, 0, 0); return +d; };
const weekStart = t => { const d = new Date(midnight(t)); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return +d; };
const monthStart = t => { const d = new Date(midnight(t)); d.setDate(1); return +d; };
const addMonths = (t, n) => { const d = new Date(t); d.setMonth(d.getMonth() + n); return +d; };
const dayLabel = t => new Date(t).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
const monthLabel = t => new Date(t).toLocaleDateString("ru-RU", { month: "short", year: "2-digit" }).replace(" г.", "");
const hourLabel = t => new Date(t).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

// Подписи оси: круглые часы, дни или месяцы — столько, чтобы читались (не больше восьми)
function timeTicks(min, max) {
  const span = Math.max(max - min, HOUR);
  const out = [];
  if (span > 100 * DAY) {
    const step = Math.max(1, Math.round(span / DAY / 30 / 7));
    for (let t = monthStart(min); t <= max; t = addMonths(t, step)) if (t >= min) out.push(t);
    if (!out.length) out.push(min);
    return { ticks: out, fmt: monthLabel };
  }
  const step = TICK_STEPS.find(s => span / s <= 7) || 30 * DAY;
  for (let t = midnight(min); t <= max; t += step) if (t >= min) out.push(t);
  if (!out.length) out.push(min);
  return { ticks: out, fmt: step >= DAY ? dayLabel : t => (new Date(t).getHours() ? hourLabel(t) : dayLabel(t)) };
}
const signed = (v, d = 1) => v == null ? "" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`;

// ---------- дашборд ----------
// Период, за который графики показывают проверки рынка (число дней или «всё время»)
const DASH_PERIODS = [["3", "3 дня"], ["7", "Неделя"], ["30", "Месяц"], ["182", "Полгода"], ["365", "Год"], ["all", "Всё время"]];
let DASH_PERIOD = store.get("rw.dashPeriod", "all"), DASH_S = [], DASH_D = null;
// Только внутри круга (зона A): все блоки пересчитываются по квартирам в круге
let DASH_ZONE = store.get("rw.dashZone", "0") === "1";
// Скрытые квартиры (👎) не попадают в списки квартир дашборда — «Подешевели» и «Самые выгодные».
// Медианы и графики рынка считаются по всем: рынок от того, что квартира вам не понравилась, не меняется
let DASH_NODIS = store.get("rw.dashNoDis", "1") === "1";
// Движение рынка: по умолчанию столбик — день (неделя, месяц на длинном периоде), по кнопке — проверка
let DASH_EV_SCANS = store.get("rw.dashEvScans", "0") === "1";

// Что показывает каждый блок и как его читать (подсказка на заголовке блока)
const DASH_TIP = {
  rooms: z => `Как меняется типичная цена квартиры ${z ? "в красном круге (зона A)" : "в Ялте (зоны A и B)"}.
Каждая точка — одна проверка рынка после сбора. Линия — медиана: половина подходящих квартир дешевле, половина дороже; отдельно для 1-, 2-, 3-комнатных и для всех вместе.
Рост линии — квартиры дорожают. Резкий скачок одной точки обычно значит, что в выборку пришло или из неё ушло несколько дорогих или дешёвых квартир, — смотрите на общий наклон, а не на отдельную точку.`,
  ppm: z => z ? `Медианная цена квадратного метра в красном круге: для всех квартир и отдельно по комнатности.
Цена м² сравнивает квартиры разной площади честнее, чем цена целиком. Обычно у малогабаритных квартир м² дороже.`
    : `Медианная цена квадратного метра: по всей Ялте (A+B), в красном круге (A) и в остальной Ялте (B).
Расстояние между линиями A и B — надбавка за расположение в круге. Цена м² сравнивает квартиры разной площади честнее, чем цена целиком.`,
  events: z => `Что произошло на рынке ${z ? "в круге " : ""}между соседними проверками:
• новые — появились новые объявления;
• снятые — сайт подтвердил, что объявление снято (статус на странице или страница удалена);
• смена цены — продавец изменил цену (чаще снижает).
Много новых — предложение растёт; много снятых — квартиры продаются или уходят с рынка; много смен цены — продавцы торгуются. Первичная загрузка сайта не считается.
Столбик — день целиком (на длинном периоде — неделя или месяц): все проверки за него сложены, и пустые дни видно как пустые. Кнопка «по проверкам» разбирает их обратно по сборам.`,
  count: z => z ? `Сколько подходящих квартир в продаже в красном круге в момент каждой проверки: всего и по комнатности.
Дубли с разных сайтов считаются одной квартирой. Падение — предложение сокращается, рост — новых больше, чем уходит.`
    : `Сколько подходящих квартир в продаже в момент каждой проверки: по всей Ялте (A+B) и в красном круге (A).
Дубли с разных сайтов считаются одной квартирой. Падение — предложение сокращается (квартиры продаются), рост — новых больше, чем уходит.`,
  drops: z => `Квартиры ${z ? "в круге " : ""}, которые сильнее всего подешевели за выбранный период графиков (если объявление появилось позже начала периода — с момента появления).
Снижение цены — признак продавца, готового торговаться: здесь стоит звонить первыми. «на рынке» — сколько дней квартира в базе. Клик — карточка в подборке.`,
  slice: z => `Текущие медианы по подходящим квартирам ${z ? "в красном круге" : "Ялты (A+B)"} для каждой комнатности: сколько квартир в продаже, типичная цена, цена м² и площадь.${z ? "" : "\nПоследние две колонки — цена м² в круге (A) и в остальной Ялте (B) для этой комнатности."}
Строка «Все» — по всем комнатностям вместе.`,
  sources: z => `Сколько объявлений собрано с каждого сайта${z ? " в красном круге" : ""}: активных, подходящих под параметры поиска, сколько объектов ${z ? "в круге" : "Ялты"} есть на этом сайте, сколько снято за всё время, и как прошёл последний сбор.
Одна квартира может быть на нескольких сайтах, поэтому сумма по сайтам больше числа объектов.`,
  deals: z => `Квартиры ${z ? "в круге " : ""}с ценой м² ниже медианы для такой же комнатности в той же зоне (сравнение минимум с тремя похожими).
«к рынку» — на сколько процентов дешевле. Сильно дешевле рынка бывает из-за состояния, этажа, вида или ошибки в объявлении — проверьте карточку. Клик — карточка в подборке.`,
  likes: z => `Квартиры, отмеченные ♥${z ? " (только в круге)" : ""}: сколько ещё в продаже и сколько снято, как изменилась цена с момента появления в базе и насколько цена м² отличается от рынка той же комнатности и зоны.`,
};

const dcHead = (title, meta, tip, tbl, extra = "") => `<div class="dc-head" title="${esc(tip)}"><h3>${title}<i class="info" aria-hidden="true">i</i></h3>
  <span class="meta">${meta}${extra}${tbl ? ` <button class="link sm" data-tbl="${tbl}" title="Показать значения таблицей">таблица</button>` : ""}</span></div>`;
const chartCard = (id, cls, title, meta, tip, extra = "") => `<div class="panel dash-card ${cls}">${dcHead(title, meta, tip, id, extra)}
  <div class="chart-box"><canvas id="${id}"></canvas></div><div class="chart-tbl hidden" id="${id}-tbl"></div></div>`;

// Срез снимка рынка в текущем режиме: вся Ялта (A+B) или только круг (A)
const part = s => DASH_ZONE ? (s && s.a) || {} : { all: s && s.city, rooms: s && s.rooms, sources: s && s.sources, multi: s && s.multi };
const partAll = s => part(s).all || {};
const partRooms = (s, n) => (part(s).rooms || {})[n] || {};
const partEvents = s => (DASH_ZONE ? s.events_a : s.events) || {};

async function renderDashboard() {
  const el = $("#view-dashboard");
  try { DASH_D = await api(`/api/dashboard?days=${DASH_PERIOD === "all" ? 0 : DASH_PERIOD}`); } catch (e) { el.innerHTML = `<div class="empty"><b>Не удалось загрузить сводку</b>${esc(e.message)}</div>`; return; }
  if (view !== "dashboard") return;
  paintDashboard();
}

// Первый запуск: в базе нет ни одного объявления — вместо пустого дашборда краткая инструкция
function startScreen(el) {
  const f = (SETTINGS && SETTINGS.filters) || {};
  const what = [(f.rooms || []).length ? `${f.rooms.join(", ")}-комнатные` : "любые квартиры",
                f.price_min || f.price_max ? `${mln(f.price_min)}–${mln(f.price_max)} млн ₽` : "любая цена",
                f.area_min ? `от ${f.area_min} м²` : null].filter(Boolean).join(", ");
  el.innerHTML = `
    <div class="start">
      <div class="panel start-hero">
        <img src="/static/icon.png" alt="">
        <div>
          <h2>С чего начать</h2>
          <p>RealtyWatch следит за квартирами в Ялте на ЦИАН, Авито и Яндекс Недвижимости: собирает объявления,
            сводит одну и ту же квартиру с разных сайтов в один объект, хранит историю цен и показывает, что изменилось
            с прошлой проверки. База пока пуста — вот три шага до первых цифр.</p>
        </div>
      </div>
      <ol class="start-steps">
        <li class="panel"><span class="n">1</span>
          <h3>Проверьте, что ищем</h3>
          <p>Комнаты, цена, площадь, ремонт и красный круг на карте — в настройках.
            Сейчас: <b>${esc(what)}</b>.</p>
          <button class="btn-sec" data-go="settings">Открыть настройки</button></li>
        <li class="panel"><span class="n">2</span>
          <h3>Запустите сбор</h3>
          <p>Отметьте сайты слева и нажмите «Запустить» — или прямо отсюда. Первый сбор идёт 10–30 минут: программа
            читает выдачу и открывает каждое объявление. Авито иногда показывает проверку — пазл программа проходит
            сама, а если попросят другое, позовёт вас звуком.</p>
          <button class="btn-run" data-run><svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z"/></svg>Запустить сбор</button></li>
        <li class="panel"><span class="n">3</span>
          <h3>Смотрите, что нашлось</h3>
          <p>«Подборка» — карточки с фото и картой, «Таблица» — всё списком, «Карта» — точки по зонам,
            «Изменения» — новое, снятое и смена цены. Клик по карточке открывает историю цен по каждому сайту.</p></li>
      </ol>
      <div class="panel start-more">
        <h3>Что дальше</h3>
        <ul>
          <li><b>Автообновление</b> слева повторяет сбор по таймеру — даже когда окно свёрнуто в Dock.</li>
          <li><b>Графики</b> этого экрана строятся по проверкам: после первого сбора появятся цифры, после второго — линии.</li>
          <li><b>♥ Избранное</b>: отметьте квартиру в подборке — её цена и динамика будут здесь, на виду.</li>
          <li><b>Красный круг</b> на карте — ваш район: по нему считается отдельный срез рынка.</li>
          <li><b>Вся база</b> лежит в папке программы; в настройках есть экспорт и импорт одним файлом.</li>
        </ul>
      </div>
    </div>`;
  el.querySelector("[data-go]").onclick = () => setView("settings");
  el.querySelector("[data-run]").onclick = () => $("#btnRun").click();
}

function paintDashboard() {
  const el = $("#view-dashboard"), d = DASH_D, Z = DASH_ZONE;
  if (!Object.keys(d.sources || {}).length) return startScreen(el);  // в базе ни одного объявления
  const S = d.snapshots, R = d.recent || S, last = R[R.length - 1], prev = R[R.length - 2];
  const cur = partAll(last), pcur = partAll(prev);
  const e24 = Z ? d.events_24h_a : d.events_24h, e7 = Z ? d.events_7d_a : d.events_7d;
  const where = Z ? "в круге" : "в Ялте";
  const kpi = (val, label, tip, delta, cls = "") => `<div class="kpi ${cls}" title="${esc(tip)}"><b>${val}</b><span>${label}</span>${delta ? `<em class="${delta.startsWith("−") ? "neg" : "pos"}">${delta}</em>` : ""}</div>`;
  const delta = (a, b) => (a != null && b ? signed(pct(a, b)) : "");
  const srcRows = ["cian", "avito", "yandex"].map(s => {
    const x = d.sources[s] || {}, r = d.last_runs[s] || {};
    return `<tr title="${esc(`${SRC[s]}: последний сбор ${r.started ? tsLabel(r.started) : "—"} — ${r.message || "ещё не было"}`)}">
      <td><i class="sdot ${s}"></i> ${SRC[s]}</td><td class="num">${(Z ? x.active_a : x.active) ?? 0}</td><td class="num">${(Z ? x.passing_a : x.passing) ?? 0}</td>
      <td class="num">${(part(last).sources || {})[s] ?? "—"}</td><td class="num">${(Z ? x.removed_a : x.removed) ?? 0}</td>
      <td class="last-run"><span class="st-badge ${r.status || ""}" title="${esc(STATE_TIP[r.status] || "")}">${STATE[r.status] || r.status || "—"}</span><small>${r.finished ? tsLabel(r.finished) : ""}</small></td></tr>`;
  }).join("");
  const rz = (z, k) => (last && last.zones && last.zones[z]) ? last.zones[z][k] : null;
  const rr = (n, k) => partRooms(last, n)[k];
  const scope = Z ? "только красный круг (зона A)" : "Ялта (зоны A+B)";
  el.innerHTML = `
    <div class="dash-grid">
      <div class="kpis wide">
        ${kpi(cur.count ?? "—", `объектов ${where}`, `Квартир ${Z ? "в красном круге" : "в зонах A и B"}, подходящих под параметры поиска (дубли с разных сайтов объединены). Изменение — к предыдущей проверке`, delta(cur.count, pcur.count))}
        ${kpi(mln(cur.median_price), "медиана цены, млн ₽", "Медианная цена: половина квартир дешевле, половина дороже. Изменение — к предыдущей проверке", delta(cur.median_price, pcur.median_price))}
        ${kpi(fmt(cur.median_ppm), "медиана, ₽/м²", "Медианная цена квадратного метра общей площади. Изменение — к предыдущей проверке", delta(cur.median_ppm, pcur.median_ppm), "gold")}
        ${kpi(`+${e24.new}`, "новых за сутки", `Новых объявлений ${Z ? "в круге " : ""}за последние 24 часа (за 7 дней: ${e7.new}). Первичная загрузка сайта не считается`)}
        ${kpi(e7.removed, "снято за неделю", `Объявлений ${Z ? "в круге " : ""}, снятых с продажи за 7 дней (снятие подтверждено на странице объявления: статус «снято/закрыто» или страница не найдена)`)}
        ${kpi(`↓${e7.price_down} ↑${e7.price_up}`, "цены за неделю", `Сколько объявлений ${Z ? "в круге " : ""}подешевело (↓) и подорожало (↑) за 7 дней`)}
        ${kpi(part(last).multi ?? "—", "на 2+ сайтах", `Объектов ${Z ? "в круге" : "в Ялте"}, найденных сразу на нескольких сайтах`)}
      </div>

      <div class="dash-period">
        <span class="lbl" title="Какой отрезок истории показывают графики, их таблицы и блок «Подешевели». Показатели вверху и таблицы ниже — всегда на текущий момент">Период графиков</span>
        <div class="seg small">${DASH_PERIODS.map(([v, l]) => `<label><input type="radio" name="dashPeriod" value="${v}" ${v === DASH_PERIOD ? "checked" : ""}><span>${l}</span></label>`).join("")}</div>
        <span class="meta" id="dashPeriodNote"></span>
        <label class="zone-only ${Z ? "on" : ""}" title="Пересчитать весь дашборд только по квартирам внутри красного круга (зона A): показатели, графики, срез, источники, самые выгодные, подешевевшие и избранное. Выключено — вся Ялта (зоны A и B)">
          <input type="checkbox" id="dashZone" ${Z ? "checked" : ""}><span class="sw-mini"></span><span>Только внутри круга</span></label>
        <label class="zone-only ${DASH_NODIS ? "on" : ""}" title="Не показывать в списках «Подешевели» и «Самые выгодные» квартиры, отмеченные «не нравится» (👎). Медианы, графики и срез рынка считаются по всем квартирам: рынок от ваших отметок не меняется">
          <input type="checkbox" id="dashNoDis" ${DASH_NODIS ? "checked" : ""}><span class="sw-mini"></span><span>Исключить дизлайки</span></label>
      </div>

      ${chartCard("chRooms", "span2", "Медианная цена по комнатности", `${scope}, каждая точка — одна проверка рынка`, DASH_TIP.rooms(Z))}
      ${chartCard("chPpm", "", Z ? "Цена м² в круге" : "Цена м² по зонам", Z ? "медиана по комнатности, ₽/м²" : "медиана, ₽/м²", DASH_TIP.ppm(Z))}
      ${chartCard("chEvents", "", "Движение рынка", `<span id="evMeta"></span>`, DASH_TIP.events(Z),
        ` <button class="link sm" id="evMode" title="Переключить вид: сложить все проверки каждого дня в один столбик — или показать каждую проверку отдельно, с её временем под курсором"></button>`)}
      ${chartCard("chCount", "", Z ? "Предложение в круге" : "Предложение", "объектов в продаже", DASH_TIP.count(Z))}
      <div class="panel dash-card drops-card">${dcHead("Подешевели", "сильнее всего за период", DASH_TIP.drops(Z))}<div id="dropsBox"></div></div>

      <div class="panel dash-card span2">${dcHead("Срез рынка сейчас", `медианы по подходящим объектам; ${Z ? "только круг" : "«все» — зоны A+B"}`, DASH_TIP.slice(Z))}
        <table class="grid compact"><thead><tr><th title="Число комнат">Комнаты</th><th class="num" title="Объектов в продаже">Объектов</th>
          <th class="num" title="Медианная цена">Цена, млн</th><th class="num" title="Медианная цена за м²">₽/м²</th><th class="num" title="Медианная общая площадь">м²</th>
          ${Z ? "" : `<th class="num" title="Медиана ₽/м² в красном круге (зона A)">₽/м² круг</th><th class="num" title="Медиана ₽/м² в остальной Ялте (зона B)">₽/м² Ялта</th>`}</tr></thead><tbody>
          ${["1", "2", "3"].map(n => `<tr><td>${n}-комнатные</td><td class="num">${rr(n, "count") ?? "—"}</td><td class="num">${mln(rr(n, "median_price"))}</td>
            <td class="num">${fmt(rr(n, "median_ppm"))}</td><td class="num">${rr(n, "median_area") ?? "—"}</td>
            ${Z ? "" : `<td class="num">${fmt(roomZonePpm(n, "A"))}</td><td class="num">${fmt(roomZonePpm(n, "B"))}</td>`}</tr>`).join("")}
          <tr class="total"><td>Все</td><td class="num">${cur.count ?? "—"}</td><td class="num">${mln(cur.median_price)}</td><td class="num">${fmt(cur.median_ppm)}</td>
            <td class="num">${cur.median_area ?? "—"}</td>${Z ? "" : `<td class="num">${fmt(rz("A", "median_ppm"))}</td><td class="num">${fmt(rz("B", "median_ppm"))}</td>`}</tr>
        </tbody></table></div>
      <div class="panel dash-card">${dcHead("Источники", `объявлений по сайтам${Z ? " в круге" : ""}`, DASH_TIP.sources(Z))}
        <table class="grid compact"><thead><tr><th>Сайт</th><th class="num" title="Активных объявлений">Активно</th><th class="num" title="Подходят под параметры поиска">Подходят</th>
          <th class="num" title="${Z ? "Объектов в круге" : "Объектов в Ялте"}, у которых есть объявление на этом сайте">${Z ? "В круге" : "В Ялте"}</th><th class="num" title="Снято с продажи за всё время">Снято</th><th>Последний сбор</th></tr></thead>
          <tbody>${srcRows}</tbody></table></div>

      <div class="panel dash-card span3">${dcHead("Самые выгодные сейчас", `цена м² ниже медианы для той же комнатности и зоны${Z ? ", только круг" : ""}`, DASH_TIP.deals(Z))}
        ${dealsTable()}</div>

      <div class="panel dash-card span3 likes-card">${dcHead("♥ Избранное", `отмеченные квартиры: цена, динамика, сравнение с рынком${Z ? " — только в круге" : ""}`, DASH_TIP.likes(Z))}
        ${likesBlock()}</div>
    </div>`;
  el.querySelectorAll("[data-open]").forEach(a => a.onclick = () => openObject(a.dataset.open));
  el.querySelectorAll("[data-card]").forEach(a => a.onclick = () => goToCard(a.dataset.card));
  el.querySelectorAll("[data-tbl]").forEach(b => b.onclick = () => {
    const t = $(`#${b.dataset.tbl}-tbl`), on = t.classList.toggle("hidden");
    b.textContent = on ? "таблица" : "график";
    $(`#${b.dataset.tbl}`).parentNode.classList.toggle("hidden", !on);
  });
  el.querySelectorAll("input[name=dashPeriod]").forEach(r => r.onchange = async () => {
    DASH_PERIOD = r.value;
    store.set("rw.dashPeriod", r.value);
    const y = $(".main").scrollTop;
    await renderDashboard();  // сервер отдаёт проверки только за период (не больше 500 точек)
    $(".main").scrollTop = y;
  });
  $("#evMode").onclick = () => {
    DASH_EV_SCANS = !DASH_EV_SCANS;
    store.set("rw.dashEvScans", DASH_EV_SCANS ? "1" : "0");
    drawDashCharts();
  };
  $("#dashZone").onchange = () => {
    DASH_ZONE = $("#dashZone").checked;
    store.set("rw.dashZone", DASH_ZONE ? "1" : "0");
    const y = $(".main").scrollTop;
    paintDashboard();
    $(".main").scrollTop = y;
  };
  $("#dashNoDis").onchange = () => {
    DASH_NODIS = $("#dashNoDis").checked;
    store.set("rw.dashNoDis", DASH_NODIS ? "1" : "0");
    const y = $(".main").scrollTop;
    paintDashboard();
    $(".main").scrollTop = y;
  };
  DASH_S = S;
  drawDashCharts();
}

function periodStart() {
  return DASH_PERIOD === "all" ? 0 : Date.now() - +DASH_PERIOD * 864e5;
}

function drawDashCharts() {
  const from = periodStart();
  const S = DASH_S.filter(s => new Date(s.ts).getTime() >= from);
  const name = (DASH_PERIODS.find(p => p[0] === DASH_PERIOD) || [, ""])[1].toLowerCase();
  const note = $("#dashPeriodNote");
  const recent = (DASH_D && DASH_D.recent) || [];
  const checks = (DASH_D && DASH_D.checks) || S.length;
  if (note) note.textContent = S.length ? `${checks} ${plural(checks, "проверка", "проверки", "проверок")}${checks > S.length ? ` (на графике ${S.length} точек)` : ""} · с ${tsLabel(S[0].ts)}`
    : recent.length ? `за ${DASH_PERIOD === "7" ? "неделю" : DASH_PERIOD === "30" ? "месяц" : name} проверок не было — последняя ${tsLabel(recent[recent.length - 1].ts)}`
    : "проверок ещё не было";
  // Нет проверок за период — вместо пустых осей пояснение
  const empty = !S.length ? (recent.length ? "За выбранный период проверок не было — выберите период длиннее" : "Графики появятся после первого сбора: каждая точка — одна проверка рынка") : "";
  for (const id of ["chRooms", "chPpm", "chEvents", "chCount"]) {
    const box = document.getElementById(id) && document.getElementById(id).parentNode;
    if (!box) continue;
    box.classList.toggle("no-data", !!empty);
    box.dataset.empty = empty;
    if (empty && charts[id]) { charts[id].destroy(); delete charts[id]; }
  }
  const labels = S.map(s => tsLabel(s.ts));
  const xs = S.map(s => new Date(s.ts).getTime());  // настоящее время каждой проверки — ось времени
  const byRooms = key => [
    { label: "Все", data: S.map(s => partAll(s)[key] ?? null) },
    ...["1", "2", "3"].map(n => ({ label: `${n}-комн.`, data: S.map(s => partRooms(s, n)[key] ?? null) })),
  ];
  const box = $("#dropsBox");
  if (box) {
    box.innerHTML = dropsBlock();
    box.querySelectorAll("[data-card]").forEach(a => a.onclick = () => goToCard(a.dataset.card));
  }
  if (empty) return;
  lineChart("chRooms", labels, byRooms("median_price"), v => `${mln(v)} млн`, xs);
  if (DASH_ZONE) {
    lineChart("chPpm", labels, byRooms("median_ppm"), v => fmt(v), xs);
    lineChart("chCount", labels, byRooms("count").map((x, i) => i ? x : { ...x, label: "Круг (A)" }), v => fmt(v), xs);
  } else {
    lineChart("chPpm", labels, [
      { label: "Ялта A+B", data: S.map(s => s.city.median_ppm) },
      { label: "Круг (A)", data: S.map(s => s.zones.A.median_ppm) },
      { label: "Ялта (B)", data: S.map(s => s.zones.B.median_ppm) },
    ], v => fmt(v), xs);
    lineChart("chCount", labels, [
      { label: "Ялта A+B", data: S.map(s => s.city.count) },
      { label: "Круг (A)", data: S.map(s => s.zones.A.count) },
    ], v => fmt(v), xs);
  }
  drawEvents(S, Math.max(from, xs[0]), Date.now());
}

// Движение рынка. Столбик — календарный день (на длинном периоде неделя или месяц): за день бывает
// десяток проверок, и каждая отдельным столбиком ничего не говорит, а дни без проверок в таком ряду
// не видны вовсе. Кнопка в шапке карточки возвращает подробный вид — столбик на проверку.
const EV_UNIT = { day: ["дням", "День"], week: ["неделям", "Неделя"], month: ["месяцам", "Месяц"] };

function evBuckets(S, from, to) {
  const span = to - from;
  const kind = span <= 45 * DAY ? "day" : span <= 220 * DAY ? "week" : "month";
  const start = kind === "day" ? midnight : kind === "week" ? weekStart : monthStart;
  const next = kind === "month" ? (t => addMonths(t, 1)) : (t => t + (kind === "week" ? WEEK : DAY));
  const sum = new Map();
  for (const s of S) {
    const k = start(new Date(s.ts).getTime()), e = partEvents(s);
    const b = sum.get(k) || { new: 0, removed: 0, price: 0, checks: 0 };
    b.new += e.new || 0;
    b.removed += e.removed || 0;
    b.price += (e.price_down || 0) + (e.price_up || 0);
    b.checks++;
    sum.set(k, b);
  }
  const rows = [];
  for (let t = start(from); t <= to; t = next(t)) rows.push({ t, end: next(t), ...(sum.get(t) || { new: 0, removed: 0, price: 0, checks: 0 }) });
  return { kind, rows };
}

function drawEvents(S, from, to) {
  const meta = $("#evMeta"), btn = $("#evMode");
  if (btn) btn.textContent = DASH_EV_SCANS ? "по дням" : "по проверкам";
  const series = take => [
    { label: "новые", data: take("new") },
    { label: "снятые", data: take("removed") },
    { label: "смена цены", data: take("price") },
  ];
  if (DASH_EV_SCANS) {
    if (meta) meta.textContent = "событий между проверками";
    barChart("chEvents", S.map(s => tsLabel(s.ts)), series(k => S.map(s => {
      const e = partEvents(s);
      return k === "price" ? (e.price_down || 0) + (e.price_up || 0) : e[k] || 0;
    })), v => fmt(v));
    return;
  }
  const { kind, rows } = evBuckets(S, from, to);
  const unit = EV_UNIT[kind];
  if (meta) meta.textContent = `событий по ${unit[0]}`;
  const axis = r => kind === "month" ? monthLabel(r.t) : dayLabel(r.t);
  const full = r => kind === "week" ? `${dayLabel(r.t)} — ${dayLabel(r.end - DAY)}` : axis(r);
  barChart("chEvents", rows.map(axis), series(k => rows.map(r => r[k])), v => fmt(v), {
    head: unit[1],
    titles: rows.map(r => `${full(r)} · ${r.checks ? `${r.checks} ${plural(r.checks, "проверка", "проверки", "проверок")}` : "проверок не было"}`),
  });
}

// Подешевели за период графиков: цена на начало периода (или при появлении) → сейчас, по каждому сайту объекта
function dropsBlock() {
  const from = periodStart(), now = Date.now();
  const rows = [];
  for (const o of scopeObjs()) {
    let best = null;
    for (const i of o.items) {
      if (!i.active || !i.price) continue;
      const h = (i.history || []).filter(x => x[1]);
      if (!h.length) continue;
      const before = h.filter(x => new Date(x[0]).getTime() <= from);
      const p0 = before.length ? before[before.length - 1][1] : h[0][1];
      const d = pct(i.price, p0);
      if (d < 0 && (!best || d < best.d)) best = { d, p0, p1: i.price, src: i.source, relisted: false };
    }
    if (o.relisted && o.relisted.diff < 0 && new Date(o.relisted.newFirst || 0).getTime() >= from) {
      if (!best || o.relisted.pct < best.d)
        best = { d: o.relisted.pct, p0: o.relisted.old, p1: o.relisted.now, src: o.relisted.oldSrc, relisted: true };
    }
    if (best) rows.push({ o, ...best });
  }
  rows.sort((a, b) => a.d - b.d);
  if (!rows.length) return `<div class="empty small"><b>Снижений цены пока нет</b>За выбранный период ни одна подходящая квартира${DASH_ZONE ? " в круге" : ""} не подешевела. Как только продавцы начнут снижать цены, самые сильные снижения появятся здесь — с этими продавцами проще торговаться.</div>`;
  const days = o => Math.max(0, Math.round((now - new Date(o.first_seen).getTime()) / 864e5));
  return `<div class="drops">${rows.slice(0, 8).map(({ o, d, p0, p1, src, relisted }) => `<button class="drop" data-card="${esc(o.id)}" title="${esc(`${o.address || ""}\n${relisted ? `Перевыставлено дешевле (ранее ${SRC[src]}): ` : `${SRC[src]}: `}${fmt(p0)} → ${fmt(p1)} ₽ (${signed(d)}). Клик — карточка в подборке`)}">
      <span class="d-main"><b>${o.rooms === 0 ? "студия" : `${o.rooms}-к`}, ${o.area} м²</b> <span class="meta">${esc(shortAddr(o.address))}</span></span>
      <span class="d-price">${mln(p0)} → <b>${mln(p1)}</b> млн</span>
      <span class="d-pct">${relisted ? "↻ " : ""}${signed(d)}</span>
      <span class="d-age">${days(o)} дн.</span></button>`).join("")}
    ${rows.length > 8 ? `<div class="meta d-more">ещё ${rows.length - 8} ${plural(rows.length - 8, "квартира", "квартиры", "квартир")} подешевели</div>` : ""}</div>`;
}

const shortAddr = a => (a || "").replace(/^(Россия,\s*)?(Республика Крым,\s*)?(городской округ Ялта,\s*)?(Ялта,\s*)?/i, "").slice(0, 40);

// Квартиры для списков дашборда: вся Ялта (A+B) или только круг (A); скрытые (👎) — по галочке
function scopeObjs() {
  return cityObjs().filter(o => (!DASH_ZONE || o.zone === "A") && !(DASH_NODIS && o.disliked));
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
  const rows = scopeObjs().map(o => ({ o, m: marketPpm(o) })).filter(x => x.m).map(x => ({ ...x, d: pct(x.o.price_m2, x.m) }))
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
  const L = OBJS.filter(o => o.liked && (!DASH_ZONE || o.zone === "A"));
  if (!L.length) return `<div class="empty small"><b>${DASH_ZONE && OBJS.some(o => o.liked) ? "В круге избранных нет" : "Пока пусто"}</b>${DASH_ZONE && OBJS.some(o => o.liked) ? "Отмеченные квартиры находятся вне красного круга — выключите «Только внутри круга», чтобы их увидеть" : "Отмечайте квартиры сердечком на карточке — здесь появится их сводка"}</div>`;
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
// Период экрана «Изменения»: последнее обновление (последний сбор каждого сайта), часы или дни
const CH_PERIODS = [
  ["last", "последнее обновление", "Что нашёл последний законченный сбор каждого сайта"],
  ["6h", "6 часов", "Изменения за последние 6 часов"], ["1", "сутки", "Изменения за последние 24 часа"],
  ["3", "3 дня", "Изменения за 3 дня"], ["7", "неделя", "Изменения за 7 дней"], ["30", "месяц", "Изменения за 30 дней"],
];
let CH_PERIOD = store.get("rw.chPeriod", "last");
if (!CH_PERIODS.some(p => p[0] === CH_PERIOD)) CH_PERIOD = "last";
const chQuery = p => p === "last" ? "last=1" : p.endsWith("h") ? `hours=${parseFloat(p)}` : `days=${p}`;

// Открыть экран, в т.ч. из уведомления системы: "changes:last" — «Изменения» за последнее обновление
function openView(v) {
  const [name, arg] = String(v).split(":");
  if (name === "changes" && arg) { CH_PERIOD = arg; store.set("rw.chPeriod", arg); }
  if (name === "cards" && arg === "hidden") {
    // уведомление «в скрытые квартиры добавились объявления» — сразу «Скрытые»
    const h = $("#fHidden");
    if (!h.checked) { h.checked = true; h.dispatchEvent(new Event("input", { bubbles: true })); }
  }
  setView(name);
}

let CH_DATA = null;

async function renderChanges() {
  const reqPeriod = CH_PERIOD;
  const [ev, lastRuns] = await Promise.all([api(`/api/changes?${chQuery(reqPeriod)}`), reqPeriod === "last" ? api("/api/last_update") : null]);
  if (CH_PERIOD !== reqPeriod) return;
  CH_DATA = { period: reqPeriod, ev, lastRuns };
  if (view !== "changes") return;
  paintChanges();
}

function paintChanges() {
  const el = $("#view-changes");
  if (!CH_DATA) return;
  const { ev, lastRuns } = CH_DATA;
  const kinds = JSON.parse(store.get("rw.chKinds", '["new","down","up","removed","returned"]'));
  const kindOf = e => e.kind === "price" ? ((e.new_value || 0) < (e.old_value || 0) ? "down" : "up") : e.kind;
  const f = readFilters();
  const objById = new Map(OBJS.map(o => [o.id, o]));
  const remObjs = new Set();
  for (const e of ev) {
    if (e.kind === "removed") remObjs.add(e.object_id || `${e.source}:${e.source_id}`);
  }
  const counts = { new: 0, down: 0, up: 0, removed: 0, returned: 0 };
  const groups = new Map();
  const zoneMap = new Map();
  for (const e of ev) {
    const k = kindOf(e);
    const oid = e.object_id || `${e.source}:${e.source_id}`;
    const o = objById.get(oid);
    const allowRemoved = k === "removed" || (kinds.includes("removed") && remObjs.has(oid));
    if (matchObject(o, f, { ignoreZone: true, allowRemoved })) {
      if (f.zones.includes(o.zone) && k in counts) counts[k]++;
      if (kinds.includes(k) && o && !zoneMap.has(oid)) zoneMap.set(oid, o);
    }
    if (!kinds.includes(k)) continue;
    if (!groups.has(oid)) groups.set(oid, []);
    groups.get(oid).push({ ...e, k });
  }
  const tiles = [];
  for (const [oid, list] of groups) {
    const o = objById.get(oid);
    if (!matchObject(o, f, { allowRemoved: list.some(e => e.k === "removed") })) continue;
    const lastTs = list.reduce((m, e) => (e.ts > m ? e.ts : m), "");
    tiles.push({ oid, list, o, lastTs });
  }
  if (f.sort === "new") {
    tiles.sort((a, b) => (b.lastTs > a.lastTs ? 1 : b.lastTs < a.lastTs ? -1 : 0));
  } else {
    const dir = f.sort.startsWith("-") ? -1 : 1;
    tiles.sort((a, b) => ((sortKey(a.o, f.sort) > sortKey(b.o, f.sort)) ? 1 : (sortKey(a.o, f.sort) < sortKey(b.o, f.sort)) ? -1 : 0) * dir);
  }
  if (f.hiddenOnly) tiles.sort((a, b) => (b.o.fresh.length > 0) - (a.o.fresh.length > 0));
  renderHeader([], [...zoneMap.values()]);
  fitToolbar();
  const emptyMsg = ev.length
    ? `<div class="empty"><b>По выбранным фильтрам изменений нет</b>Всего за период событий: ${ev.length} — измените фильтры сверху (зону, комнаты, статус, цену или типы изменений).</div>`
    : `<div class="empty"><b>Изменений нет</b>${CH_PERIOD === "last" ? "Последнее обновление ничего не изменило" : "За выбранный период"} по выбранным типам. Первичная загрузка сайта изменениями не считается.</div>${aboutTab("changes")}`;
  el.innerHTML = `<div class="ch-bar panel">
      <div class="seg small">${CH_PERIODS.map(([v, l, tip]) => `<label title="${tip}"><input type="radio" name="chPeriod" value="${v}" ${v === CH_PERIOD ? "checked" : ""}><span>${l}</span></label>`).join("")}</div>
      <div class="seg small">${Object.entries(KIND).map(([k, v]) => `<label title="${v.tip}"><input type="checkbox" name="chKind" value="${k}" ${kinds.includes(k) ? "checked" : ""}><span><i class="kdot ${v.cls}"></i>${v.label} <em>${counts[k] || 0}</em></span></label>`).join("")}</div>
      <span class="meta">${tiles.length} ${plural(tiles.length, "объект", "объекта", "объектов")} с изменениями</span>
      ${lastRuns ? `<div class="ch-last" title="Последний законченный сбор каждого сайта: изменения выше найдены именно им">${lastRuns.length
        ? "Последнее обновление: " + lastRuns.map(r => `<span><i class="sdot ${r.source}"></i>${SRC[r.source]} ${dtt(r.finished)}${r.status !== "done" ? ` <em class="st-badge ${r.status}">${STATE[r.status] || r.status}</em>` : ""}</span>`).join("")
        : "Сборов ещё не было"}</div>` : ""}
    </div>
    ${tiles.length ? `<div class="ch-grid">${tiles.map(changeTile).join("")}</div>` : emptyMsg}`;
  el.querySelectorAll("input[name=chPeriod]").forEach(r => r.onchange = () => { CH_PERIOD = r.value; store.set("rw.chPeriod", r.value); renderChanges(); });
  el.querySelectorAll("input[name=chKind]").forEach(c => c.onchange = () => {
    store.set("rw.chKinds", JSON.stringify([...el.querySelectorAll("input[name=chKind]:checked")].map(x => x.value)));
    paintChanges();
  });
  el.querySelectorAll("[data-card]").forEach(b => b.onclick = () => goToCard(b.dataset.card));
  el.querySelectorAll("[data-open]").forEach(b => b.onclick = () => openObject(b.dataset.open));
}

function eventText(e, o) {
  if (e.k === "down" || e.k === "up") {
    const d = e.new_value - e.old_value;
    return `цена ${mln(e.old_value)} → <b>${mln(e.new_value)} млн</b> <span class="${d < 0 ? "good" : "bad"}">(${d > 0 ? "+" : "−"}${fmt(Math.abs(d))} ₽, ${signed(pct(e.new_value, e.old_value))})</span>`;
  }
  if (e.k === "new") {
    let tail = "";
    const it = o && o.items.find(i => i.source === e.source && i.source_id === e.source_id);
    if (o && o.relisted && (!it || it.active) && e.new_value && Math.abs(e.new_value - o.relisted.old) >= 50000) {
      const d = e.new_value - o.relisted.old;
      tail = ` · <span class="${d < 0 ? "good" : "bad"}" title="${esc(`Ранее эта же квартира продавалась на ${SRC[o.relisted.oldSrc]} за ${fmt(o.relisted.old)} ₽`)}">↻ перевыставлено (${d > 0 ? "+" : "−"}${fmt(Math.abs(d))} ₽ к прежним ${mln(o.relisted.old)} млн)</span>`;
    }
    return `новое объявление, ${fmt(e.new_value)} ₽${tail}`;
  }
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
      <ul class="ch-list">${t.list.map(e => {
        const it = o && o.items.find(i => i.source === e.source && i.source_id === e.source_id);
        const link = it && it.off_site
          ? `<span class="off-link" title="${esc(offTip(it, o.active))}">${ICON.noext}</span>`
          : `<a href="${esc(e.url)}" target="_blank" rel="noopener" title="Открыть объявление на ${SRC[e.source]}">${ICON.ext}</a>`;
        return `<li title="${esc(KIND[e.k].tip + " — " + tsLabel(e.ts))}"><span class="kbadge ${KIND[e.k].cls}">${KIND[e.k].label}</span>
        <span class="ch-when">${tsLabel(e.ts)}</span><i class="sdot ${e.source}" title="${SRC[e.source]}"></i> <span>${SRC[e.source]}: ${eventText(e, o)}</span>
        ${link}</li>`;
      }).join("")}</ul>
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
  store.set("rw.chSeen", localIso(Date.now() + 60000));
  $("#chBadge").classList.add("hidden");
}

// ---------- звук: колокольчик у кнопки «Запустить» ----------
const SOUND_TIP = {
  on: "Звук включён: когда сайт попросит пройти капчу, программа подаст сигнал и будет повторять его каждые 30 секунд, пока капча на экране; уведомления системы — со звуком.\nНажмите, чтобы выключить все звуки (например, на ночь)",
  off: "Без звука: ни сигналов капчи, ни звука уведомлений системы — чтобы не мешать спать. Капча по-прежнему появляется в окне браузера.\nНажмите, чтобы включить звук",
};
async function initSound() {
  const b = $("#btnSound");
  const paint = m => {
    b.classList.toggle("muted", m);
    b.innerHTML = m ? ICON.bellOff : ICON.bell;
    b.title = m ? SOUND_TIP.off : SOUND_TIP.on;
    b.dataset.muted = m ? "1" : "0";
  };
  try { paint((await api("/api/sound")).muted); } catch (e) { paint(false); }
  b.onclick = async () => {
    const r = await post("/api/sound", { muted: b.dataset.muted !== "1" });
    paint(r.muted);
    toast("ok", r.muted ? "Звук выключен" : "Звук включён", r.muted ? "Сигналов капчи и звука уведомлений не будет" : "При капче прозвучит сигнал");
  };
}

// ---------- окно объекта: история цен и личная заметка ----------
async function saveObjectNote(o, rawText) {
  const btn = $("#noteSave"), del = $("#noteDel");
  if (btn) btn.disabled = true;
  if (del) del.disabled = true;
  try {
    const r = await post("/api/note", { keys: o.items.map(key), text: rawText });
    o.note = r.text;
    o.note_ts = r.ts;
    o.items.forEach(i => { i.note = r.text; i.note_ts = r.ts; });
    o.text = [...o.items.map(i => `${i.title} ${i.address} ${i.description}`), o.note].join(" ").toLowerCase();
    const ta = $("#objNote");
    if (ta) ta.value = r.text;
    const st = $("#noteStatus");
    if (st) st.textContent = r.ts ? `сохранено ${tsLabel(r.ts)}` : "хранится только у вас, в базе";
    if (del) del.classList.toggle("hidden", !r.text);
    if (["cards", "table", "map", "changes"].includes(view)) render();
    toast("ok", r.text ? "Заметка сохранена" : "Заметка удалена", r.text ? "Она видна на карточке и в выгрузке избранного" : "");
  } catch (e) {
    toast("warn", "Не удалось сохранить заметку", e.message);
  } finally {
    if (btn) btn.disabled = false;
    if (del) del.disabled = false;
  }
}

function openObject(id, focusNote = false) {
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
        <div class="m-note">
          <label for="objNote">Личная заметка</label>
          <textarea id="objNote" rows="2" maxlength="2000" placeholder="Впечатления от квартиры, контакты, итог звонка, условия торга…">${esc(o.note || "")}</textarea>
          <div class="m-note-acts">
            <span class="meta" id="noteStatus">${o.note_ts ? `сохранено ${tsLabel(o.note_ts)}` : "хранится только у вас, в базе"}</span>
            <span class="m-note-btns">
              <button type="button" class="btn-sec sm ${o.note ? "" : "hidden"}" id="noteDel">Очистить</button>
              <button type="button" class="btn-pri sm" id="noteSave">${ICON.ok}Сохранить заметку</button>
            </span>
          </div>
        </div>
      </div>
    </div>
    <div class="m-chart-head"><h3>История цен по сайтам</h3><span class="meta">${changed ? "ступенька — изменение цены" : "цена не менялась с момента появления в базе"}</span>
      <button class="link sm" id="chObjScale"></button></div>
    <div class="chart-box tall"><canvas id="chObj"></canvas></div>
    <div class="m-hist">${priceTable(o)}</div>
    ${desc ? `<details class="m-desc"><summary>Описание</summary><p>${esc(desc)}</p></details>` : ""}`;
  $("#modal").classList.remove("hidden");
  drawObjectChart(o);
  const ta = $("#objNote");
  $("#noteSave").onclick = () => saveObjectNote(o, ta.value);
  $("#noteDel").onclick = () => saveObjectNote(o, "");
  ta.onkeydown = e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveObjectNote(o, ta.value); } };
  if (focusNote) requestAnimationFrame(() => ta.focus());
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

// Шкала графика цен: от нуля (по умолчанию) — изменение цены видно в настоящем масштабе: снижение на 300 тысяч
// при цене 10 млн выглядит как 3%, а не как обрыв. «По ценам» — ось от меньшей цены до большей, как раньше:
// видна каждая ступенька. Переключается кликом по графику или кнопкой над ним; выбор запоминается
let OBJ_ABS = store.get("rw.objChartAbs", "1") !== "0";
const SCALE_TIP = {
  abs: ["шкала от нуля", "Шкала от нуля: изменения цены видны в настоящем масштабе. Клик по графику — шкала по ценам, крупнее"],
  rel: ["шкала по ценам", "Шкала от меньшей цены до большей: видна каждая ступенька, но изменение выглядит крупнее, чем есть. Клик по графику — шкала от нуля"],
};

function drawObjectChart(o) {
  if (charts.chObj) charts.chObj.destroy();
  const btn = $("#chObjScale"), [lbl, tip] = SCALE_TIP[OBJ_ABS ? "abs" : "rel"];
  if (btn) {
    btn.textContent = lbl;
    btn.title = tip;
    btn.onclick = () => { OBJ_ABS = !OBJ_ABS; store.set("rw.objChartAbs", OBJ_ABS ? "1" : "0"); drawObjectChart(o); };
  }
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
  if (OBJ_ABS) opt.scales.y.min = 0;
  opt.interaction = { mode: "nearest", intersect: false };
  opt.plugins.tooltip.callbacks = { title: c => tsLabel(c[0].parsed.x), label: c => ` ${c.dataset.label}: ${fmt(c.parsed.y)} ₽` };
  // клик по полю графика (не по легенде: она прячет линию сайта) переключает шкалу
  const inPlot = (e, ch) => { const a = ch.chartArea; return e.x >= a.left && e.x <= a.right && e.y >= a.top && e.y <= a.bottom; };
  // пересоздать график — уже после того, как Chart.js закончит разбирать этот клик: внутри его обработчика
  // уничтоженный график роняет плагины («reading 'handleEvent'»)
  opt.onClick = (e, els, ch) => { if (btn && inPlot(e, ch)) setTimeout(() => btn.click(), 0); };
  opt.onHover = (e, els, ch) => { ch.canvas.style.cursor = inPlot(e, ch) ? "pointer" : "default"; };
  charts.chObj = new Chart($("#chObj"), { type: "line", data: { datasets: ds }, options: opt });
}

function closeModal() {
  $("#modal").classList.add("hidden");
  if (charts.chObj) { charts.chObj.destroy(); delete charts.chObj; }
}
$("#modalX").onclick = closeModal;
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  if (!$("#modal").classList.contains("hidden")) closeModal();
  else if (ZE) endZoneEdit();  // Esc — отменить изменение круга
});
$("#modalBody").addEventListener("click", e => {
  const h = e.target.closest(".heart");
  if (h) toggleLike(h.closest("[data-id]")?.dataset.id, h);
});

// ---------- автообновление ----------
let AUTO = null;
const DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
async function initAuto() {
  try { AUTO = await api("/api/schedule"); } catch (e) { return; }
  const sel = $("#autoHours"), custom = $("#autoCustom"), mode = $("#autoMode");
  const preset = ["1", "3", "6", "8", "12"].includes(String(AUTO.hours));
  sel.value = preset ? String(AUTO.hours) : "custom";
  custom.value = AUTO.hours;
  custom.classList.toggle("hidden", preset);
  $("#autoOn").checked = AUTO.enabled;
  mode.value = AUTO.mode === "weekly" ? "weekly" : "timer";
  $$(".src-run").forEach(c => { c.checked = AUTO.sources.includes(c.value); });
  const save = async patch => { AUTO = await post("/api/schedule", patch); tick(); };
  $("#autoOn").onchange = () => save({ enabled: $("#autoOn").checked, hours: currentHours(), sources: $$(".src-run:checked").map(i => i.value) });
  sel.onchange = () => {
    custom.classList.toggle("hidden", sel.value !== "custom");
    if (sel.value === "custom") { custom.focus(); return; }
    save({ hours: +sel.value });
  };
  custom.onchange = () => { if (+custom.value > 0) save({ hours: +custom.value }); };
  mode.onchange = async () => {
    await save({ mode: mode.value });
    if (mode.value === "weekly" && !(AUTO.slots || []).length) schedBox();  // расписания ещё нет — сразу его и зададим
  };
  $("#autoPlan").onclick = schedBox;
  $("#sources").addEventListener("change", () => save({ sources: $$(".src-run:checked").map(i => i.value) }));
  setInterval(tick, 1000);
  // пропуски запуска и идущие сборы меняются со временем — обновляем расписание раз в 20 с
  setInterval(() => api("/api/schedule").then(a => { AUTO = a; }).catch(() => {}), 20000);
  tick();
}
function currentHours() { return $("#autoHours").value === "custom" ? +$("#autoCustom").value || 6 : +$("#autoHours").value; }

// «пн–пт», «сб, вс», «каждый день» — подряд идущие дни сворачиваются в промежуток
function daysLabel(days) {
  const s = [...new Set(days)].sort((a, b) => a - b);
  if (s.length === 7) return "каждый день";
  const parts = [];
  for (let i = 0; i < s.length;) {
    let j = i;
    while (j + 1 < s.length && s[j + 1] === s[j] + 1) j++;
    parts.push(j - i >= 2 ? `${DAYS[s[i] - 1]}–${DAYS[s[j] - 1]}` : s.slice(i, j + 1).map(x => DAYS[x - 1]).join(", "));
    i = j + 1;
  }
  return parts.join(", ");
}
const slotsLabel = slots => (slots || []).map(s => `${daysLabel(s.days)} в ${s.time}`).join(" · ");

// Окно расписания: время запуска + дни недели у каждого пункта, плюс догонять ли пропущенное
function schedBox() {
  const slots = JSON.parse(JSON.stringify(AUTO.slots || []));
  // время — двумя списками: у input[type=time] формат берётся из языка браузера, и в окне
  // появлялось «09:00 AM», которое не влезало в поле
  const opts = (n, step, v) => {
    const vals = Array.from({ length: Math.ceil(n / step) }, (_, i) => i * step);
    if (!vals.includes(+v)) vals.push(+v);   // время из файла могло быть не кратно шагу
    return vals.sort((a, b) => a - b).map(x => `<option value="${String(x).padStart(2, "0")}"${+v === x ? " selected" : ""}>${String(x).padStart(2, "0")}</option>`).join("");
  };
  const row = (s, i) => `<div class="sch-slot" data-i="${i}">
    <span class="sch-time" title="Время запуска, местное, 24 часа">
      <select class="sch-h">${opts(24, 1, s.time.slice(0, 2))}</select><i>:</i><select class="sch-m">${opts(60, 5, s.time.slice(3))}</select></span>
    <span class="sch-days">${DAYS.map((d, n) => `<button class="sch-day ${s.days.includes(n + 1) ? "on" : ""}" data-d="${n + 1}" title="${d}">${d}</button>`).join("")}</span>
    <button class="icon-btn" data-del title="Убрать это время">${ICON.trash}</button></div>`;
  $("#modalBody").innerHTML = `<div class="sched"><h3>Расписание автообновления</h3>
    <p class="meta">Сбор выбранных сайтов запускается в это время в отмеченные дни. Время местное, по часам компьютера.
      Сайт, который в этот момент ещё собирает с прошлого раза, пропустит запуск — остальные запустятся.</p>
    <div class="sch-list" id="schList">${slots.map(row).join("")}</div>
    <div class="sch-tools"><button class="chip" id="schAdd">+ Добавить время</button><span class="sch-note" id="schNote"></span></div>
    <label class="sch-catch"><input type="checkbox" id="schCatch" ${AUTO.catch_up ? "checked" : ""}>
      <span>Догонять пропущенный запуск при старте программы
        <i>Программа не работала в назначенное время — тогда при следующем её запуске сбор начнётся сам, через полторы минуты.
          Выключено: пропущенное время просто пропускается, программа ждёт следующего по расписанию.</i></span></label>
    <div class="confirm-acts"><button class="btn-sec" data-c="no">Отмена</button><button class="btn-pri" id="schSave">Сохранить</button></div></div>`;
  $("#modal").classList.remove("hidden");
  const list = $("#schList"), note = $("#schNote"), save = $("#schSave");
  const read = () => $$(".sch-slot", list).map(el => ({
    time: `${el.querySelector(".sch-h").value}:${el.querySelector(".sch-m").value}`,
    days: $$(".sch-day.on", el).map(b => +b.dataset.d),
  }));
  const upd = () => {
    const cur = read();
    const bad = cur.some(s => !s.days.length || !s.time);
    $$(".sch-slot", list).forEach((el, i) => el.classList.toggle("bad", !cur[i].days.length || !cur[i].time));
    note.textContent = bad ? "у каждого времени нужен хотя бы один день недели" : cur.length ? slotsLabel(cur) : "расписание пустое — автообновление ничего не запустит";
    note.classList.toggle("warn", bad);
    save.disabled = bad;
  };
  list.onclick = e => {
    const day = e.target.closest(".sch-day"), del = e.target.closest("[data-del]");
    if (day) { day.classList.toggle("on"); upd(); }
    if (del) { del.closest(".sch-slot").remove(); upd(); }
  };
  list.onchange = upd;
  $("#schAdd").onclick = () => {
    const cur = read(), last = cur[cur.length - 1];
    const h = last ? (+last.time.slice(0, 2) + 3) % 24 : 9;   // новое время — через три часа от последнего
    list.insertAdjacentHTML("beforeend", row({ time: `${String(h).padStart(2, "0")}:00`, days: [1, 2, 3, 4, 5, 6, 7] }, cur.length));
    upd();
  };
  $('#modalBody [data-c="no"]').onclick = closeModal;
  save.onclick = async () => {
    save.disabled = true;
    try {
      AUTO = await post("/api/schedule", { mode: "weekly", slots: read(), catch_up: $("#schCatch").checked });
      $("#autoMode").value = "weekly";
      closeModal();
      tick();
      toast("ok", "Расписание сохранено", AUTO.slots.length
        ? `${slotsLabel(AUTO.slots)}${AUTO.enabled ? "" : " — включите автообновление, чтобы оно заработало"}`
        : "Пусто: автообновление ничего не запустит");
    } catch (e) {
      save.disabled = false;
      toast("warn", "Не получилось", e.message);
    }
  };
  upd();
}
let autoSkew = 0;
const hhmm = t => new Date(t).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
// «сегодня в 21:00», «завтра в 09:00», «в пн, 09:00» — когда до запуска больше часа, часы с секундами не нужны
const whenJs = iso => {
  const d = new Date(iso), n = new Date(), day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const days = Math.round((day(d) - day(n)) / 864e5), hm = hhmm(iso);
  return days === 0 ? `сегодня в ${hm}` : days === 1 ? `завтра в ${hm}`
    : days < 7 ? `в ${DAYS[(d.getDay() + 6) % 7]}, ${hm}` : `${d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} в ${hm}`;
};
function tick() {
  const t = $("#autoTimer");
  if (!AUTO) return;
  $("#autoRow").classList.toggle("on", AUTO.enabled);
  const weekly = AUTO.mode === "weekly", sum = $("#autoPlanSum");
  $("#autoTimerCfg").classList.toggle("hidden", weekly);
  $("#autoPlan").classList.toggle("hidden", !weekly);
  sum.classList.toggle("hidden", !weekly);
  if (weekly) {
    sum.innerHTML = (AUTO.slots || []).length
      ? esc(slotsLabel(AUTO.slots)) + (AUTO.catch_up ? " · <b>догоняет пропущенное</b>" : "")
      : "расписание пустое — задайте время";
  }
  const skip = $("#autoSkip"), sk = AUTO.enabled ? Object.entries(AUTO.skipped || {}) : [];
  const ov = AUTO.enabled ? Object.entries(AUTO.overrun || {}) : [];
  skip.classList.toggle("hidden", !sk.length && !ov.length);
  skip.innerHTML = [...sk.map(([s, v]) => `${SRC[s]} ещё собирает${v.since ? ` с ${hhmm(v.since)}` : ""} — пропустил запуск ${hhmm(v.tick)}`),
    ...ov.map(([s, v]) => `⚠ ${SRC[s]}: ${v.streak} сбора подряд не уложились в шаг${v.avg_hours ? ` (сбор ~${v.avg_hours.toFixed(1)} ч)` : ""} — увеличьте шаг`)].join("<br>");
  if (!AUTO.enabled || !AUTO.next_run) {
    t.textContent = AUTO.enabled && weekly ? "расписание пустое" : "выключено";
    return;
  }
  const left = Math.max(0, (new Date(AUTO.next_run) - new Date()) / 1000);
  if (weekly && left > 3600) { t.innerHTML = `следующий запуск <b>${whenJs(AUTO.next_run)}</b>`; return; }
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
