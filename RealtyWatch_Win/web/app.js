"use strict";
const SRC = { cian: "ЦИАН", avito: "Авито", yandex: "Яндекс" };
const ZONE = { A: "Красный круг", B: "Остальная Ялта", C: "Посёлки Большой Ялты" };
const ZONE_SHORT = { A: "Круг", B: "Ялта", C: "Посёлок" };
const RENO = { design: "дизайнерский ремонт", euro: "евроремонт", cosmetic: "косметический ремонт", old: "старый ремонт",
  finish: "чистовая отделка", none: "без ремонта", any: "с ремонтом" };
const RENO_RANK = { design: 6, euro: 5, cosmetic: 4, finish: 3, old: 2, any: 1 };
const SELLER = { owner: "собственник", agent: "агент", developer: "застройщик" };
const STATE = { running: "идёт сбор", paused: "пауза", done: "готово", partial: "неполный", error: "ошибка", blocked: "блокировка", stopped: "остановлен", interrupted: "прерван" };
const STATE_TIP = {
  done: "Выдача прочитана полностью; пропавшие из поиска объявления проверены на сайте",
  partial: "Сбор закончился, но выдаче нельзя доверять полностью (не все страницы, сайт отдал меньше заявленного или из поиска пропало слишком много). Новые и изменённые цены записаны, а снятые с продажи не проверялись — подробности в итоге и журнале прогона",
  error: "Сбор прерван ошибкой (сайт не ответил, страница пришла пустой и т.п.). Снятые не проверялись. Подробности — в журнале прогона",
  blocked: "Сайт заблокировал доступ (капча не пройдена, ограничение IP). Снятые не проверялись",
  stopped: "Остановлено вручную или при закрытии программы",
  interrupted: "Сбор не доведён до конца: программа закрылась во время него. Данные, которые он успел записать, в базе есть, но выдача прочитана не вся",
};
const VIEW_TITLE = { dashboard: "Дашборд", cards: "Подборка", table: "Таблица", changes: "Изменения", map: "Карта", runs: "Прогоны", imports: "Импорт", settings: "Настройки" };
const ICON = {
  pin: '<svg viewBox="0 0 24 24"><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/></svg>',
  ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  noext: '<svg viewBox="0 0 24 24"><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5M14 4h6v6M4 4l16 16"/></svg>',
  map: '<svg viewBox="0 0 24 24"><circle cx="12" cy="10" r="2.5"/><path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/></svg>',
  heart: '<svg viewBox="0 0 24 24"><path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/></svg>',
  dislike: '<svg viewBox="0 0 24 24"><path d="M10 14.5v4.3a2.2 2.2 0 0 0 4 1.2l3.6-5.5V4H8.4a2 2 0 0 0-2 1.6l-1.3 6.6a2 2 0 0 0 2 2.3zM17.6 4h2.4v10.5h-2.4"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4.3h-4.3"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2v.1"/></svg>',
  ok: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 4v12M7 11l5 5 5-5M5 20h14"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="M12 16V4M7 9l5-5 5 5M5 20h14"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3.5 6.5a1.5 1.5 0 0 1 1.5-1.5h4.5l2 2.5H19a1.5 1.5 0 0 1 1.5 1.5v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4.5 7h15M9.5 7V5.2c0-.7.5-1.2 1.2-1.2h2.6c.7 0 1.2.5 1.2 1.2V7M6.5 7l.8 11.6c.1 1 .9 1.9 2 1.9h5.4c1.1 0 1.9-.9 2-1.9L17.5 7M10 11v6M14 11v6"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15zM10 20.5a2.2 2.2 0 0 0 4 0"/></svg>',
  bellOff: '<svg viewBox="0 0 24 24"><path d="M6 16.5V11a6 6 0 0 1 9.4-4.9M18 11v5.5l1.5 2h-15M10 20.5a2.2 2.2 0 0 0 4 0M4 4l16 16"/></svg>',
  list: '<svg viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  chart: '<svg viewBox="0 0 24 24"><path d="M4 19h16M6 15l4-4 3 3 5-6"/></svg>',
  img: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-4.5 4 3.5 3-2.5 4 3.5"/></svg>',
  share: '<svg viewBox="0 0 24 24"><path d="M12 15V4M8 8l4-4 4 4M5 15v4a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-4"/></svg>',
  note: '<svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  merge: '<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.1 0l2.1-2.1a5 5 0 0 0-7.1-7.1L10.8 5"/><path d="M14 11a5 5 0 0 0-7.1 0l-2.1 2.1a5 5 0 0 0 7.1 7.1L13.2 19"/></svg>',
  unmerge: '<svg viewBox="0 0 24 24"><path d="M9.5 6.5 11 5a4.5 4.5 0 0 1 6.4 6.4L15.9 13"/><path d="M14.5 17.5 13 19a4.5 4.5 0 0 1-6.4-6.4L8.1 11"/><path d="M8 4v2.5M4 8h2.5M16 17.5V20M17.5 16H20"/></svg>',
  spot: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>',
};

let LASTRUN = {}, ALL = [], OBJS = [], SETTINGS = null, view = "cards", jAfter = 0, lastStates = {};
const EXPORT_PICK = new Set();
let IMP_BATCH_ID = null, IMP_FILTER = "all", IMP_DETAIL = null, IMP_HIGHLIGHT_ID = null;
let LAN_SUM = { enabled: false, listening: false, online_peers: 0, inbox_count: 0, outbox_count: 0, data_rev: 0, newer_db_peer: null };
let lanPinTimer = null, lanDiscTimer = null, lanPollInit = false, LAN_CACHE = null, lanAfter = 0, SPOTLIGHT = null, dismissedFreshDb = "";
const canSpotlight = () => !!(LAN_SUM && LAN_SUM.enabled && (LAN_SUM.online_peers || 0) > 0);
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NF_INT = new Intl.NumberFormat("ru-RU");
const NF_MLN = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
const NF_MB = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const DF_SHORT = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short" });
const DF_DTT = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const fmt = n => n == null || !isFinite(n) ? "—" : NF_INT.format(Math.round(n));
const mln = n => n == null || !isFinite(n) ? "—" : NF_MLN.format(n / 1e6);
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `Ошибка ${r.status}`);
  return j;
};
const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const dt = s => s ? DF_SHORT.format(new Date(s)) : "";
const dtt = s => s ? DF_DTT.format(new Date(s)) : "";
const fileName = p => String(p || "").split(/[\\/]/).pop();   // в уведомлении — имя файла: весь путь в него не влезает
const bkLabel = n => fileName(n).replace(/_\d{4}-\d{2}-\d{2}_\d{4}\.zip$/, "").replace(/_/g, " ");
const mbs = v => `${NF_MB.format(Number(v))} МБ`;
const localIso = (ms = Date.now()) => { const d = new Date(ms); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19); };
const today = () => localIso().slice(0, 10);
const key = r => `${r.source}:${r.source_id}`;
const desktop = () => window.pywebview && window.pywebview.api;
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* хранилище недоступно */ } },
};
// Первый запуск: в базе нет объявлений, поэтому пуста и вкладка — коротко объясняем, что здесь будет
const TAB_ABOUT = {
  cards: "Карточки найденных квартир: фото, цена и её история по каждому сайту, адрес, этаж, площадь, ремонт. Рядом карта — наведите на карточку, и точка подсветится. Сердечком отмечают то, за чем хочется следить: такие квартиры попадают на дашборд.",
  table: "Те же квартиры одним списком: цена, ₽/м², комнаты, площадь, этаж, ремонт, адрес, дата находки. Клик по заголовку колонки сортирует (порядок запоминается), клик по строке открывает карточку в подборке.",
  changes: "Что изменилось на рынке между проверками: появилось новое объявление, квартиру сняли с продажи, продавец поднял или снизил цену. Можно выбрать период, типы изменений и отфильтровать по зоне, комнатам, цене и другим параметрам. Первичная загрузка сайта изменениями не считается.",
  runs: "История сборов: когда запускался каждый сайт, сколько страниц прочитано и объявлений собрано, сколько новых и снятых. У каждого прогона есть подробный журнал — по нему видно, где сбор споткнулся.",
  imports: "Подборки карточек (.rwcards.zip или по сети) от других устройств: новые и совпавшие квартиры интегрируются без искажения статистики рынка, а уже снятые с продажи или исчезнувшие из свежей выдачи помечаются как неактуальные с указанием причины.",
};
const aboutTab = key => ALL.length ? "" : `<div class="about"><b>Краткое описание</b><p>${TAB_ABOUT[key]}</p></div>`;

// Порядок строк таблицы: колонка и направление (−1 — по убыванию). Задаётся кликом по заголовку колонки
// (выпадающей «Сортировки» в таблице нет) и помнится между запусками. По умолчанию — сначала найденные недавно.
const TSORT_DEF = ["first_seen", -1];
let tableSort = (() => { const s = String(store.get("rw.tableSort", "")).split(":"); return s.length === 2 ? [s[0], s[1] === "1" ? 1 : -1] : TSORT_DEF.slice(); })();
const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? few : many; };

// ---------- данные: объявления → объекты ----------
async function loadData() {
  ALL = await api("/api/listings");
  buildObjects();
  if (view === "changes") { await renderChanges(); return; }
  if (view === "imports") {
    renderHeader([]);
    if (IMP_BATCH_ID) await openImportBatch(IMP_BATCH_ID);
    else await renderImports();
    return;
  }
  render();
  if (view === "dashboard") renderDashboard();
}

function applyListingUpdates(updatedRows) {
  if (!Array.isArray(updatedRows) || !updatedRows.length) return loadData();
  const upd = new Map(updatedRows.map(r => [key(r), r]));
  for (let i = 0; i < ALL.length; i++) {
    const k = key(ALL[i]);
    if (upd.has(k)) {
      ALL[i] = upd.get(k);
      upd.delete(k);
    }
  }
  for (const r of upd.values()) ALL.push(r);
  buildObjects();
  if (typeof CH_DATA !== "undefined" && CH_DATA && Array.isArray(CH_DATA.ev)) {
    const oidByKey = new Map(updatedRows.map(r => [key(r), r.object_id]));
    for (const e of CH_DATA.ev) {
      const oid = oidByKey.get(`${e.source}:${e.source_id}`);
      if (oid) e.object_id = oid;
    }
  }
  if (view === "changes") {
    if (typeof paintChanges === "function") paintChanges();
    return;
  }
  if (view === "imports") {
    if (IMP_BATCH_ID) openImportBatch(IMP_BATCH_ID);
    else renderImports();
    return;
  }
  render();
  if (view === "dashboard" && typeof renderDashboard === "function") renderDashboard();
}
async function loadSettings() { SETTINGS = await api("/api/settings"); }

function relistedInfo(items, price) {
  const live = items.filter(i => i.active && !i.off_site && i.price);
  const gone = items.filter(i => (!i.active || i.off_site) && i.price);
  if (!live.length || !gone.length || !price) return null;
  const best = live.filter(i => i.price === price);
  if (!best.length) return null;
  const goneBefore = gone.filter(g => best.every(l => {
    const gf = g.first_seen || "", lf = l.first_seen || "";
    if (gf >= lf) return false;
    const t0 = Date.parse(gf), t1 = Date.parse(lf);
    return !isFinite(t0) || !isFinite(t1) || (t1 - t0) >= 6 * 3600e3;
  }));
  if (!goneBefore.length) return null;
  goneBefore.sort((a, b) => ((b.removed_at || b.last_seen || "") > (a.removed_at || a.last_seen || "") ? 1 : -1));
  const prev = goneBefore[0];
  const diff = price - prev.price;
  if (Math.abs(diff) < 50000) return null;
  return {
    old: prev.price, now: price, diff, pct: 100 * diff / prev.price,
    oldSrc: prev.source, oldSeen: prev.removed_at || prev.last_seen, newFirst: best[0].first_seen,
  };
}

function buildObjects() {
  const groups = new Map();
  for (const r of ALL) {
    if (!groups.has(r.object_id)) groups.set(r.object_id, []);
    groups.get(r.object_id).push(r);
  }
  const rank = r => (r.excluded ? 0 : 8) + (r.active ? 4 : 0) + (r.has_img ? 2 : 0) + (r.lat != null ? 1 : 0);
  OBJS = [...groups.values()].map(items => {
    // Объявление, отмеченное главным при ручном объединении (manual_primary), всегда задаёт параметры объекта
    items.sort((a, b) => (!!b.manual_primary - !!a.manual_primary) || rank(b) - rank(a) || (a.first_seen > b.first_seen ? 1 : -1));
    const p = items[0];
    const manual_merged = items.some(i => i.manual_merged);
    const live = items.filter(i => i.active);
    const prices = (live.length ? live : items).map(i => i.price).filter(Boolean);
    const reno = items.map(i => i.renovation).filter(Boolean).sort((a, b) => (RENO_RANK[b] || 0) - (RENO_RANK[a] || 0));
    const flags = {};
    items.forEach(i => Object.assign(flags, i.flags || {}));
    const noted = (manual_merged && p.note)
      ? p
      : items.filter(i => i.note).sort((a, b) => ((b.note_ts || "") > (a.note_ts || "") ? 1 : -1))[0];
    const note = noted ? noted.note : "", note_ts = noted ? (noted.note_ts || "") : "";
    const impMap = new Map();
    for (const i of items) {
      for (const n of (i.imported_notes || [])) {
        if (!n || !n.origin_id || !String(n.text || "").trim()) continue;
        const prev = impMap.get(n.origin_id);
        if (!prev || (n.ts || "") > (prev.ts || "")) impMap.set(n.origin_id, n);
      }
    }
    const imported_notes = [...impMap.values()].sort((a, b) => ((a.ts || "") > (b.ts || "") ? 1 : (a.ts || "") < (b.ts || "") ? -1 : 0));
    const _impText = imported_notes.map(n => `${n.origin_name || ""} ${n.text || ""}`).join(" ").toLowerCase();
    const price = prices.length ? Math.min(...prices) : null;
    for (const i of items) {
      const rawSrc = `${i.title}\0${i.address}\0${i.description}`;
      if (i._lcSrc !== rawSrc) {
        i._lcSrc = rawSrc;
        i._lc = `${i.title} ${i.address} ${i.description}`.toLowerCase();
      }
    }
    const textParts = items.map(i => i._lc);
    if (note) textParts.push(note.toLowerCase());
    if (_impText) textParts.push(_impText);
    const text = items.length === 1 && !note && !_impText ? p._lc : textParts.join(" ");
    return {
      id: p.object_id, items, p, flags, note, note_ts, imported_notes, _impText, manual_merged,
      img: (manual_merged && (p.has_img || p.photo)) ? p : (items.find(i => i.has_img) || items.find(i => i.photo)),
      sources: [...new Set(items.map(i => i.source))],
      price, priceMax: prices.length ? Math.max(...prices) : null,
      liked: items.some(i => i.liked), active: items.some(i => i.active),
      // лайк и дизлайк взаимоисключающие; если при слиянии баз квартира оказалась и там, и там — побеждает ♥
      disliked: !items.some(i => i.liked) && (manual_merged ? !!p.disliked : items.some(i => i.disliked)),
      excluded: items.every(i => i.excluded) ? p.excluded : null,
      renovation: manual_merged ? (p.renovation || reno[0] || null) : (p.excluded || !reno.length ? p.renovation : reno[0]),
      relisted: relistedInfo(items, price),
      first_seen: items.reduce((m, i) => (i.first_seen < m ? i.first_seen : m), p.first_seen),
      zone: p.zone, rooms: p.rooms, area: p.area, floor: p.floor, floors: p.floors, lat: p.lat, lon: p.lon,
      kitchen_area: p.kitchen_area ?? items.find(i => i.kitchen_area != null)?.kitchen_area ?? null,
      build_year: p.build_year ?? items.find(i => i.build_year != null)?.build_year ?? null,
      address: manual_merged
        ? (p.address || items.map(i => i.address).filter(Boolean)[0] || "")
        : (items.map(i => i.address).filter(Boolean).sort((a, b) => b.length - a.length)[0] || ""),
      text,
    };
  });
  OBJS.forEach(o => { o.price_m2 = o.price && o.area ? o.price / o.area : null; o.fresh = freshItems(o); });
  if (MERGE_PICK && !OBJS.some(o => o.id === MERGE_PICK)) MERGE_PICK = null;
  const liveIds = new Set(OBJS.map(o => o.id));
  for (const id of [...EXPORT_PICK]) if (!liveIds.has(id)) EXPORT_PICK.delete(id);
}

// Скрытая квартира: дизлайк ставится каждому её объявлению, поэтому объявление без дизлайка в скрытой
// карточке добавилось в неё позже — новая квартира в том же доме или та же квартира на другом сайте.
// Такие объявления подсвечиваются в «Скрытых», а их число стоит кружком на кнопке
function freshItems(o) {
  return o.disliked ? o.items.filter(i => !i.disliked) : [];
}

// ---------- фильтр просмотра ----------
const checked = name => $$(`input[name=${name}]:checked`).map(i => i.value);

function readFilters() {
  return {
    src: checked("src"), zones: checked("zone"), rooms: checked("rooms").map(Number),
    pmin: parseFloat($("#fPmin").value) * 1e6, pmax: parseFloat($("#fPmax").value) * 1e6,
    amin: parseFloat($("#fAmin").value),
    kmin: parseFloat($("#fKmin").value), kmax: parseFloat($("#fKmax").value),
    ymin: parseInt($("#fYmin").value, 10), ymax: parseInt($("#fYmax").value, 10),
    reno: $("#fReno").value, nb: $("#fNew").value,
    st: $("#fState").value, active: $("#fActive").checked, noCoords: $("#fNoCoords").checked,
    manual: $("#fManual").checked,
    liked: $("#fLiked").checked, hiddenOnly: $("#fHidden").checked, noDis: $("#fNoDis").checked,
    text: $("#fText").value.trim().toLowerCase(), sort: $("#fSort").value,
  };
}

function matchObject(o, f, { ignoreZone = false, allowRemoved = false } = {}) {
  if (!o) return false;
  // «Скрытые» показывают только квартиры с дизлайком; иначе их убирает галочка «исключить дизлайки»
  const { hiddenOnly, noDis } = f;
  return o.sources.some(s => f.src.includes(s)) && (ignoreZone || f.zones.includes(o.zone)) &&
    (o.rooms == null || f.rooms.includes(o.rooms)) &&
    !(f.pmin && o.price < f.pmin) && !(f.pmax && o.price > f.pmax) && !(f.amin && o.area < f.amin) &&
    (!Number.isFinite(f.kmin) || (o.kitchen_area != null && o.kitchen_area >= f.kmin)) &&
    (!Number.isFinite(f.kmax) || (o.kitchen_area != null && o.kitchen_area <= f.kmax)) &&
    (!Number.isFinite(f.ymin) || (o.build_year != null && o.build_year >= f.ymin)) &&
    (!Number.isFinite(f.ymax) || (o.build_year != null && o.build_year <= f.ymax)) &&
    (!f.reno || (f.reno === "known" ? o.renovation && o.renovation !== "any" : o.renovation === f.reno)) &&
    (f.nb === "" || String(o.p.is_new_building ?? 0) === f.nb) &&
    (f.st === "all" || (f.st === "pass" ? !o.excluded : !!o.excluded)) &&
    (!f.active || o.active || allowRemoved) && (!f.noCoords || o.lat == null) &&
    (!f.manual || o.manual_merged) && (!f.liked || o.liked) &&
    (hiddenOnly ? o.disliked : !(noDis && o.disliked)) &&
    (!f.text || o.text.includes(f.text));
}

function sortKey(o, s) {
  if (!o) return 0;
  return ({ new: o.first_seen, price: o.price, "-price": o.price, ppm: o.price_m2, "-area": o.area }[s]) ?? 0;
}

function filtered(ignoreZone = false, f = readFilters()) {
  const rows = OBJS.filter(o => matchObject(o, f, { ignoreZone }));
  if (ignoreZone) return rows;
  const dir = f.sort === "new" || f.sort.startsWith("-") ? -1 : 1;
  rows.sort((a, b) => ((sortKey(a, f.sort) > sortKey(b, f.sort)) ? 1 : (sortKey(a, f.sort) < sortKey(b, f.sort)) ? -1 : 0) * dir);
  // в «Скрытых» первыми — карточки, в которые после скрытия добавились объявления
  if (f.hiddenOnly) rows.sort((a, b) => (b.fresh.length > 0) - (a.fresh.length > 0));
  return rows;
}

function renderHeader(rows, zoneRows = null) {
  const zr = zoneRows ?? filtered(true);
  for (const z of ["A", "B", "C"]) $(`#zc-${z}`).textContent = zr.filter(o => o.zone === z).length;
  const likes = OBJS.filter(o => o.liked).length;
  $("#likeCount").textContent = likes || "";
  $("#hideCount").textContent = OBJS.filter(o => o.disliked).length || "";
  const fresh = OBJS.filter(o => o.fresh.length).length, nb = $("#hideNew");
  nb.textContent = fresh || "";
  nb.hidden = !fresh;
  nb.title = fresh ? `В ${fresh} ${plural(fresh, "скрытую квартиру", "скрытые квартиры", "скрытых квартир")} после скрытия добавились объявления — ` +
    "это может быть новая квартира в том же доме или та же квартира на другом сайте. Откройте «Скрытые»: эти карточки там первые, новые объявления подсвечены" : "";
  $("#fNoDisBox").classList.toggle("off", $("#fHidden").checked);  // в «Скрытых» галочка ни на что не влияет
  updateExportBtnUI();
  const ppm = median(rows.map(o => o.price_m2).filter(Boolean));
  const pr = median(rows.map(o => o.price).filter(Boolean));
  const multi = rows.filter(o => o.sources.length > 1).length;
  $("#kpis").innerHTML = ["runs", "imports", "settings", "dashboard", "changes"].includes(view) ? "" : `
    <div class="kpi" title="Объектов по текущим фильтрам. Объект — одна квартира; её объявления с разных сайтов объединены"><b>${rows.length}</b><span>${plural(rows.length, "объект", "объекта", "объектов")}</span></div>
    <div class="kpi gold" title="Медиана цены за м² по показанным объектам: половина дешевле, половина дороже"><b>${fmt(ppm)}</b><span>медиана, ₽/м²</span></div>
    <div class="kpi" title="Медианная цена показанных объектов"><b>${mln(pr)}</b><span>медиана, млн ₽</span></div>
    <div class="kpi" title="Сколько показанных объектов найдено сразу на двух или трёх сайтах"><b>${multi}</b><span>на 2+ сайтах</span></div>`;
  const by = ALL.reduce((m, r) => (m[r.source] = (m[r.source] || 0) + 1, m), {});
  $("#viewSub").textContent = view === "runs" ? "История запусков парсеров"
    : view === "imports" ? (IMP_BATCH_ID ? "Подборка импорта" : "Импортированные подборки карточек и заметки")
    : view === "settings" ? "Параметры поиска, геозона, сбор данных и база"
    : view === "dashboard" ? `Сводка рынка Ялты · в базе ${OBJS.length} ${plural(OBJS.length, "объект", "объекта", "объектов")} из ${ALL.length} объявлений`
    : view === "changes" ? "Новые, снятые, изменения цен — по каждому сайту. Первичная загрузка сайтов изменениями не считается"
    : `В базе ${OBJS.length} ${plural(OBJS.length, "объект", "объекта", "объектов")} из ${ALL.length} объявлений · ` + Object.keys(SRC).map(k => `${SRC[k]} ${by[k] || 0}`).join(" · ");
}

function render() {
  markFilters();
  if (view === "changes") {
    if (typeof paintChanges === "function") paintChanges();
    return;
  }
  const rows = filtered();
  // «Только в видимой области карты» сужает список карточек, а карта рядом показывает всё по фильтрам:
  // иначе, сдвинув карту, вы увидели бы пустое место там, где квартиры есть
  const list = view === "cards" ? inArea(rows) : rows;
  renderHeader(list);
  fitToolbar();
  if (view === "cards") renderCards(list, rows);
  if (view === "table") renderTable(rows);
  if (view === "map") renderMap(rows);
}

// ---------- карточки ----------
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

function relistTip(o) {
  const rl = o.relisted;
  if (!rl) return "";
  const d = rl.diff;
  return `Квартира перевыставлена с изменением цены: раньше на ${SRC[rl.oldSrc]} она продавалась за ${fmt(rl.old)} ₽` +
    `${rl.oldSeen ? ` (снято ${dtt(rl.oldSeen)})` : ""}, сейчас — ${fmt(rl.now)} ₽ (${d > 0 ? "+" : "−"}${fmt(Math.abs(d))} ₽, ${d > 0 ? "+" : ""}${rl.pct.toFixed(1)}%).`;
}

function delta(o) {
  const i = o.items.find(x => x.active && x.price === o.price && lastChange(x)) || o.items.find(x => x.active && lastChange(x));
  if (i) {
    const [old, now, ts] = lastChange(i), d = now - old;
    return `<span class="delta ${d < 0 ? "down" : "up"}" title="${SRC[i.source]}: цена ${d < 0 ? "снижена" : "повышена"} ${dtt(ts)} — с ${fmt(old)} до ${fmt(now)} ₽ (${d > 0 ? "+" : "−"}${fmt(Math.abs(d))} ₽, ${(100 * d / old).toFixed(1)}%)">${d < 0 ? "↓" : "↑"} ${mln(Math.abs(d))} млн</span>`;
  }
  if (o.relisted) {
    const rl = o.relisted, d = rl.diff;
    return `<span class="delta ${d < 0 ? "down" : "up"}" title="${esc(relistTip(o))}">↻ ${d < 0 ? "↓" : "↑"} ${mln(Math.abs(d))} млн</span>`;
  }
  return "";
}

function oldPrice(o) {
  const i = o.items.find(x => x.active && x.price === o.price && lastChange(x));
  if (!i) return "";
  const [old, , ts] = lastChange(i);
  return `<s class="old-price" title="Прежняя цена на ${SRC[i.source]} до ${dtt(ts)}. Клик по карточке — вся история цен">${fmt(old)} ₽</s>`;
}

function renoTip(o) {
  const r = o.p;
  if (o.renovation === "any") return "Прежняя отметка ЦИАН «с ремонтом» без вида ремонта: так помечались объявления, пока программа не узнавала у ЦИАН вид ремонта. Уточнится при следующем сборе ЦИАН";
  if (!o.renovation) return "Ни сайт, ни описание не указывают вид ремонта. Объявление не отсеяно — проверьте по фото";
  return `Ремонт: ${RENO[o.renovation]}. ` + (r.renovation_raw ? `На сайте указано «${r.renovation_raw}»` : "Определено по тексту описания");
}

function goneTip(i) {
  return `снято ${dtt(i.removed_at) || "—"}. ${i.removed_reason || "Не найдено в поиске сайта"}. Последний раз в поиске: ${dtt(i.last_seen)}`;
}

// Пропало из полной выдачи, но сайт не подтвердил снятие (страницу не удалось проверить)
function missTip(i) {
  const was = `${i.missed} ${plural(i.missed, "полный сбор", "полных сбора", "полных сборов")} подряд (последний раз видели ${dtt(i.last_seen)})`;
  return `Нет в поиске ${SRC[i.source]} ${was}, и открыть страницу объявления не удалось. ` +
    "Один сбор мог и не доехать до конца, поэтому ссылку пока оставляем — но если объявление не появится и в следующем сборе, " +
    "программа решит, что на этом сайте его больше нет";
}

// Объявления на сайте нет совсем: ссылку показывать незачем — она никуда не ведёт
function offTip(i, alive) {
  return `На ${SRC[i.source]} этого объявления больше нет: ${i.missed} ${plural(i.missed, "полный сбор", "полных сбора", "полных сборов")} подряд ` +
    `его нет в поиске, а страницу открыть не удалось — попасть на него нельзя, поэтому ссылка убрана. ` +
    `Последний раз в поиске: ${dtt(i.last_seen)}, цена ${fmt(i.price)} ₽ — последняя известная.\n\n` +
    (alive ? "Квартира остаётся в продаже: её показывает другой сайт." :
      "Квартиры не осталось ни на одном сайте, поэтому она снята с продажи. " +
      "Если объявление всё же живо — «Настройки → База данных → Перепроверить снятые»");
}

// Причины отсева объекта по одной: в базе они записаны через «; »
const reasons = o => o.excluded ? String(o.excluded).split(/;\s*/).filter(Boolean) : [];
const EXCL_TIP = "Не прошло отсев по параметрам поиска (параметры — в «Настройках»)";

function tags(o) {
  const x = [], t = [], r = o.p;  // x — причины отсева (✕), идут первыми и подряд; t — остальные бэджи
  if (o.manual_merged) t.push(`<span class="tag manual" title="${esc(manualTip(o))}">${ICON.merge}объединено вручную</span>`);
  // Причина отсева, у которой есть свой бэдж (ремонт, обременение, доля, торги), показывается этим же бэджем
  // с крестиком — а не вторым бэджем рядом: «без ремонта» и «✕ без ремонта» говорили одно и то же дважды
  const why = reasons(o), shown = new Set();
  const excl = reason => why.includes(reason) && !shown.has(reason) && shown.add(reason);
  if (o.renovation === "none" && excl("без ремонта"))
    x.push(`<span class="tag bad" title="${esc(`${EXCL_TIP}: без ремонта.\n${renoTip(o)}`)}">✕ без ремонта</span>`);
  else if (!o.renovation && excl("ремонт не указан"))
    x.push(`<span class="tag bad" title="${esc(`${EXCL_TIP}: ремонт не указан, а в настройках — «только указанный ремонт».\n${renoTip(o)}`)}">✕ ремонт не указан</span>`);
  else if (o.renovation) t.push(`<span class="tag ${o.renovation === "none" ? "bad" : "good"}" title="${esc(renoTip(o))}">${RENO[o.renovation] || o.renovation}</span>`);
  else t.push(`<span class="tag warn" title="${esc(renoTip(o))}">ремонт не указан</span>`);
  if (r.is_new_building) t.push(`<span class="tag" title="Квартира в новостройке (по данным сайта)">новостройка</span>`);
  if (o.items.some(i => i.apartments || (i.raw && (i.raw.apartments || i.raw.isApartments)))) t.push(`<span class="tag warn" title="Юридически апартаменты, а не квартира: нельзя прописаться, выше налог и тарифы ЖКХ">апартаменты</span>`);
  if (o.relisted) {
    const rl = o.relisted;
    t.push(`<span class="tag ${rl.diff < 0 ? "good" : "warn"}" title="${esc(relistTip(o))}">↻ перевыставлено ${rl.diff < 0 ? "↓" : "↑"} ${mln(Math.abs(rl.diff))} млн</span>`);
  }
  const sellers = [...new Set(o.items.map(i => i.seller_type).filter(Boolean))];
  sellers.forEach(s => t.push(`<span class="tag" title="${esc(SELLER_TIP[s] || s)}">${SELLER[s] || s}</span>`));
  if (o.lat == null) t.push(`<span class="tag warn" title="Сайт не дал координаты: зона определена по адресу, на карте объекта нет">нет координат</span>`);
  for (const [k, lbl, what, reason] of [["encumbrance", "обременение?", "обременение", "обременение"], ["share", "доля?", "продажа доли", "доля"],
    ["auction", "торги?", "торги / аукцион", "аукцион/торги"]]) {
    const found = o.flags[k] ? `В тексте найдено: «${o.flags[k].join("» … «")}».` : "";
    if (excl(reason)) x.push(`<span class="tag bad" title="${esc(`${EXCL_TIP}: ${what}. ${found}`)}">✕ ${reason}</span>`);
    else if (o.flags[k]) t.push(`<span class="tag warn" title="${esc(`Возможно ${what}. ${found} Проверьте вручную`)}">${lbl}</span>`);
  }
  // остальные причины — каждая отдельным бэджем: длинная строка через «; » не помещалась в карточку
  why.filter(w => !shown.has(w)).forEach(w => x.push(`<span class="tag bad" title="${esc(`${EXCL_TIP}: ${w}`)}">✕ ${esc(w)}</span>`));
  if (!o.active) t.push(`<span class="tag bad" title="${esc("Снято со всех сайтов:\n" + o.items.map(i => `${SRC[i.source]}: ${goneTip(i)}`).join("\n"))}">снято с продажи</span>`);
  return x.concat(t).join("");
}

function offers(o, inModal = false) {
  const best = o.items.length > 1 && o.price !== o.priceMax ? o.price : null;
  const canExcl = inModal && o.manual_merged;
  const list = [...o.items].sort((a, b) => (b.active - a.active) || ((a.price || 0) - (b.price || 0)));
  return `<div class="offers">${list.map(i => {
    const ch = i.active ? lastChange(i) : null;
    const tip = i.off_site ? offTip(i, o.active)
      : i.active
        ? `Открыть объявление на ${SRC[i.source]}.\nЦена ${fmt(i.price)} ₽` + (ch ? `, до ${dtt(ch[2])} было ${fmt(ch[0])} ₽` : ", не менялась") +
          `.\nВ базе с ${dtt(i.first_seen)}, последний раз в поиске ${dtt(i.last_seen)}` + (i.seller_type ? `.\n${SELLER_TIP[i.seller_type]}` : "") +
          (i.missed ? `.\n\n${missTip(i)}` : "")
        : `Перечёркнуто — объявление считается снятым с продажи: ${goneTip(i)}.\nЕсли оно на сайте есть — «Настройки → База данных → Перепроверить снятые»`;
    const isNew = o.fresh.includes(i);
    const ag = i.auto_group || key(i);
    const mark = (canExcl && i.manual_primary ? ` <span class="tag miss manual" title="От этого объявления взяты описание, адрес и параметры карточки">основное</span>` : "") +
      (isNew ? ` <span class="tag miss fresh" title="${esc(`Добавилось в карточку после того, как вы её скрыли. В базе с ${dtt(i.first_seen)}`)}">новое</span>` : "") +
      (i.off_site ? ` <span class="tag miss" title="${esc(offTip(i, o.active))}">нет на сайте</span>`
      : i.active && i.missed ? ` <span class="tag miss" title="${esc(missTip(i))}">нет в поиске</span>` : "");
    let exclBtn = "";
    if (canExcl) {
      const peers = o.items.filter(x => (x.auto_group || key(x)) === ag);
      const exclTip = (peers.length > 1
        ? `Исключить из объединения исходную группу (${peers.length} ${plural(peers.length, "объявление", "объявления", "объявлений")}: ` +
          peers.map(p => `${SRC[p.source]} ${fmt(p.price)} ₽`).join(", ") + ").\nОни отделятся вместе в самостоятельную карточку"
        : `Исключить это объявление (${SRC[i.source]}, ${fmt(i.price)} ₽) из ручного объединения в отдельную карточку`) +
        (i.manual_primary ? "\nСейчас от него взяты описание и параметры карточки — при исключении основным станет следующее из оставшихся" : "");
      exclBtn = `<button type="button" class="offer-excl" data-src="${esc(i.source)}" data-sid="${esc(i.source_id)}" title="${esc(exclTip)}">${ICON.unmerge}<span>Исключить</span></button>`;
    }
    const body = `<i class="sdot ${i.source}"></i><span>${SRC[i.source]}${mark}${i.excluded && !o.excluded ? ` <span class="tag warn" title="${esc(`Это объявление не прошло отсев (${i.excluded}), но другое объявление этой квартиры прошло`)}">!</span>` : ""}</span>
    ${exclBtn}<span>${best && i.price === best && i.active ? '<span class="best" title="Здесь цена ниже, чем на других сайтах">дешевле</span> ' : ""}${ch ? `<s class="old-mini">${mln(ch[0])}</s> ` : ""}<b>${fmt(i.price)} ₽</b></span>`;
    const cls = `offer ${i.off_site ? "off " : ""}${i.active ? "" : "gone"} ${isNew ? "fresh" : ""} ${canExcl ? "has-excl" : ""}`.trim();
    const agAttr = canExcl ? ` data-ag="${esc(ag)}"` : "";
    // ссылки нет: объявление с сайта пропало, открывать нечего
    return i.off_site
      ? `<div class="${cls}"${agAttr} title="${esc(tip)}">${body}${ICON.noext}</div>`
      : `<a class="${cls}"${agAttr} href="${esc(i.url)}" target="_blank" rel="noopener" title="${esc(tip)}">${body}${ICON.ext}</a>`;
  }).join("")}</div>`;
}

function media(o) {
  if (!o.img) return `<div class="media noimg" title="Фото не найдено на сайте — повторная попытка при следующем сборе">`;
  return `<div class="media"><img src="/img/${encodeURIComponent(o.img.source)}/${encodeURIComponent(o.img.source_id)}" loading="lazy" alt=""
    onerror="this.parentNode.classList.add('noimg');this.remove()">`;
}

function cardImpNotesHtml(o) {
  const list = o.imported_notes || [];
  if (!list.length) return "";
  const first = list[0];
  const who = esc(first.origin_name || "Импорт");
  const extra = list.length > 1 ? ` (+${list.length - 1})` : "";
  const body = list.map(n => `<div class="cin-item"><div class="cin-head"><b>${esc(n.origin_name || "Импорт")}</b>${n.ts ? `<span>${dtt(n.ts)}</span>` : ""}</div><div class="cin-body">${esc(n.text)}</div></div>`).join("");
  return `<details class="card-imp-notes"><summary title="Импортированная заметка — нажмите, чтобы развернуть полностью"><span class="cin-who">💬 ${who}${extra}:</span> <span class="cin-preview">${esc(first.text)}</span></summary><div class="cin-list">${body}</div></details>`;
}

function card(o) {
  const meta = [o.p.living_area && `жилая ${o.p.living_area} м²`, o.kitchen_area && `кухня ${o.kitchen_area} м²`, o.build_year && `${o.build_year} г.`].filter(Boolean).join(" · ");
  const rooms = o.rooms === 0 ? "студия" : o.rooms ?? "—";
  const n = o.items.length;
  const picking = MERGE_PICK === o.id;
  const expOn = EXPORT_PICK.has(o.id);
  const spOn = canSpotlight();
  const isSpot = !!(SPOTLIGHT && SPOTLIGHT.id === o.id);
  const multiTip = (o.manual_merged ? "В карточку добавлены объявления, объединённые вручную (описание и параметры — от первой выбранной карточки).\n\n" : "") +
    `Эта квартира найдена на: ${o.sources.map(s => SRC[s]).join(", ")} — объявления объединены в одну карточку.\n` +
    "Признаки одной квартиры: те же комнаты, этаж и этажность, площадь ±1 м², адрес в пределах 150 м, цена ±10%. " +
    "Во вторичном жилье на разных сайтах допускается одно расхождение — площадь до 3 м² или этажность дома — " +
    "если дом тот же, а цена отличается не больше чем на 2%";
  return `<article class="card ${o.excluded ? "excl" : ""} ${o.active ? "" : "inactive"} ${o.disliked ? "disliked" : ""} ${o.id === PICK ? "picked" : ""} ${picking ? "merging" : ""} ${expOn ? "exporting" : ""} ${isSpot ? "spotlighted" : ""}" data-id="${esc(o.id)}">
    ${picking ? `<div class="merge-note" title="Нажмите значок объединения на второй карточке — она вольётся в эту. Повторный клик сюда или Esc — отмена">${ICON.merge}<span>Выберите вторую карточку (Esc — отмена)</span></div>` : ""}
    ${o.id === PICK ? `<div class="pick-note" title="Подсветка снимется по клику на карточку">${ICON.pin}<span>Вы перешли к этой квартире</span></div>` : ""}
    ${isSpot ? `<div class="spot-note"><span>👁 ${esc(SPOTLIGHT.from)} показывает эту квартиру</span><button type="button" class="spot-x" title="Скрыть подсветку">✕</button></div>` : ""}
    ${o.fresh.length ? freshNote(o) : ""}
    ${media(o)}
      <div class="media-top"><span class="left"><span class="pill zone-${o.zone}" title="${esc(ZONE_TIP[o.zone] || "")}">${ZONE_SHORT[o.zone] || o.zone}</span>
        ${o.sources.length > 1 ? `<span class="pill multi" title="${esc(multiTip)}">${o.sources.length} ${plural(o.sources.length, "сайт", "сайта", "сайтов")}</span>`
          : o.manual_merged ? `<span class="pill manual" title="${esc(manualTip(o))}">${ICON.merge}вручную</span>` : ""}</span>
        <span class="marks"><button class="heart dis ${o.disliked ? "on" : ""}" title="${o.disliked ? "Скрыта — нажмите, чтобы вернуть в подборку" : "Не нравится — скрыть из подборки, таблицы, карты и дашборда. Сбор по ней продолжится; посмотреть и вернуть — кнопка «Скрытые»"}">${ICON.dislike}</button>
        <button class="heart ${o.liked ? "on" : ""}" title="${o.liked ? "В избранном — нажмите, чтобы убрать" : "Добавить в избранное: фильтр «Избранное» и отдельный блок на дашборде"}">${ICON.heart}</button></span></div>
      <div class="media-bottom"><div><div class="price" title="${esc(priceTip(o))}">${fmt(o.price)} ₽ ${oldPrice(o)}</div>
        <div class="ppm" title="Цена за квадратный метр общей площади${o.priceMax > o.price ? `; на других сайтах цена до ${fmt(o.priceMax)} ₽` : ""}">${fmt(o.price_m2)} ₽/м²${o.priceMax > o.price ? ` · до ${mln(o.priceMax)} млн` : ""}</div></div>${delta(o)}</div>
    </div>
    <div class="body">
      <div class="facts">
        <div class="fact" title="Число комнат"><b>${rooms}</b><span>${o.rooms === 0 ? "" : "комнат"}</span></div>
        <div class="fact" title="Общая площадь квартиры"><b>${o.area ?? "—"}</b><span>м² общая</span></div>
        <div class="fact" title="Этаж квартиры / этажей в доме"><b>${o.floor ?? "?"}<small>/${o.floors ?? "?"}</small></b><span>этаж</span></div>
      </div>
      <div class="addr" title="${o.manual_merged ? "Адрес основной карточки" : "Адрес (самый подробный из объявлений объекта)"}">${ICON.pin}<span>${esc(o.address || "адрес не указан")}</span></div>
      ${meta ? `<div class="meta" title="Жилая площадь, кухня, год постройки — со страниц объявлений">${esc(meta)}</div>` : ""}
      <div class="tags">${tags(o)}</div>
      ${o.note ? `<div class="card-note" title="${esc("Личная заметка (клик по кнопке ✎ или карточке — изменить):\n" + o.note)}">${ICON.note}<span>${esc(o.note)}</span></div>` : ""}
      ${cardImpNotesHtml(o)}
      ${offers(o)}
      <p class="desc" title="Начало описания; полностью — в окне карточки (клик по карточке)">${esc((o.p.description || "").slice(0, 320))}</p>
    </div>
    <div class="card-foot"><span title="Когда объект впервые появился в базе; число объявлений на всех сайтах">с ${dt(o.first_seen)} · ${n} ${plural(n, "объявление", "объявления", "объявлений")}</span>
      <span class="acts"><button class="icon-btn export-btn ${expOn ? "on" : ""}" title="${esc(exportBtnTip(o))}">${ICON.share}</button>${o.manual_merged ? `<button class="icon-btn unmerge-btn" title="${esc(unmergeBtnTip(o))}">${ICON.unmerge}</button>` : ""}<button class="icon-btn merge-btn ${picking ? "on" : ""}" title="${esc(mergeBtnTip(o))}">${ICON.merge}</button><button class="icon-btn note-btn ${o.note ? "on" : ""}" title="${esc(o.note ? "Изменить заметку" : "Добавить заметку к квартире")}">${ICON.note}</button>${o.lat != null ? `<button class="icon-btn pin" title="Показать на карте рядом">${ICON.map}</button>` : ""}${spOn ? `<button class="icon-btn spotlight-btn" data-spot="${esc(o.id)}" title="Подсветить партнёру в открытом окне RealtyWatch">${ICON.spot}</button>` : ""}</span></div>
  </article>`;
}

// Плашка на скрытой карточке, в которую после скрытия добавились объявления
function freshNote(o) {
  const n = o.fresh.length, where = [...new Set(o.fresh.map(i => SRC[i.source]))].join(", ");
  return `<div class="fresh-note" title="${esc("Новые объявления подсвечены ниже. Если это другая квартира, которая вас интересует, — верните карточку в подборку (👎 ещё раз). " +
    "Если та же квартира на другом сайте или такая же неинтересная — «Оставить скрытой»: кружок на кнопке «Скрытые» погаснет")}">
    ${ICON.dislike}<span>После скрытия ${plural(n, "добавилось", "добавились", "добавилось")} ${n} ${plural(n, "объявление", "объявления", "объявлений")}: ${esc(where)}</span>
    <button class="keep-hidden">Оставить скрытой</button></div>`;
}

// «Оставить скрытой»: дизлайк ставится и новым объявлениям — карточка больше не считается изменившейся
async function keepHidden(id) {
  const o = OBJS.find(x => x.id === id);
  if (!o || !o.fresh.length) return;
  const keys = o.fresh.map(key);
  o.fresh.forEach(i => { i.disliked = true; i.liked = false; });
  o.fresh = [];
  try {
    await post("/api/dislike", { keys, on: true });
  } catch (e) {
    toast("warn", "Не удалось сохранить", e.message);
  }
  render();
}

const CARD_DOM = new Map();

function cardSig(o) {
  const impSig = (o.imported_notes || []).map(n => `${n.origin_id}:${n.ts}:${n.text}`).join("|");
  const spSig = SPOTLIGHT && SPOTLIGHT.id === o.id ? SPOTLIGHT.from : "";
  return `${o.id}\x01${o.zone}\x01${o.price}\x01${o.priceMax}\x01${o.liked ? 1 : 0}\x01${o.disliked ? 1 : 0}\x01${o.fresh.length}\x01${o.active ? 1 : 0}\x01${o.excluded || ""}\x01${o.manual_merged ? 1 : 0}\x01${o.renovation || ""}\x01${o.address}\x01${o.note_ts}\x01${o.note}\x01${impSig}\x01${o.p.source}:${o.p.source_id}\x01${o.id === PICK ? 1 : 0}\x01${MERGE_PICK === o.id ? 1 : 0}\x01${EXPORT_PICK.has(o.id) ? 1 : 0}\x01${canSpotlight() ? 1 : 0}\x01${spSig}\x01${o.rooms}\x01${o.area}\x01${o.kitchen_area ?? ""}\x01${o.build_year ?? ""}\x01${o.floor}/${o.floors}\x01${o.lat != null ? 1 : 0}\x01${o.img ? key(o.img) : ""}\x01${o.relisted ? o.relisted.diff : ""}\x01${o.items.map(i => `${i.source}:${i.source_id}:${i.price}:${i.active ? 1 : 0}:${i.missed || 0}:${i.off_site ? 1 : 0}:${i.manual_primary ? 1 : 0}`).join(",")}`;
}

function getCardEl(o) {
  const sig = cardSig(o);
  const hit = CARD_DOM.get(o.id);
  if (hit && hit.sig === sig) return hit.el;
  const tpl = document.createElement("template");
  tpl.innerHTML = card(o).trim();
  const el = tpl.content.firstElementChild;
  CARD_DOM.set(o.id, { el, sig });
  return el;
}

function syncCardContainer(box, objs) {
  const desired = objs.map(getCardEl);
  const keep = new Set(desired);
  for (let k = box.firstElementChild; k;) {
    const nxt = k.nextElementSibling;
    if (!keep.has(k)) k.remove();
    k = nxt;
  }
  let cur = box.firstElementChild;
  for (const node of desired) {
    if (cur === node) cur = cur.nextElementSibling;
    else box.insertBefore(node, cur);
  }
}

function _refreshCardMergeTips(el) {
  const hasPick = !!MERGE_PICK;
  if (hasPick || renderCards._hadMergePick) {
    const byId = new Map(OBJS.map(o => [o.id, o]));
    el.querySelectorAll(".card").forEach(c => {
      const o = byId.get(c.dataset.id);
      if (o) _setTip(c.querySelector(".merge-btn"), mergeBtnTip(o));
    });
  }
  renderCards._hadMergePick = hasPick;
}

function _syncMergeBanner(el, beforeNode = undefined) {
  const curBar = el.querySelector(":scope > .merge-bar");
  if (MERGE_PICK) {
    const html = mergeBannerHtml();
    if (curBar) curBar.outerHTML = html;
    else if (beforeNode) beforeNode.insertAdjacentHTML("beforebegin", html);
    else el.insertAdjacentHTML("afterbegin", html);
  } else if (curBar) {
    curBar.remove();
  }
}

function _syncExportBanner(el) {
  const curBar = el.querySelector(":scope > .export-bar");
  if (EXPORT_PICK.size) {
    const html = exportBannerHtml();
    const mergeBar = el.querySelector(":scope > .merge-bar");
    if (curBar) curBar.outerHTML = html;
    else if (mergeBar) mergeBar.insertAdjacentHTML("afterend", html);
    else el.insertAdjacentHTML("afterbegin", html);
  } else if (curBar) {
    curBar.remove();
  }
}

function renderCards(rows, mapRows = rows) {
  if (sideOn()) {
    if (!SIDE) { SIDE = makeMap("sideMap"); areaControl(SIDE).addTo(SIDE.map); }
    drawMap(SIDE, mapRows);
    hl = null;
  }
  if (CARD_DOM.size > OBJS.length + 60) {
    const liveIds = new Set(OBJS.map(o => o.id));
    for (const k of CARD_DOM.keys()) if (!liveIds.has(k)) CARD_DOM.delete(k);
  }
  const el = $("#cardsList");
  const LIMIT = 240;
  if (PICK) {
    // выбранная квартира показывается, даже если её скрывают фильтры или лимит карточек
    const p = OBJS.find(o => o.id === PICK);
    const shown = groupOn() ? rows.filter(o => o.zone === p?.zone).slice(0, LIMIT) : rows.slice(0, LIMIT * 2);
    if (p && !shown.includes(p)) rows = [p, ...rows.filter(o => o !== p)];
  }
  if (SPOTLIGHT && SPOTLIGHT.id !== PICK) {
    // подсвеченная партнёром квартира гарантированно выводится, даже если скрыта фильтрами или лимитом
    const sp = OBJS.find(o => o.id === SPOTLIGHT.id);
    const shown = groupOn() ? rows.filter(o => o.zone === sp?.zone).slice(0, LIMIT) : rows.slice(0, LIMIT * 2);
    if (sp && !shown.includes(sp)) rows = [sp, ...rows.filter(o => o !== sp)];
  }
  if (!rows.length) {
    const liked = $("#fLiked").checked, hidden = $("#fHidden").checked, manual = $("#fManual").checked, area = areaOn() && mapRows.length;
    const [head, text] = hidden ? ["Скрытых квартир нет", "Нажмите 👎 на карточке, чтобы убрать квартиру из подборки — она появится здесь"]
      : liked ? ["В избранном пока пусто", "Нажмите ♥ на карточке, чтобы добавить объект"]
      : manual ? ["Карточек, объединённых вручную, нет", "Объединить объявления одной квартиры можно кнопкой со значком звена внизу карточки"]
      : area ? ["В видимой области карты квартир нет", "Сдвиньте или отдалите карту и нажмите «Обновить» на карте — или снимите галочку «Только в видимой области»"]
      : ALL.length ? ["Ничего не найдено", "Ослабьте фильтры или включите другие зоны"] : ["База пока пуста", "Нажмите «Запустить» слева, чтобы собрать объявления"];
    el.innerHTML = mergeBannerHtml() + exportBannerHtml() + `<div class="empty"><b>${head}</b>${text}</div>` + (liked || hidden || manual || area ? "" : aboutTab("cards"));
    return;
  }
  for (const junk of el.querySelectorAll(":scope > .empty, :scope > .about")) junk.remove();
  _syncMergeBanner(el);
  _syncExportBanner(el);
  if (!groupOn()) {
    for (const sec of el.querySelectorAll(":scope > section.zone")) sec.remove();
    let flatBox = el.querySelector(":scope > .cards.flat");
    if (!flatBox) {
      flatBox = document.createElement("div");
      flatBox.className = "cards flat";
      el.appendChild(flatBox);
    }
    syncCardContainer(flatBox, rows.slice(0, LIMIT * 2));
    _refreshCardMergeTips(el);
    return;
  }
  const oldFlat = el.querySelector(":scope > .cards.flat");
  if (oldFlat) oldFlat.remove();
  const zones = Object.keys(ZONE);
  for (let zi = 0; zi < zones.length; zi++) {
    const z = zones[zi];
    const zr = rows.filter(o => o.zone === z);
    let sec = el.querySelector(`:scope > section.zone[data-zone="${z}"]`);
    if (!zr.length) {
      if (sec) sec.remove();
      continue;
    }
    const ppm = median(zr.map(o => o.price_m2).filter(Boolean)), pr = median(zr.map(o => o.price).filter(Boolean));
    const subText = `${zr.length} ${plural(zr.length, "объект", "объекта", "объектов")} · медиана ${fmt(ppm)} ₽/м² · ${mln(pr)} млн ₽${zr.length > LIMIT ? ` · показаны первые ${LIMIT}` : ""}`;
    if (!sec) {
      const tpl = document.createElement("template");
      tpl.innerHTML = `<section class="zone" data-zone="${z}"><div class="zone-head" title="${esc(ZONE_TIP[z])}"><div class="zone-mark ${z}">${z}</div>
        <div><h2>${ZONE[z]}</h2><p>${subText}</p></div>
        <div class="rule"></div></div><div class="cards"></div></section>`;
      sec = tpl.content.firstElementChild;
      let anchor = null;
      for (let k = zi + 1; k < zones.length; k++) {
        anchor = el.querySelector(`:scope > section.zone[data-zone="${zones[k]}"]`);
        if (anchor) break;
      }
      el.insertBefore(sec, anchor);
    } else {
      const pEl = sec.querySelector(".zone-head p");
      if (pEl && pEl.textContent !== subText) pEl.textContent = subText;
    }
    syncCardContainer(sec.querySelector(".cards"), zr.slice(0, LIMIT));
  }
  _refreshCardMergeTips(el);
}

// ♥ и 👎 на карточке. Отметки взаимоисключающие: ♥ снимает 👎 и наоборот (так же и на сервере)
async function toggleLike(id, btn) { return toggleMark(id, "liked", btn); }
async function toggleDislike(id, btn) { return toggleMark(id, "disliked", btn); }

async function toggleMark(id, kind, btn) {
  const o = OBJS.find(x => x.id === id);
  if (!o) return;
  const other = kind === "liked" ? "disliked" : "liked";
  const on = !o[kind];
  o[kind] = on;
  if (on) o[other] = false;
  o.items.forEach(i => { i[kind] = on; if (on) i[other] = false; });
  o.fresh = freshItems(o);
  CARD_DOM.delete(id);
  if (btn) { btn.classList.toggle("on", on); btn.classList.add("pop"); setTimeout(() => btn.classList.remove("pop"), 400); }
  try {
    await post(kind === "liked" ? "/api/like" : "/api/dislike", { keys: o.items.map(key), on });
  } catch (e) {
    toast("warn", "Не удалось сохранить", e.message);
  }
  if (kind === "disliked") {
    // квартира уходит из списка (или возвращается из «Скрытых») — список перерисовываем сразу
    if (["cards", "table", "map", "changes"].includes(view)) render();
    else if (view === "imports" && IMP_BATCH_ID) { renderHeader([]); openImportBatch(IMP_BATCH_ID); }
    else renderHeader(filtered());
    if (view === "dashboard" && typeof paintDashboard === "function") paintDashboard();
    if (on) toast("ok", "Квартира скрыта", "Её не видно в подборке, таблице, на карте и в списках дашборда. Сбор по ней продолжается. Вернуть — кнопка «Скрытые»",
      { label: "Вернуть", fn: () => toggleDislike(id) });
    else if ($("#fHidden").checked) toast("ok", "Квартира возвращена", "Она снова в подборке");
    return;
  }
  if (($("#fLiked").checked && !on) || view === "changes") render();
  else if (view === "imports" && IMP_BATCH_ID) { renderHeader([]); openImportBatch(IMP_BATCH_ID); }
  else renderHeader(filtered());
}

// ---------- таблица ----------
const COLS = [
  ["sources", "Сайты", o => o.items.map(i => (i.off_site
    ? `<span class="off-link" title="${esc(offTip(i, o.active))}"><i class="sdot ${i.source}"></i> ${SRC[i.source]}</span>`
    : `<a href="${esc(i.url)}" target="_blank" rel="noopener" class="${i.active ? "" : "gone"}" title="${fmt(i.price)} ₽"><i class="sdot ${i.source}"></i> ${SRC[i.source]}</a>`) +
    (o.fresh.includes(i) ? ` <span class="tag miss fresh" title="Добавилось в скрытую карточку после того, как вы её скрыли">новое</span>` : "")).join("<br>")],
  ["zone", "Зона", o => `<i class="zdot ${o.zone}"></i> ${ZONE_SHORT[o.zone]}`],
  ["price", "Цена", o => fmt(o.price) + (o.priceMax > o.price ? `<br><small>до ${fmt(o.priceMax)}</small>` : ""), 1],
  ["price_m2", "₽/м²", o => fmt(o.price_m2), 1], ["rooms", "Комн.", o => o.rooms ?? "", 1],
  ["area", "м²", o => o.area ?? "", 1], ["floor", "Этаж", o => `${o.floor ?? "?"}/${o.floors ?? "?"}`, 1],
  ["renovation", "Ремонт", o => RENO[o.renovation] || ""], ["address", "Адрес", o => (SPOTLIGHT && SPOTLIGHT.id === o.id ? `<span class="spot-tbl-tag">👁 ${esc(SPOTLIGHT.from)}</span> ` : "") + esc(o.address || "")],
  ["note", "Заметка", o => o.note ? `<span class="tbl-note" title="${esc(o.note)}">${esc(o.note)}</span>` : ""],
  ["excluded", "Отсев", o => esc(o.excluded || "")], ["first_seen", "Найдено", o => dt(o.first_seen)],
  ["liked", "♥", o => (o.liked ? "♥" : o.disliked ? `<span class="dis-mark" title="Скрыта — отмечена «не нравится»">${ICON.dislike}</span>` : "") + (canSpotlight() ? ` <button type="button" class="icon-btn sm spotlight-btn tbl-spot" data-spot="${esc(o.id)}" title="Подсветить партнёру">${ICON.spot}</button>` : "")],
];

function renderTable(rows) {
  if (!rows.length && !ALL.length) {
    $("#view-table").innerHTML = '<div class="empty"><b>База пока пуста</b>Нажмите «Запустить» слева, чтобы собрать объявления</div>' + aboutTab("table");
    return;
  }
  if (!COLS.some(c => c[0] === tableSort[0])) tableSort = TSORT_DEF.slice();  // колонки больше нет
  {
    const [k, d] = tableSort;
    const v = o => (k === "sources" ? o.sources.length : o[k]) ?? "";
    rows = [...rows].sort((a, b) => (v(a) > v(b) ? 1 : v(a) < v(b) ? -1 : 0) * d);
  }
  $("#view-table").innerHTML = `<div class="panel"><table class="grid"><thead><tr>${COLS.map(c => `<th data-k="${c[0]}" class="${c[3] ? "num" : ""}" title="Клик — сортировать по колонке «${esc(c[1])}»; порядок запомнится">${c[1]}${tableSort[0] === c[0] ? (tableSort[1] > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(0, 1500).map(o => `<tr class="${o.excluded ? "excl" : ""} ${SPOTLIGHT && SPOTLIGHT.id === o.id ? "spotlighted" : ""}" data-id="${esc(o.id)}" title="Клик — открыть карточку в подборке">${COLS.map(c => `<td class="${c[3] ? "num" : ""}">${c[2](o)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  $$("#view-table tbody tr").forEach(tr => tr.onclick = e => { if (!e.target.closest("a, button")) goToCard(tr.dataset.id); });
  $$("#view-table .spotlight-btn").forEach(b => b.onclick = e => { e.stopPropagation(); spotlightObject(b.dataset.spot); });
  $$("#view-table th").forEach(th => th.onclick = () => {
    const k = th.dataset.k;
    tableSort = tableSort[0] === k ? [k, -tableSort[1]] : [k, 1];
    store.set("rw.tableSort", tableSort.join(":"));
    render();
  });
}

// ---------- карты ----------
const COLOR = { cian: "#3E7BFA", avito: "#1FAF6A", yandex: "#F2552F" };
let MAIN = null, SIDE = null, hl = null;
let PICK = null;  // карточка, к которой перешли из таблицы/дашборда: подсвечена, пока по ней не кликнут
let MERGE_PICK = null, mergeBusy = false;  // выбранная первая (основная) карточка для ручного объединения

const objBrief = o => {
  if (!o) return "";
  const r = o.rooms === 0 ? "студия" : o.rooms != null ? `${o.rooms}-к` : "";
  return [r, o.area != null && `${o.area} м²`, o.price && `${fmt(o.price)} ₽`].filter(Boolean).join(", ");
};

function manualTip(o) {
  const list = o.items.map(i => `${SRC[i.source]} · ${fmt(i.price)} ₽${i.area ? ` (${i.area} м², ${i.floor ?? "?"}/${i.floors ?? "?"} эт.)` : ""}`).join("\n");
  return "Объединено вручную: текст, описание и параметры взяты от первой выбранной карточки.\n" +
    `Вошедшие объявления (${o.items.length}):\n${list}\n\n` +
    "Разделить все ручные объединения можно в «Настройках → База данных → Разделить объединенные».";
}

function mergeBtnTip(o) {
  if (!MERGE_PICK) return "Объединить с другой карточкой вручную.\n1-й клик — выбрать эту карточку основной (её текст, описание и параметры сохранятся).\n2-й клик по другой карточке (в «Подборке» или «Изменениях») — влить её сюда.";
  if (MERGE_PICK === o.id) return "Выбрана основной для объединения.\nНажмите значок объединения на второй карточке, чтобы влить её в эту.\nПовторный клик сюда или клавиша Esc — отменить выбор.";
  const first = OBJS.find(x => x.id === MERGE_PICK);
  return `Влить эту карточку в выбранную основную${first ? ` (${objBrief(first)})` : ""}.\nОписание и параметры останутся от основной, а все объявления и цены окажутся в одной карточке.`;
}

function unmergeBtnTip() {
  return "Разделить эту объединённую карточку обратно на исходные по обычным правилам программы.\nОтметки «Избранное», «Скрытые» и заметки сохранятся";
}

function _setTip(el, text) {
  if (!el) return;
  if (el.dataset.tip !== undefined) el.dataset.tip = text;
  else el.title = text;
}

function syncMergePickUI(prevId) {
  const byId = new Map(OBJS.map(o => [o.id, o]));
  if (view === "cards") {
    const list = $("#cardsList");
    if (!list || list.querySelector(".empty")) { render(); return; }
    const bar = list.querySelector(":scope > .merge-bar");
    if (MERGE_PICK) {
      const html = mergeBannerHtml();
      if (bar) bar.outerHTML = html;
      else list.insertAdjacentHTML("afterbegin", html);
    } else if (bar) {
      bar.remove();
    }
    for (const id of new Set([prevId, MERGE_PICK].filter(Boolean))) {
      const cardEl = list.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
      if (!cardEl) continue;
      const on = MERGE_PICK === id;
      cardEl.classList.toggle("merging", on);
      cardEl.querySelector(".merge-btn")?.classList.toggle("on", on);
      const note = cardEl.querySelector(":scope > .merge-note");
      if (on && !note) {
        cardEl.insertAdjacentHTML("afterbegin",
          `<div class="merge-note" title="Нажмите значок объединения на второй карточке — она вольётся в эту. Повторный клик сюда или Esc — отмена">${ICON.merge}<span>Выберите вторую карточку (Esc — отмена)</span></div>`);
      } else if (!on && note) {
        note.remove();
      }
      const cached = CARD_DOM.get(id);
      if (cached && cached.el === cardEl && byId.has(id)) cached.sig = cardSig(byId.get(id));
    }
    list.querySelectorAll(".card").forEach(cardEl => {
      const o = byId.get(cardEl.dataset.id);
      if (o) _setTip(cardEl.querySelector(".merge-btn"), mergeBtnTip(o));
    });
    renderCards._hadMergePick = !!MERGE_PICK;
    return;
  }
  if (view === "changes") {
    const root = $("#view-changes");
    const chBar = root?.querySelector(":scope > .ch-bar");
    if (!root || !chBar) { if (typeof paintChanges === "function") paintChanges(); return; }
    const bar = root.querySelector(":scope > .merge-bar");
    if (MERGE_PICK) {
      const html = mergeBannerHtml();
      if (bar) bar.outerHTML = html;
      else chBar.insertAdjacentHTML("afterend", html);
      root.querySelectorAll(".merge-cancel").forEach(b => b.onclick = cancelMerge);
    } else if (bar) {
      bar.remove();
    }
    for (const id of new Set([prevId, MERGE_PICK].filter(Boolean))) {
      const tile = root.querySelector(`.ch-tile[data-id="${CSS.escape(id)}"]`);
      if (!tile) continue;
      const on = MERGE_PICK === id;
      tile.classList.toggle("merging", on);
      tile.querySelector("[data-merge]")?.classList.toggle("on", on);
      const body = tile.querySelector(".ch-body");
      const note = body?.querySelector(":scope > .merge-note");
      if (on && body && !note) {
        body.insertAdjacentHTML("afterbegin",
          `<div class="merge-note ch" title="Нажмите значок объединения на второй карточке — она вольётся в эту. Повторный клик сюда или Esc — отмена">${ICON.merge}<span>Выберите вторую карточку (Esc — отмена)</span></div>`);
      } else if (!on && note) {
        note.remove();
      }
    }
    root.querySelectorAll(".ch-tile[data-id]").forEach(tile => {
      const o = byId.get(tile.dataset.id);
      if (o) _setTip(tile.querySelector("[data-merge]"), mergeBtnTip(o));
    });
    return;
  }
  render();
}

async function unmergeObject(id, triggerEl) {
  if (!id || mergeBusy) return;
  $("#tip")?.classList.remove("on");
  mergeBusy = true;
  const busyEl = triggerEl?.closest?.(".card, .ch-tile") || triggerEl;
  if (busyEl) busyEl.classList.add("is-busy");
  try {
    const r = await post("/api/objects/unmerge", { object_id: id, defer_snapshots: true });
    if (MERGE_PICK === id) MERGE_PICK = null;
    if (PICK === id) PICK = null;
    if (EXPORT_PICK.has(id) && Array.isArray(r.updated)) {
      EXPORT_PICK.delete(id);
      r.updated.forEach(x => x.object_id && EXPORT_PICK.add(x.object_id));
    }
    if (r.updated) applyListingUpdates(r.updated);
    else await loadData();
    if (view === "settings") renderDbInfo();
    toast("ok", "Карточка разделена", `Разошлась на ${r.parts} ${plural(r.parts, "карточку", "карточки", "карточек")}`);
  } catch (e) {
    toast("warn", "Не удалось разделить", e.message || String(e));
  } finally {
    if (busyEl) busyEl.classList.remove("is-busy");
    mergeBusy = false;
  }
}

function mergeBannerHtml() {
  if (!MERGE_PICK) return "";
  const first = OBJS.find(o => o.id === MERGE_PICK);
  const info = objBrief(first);
  return `<div class="merge-bar">${ICON.merge}<span><b>Режим объединения:</b> выберите вторую карточку — она вольётся в${info ? ` <b>${esc(info)}</b>` : " выбранную"}${first && first.address ? ` (${esc(first.address)})` : ""}. Описание и параметры останутся от первой.</span>
    <button type="button" class="btn-sec sm merge-cancel" title="Отменить выбор (Esc)">Отменить (Esc)</button></div>`;
}

function cancelMerge() {
  if (!MERGE_PICK) return;
  const prev = MERGE_PICK;
  MERGE_PICK = null;
  $("#tip")?.classList.remove("on");
  syncMergePickUI(prev);
}

async function toggleMerge(id, triggerEl) {
  if (!id || mergeBusy) return;
  $("#tip")?.classList.remove("on");
  if (!MERGE_PICK) {
    MERGE_PICK = id;
    syncMergePickUI(null);
    return;
  }
  if (MERGE_PICK === id) {
    cancelMerge();
    return;
  }
  const primaryId = MERGE_PICK;
  mergeBusy = true;
  const busyEl = triggerEl?.closest?.(".card, .ch-tile") || triggerEl;
  if (busyEl) busyEl.classList.add("is-busy");
  try {
    const r = await post("/api/objects/merge", { primary_id: primaryId, secondary_id: id, defer_snapshots: true });
    MERGE_PICK = null;
    if (PICK === primaryId || PICK === id) PICK = r.object_id;
    if (EXPORT_PICK.has(primaryId) || EXPORT_PICK.has(id)) {
      EXPORT_PICK.delete(primaryId);
      EXPORT_PICK.delete(id);
      if (r.object_id) EXPORT_PICK.add(r.object_id);
    }
    if (r.updated) applyListingUpdates(r.updated);
    else await loadData();
    if (view === "settings") renderDbInfo();
    toast("ok", "Карточки объединены", `Объявлений в объединённой карточке: ${r.items}. Описание и параметры сохранены от первой карточки`);
  } catch (e) {
    toast("warn", "Не удалось объединить", e.message || String(e));
  } finally {
    if (busyEl) busyEl.classList.remove("is-busy");
    mergeBusy = false;
  }
}

function makeMap(el) {
  const m = L.map(el, { zoomControl: true, attributionControl: true }).setView([44.49, 34.155], 13);
  L.tileLayer("/tiles/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(m);
  legend().addTo(m);
  const M = { map: m, layer: L.layerGroup().addTo(m), zone: null, markers: {} };
  lockControl(M).addTo(m);
  popupFollowsMouse(M);
  return M;
}

// Подсказка точки (цена, ссылки на объявления) после клика держится, пока мышь на точке, и закрывается, как только
// мышь уходит, — чтобы не загораживать соседние точки. На саму подсказку перейти можно: по её ссылкам кликают.
// Закрывает только движение мыши: если карта сама сдвинулась, чтобы показать подсказку целиком, подсказка остаётся.
function popupFollowsMouse(M) {
  const map = M.map;
  let timer = null;
  const hold = () => { clearTimeout(timer); timer = null; };
  const drop = () => { timer = timer || setTimeout(() => { timer = null; map.closePopup(); }, 250); };
  const tracked = () => M.clicked && M.clicked.isPopupOpen();
  map.on("mousemove", e => {
    if (!tracked()) return;
    if (e.originalEvent.target.closest(".leaflet-popup")) return hold();
    const d = map.latLngToContainerPoint(M.clicked.getLatLng()).distanceTo(e.containerPoint);
    d <= M.clicked.getRadius() + 6 ? hold() : drop();
  });
  map.on("popupopen", e => {
    const el = e.popup.getElement();
    if (el && !el.rwHold) {
      el.rwHold = true;
      el.addEventListener("mouseenter", hold);
      el.addEventListener("mousemove", hold);
    }
  });
  map.on("popupclose", hold);
  map.getContainer().addEventListener("mouseleave", () => { if (tracked()) drop(); });
}

// Цвет точки — набор сайтов объекта (среди выбранных в фильтре): один сайт — его цвет, два — смешанный,
// все три — чёрная точка в белом кольце: её не спутать ни с одной цветной
const ALL3 = "avito+cian+yandex";
const MIX = {
  "cian": "#3E7BFA", "avito": "#1FAF6A", "yandex": "#F2552F",
  "cian+yandex": "#9B5DE5", "avito+yandex": "#F2C230", "avito+cian": "#19B9C4", [ALL3]: "#151515",
};
const MIX_LABEL = {
  "cian": "только ЦИАН", "avito": "только Авито", "yandex": "только Яндекс", "cian+yandex": "ЦИАН + Яндекс",
  "avito+yandex": "Авито + Яндекс", "avito+cian": "ЦИАН + Авито", "avito+cian+yandex": "на всех трёх сайтах",
};
function markerKey(o, srcList = null) {
  const src = srcList || checked("src");
  const s = o.sources.filter(x => src.includes(x));
  return (s.length ? s : o.sources).slice().sort().join("+");
}

function markerStyle(o, on, srcList = null) {
  const k = markerKey(o, srcList), n = k.split("+").length;
  if (k === ALL3)  // белое кольцо толще обычной обводки; тёмный ореол (класс mk-all3) отделяет его от светлой карты
    return { radius: on ? 11 : 7.5, color: o.liked ? "#E0475B" : "#fff", weight: on ? 4.5 : 3.5,
      fillColor: MIX[k], fillOpacity: o.excluded ? .4 : 1, className: "mk-all3" };
  return { radius: on ? 11 : n > 1 ? 8 : 6.5, color: on ? "#111" : o.liked ? "#E0475B" : "#fff", weight: on || o.liked ? 3 : 1.5,
    fillColor: MIX[k] || COLOR[o.p.source], fillOpacity: o.excluded ? .35 : .95 };
}

function markerPopupHtml(o) {
  const links = o.items.map(i => i.off_site
    ? `<span class="off-link" title="${esc(offTip(i, o.active))}">${SRC[i.source]} · ${fmt(i.price)} ₽ (нет на сайте)</span>`
    : `<a href="${esc(i.url)}" target="_blank" rel="noopener">${SRC[i.source]} · ${fmt(i.price)} ₽ →</a>`).join("<br>");
  return `<b>${fmt(o.price)} ₽</b>${o.liked ? " ♥" : ""}<br>${o.rooms ?? "?"}-к, ${o.area ?? "?"} м², ${o.floor ?? "?"}/${o.floors ?? "?"} эт.<br><span style="opacity:.7">${esc(o.address)}</span><br>${links}`;
}

function legend() {
  const c = L.control({ position: "bottomleft" });
  c.onAdd = () => {
    const d = L.DomUtil.create("div", "map-legend");
    d.innerHTML = Object.keys(MIX).map(k => `<span title="${MIX_LABEL[k]}"><i ${k === ALL3 ? 'class="all3"' : `style="background:${MIX[k]}"`}></i>${MIX_LABEL[k]}</span>`).join("") +
      `<span title="Объект в избранном — отмечен сердечком (♥ в подписи точки)"><i class="liked"></i>♥ избранное</span>` +
      `<span title="Объявление не прошло отсев по параметрам поиска"><i class="dim"></i>отсеяно</span>` +
      `<span title="Зона A — красный круг с GeoZone_v1.jpg"><i class="zone"></i>красный круг</span>`;
    L.DomEvent.disableClickPropagation(d);
    return d;
  };
  return c;
}

function drawMap(M, rows) {
  if (!M._sizeTimer) M._sizeTimer = setTimeout(() => { M._sizeTimer = null; M.map.invalidateSize(); }, 60);
  if (!(ZE && ZE.M === M)) drawZone(M);  // во время изменения круга его рисует редактор
  const withCoords = rows.filter(o => o.lat != null);
  const keep = new Set(withCoords.map(o => o.id));
  for (const id of Object.keys(M.markers)) {
    if (!keep.has(id)) {
      M.layer.removeLayer(M.markers[id]);
      delete M.markers[id];
    }
  }
  const activeSrc = checked("src");
  for (const o of withCoords) {
    const st = markerStyle(o, false, activeSrc);
    const cls = st.className || "";
    const sig = `${st.radius}|${st.color}|${st.weight}|${st.fillColor}|${st.fillOpacity}|${cls}`;
    let mk = M.markers[o.id];
    if (mk) {
      mk.obj = o;
      const ll = mk.getLatLng();
      if (ll.lat !== o.lat || ll.lng !== o.lon) mk.setLatLng([o.lat, o.lon]);
      if (mk._sig !== sig || mk._hl) {
        mk._hl = false;
        if (mk._cls !== cls) {
          M.layer.removeLayer(mk);
          mk = null;
        } else {
          mk.setStyle(st);
          mk._sig = sig;
        }
      }
      if (mk && mk.isPopupOpen()) mk.setPopupContent(markerPopupHtml(o));
    }
    if (!mk) {
      mk = L.circleMarker([o.lat, o.lon], st)
        .bindPopup(() => markerPopupHtml(mk.obj))
        .addTo(M.layer);
      mk.obj = o;
      mk._sig = sig;
      mk._cls = cls;
      mk._hl = false;
      mk.on("click", () => { M.clicked = mk; focusCard(mk.obj.id); });
      M.markers[o.id] = mk;
    }
  }
}

function renderMap(rows) { if (!MAIN) MAIN = makeMap("map"); drawMap(MAIN, rows); }

// ---------- подборка: только квартиры в видимой области карты рядом ----------
// Область запоминается в момент, когда ставят галочку или нажимают «Обновить», — а не на каждый сдвиг карты:
// карту двигают, чтобы посмотреть, и список не должен прыгать под рукой. Сдвинули — кнопка «Обновить» подсвечивается.
const AREA = { on: false, bounds: null };
const areaOn = () => AREA.on && !!AREA.bounds && view === "cards" && sideOn();
const inArea = rows => areaOn() ? rows.filter(o => o.lat != null && AREA.bounds.contains([o.lat, o.lon])) : rows;

function areaControl(M) {
  const c = L.control({ position: "topleft" });
  c.onAdd = () => {
    const box = L.DomUtil.create("div", "area-ctl");
    box.innerHTML = `<label class="area-chk" title="Показывать в списке карточек только квартиры, которые сейчас видны на карте. Карта при этом показывает все квартиры по фильтрам — двигайте и приближайте её, а потом нажмите «Обновить»">
        <input type="checkbox"><span>Только в видимой области</span></label>
      <button class="area-upd" disabled title="Взять видимую сейчас область карты и пересобрать по ней список карточек">${ICON.refresh}<span>Обновить</span></button>`;
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    const chk = box.querySelector("input"), upd = box.querySelector(".area-upd");
    const take = () => { AREA.bounds = M.map.getBounds(); upd.classList.remove("stale"); render(); $("#cardsList").scrollTop = 0; };
    chk.checked = AREA.on;
    chk.onchange = () => {
      AREA.on = chk.checked;
      upd.disabled = !AREA.on;
      box.classList.toggle("on", AREA.on);
      if (AREA.on) take(); else { upd.classList.remove("stale"); render(); }
    };
    upd.onclick = take;
    // карту сдвинули или приблизили — список отстал от того, что на экране: подсказываем «Обновить»
    M.map.on("moveend zoomend", () => {
      if (!AREA.on || !AREA.bounds) return;
      const b = M.map.getBounds();
      upd.classList.toggle("stale", !(b.getSouthWest().equals(AREA.bounds.getSouthWest(), 1e-6) && b.getNorthEast().equals(AREA.bounds.getNorthEast(), 1e-6)));
    });
    return box;
  };
  return c;
}

// ---------- круг (зона A) на карте: замок, перенос центра кликом, радиус — за край круга ----------
const LOCK_ICON = {
  closed: '<svg viewBox="0 0 24 24"><rect x="5" y="10.5" width="14" height="10" rx="2.2"/><path d="M8 10.5V7.8a4 4 0 0 1 8 0v2.7"/></svg>',
  open: '<svg viewBox="0 0 24 24"><rect x="5" y="10.5" width="14" height="10" rx="2.2"/><path d="M8 10.5V7.8a4 4 0 0 1 7.6-1.7"/></svg>',
};
const LOCK_TIP = {
  closed: "Круг (зона A) закреплён.\nНажмите на замок, чтобы изменить круг: клик по карте переносит центр, край круга тянется мышью. Точки на карте в это время не нажимаются",
  open: "Изменение круга: клик по карте — перенести центр, потяните край круга или белую ручку — изменить радиус.\nНажмите на замок, чтобы закрепить и сохранить круг",
};
let ZE = null;  // идёт изменение круга: { M, center, radius, orig, layers, resizing, suppress }

const zoneColor = () => getComputedStyle(document.documentElement).getPropertyValue("--gold").trim() || "#B4873B";

function drawZone(M) {
  const z = SETTINGS.zone, gold = zoneColor();
  const sig = `${gold}|${JSON.stringify(z)}`;
  if (M.zone && M._zoneSig === sig) return;
  if (M.zone) { M.zone.remove(); M._zoneSig = null; }
  const style = { color: gold, weight: 2.5, dashArray: "7 6", fillColor: gold, fillOpacity: .10 };
  M.zone = (z.polygon ? L.polygon(z.polygon, style) : L.circle(z.center, { radius: z.radius_m, ...style })).addTo(M.map);
  M._zoneSig = sig;
}

function lockControl(M) {
  const c = L.control({ position: "topright" });
  c.onAdd = () => {
    const box = L.DomUtil.create("div", "zone-ctl");
    box.innerHTML = `<div class="leaflet-bar"><a href="#" role="button" class="zone-lock" title="${esc(LOCK_TIP.closed)}">${LOCK_ICON.closed}</a></div>
      <div class="zone-edit hidden"></div>`;
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    box.querySelector(".zone-lock").addEventListener("click", e => { e.preventDefault(); toggleZoneEdit(M); });
    M.lockBox = box;
    return box;
  };
  return c;
}

// Точка на окружности к востоку от центра — там стоит ручка радиуса
function eastOf(c, r) {
  return L.latLng(c.lat, c.lng + r / (111320 * Math.cos(c.lat * Math.PI / 180)));
}

function toggleZoneEdit(M) {
  if (ZE && ZE.M === M) return finishZoneEdit();
  if (ZE) endZoneEdit();
  if (Object.values(lastStates).includes("running"))
    return toast("warn", "Идёт сбор данных", "Круг можно изменить после окончания сбора: зоны пересчитываются по всей базе");
  if (SETTINGS.zone.polygon) return toast("warn", "Зона задана многоугольником", "Изменить на карте можно только круг");
  const z = SETTINGS.zone, gold = zoneColor(), map = M.map;
  const center = L.latLng(z.center);
  ZE = { M, center, radius: z.radius_m, orig: { center, radius: z.radius_m }, resizing: false, suppress: false };
  map.closePopup();
  if (M.zone) { M.zone.remove(); M._zoneSig = null; }
  const circle = L.circle(center, { radius: ZE.radius, interactive: false, color: gold, weight: 3, fillColor: gold, fillOpacity: .16 }).addTo(map);
  const dot = L.circleMarker(center, { radius: 5, interactive: false, color: "#fff", weight: 2, fillColor: gold, fillOpacity: 1 }).addTo(map);
  const handle = L.marker(eastOf(center, ZE.radius), {
    draggable: true, keyboard: false, title: "Потяните, чтобы изменить радиус",
    icon: L.divIcon({ className: "zone-handle", iconSize: [18, 18], iconAnchor: [9, 9] }),
  }).addTo(map);
  ZE.layers = { circle, dot, handle };
  handle.on("drag", () => setZoneRadius(ZE.center.distanceTo(handle.getLatLng()), false));
  handle.on("dragend", () => { ZE.suppress = true; setZoneRadius(ZE.radius, true); });
  // край круга: рядом с окружностью курсор меняется, и её можно тянуть
  ZE.onMove = e => {
    const near = edgeDistance(e.containerPoint) < 9;
    if (ZE.resizing) setZoneRadius(ZE.center.distanceTo(e.latlng), true);
    map.getContainer().classList.toggle("zone-near-edge", near || ZE.resizing);
  };
  ZE.onDown = e => {
    if (edgeDistance(e.containerPoint) >= 9) return;
    ZE.resizing = true;
    map.dragging.disable();
  };
  ZE.onUp = () => {
    if (!ZE || !ZE.resizing) return;
    ZE.resizing = false;
    ZE.suppress = true;
    map.dragging.enable();
  };
  ZE.onClick = e => {
    if (ZE.suppress) { ZE.suppress = false; return; }
    ZE.center = e.latlng;
    circle.setLatLng(e.latlng);
    dot.setLatLng(e.latlng);
    setZoneRadius(ZE.radius, true);
  };
  map.on("mousemove", ZE.onMove).on("mousedown", ZE.onDown).on("mouseup", ZE.onUp).on("click", ZE.onClick);
  map.doubleClickZoom.disable();  // двойной клик не должен приближать карту, пока клик переносит круг
  document.addEventListener("mouseup", ZE.onUp);
  map.getContainer().classList.add("zone-editing");
  const lock = M.lockBox.querySelector(".zone-lock");
  lock.innerHTML = LOCK_ICON.open;
  lock.title = LOCK_TIP.open;
  lock.classList.add("open");
  M.lockBox.querySelector(".zone-edit").classList.remove("hidden");
  setZoneRadius(ZE.radius, true);
}

// Расстояние в пикселях от точки экрана до окружности круга
function edgeDistance(pt) {
  const map = ZE.M.map, c = map.latLngToContainerPoint(ZE.center), e = map.latLngToContainerPoint(eastOf(ZE.center, ZE.radius));
  return Math.abs(pt.distanceTo(c) - c.distanceTo(e));
}

function setZoneRadius(r, moveHandle) {
  ZE.radius = Math.max(50, Math.min(50000, r));
  ZE.layers.circle.setRadius(ZE.radius);
  if (moveHandle) ZE.layers.handle.setLatLng(eastOf(ZE.center, ZE.radius));
  const moved = ZE.orig.center.distanceTo(ZE.center);
  ZE.M.lockBox.querySelector(".zone-edit").innerHTML = `
    <div class="ze-row"><span>Радиус</span><b>${fmt(Math.round(ZE.radius))} м</b></div>
    <div class="ze-row"><span>Центр</span><b>${ZE.center.lat.toFixed(5)}, ${ZE.center.lng.toFixed(5)}</b></div>
    ${moved >= 1 || Math.round(ZE.radius) !== ZE.orig.radius ? `<div class="ze-diff">было: ${fmt(ZE.orig.radius)} м${moved >= 1 ? `, центр сдвинут на ${fmt(Math.round(moved))} м` : ""}</div>` : ""}
    <div class="ze-hint">Клик по карте — перенести центр. Тяните край круга или белую ручку — радиус.</div>
    <div class="ze-acts"><button class="btn-sec sm" data-ze="cancel" title="Вернуть круг как был и закрыть замок">Отменить</button>
      <button class="btn-pri sm" data-ze="save" title="Закрыть замок и сохранить круг">${ICON.ok}Готово</button></div>`;
  ZE.M.lockBox.querySelector('[data-ze="cancel"]').onclick = () => { endZoneEdit(); };
  ZE.M.lockBox.querySelector('[data-ze="save"]').onclick = () => finishZoneEdit();
}

function endZoneEdit() {
  if (!ZE) return;
  const { M, layers } = ZE, map = M.map;
  Object.values(layers).forEach(l => l.remove());
  map.off("mousemove", ZE.onMove).off("mousedown", ZE.onDown).off("mouseup", ZE.onUp).off("click", ZE.onClick);
  document.removeEventListener("mouseup", ZE.onUp);
  map.dragging.enable();
  map.doubleClickZoom.enable();
  map.getContainer().classList.remove("zone-editing", "zone-near-edge");
  const lock = M.lockBox.querySelector(".zone-lock");
  lock.innerHTML = LOCK_ICON.closed;
  lock.title = LOCK_TIP.closed;
  lock.classList.remove("open");
  M.lockBox.querySelector(".zone-edit").classList.add("hidden");
  ZE = null;
  drawZone(M);
}

function finishZoneEdit() {
  const { center, radius, orig } = ZE;
  const moved = orig.center.distanceTo(center), r = Math.round(radius);
  if (moved < 1 && r === orig.radius) return endZoneEdit();  // ничего не изменилось
  // какие квартиры (с координатами) войдут в круг и выйдут из него
  const inside = (o, c, rad) => L.latLng(o.lat, o.lon).distanceTo(c) <= rad;
  const objs = OBJS.filter(o => o.active && o.lat != null);
  const inN = objs.filter(o => !inside(o, orig.center, orig.radius) && inside(o, center, r)).length;
  const outN = objs.filter(o => inside(o, orig.center, orig.radius) && !inside(o, center, r)).length;
  const nowIn = objs.filter(o => inside(o, center, r)).length;
  $("#modalBody").innerHTML = `<div class="confirm">
      <h3>Сохранить новый круг?</h3>
      <p>${moved >= 1 ? `Центр сдвинут на <b>${fmt(Math.round(moved))} м</b>. ` : ""}Радиус ${fmt(orig.radius)} → <b>${fmt(r)} м</b>.</p>
      <p>В круг войдут <b class="good">${inN}</b> ${plural(inN, "квартира", "квартиры", "квартир")}, выйдут <b class="bad">${outN}</b>; всего в круге будет ${nowIn} (по квартирам в продаже с координатами).</p>
      <p class="meta">Зоны всех объявлений, подборка, карта, дашборд и история графиков пересчитаются под новый круг. Это займёт несколько секунд.</p>
      <div class="confirm-acts"><button class="btn-sec" data-c="back">Продолжить изменять</button><button class="btn-sec" data-c="revert">Вернуть как было</button>
        <button class="btn-pri" data-c="save">${ICON.ok}Сохранить круг</button></div></div>`;
  $("#modal").classList.remove("hidden");
  $('#modalBody [data-c="back"]').onclick = closeModal;
  $('#modalBody [data-c="revert"]').onclick = () => { closeModal(); endZoneEdit(); };
  $('#modalBody [data-c="save"]').onclick = async () => {
    const btn = $('#modalBody [data-c="save"]');
    btn.disabled = true;
    btn.textContent = "Пересчитываю…";
    try {
      const res = await post("/api/zone", { center: [center.lat, center.lng], radius_m: r });
      closeModal();
      await loadSettings();
      endZoneEdit();
      await loadData();
      toast("ok", "Круг сохранён", `В круг вошло ${res.moved_in}, вышло ${res.moved_out} объявлений. История рынка пересчитана (${res.snapshots} ${plural(res.snapshots, "проверка", "проверки", "проверок")})`);
    } catch (e) {
      closeModal();
      toast("warn", "Круг не сохранён", e.message);
    }
  };
}

function highlight(id, fly) {
  if (!SIDE || !sideOn()) return;
  if (hl && SIDE.markers[hl]) {
    SIDE.markers[hl]._hl = false;
    SIDE.markers[hl].setStyle(markerStyle(SIDE.markers[hl].obj, false));
  }
  hl = id;
  const mk = SIDE.markers[id];
  if (!mk) return;
  mk._hl = true;
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
function runGroupBadge(g) {
  if (g.runs.some(r => r.status === "running")) return `<span class="st-badge running">идёт сбор</span>`;
  return `<span class="st-badge ${g.bad ? "partial" : "done"}">${g.bad ? "неудачный" : "готово"}</span>`;
}

// в итоге прогона цифры уже есть в строке; важна приписка в скобках — ею сбор сам признаётся,
// что выдаче верить нельзя («пропало 40 из 400 — похоже на сбой», «выдача неполная»)
function runNote(r) {
  const m = /\(([^)]*)\)\s*$/.exec(r.message || "");
  return m ? m[1] : r.status === "done" ? "" : r.message || "";
}

function runLowCoverage(r) {
  return !!(r.declared && r.collected != null && r.collected < (r.source === "cian" ? 0.85 : 0.95) * r.declared && r.source !== "avito");
}

async function renderRuns() {
  const groups = await api("/api/db/launches?limit=200");
  const hm = s => (s || "").replace("T", " ").slice(11, 16);
  const num = (label, v, cls = "", tip = "") => v == null ? ""
    : `<span class="rp-num"${tip ? ` title="${esc(tip)}"` : ""}>${label} <b${cls ? ` class="${cls}"` : ""}>${fmt(v)}</b></span>`;
  $("#view-runs").innerHTML = groups.length ? `<div class="runs-groups">${groups.map(g => `<div class="rp-g">
    <div class="rp-gh">
      <span class="rp-when"><b>${dtt(g.started)}</b><i>${g.runs.length} ${plural(g.runs.length, "сайт", "сайта", "сайтов")} · собрано ${fmt(g.collected)}</i></span>
      ${runGroupBadge(g)}
    </div>
    ${g.runs.map(r => {
      const col = r.collected != null ? r.collected : r.found, low = runLowCoverage(r);
      const span = hm(r.started) + (r.finished ? `–${hm(r.finished)}` : "");
      const meta = [span, r.mode].filter(Boolean).join(" · ");
      const timeTip = [`Старт: ${dtt(r.started)}`, r.finished ? `Финиш: ${dtt(r.finished)}` : "", r.mode ? `Режим: ${r.mode}` : ""].filter(Boolean).join("\n");
      return `<div class="rp-r">
        <span class="rp-src"><i class="sdot ${r.source}"></i>${SRC[r.source] || r.source}</span>
        <span class="st-badge ${r.status}" title="${esc(STATE_TIP[r.status] || "")}">${STATE[r.status] || r.status}</span>
        ${meta ? `<span class="rp-time" title="${esc(timeTip)}">${esc(meta)}</span>` : ""}
        ${num("стр.", r.pages)}${num("заявлено", r.declared, "", "Сколько объявлений сайт заявил в выдаче")}${num("собрано", col, low ? "bad" : "", low ? `Собрано меньше ожидаемой доли от заявленных ${fmt(r.declared)}` : "Сколько уникальных объявлений получено")}${num("новых", r.new)}${num("изм.", r.changed)}${num("снято", r.removed)}
        <span class="rp-msg" title="${esc(r.message || "")}">${esc(runNote(r))}</span>
        <span class="rp-act">${r.can_continue ? `<button type="button" class="btn-sec sm rp-cont" data-continue-run="${r.id}" data-src="${esc(r.source)}" title="Продолжить этот прогон с сохранённой позиции в рамках текущего запуска">${ICON.refresh}Продолжить</button>` : ""}${r.has_log ? `<button class="icon-btn" data-log="${r.id}" title="Подробный журнал прогона: каждый запрос, коды ответов, время, страницы выдачи, сохранённые при сбоях страницы">${ICON.list}</button>` : ""}</span>
      </div>`;
    }).join("")}
  </div>`).join("")}</div>`
    : `<div class="empty"><b>Прогонов ещё не было</b>Запустите сбор данных в панели слева</div>${aboutTab("runs")}`;
  $$("#view-runs [data-log]").forEach(b => b.onclick = () => openRunLog(+b.dataset.log));
  $$("#view-runs [data-continue-run]").forEach(b => b.onclick = () => continueRun({ run_id: +b.dataset.continueRun, source: b.dataset.src }, b));
}

// Подробный журнал прогона в окне: просмотр, сохранение в файл, папка журналов
async function openRunLog(id) {
  let d;
  try { d = await api(`/api/runs/${id}/log`); } catch (e) { return toast("warn", "Журнала нет", e.message); }
  $("#modalBody").innerHTML = `<div class="log-head"><h3>Журнал прогона №${id}</h3><span class="meta">${esc(d.file)}</span>
      <span class="log-acts"><button class="btn-sec" id="logSave" title="Сохранить журнал прогона в файл">${ICON.down}Сохранить в файл…</button>
      <button class="btn-sec" id="logFolder" title="Открыть папку logs: общий журнал, журналы прогонов (runs) и сохранённые страницы (snapshots)">${ICON.folder}Папка журналов</button></span></div>
    ${d.snapshots.length ? `<div class="log-snaps" title="Страницы, которые сайт показал при сбое, — лежат в logs/snapshots">Сохранённые страницы: ${d.snapshots.map(esc).join(", ")}</div>` : ""}
    <pre class="log-text">${esc(d.text)}</pre>`;
  $("#modal").classList.remove("hidden");
  $("#logSave").onclick = async () => {
    const r = desktop() ? await desktop().export_run_log(id) : await post(`/api/runs/${id}/log/export`);
    if (r && r.path) toast("ok", "Журнал сохранён", r.path);
  };
  $("#logFolder").onclick = () => desktop() ? desktop().open_logs_folder() : post("/api/logs/open");
}

// ---------- настройки и база ----------
const sw = (id, on) => `<span class="sw"><input type="checkbox" id="${id}" ${on ? "checked" : ""}><i></i></span>`;

async function renderSettings() {
  await loadSettings();  // свежие значения: задержка Авито могла вырасти во время сбора
  const f = SETTINGS.filters, z = SETTINGS.zone, s = SETTINGS.sites, b = SETTINGS.browser || {};
  const field = (label, control, hint) => `<div class="field"><label>${label}${hint ? `<span class="f-hint">${hint}</span>` : ""}</label>${control}</div>`;
  const modeSel = k => `<select data-site="${k}">${[["auto", "авто"], ["http", "только HTTP"], ["browser", "только браузер"]]
    .map(([v, t]) => `<option value="${v}" ${s[k].mode === v ? "selected" : ""}>${t}</option>`).join("")}</select>`;
  const modeHint = "<b>авто</b> — сначала быстрый HTTP-запрос с отпечатком Chrome; если сайт блокирует, сам переходит на скрытый браузер. " +
    "<b>только HTTP</b> — быстрее, но при блокировке сбор остановится. <b>только браузер</b> — медленнее, но надёжнее на строгих сайтах.";
  $("#view-settings").innerHTML = `<div class="settings">
    <div class="s-card s-params"><h3>Параметры поиска</h3><p class="hint">Передаются сайтам при сборе и используются для отсева. После сохранения вся база пересчитывается сразу; новые границы поиска на сайтах — при следующем сборе.</p>
      ${field("Комнаты", `<input type="text" id="sRooms" value="${f.rooms.join(", ")}">`, "Через запятую. Студия — 0. Объявления с другим числом комнат отсеиваются с причиной «комнат: N».")}
      ${field("Площадь от, м²", `<input type="number" id="sArea" value="${f.area_min}">`, "Минимальная общая площадь. Меньшие — отсев «площадь».")}
      ${field("Цена от, ₽", `<input type="number" id="sPmin" value="${f.price_min}">`, "Нижняя граница цены. Передаётся сайтам; Яндекс иногда подмешивает «похожие» дешевле — они отсеиваются.")}
      ${field("Цена до, ₽", `<input type="number" id="sPmax" value="${f.price_max}">`, "Верхняя граница цены.")}
      ${field("Ремонт", `<select id="sReno">
        <option value="exclude_none" ${f.renovation === "exclude_none" ? "selected" : ""}>без ремонта — отсеять</option>
        <option value="strict" ${f.renovation === "strict" ? "selected" : ""}>только указанный ремонт</option>
        <option value="off" ${f.renovation === "off" ? "selected" : ""}>не учитывать</option></select>`,
        "<b>без ремонта — отсеять</b>: убираются «требует ремонта», «без отделки», черновая/предчистовая (по полю сайта или тексту); где ремонт не указан — остаётся с жёлтой меткой. <b>только указанный</b>: остаются лишь объявления с явно указанным ремонтом. У Авито и Яндекса вид ремонта берётся из поля «Ремонт» сайта, у ЦИАН — отдельными выдачами по каждому виду ремонта (в его общей выдаче вида ремонта нет); описание читается, только если сайт вид ремонта не сообщил.")}
      ${field("Исключать обременение", sw("sEnc", f.exclude_encumbrance), "Ищет в тексте «обременение», «арест», «в залоге», «залог у банка». Отрицания («без обременений», «аресты сняты») не считаются. Выключено — объявление остаётся с меткой «обременение?».")}
      ${field("Исключать доли", sw("sShare", f.exclude_share), "Категория «доля» на ЦИАН, заголовок «Доля…» на Авито, в тексте «доля», «1/2 доли», «долевая собственность».")}
      ${field("Исключать аукционы и торги", sw("sAuc", f.exclude_auction), "В тексте «аукцион», «торги», «банкротство», «конкурсный управляющий». «Торг уместен» не считается.")}
    </div>
    <div class="s-card s-geo"><h3>Геозона «Красный круг»</h3><p class="hint">Зона A на карточках и карте. Калибровка по GeoZone_v1.jpg: Ливадийский дворец, Белая дача, «Атлантида» (точность ~50–70 м). Удобнее менять на карте: замок в правом верхнем углу карты.</p>
      ${field("Центр — широта", `<input type="number" step="0.000001" id="zLat" value="${z.center[0]}">`, "Градусы северной широты центра круга.")}
      ${field("Центр — долгота", `<input type="number" step="0.000001" id="zLon" value="${z.center[1]}">`, "Градусы восточной долготы центра круга.")}
      ${field("Радиус, м", `<input type="number" id="zR" value="${z.radius_m}">`, "Объекты ближе этого расстояния к центру относятся к зоне A, остальные в городе — к B, в посёлках — к C.")}
    </div>
    <div class="s-card s-collect"><h3>Сбор данных</h3><p class="hint">Как программа забирает объявления. Пазл-капчу Авито программа проходит сама; если сайт покажет другую капчу, окно браузера появится само и прозвучит сигнал — пройдите её, сбор продолжится.</p>
      ${field("ЦИАН", modeSel("cian"), modeHint)}
      ${field("Авито", modeSel("avito"), "Авито блокирует быстрые HTTP-запросы, поэтому в режиме <b>авто</b> он сразу работает через браузер: листает выдачу и открывает объявления, как человек. <b>Только HTTP</b> для Авито не сработает. Это самый медленный сайт.")}
      ${field("Яндекс", modeSel("yandex"), "Яндекс обычно стабильно отвечает на быстрые HTTP-запросы.")}
      ${field("Авито: страниц объявлений за прогон", `<input type="number" id="sAvDet" value="${s.avito.max_details}">`, "Сколько новых объявлений Авито открывать за один сбор (ради координат и вида ремонта). Остальные — в следующий раз. Больше — дольше и выше риск ограничения IP.")}
      ${field("Авито: пауза между запросами, с", `<span class="pair"><input type="number" step="1" min="1" id="sAvD0" value="${s.avito.delay[0]}"><i>—</i><input type="number" step="1" min="1" id="sAvD1" value="${s.avito.delay[1]}"></span>`,
        "Случайная пауза в этом диапазоне перед каждым запросом. При ограничении IP растёт сама в ~1,5 раза и сохраняется. " +
        `После 3 сборов подряд без капчи и ограничений понемногу возвращается к заданной здесь${s.avito.delay_base && s.avito.delay_base.join() !== s.avito.delay.join() ? ` (задано ${s.avito.delay_base.join("–")} с, сейчас выросла)` : ""}.`)}
      ${field("Авито: проверок снятых за прогон", `<input type="number" min="0" id="sAvVer" value="${s.avito.max_verify ?? 20}">`, "Сколько пропавших из поиска объявлений Авито открывать за один сбор, чтобы проверить, сняты ли они. Каждая проверка — отдельная страница через браузер с паузой, поэтому много проверок подряд вызывает капчу. Непроверенные получают отметку «нет в поиске»; если их нет в поиске и во втором сборе подряд, программа считает, что на Авито их больше нет — ссылка убирается, а с продажи квартира снимается, только когда её не осталось ни на одном сайте.")}
      ${field("Окно браузера", `<select id="sWin">
        ${[["hidden", "скрытое"], ["visible", "видимое"], ["headless", "без окна (headless)"]]
          .map(([v, t]) => `<option value="${v}" ${(b.window || "hidden") === v ? "selected" : ""}>${t}</option>`).join("")}</select>`,
        "<b>скрытое</b> — полноценное окно за пределами экрана, появляется только для капчи. <b>видимое</b> — можно наблюдать за сбором. <b>headless</b> — без окна; сайты распознают такой режим легче.")}
      ${field("Авито: проходить пазл-капчу автоматически", sw("sAutoCap", b.auto_captcha !== false),
        "Пазл с ползунком (GeeTest) программа собирает сама: находит вырез на картинке и тянет ползунок мышью, как человек. Капчу другого вида (иконки, галочка, символы) или после неудачных попыток проходит человек: окно браузера появится само, прозвучит сигнал и придёт уведомление.")}
    </div>
    <div class="s-card s-db" id="dbCard"><h3>База данных</h3><p class="hint">Объявления, фото, избранное, история цен и настройки поиска — в папке database. Перенос на другое устройство: «Экспорт» здесь → «Импорт» там.</p>
      <div class="db-stats" id="dbStats"></div>
      <div class="db-path" id="dbPath" title="Папка базы — её можно копировать целиком"></div>
      <div class="db-actions"><div class="btn-row">
        <button class="btn-sec" id="btnExport" title="Сохранить всю базу (объявления, фото, избранное, историю, настройки поиска) в один .zip">${ICON.down}Экспорт…</button>
        <button class="btn-sec" id="btnImport" title="Загрузить базу из .zip. Перед импортом текущая база сохраняется в database\\backups — список копий и восстановление из них в «Очистке данных»">${ICON.up}Импорт…</button>
        <select id="impMode" title="«Объединить» — добавить новое и обновить более свежим; «заменить» — полностью заменить текущую базу"><option value="merge">объединить с текущей</option><option value="replace">заменить полностью</option></select>
      </div>
      <div class="btn-row" style="margin-top:8px">
        <button class="btn-sec" id="btnImages" title="Скачать фото, которые не загрузились (повторяется и само — после сбора и при запуске)">${ICON.img}Догрузить фото</button>
        <button class="btn-sec" id="btnRecheck" title="Открыть на сайтах все объявления, помеченные снятыми, и вернуть те, что на самом деле активны. Авито проверяется через браузер — это дольше">${ICON.ok}Перепроверить снятые</button>
        ${desktop() ? `<button class="btn-sec" id="btnFolder" title="Открыть папку database в Проводнике">${ICON.folder}Открыть папку</button>` : ""}
      </div>
      <div class="btn-row" style="margin-top:8px">
        <button class="btn-sec" id="btnShareLiked" title="Сохранить избранное одной веб-страницей: плитки и таблица со своими фильтрами и сортировками, фотографии внутри файла. Открывается без интернета и без программы — такой файл можно просто отправить">${ICON.share}Экспорт избранного…</button>
        <button class="btn-sec" id="btnUnmergeAll" disabled title="Нет карточек, объединённых вручную">${ICON.unmerge}Разделить объединенные</button>
      </div></div>
    </div>
    <div class="s-card s-purge"><h3>Очистка данных</h3><p class="hint">Если сбор прошёл неудачно — сайт отдал не ту выдачу или парсер ошибся, — данные можно убрать из статистики: весь период целиком или только отдельные прогоны.</p>
      ${field("Период", `<span class="pair dates"><input type="date" id="pFrom" title="С какого дня (включительно). Пусто — с самого начала"><i>—</i><input type="date" id="pTo" title="По какой день (включительно). Пусто — по сегодня"></span>`,
        "Обе даты включительно. Пустое «с» — с самого начала, пустое «по» — по сегодняшний день.")}
      <div class="purge-presets"><span>Быстро:</span>${[["today", "сегодня"], ["yesterday", "вчера"], ["3", "3 дня"], ["7", "неделя"], ["from-start", "всё до вчера"]]
        .map(([v, l]) => `<button class="chip" data-pp="${v}">${l}</button>`).join("")}</div>
      <div class="purge-prev" id="purgePrev" title="Удаляются: проверки рынка (точки графиков), изменения, прогоны и записи цен за период; объявления, которые видели только в эти дни, — целиком. Снятые в эти дни возвращаются в продажу и проверятся заново при следующем сборе">Выберите период — здесь появится, что будет удалено.</div>
      <div class="btn-row"><button class="btn-sec danger" id="btnPickRuns" title="Список сборов за выбранный период: что собрал каждый сайт и чем закончил. Галочками отмечают неудачные — удалятся только они">${ICON.list}Удалить выбранные прогоны…</button>
        <button class="btn-sec danger" id="btnPurge" disabled title="Удалить данные выбранного периода целиком (с резервной копией)">${ICON.trash}Удалить всё за период…</button></div>
      <div class="purge-backups" id="purgeBackups"></div>
      <div class="purge-all"><span>Полностью: все объявления, история, изменения, избранное и фото. Настройки поиска и круг останутся.</span>
        <button class="btn-sec danger" id="btnClearAll" title="Очистить всю базу (с резервной копией в database\\backups)">${ICON.trash}Очистить всю базу…</button></div>
    </div>
    <div class="s-card s-ui"><h3>Интерфейс</h3><p class="hint">Поведение окна программы. Применяется сразу — отдельно сохранять не нужно.</p>
      ${field("Сворачивать в трей по закрытию", sw("sTray", (SETTINGS.ui || {}).tray_on_close !== false),
        "<b>Включено</b>: кнопка «Закрыть» убирает окно в трей — программа остаётся в области уведомлений, сбор и автообновление продолжаются (уведомление об этом приходит один раз за запуск). Чтобы выйти — «Выход» в меню значка в трее. <b>Выключено</b>: значка в трее нет вовсе, «Закрыть» закрывает программу, и уведомления Windows о рынке и о капче не приходят — показывает их именно значок. Кнопка «Свернуть» в обоих случаях сворачивает окно в панель задач.")}
    </div>
    <div class="s-card s-lan"><h3>Обмен и локальная сеть (LAN)</h3><p class="hint">Прямой обмен выбранными карточками, Избранным и полной базой между вашими компьютерами в одной локальной сети (без облаков и повторного сбора сайтов).</p>
      <div class="btn-row"><button type="button" class="btn-sec" id="btnOpenLan">${ICON.share}Локальная сеть и устройства…</button><span class="meta" id="sLanHint">${LAN_SUM.enabled ? (LAN_SUM.online_peers ? `Онлайн устройств: ${LAN_SUM.online_peers}` : "LAN включена, ожидание устройств") : "Доступ по сети выключен"}</span></div>
    </div>
    <div class="s-card s-update" id="updateCard"><h3>Обновление программы</h3><p class="hint">Проверка релизов на GitHub и аккуратное обновление с очисткой устаревших файлов. Ваша база, личные заметки, объединённые карточки, ♥ избранное и 👎 скрытые сохраняются полностью.</p>
      ${field("Автоматически проверять обновления", sw("sUpdAuto", (SETTINGS.updates || {}).auto_check !== false),
        "В фоновом режиме проверяет выход нового релиза на GitHub, заранее скачивает проверенный архив и предлагает кнопку «Обновить и перезапустить».")}
      ${field("Периодичность проверки", `<select id="sUpdInt">
        ${[["day", "раз в день"], ["week", "раз в неделю"], ["month", "раз в месяц"]]
          .map(([v, t]) => `<option value="${v}" ${((SETTINGS.updates || {}).interval || "week") === v ? "selected" : ""}>${t}</option>`).join("")}</select>`,
        "Как часто автоматически обращаться к GitHub за новой версией.")}
      <div class="upd-status" id="updStatusBox"><span class="meta">Загрузка сведений о версии…</span></div>
      <div class="btn-row" style="margin-top:10px">
        <button type="button" class="btn-sec" id="btnCheckUpdate" title="Проверить выход новой версии на GitHub прямо сейчас">${ICON.refresh}Проверить обновление</button>
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
  $("#btnShareLiked").onclick = () => shareLiked($("#btnShareLiked"));
  if ($("#btnOpenLan")) $("#btnOpenLan").onclick = () => openLanModal();
  $("#btnUnmergeAll").onclick = () => {
    const n = +($("#btnUnmergeAll").dataset.count || 0);
    if (!n) return;
    confirmBox("Разделить объединённые карточки?",
      `<p>${n === 1 ? "Будет разъединена <b>1</b> объединённая вручную карточка"
        : `Будут разъединены все <b>${n}</b> ${plural(n, "объединённая вручную карточка", "объединённые вручную карточки", "объединённых вручную карточек")}`} — ` +
      `объявления снова сгруппируются по стандартным правилам программы.</p>` +
      `<p class="meta">Избранное (♥), скрытые (👎) и личные заметки при этом сохранятся. История графиков пересчитается.</p>`,
      "Разделить", async () => {
        const r = await post("/api/db/unmerge");
        MERGE_PICK = null;
        await loadData();
        renderSettings();
        toast("ok", "Карточки разделены", `Сброшено ручных объединений: ${r.unmerged}, объектов в базе: ${r.objects}`);
      });
  };
  $("#sTray").onchange = async () => {
    const on = $("#sTray").checked;
    SETTINGS.ui = Object.assign(SETTINGS.ui || {}, { tray_on_close: on });
    await post("/api/settings", { ui: SETTINGS.ui });
    toast("ok", on ? "Значок в трее включён" : "Значок из трея убран",
      on ? "«Закрыть» убирает окно в трей: сбор и уведомления продолжаются, выход — в меню значка."
         : "«Закрыть» закрывает программу, уведомления Windows о рынке и о капче больше не приходят.");
  };
  const saveUpdCfg = async () => {
    const auto_check = $("#sUpdAuto").checked;
    const interval = $("#sUpdInt").value;
    SETTINGS.updates = { auto_check, interval };
    await post("/api/settings", { updates: SETTINGS.updates });
    const lbl = { day: "раз в день", week: "раз в неделю", month: "раз в месяц" }[interval] || interval;
    toast("ok", auto_check ? "Автопроверка обновлений включена" : "Автопроверка обновлений выключена",
      auto_check ? `Периодичность проверки: ${lbl}` : "Проверить выход новой версии всегда можно вручную кнопкой ниже");
  };
  $("#sUpdAuto").onchange = saveUpdCfg;
  $("#sUpdInt").onchange = saveUpdCfg;
  $("#btnCheckUpdate").onclick = () => checkUpdatesManual($("#btnCheckUpdate"));
  initPurge();
  renderDbInfo();
  loadUpdateStatus();
}

// ---------- очистка данных ----------
function initPurge() {
  const from = $("#pFrom"), to = $("#pTo"), prev = $("#purgePrev"), btn = $("#btnPurge");
  const iso = d => new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10);
  const day = n => { const d = new Date(); d.setDate(d.getDate() - n); return iso(d); };
  let seq = 0;
  const refresh = async () => {
    const my = ++seq;
    if (!from.value && !to.value) { prev.textContent = "Выберите период — здесь появится, что будет удалено."; btn.disabled = true; return; }
    try {
      const r = await post("/api/db/purge", { from: from.value, to: to.value, dry_run: true });
      if (my !== seq) return;
      const total = r.runs + r.snapshots + r.events + r.prices + r.listings + r.unremoved;
      prev.innerHTML = total ? `За ${from.value ? dt(from.value) : "всё время"}${to.value ? " — " + dt(to.value) : " — сегодня"} будет удалено:
        <span>прогонов <b>${r.runs}</b></span><span>проверок рынка <b>${r.snapshots}</b></span><span>изменений <b>${r.events}</b></span>
        <span>записей цен <b>${r.prices}</b></span><span>объявлений целиком <b>${r.listings}</b></span>${r.unremoved ? `<span>вернётся в продажу <b>${r.unremoved}</b></span>` : ""}`
        : "За этот период данных нет.";
      btn.disabled = !total;
    } catch (e) { prev.textContent = e.message; btn.disabled = true; }
  };
  from.onchange = to.onchange = refresh;
  $$("#view-settings [data-pp]").forEach(b => b.onclick = () => {
    const v = b.dataset.pp;
    if (v === "today") { from.value = to.value = day(0); }
    else if (v === "yesterday") { from.value = to.value = day(1); }
    else if (v === "from-start") { from.value = ""; to.value = day(1); }
    else { from.value = day(+v - 1); to.value = ""; }
    refresh();
  });
  btn.onclick = () => confirmBox("Удалить данные за период?",
    `${prev.innerHTML}<p class="meta">Графики, изменения и прогоны этих дней исчезнут, история пересчитается. Резервная копия базы сохранится в database\\backups.</p>`,
    "Удалить", async () => {
      const r = await post("/api/db/purge", { from: from.value, to: to.value });
      toast("ok", "Данные за период удалены", `Объявлений: ${r.listings}, проверок рынка: ${r.snapshots}, изменений: ${r.events}. Копия: ${fileName(r.backup)}`);
      await loadData();
      renderSettings();
    });
  $("#btnPickRuns").onclick = () => pickRuns(from.value, to.value);
  $("#btnClearAll").onclick = () => confirmBox("Очистить всю базу?",
    `<p>Будут удалены <b>все</b> объявления, история цен, изменения, проверки рынка, прогоны, избранное и фото. Настройки поиска и круг останутся.</p>
     <p class="meta">Резервная копия базы с фото сохранится в database\\backups.</p>
     <label class="confirm-type">Для подтверждения введите слово <b>ОЧИСТИТЬ</b><input type="text" id="clearWord" autocomplete="off"></label>`,
    "Очистить всё", async () => {
      const r = await post("/api/db/clear", { confirm: "ОЧИСТИТЬ" });
      toast("ok", "База очищена", `Удалено объявлений: ${r.listings}. Копия с фото: ${fileName(r.backup)}`);
      await loadData();
      renderSettings();
    }, () => $("#clearWord").value.trim().toUpperCase() === "ОЧИСТИТЬ", "#clearWord");
  renderBackups();
}

// ---------- выбор отдельных прогонов ----------
// За день бывает несколько сборов, и неудачные портят статистику: их удаляют по одному, не трогая остальные.
async function pickRuns(from, to, keep = []) {
  let groups;
  try { groups = await api(`/api/db/launches?from=${encodeURIComponent(from || "")}&to=${encodeURIComponent(to || "")}`); }
  catch (e) { return toast("warn", "Не получилось", e.message); }
  const period = from || to ? ` за ${from ? dt(from) : "всё время"} — ${to ? dt(to) : "сегодня"}` : "";
  if (!groups.length) return toast("warn", "Прогонов нет", `За выбранный период сборов не было${period ? "" : " — сбор ещё не запускался"}`);
  const num = (label, v, cls = "") => v == null ? "" : `<span class="rp-num">${label} <b${cls ? ` class="${cls}"` : ""}>${fmt(v)}</b></span>`;
  const list = groups.map((g, k) => `<div class="rp-g">
    <label class="rp-gh"><input type="checkbox" class="rp-all" data-k="${k}">
      <span class="rp-when"><b>${dtt(g.started)}</b><i>${g.runs.length} ${plural(g.runs.length, "сайт", "сайта", "сайтов")} · собрано ${fmt(g.collected)}</i></span>
      <span class="st-badge ${g.bad ? "partial" : "done"}">${g.bad ? "неудачный" : "готово"}</span></label>
    ${g.runs.map(r => `<label class="rp-r"><input type="checkbox" class="rp-run" data-k="${k}" value="${r.id}">
      <span class="rp-src"><i class="sdot ${r.source}"></i>${SRC[r.source] || r.source}</span>
      <span class="st-badge ${r.status}" title="${esc(STATE_TIP[r.status] || "")}">${STATE[r.status] || r.status}</span>
      ${num("собрано", r.collected != null ? r.collected : r.found, runLowCoverage(r) ? "bad" : "")}${num("новых", r.new)}${num("изм.", r.changed)}${num("снято", r.removed)}
      <span class="rp-msg" title="${esc(r.message || "")}">${esc(runNote(r))}</span></label>`).join("")}</div>`).join("");
  $("#modalBody").innerHTML = `<div class="runpick"><h3>Прогоны${period}</h3>
    <p class="meta">Сайты одного запуска идут вместе и дают одну точку на графиках. Отметьте неудачные: из статистики уйдут
      только их данные — объявления, которые видели лишь в этом прогоне, его записи цен и изменения, а снятые им вернутся в продажу.
      Остальные сайты запуска и их точка графика останутся.</p>
    <div class="rp-tools"><button class="chip" id="rpBad" title="Отметить все запуски, где хотя бы один сайт закончил не «готово»">Отметить неудачные</button>
      <button class="chip" id="rpNone">Снять все</button><span class="meta" id="rpCount">Ничего не выбрано</span></div>
    <div class="rp-list">${list}</div>
    <div class="confirm-acts"><button class="btn-sec" data-c="no">Отмена</button>
      <button class="btn-pri danger" id="rpGo" disabled>Удалить выбранные</button></div></div>`;
  $("#modal").classList.remove("hidden");
  const go = $("#rpGo"), boxes = () => $$("#modalBody .rp-run");
  const chosen = () => boxes().filter(b => b.checked).map(b => +b.value);
  const upd = () => {
    $$("#modalBody .rp-all").forEach(a => {
      const mine = boxes().filter(b => b.dataset.k === a.dataset.k), on = mine.filter(b => b.checked).length;
      a.checked = on === mine.length;
      a.indeterminate = on > 0 && on < mine.length;
    });
    const n = chosen().length;
    go.disabled = !n;
    $("#rpCount").textContent = n ? `Выбрано ${n} ${plural(n, "прогон", "прогона", "прогонов")}` : "Ничего не выбрано";
  };
  $$("#modalBody .rp-all").forEach(a => a.onchange = () => {
    boxes().filter(b => b.dataset.k === a.dataset.k).forEach(b => b.checked = a.checked);
    upd();
  });
  boxes().forEach(b => { b.onchange = upd; b.checked = keep.includes(+b.value); });
  $("#rpBad").onclick = () => {
    const bad = new Set(groups.filter(g => g.bad).flatMap(g => g.runs.map(r => String(r.id))));
    boxes().forEach(b => { if (bad.has(b.value)) b.checked = true; });
    upd();
  };
  $("#rpNone").onclick = () => { boxes().forEach(b => b.checked = false); upd(); };
  $('#modalBody [data-c="no"]').onclick = closeModal;
  upd();
  go.onclick = async () => {
    const ids = chosen();
    go.disabled = true;
    try {
      const p = await post("/api/db/runs/delete", { ids, dry_run: true });
      confirmBox(`Удалить ${ids.length} ${plural(ids.length, "прогон", "прогона", "прогонов")}?`,
        `<p>Будет удалено: <span>объявлений целиком <b>${p.listings}</b></span><span>изменений <b>${p.events}</b></span>
           <span>записей цен <b>${p.prices}</b></span><span>точек графика <b>${p.snapshots}</b></span>${p.unremoved ? `<span>вернётся в продажу <b>${p.unremoved}</b></span>` : ""}</p>
         <p class="meta">Подробные журналы этих прогонов удалятся вместе с ними. История графиков пересчитается.
           Резервная копия базы сохранится в database\\backups.</p>`,
        "Удалить", async () => {
          const r = await post("/api/db/runs/delete", { ids });
          toast("ok", `Удалено ${r.runs} ${plural(r.runs, "прогон", "прогона", "прогонов")}`,
            `Объявлений: ${r.listings}, изменений: ${r.events}, точек графика: ${r.snapshots}. Копия: ${fileName(r.backup)}`);
          await loadData();
          renderSettings();
        });
      $('#modalBody [data-c="no"]').onclick = () => pickRuns(from, to, ids);  // «Отмена» — назад к списку с галочками
    } catch (e) {
      toast("warn", "Не получилось", e.message);
      go.disabled = false;
    }
  };
}

// ---------- резервные копии ----------
async function renderBackups() {
  const box = $("#purgeBackups");
  if (!box) return;
  let b;
  try { b = await api("/api/db/backups"); }
  catch (e) { box.textContent = e.message; return; }
  const how = "Копии создаются сами перед очисткой, удалением прогонов и импортом — любое удаление можно отменить.";
  box.innerHTML = `<div class="pb-head"><b>Резервные копии</b>${b.count
      ? `<span>${b.count} ${plural(b.count, "копия", "копии", "копий")} · ${mbs(b.mb)}</span>` : "<span>пока нет</span>"}</div>
    <p class="meta">${b.count ? `${how} Последняя — ${dtt(b.items[0].ts)}, «${esc(b.items[0].label)}».` : how}</p>
    <div class="btn-row">
      <button class="btn-sec" id="btnRestore" ${b.count ? "" : "disabled"} title="Вернуть базу к состоянию из копии: объявления, история, избранное и настройки поиска из неё">${ICON.up}Восстановить…</button>
      <button class="btn-sec danger" id="btnDropBackups" ${b.count ? "" : "disabled"} title="${b.trash ? "Удалить все копии в корзину Windows" : "Удалить все копии (корзины на этом диске нет — насовсем)"}">${ICON.trash}Удалить копии…</button>
    </div>`;
  if (!b.count) return;
  $("#btnRestore").onclick = () => confirmBox("Восстановить базу из копии?",
    `<div class="bk-list">${b.items.map((i, n) => `<label class="bk-row"><input type="radio" name="bk" value="${esc(i.name)}" ${i.ok && !n ? "checked" : ""} ${i.ok ? "" : "disabled"}>
      <span><b>${esc(i.label)}</b><i>${dtt(i.ts)} · ${mbs(i.mb)}${i.images ? " · с фотографиями" : ""}${i.ok ? "" : " · файл не читается"}</i></span></label>`).join("")}</div>
     <p class="meta">Выбранная копия заменит нынешнюю базу, избранное и настройки поиска. Нынешнее состояние перед заменой тоже уйдёт в копию, так что вернуться обратно можно.
       Фотографии есть только в копиях «перед полной очисткой» — из остальных они не восстанавливаются, недостающие догрузятся сами.</p>`,
    "Восстановить", async () => {
      const name = $('#modalBody input[name=bk]:checked').value;
      const r = await post("/api/db/backups/restore", { name });
      toast("ok", "База восстановлена", `Из копии «${bkLabel(name)}»: объявлений ${r.added}, фото ${r.images}. Прежнее состояние: ${fileName(r.backup)}`);
      await loadData();
      renderSettings();
    }, () => !!$('#modalBody input[name=bk]:checked'), ".bk-list");
  $("#btnDropBackups").onclick = () => confirmBox("Удалить резервные копии?",
    `<p>Будут удалены все <b>${b.count}</b> ${plural(b.count, "копия", "копии", "копий")} — освободится ${mbs(b.mb)}.</p>
     <p>${b.trash ? "Копии уйдут в <b>корзину</b> Windows — оттуда их можно вернуть." : "Корзины на этом диске нет, поэтому копии будут стёрты <b>насовсем</b>."}</p>
     <p class="meta">Отменить прошлые очистки после этого будет нечем. Новая копия появится сама перед следующей очисткой или импортом.</p>`,
    "Удалить копии", async () => {
      const r = await post("/api/db/backups/delete");
      toast("ok", r.trash ? "Копии в корзине" : "Копии удалены",
        `${r.deleted} ${plural(r.deleted, "копия", "копии", "копий")}, освободилось ${mbs(r.mb)}${r.trash ? " — файлы в корзине Windows" : ""}`);
      renderBackups();
      renderDbInfo();
    });
}

// Окно подтверждения опасного действия; ok — асинхронное действие, ready — можно ли нажать (ввод подтверждения)
function confirmBox(title, html, okLabel, ok, ready, watch) {
  $("#modalBody").innerHTML = `<div class="confirm"><h3>${title}</h3><div class="confirm-text">${html}</div>
    <div class="confirm-acts"><button class="btn-sec" data-c="no">Отмена</button><button class="btn-pri danger" data-c="yes">${okLabel}</button></div></div>`;
  $("#modal").classList.remove("hidden");
  const yes = $('#modalBody [data-c="yes"]');
  const upd = () => { yes.disabled = ready ? !ready() : false; };
  if (watch) $(watch).addEventListener("input", upd);
  upd();
  $('#modalBody [data-c="no"]').onclick = closeModal;
  yes.onclick = async () => {
    yes.disabled = true;
    yes.textContent = "Выполняю…";
    try { await ok(); closeModal(); }
    catch (e) { closeModal(); toast("warn", "Не получилось", e.message); }
  };
}

async function renderDbInfo() {
  const d = await api("/api/db/info");
  if (!$("#dbStats")) return;
  $("#dbStats").innerHTML = `<div title="Уникальных квартир после объединения дублей"><b>${d.objects}</b><span>объектов</span></div>
    <div title="Всего объявлений со всех сайтов, включая снятые и отсеянные"><b>${d.listings}</b><span>объявлений</span></div>
    <div title="Сохранённых в базе главных фото"><b>${d.images}</b><span>фото</span></div>
    <div title="Объявлений, отмеченных сердечком"><b>${d.likes}</b><span>в избранном</span></div>
    <div title="Активных объявлений, подходящих под параметры поиска"><b>${d.passing}</b><span>прошли отсев</span></div>
    <div title="Объём базы с фото"><b>${mbs(d.size_mb)}</b><span>размер</span></div>`;
  $("#dbPath").textContent = d.folder;
  const ub = $("#btnUnmergeAll");
  if (ub) {
    const n = d.manual_merges || 0;
    ub.disabled = !n;
    ub.dataset.count = n;
    const tip = n
      ? `Разделить ${n} ${plural(n, "объединённую вручную карточку", "объединённые вручную карточки", "объединённых вручную карточек")} обратно по правилам программы. Избранное, дизлайки и заметки сохранятся`
      : "Нет карточек, объединённых вручную";
    if (ub.hasAttribute("title")) ub.title = tip; else ub.dataset.tip = tip;
  }
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

// Избранное одной веб-страницей: файл автономный (фото и стили внутри) — его отправляют посмотреть
async function shareLiked(btn) {
  if (btn) btn.disabled = true;
  try {
    const r = desktop() ? await desktop().export_liked() : await post("/api/liked/export");
    if (r && r.error) toast("warn", "Выгружать нечего", r.error);
    else if (r && !r.cancelled) toast("ok", `Избранное выгружено: ${r.objects} ${plural(r.objects, "объект", "объекта", "объектов")}`, r.path);
  } catch (e) {
    toast("warn", "Не удалось выгрузить", e.message || String(e));
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------- о программе ----------
async function aboutBox() {
  let d;
  try {
    d = await api("/api/about");
  } catch (e) {
    return toast("warn", "Не удалось открыть", e.message || String(e));
  }
  const row = i => `<tr><td><b>${esc(i.name)}</b>${i.version ? ` <span>${esc(i.version)}</span>` : ""}</td>
    <td>${esc(i.license)}</td><td>${esc(i.what)}</td></tr>`;
  $("#modalBody").innerHTML = `<div class="about-win">
    <header><img src="/static/icon.png" alt=""><div><h3>${esc(d.name)}${d.version ? ` ${esc(d.version)}` : ""}</h3>
      <p>${esc(d.about)}</p></div></header>
    <p class="about-author">Автор — <b>${esc(d.author)}</b></p>
    <h4 class="about-h">Используемые библиотеки и данные</h4>
    ${d.groups.map(g => `<section class="about-grp"><h5>${esc(g.title)}</h5><table>${g.items.map(row).join("")}</table></section>`).join("")}
    <p class="about-note">Каждая библиотека распространяется по своей лицензии: её полный текст идёт в составе пакета
      и опубликован на странице проекта. Карта и границы города — данные OpenStreetMap, © участники OpenStreetMap,
      лицензия ODbL 1.0.</p>
  </div>`;
  $("#modal").classList.remove("hidden");
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
    toast("ok", "Импорт завершён", `Добавлено ${r.added}, обновлено ${r.updated}, фото ${r.images}. Копия прежней базы: ${fileName(r.backup)}`);
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
  s.avito.delay_base = [d0, d1];  // заданная вручную — к ней пауза возвращается после чистых сборов
  s.avito.max_verify = Math.max(0, +$("#sAvVer").value || 0);
  SETTINGS.browser = Object.assign(SETTINGS.browser || {}, { window: $("#sWin").value, auto_captcha: $("#sAutoCap").checked });
  if ($("#sUpdAuto") && $("#sUpdInt")) {
    SETTINGS.updates = { auto_check: $("#sUpdAuto").checked, interval: $("#sUpdInt").value };
  }
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
  el.title = "Нажмите, чтобы закрыть";
  el.innerHTML = `<div class="ic">${kind === "warn" ? ICON.warn : ICON.ok}</div><div><b>${esc(title)}</b><p>${esc(text || "")}</p>
    ${action ? `<button class="toast-act">${esc(action.label)}</button>` : ""}</div>`;
  const close = () => {
    if (el.classList.contains("out")) return;
    el.classList.add("out");
    setTimeout(() => el.remove(), 180);
  };
  el.addEventListener("click", e => { if (!e.target.closest(".toast-act")) close(); });  // клик по уведомлению — закрыть
  if (action) el.querySelector(".toast-act").onclick = () => { action.fn(); close(); };
  $("#toasts").appendChild(el);
  setTimeout(close, kind === "warn" || action ? 15000 : 6000);
}

// ---------- обновление программы с GitHub ----------
let UPD_SUM = {};
let updPollInit = false;

function showUpdateNotesModal(u) {
  const ver = u?.available_version || "";
  const title = u?.title || `RealtyWatch ${ver}`;
  const notes = (u?.notes || "").trim() || "Описание изменений в этом релизе не указано.";
  $("#modalBody").innerHTML = `<div class="confirm">
    <h3>${esc(title)}</h3>
    <div class="upd-notes-box">${esc(notes)}</div>
    <p class="meta" style="margin-top:10px">При установке автоматически создаётся страховочная копия базы в <b>database\\backups</b>, обновляется код программы и удаляются устаревшие файлы. Все объявления, заметки, объединённые карточки, ♥ избранное и 👎 скрытые сохраняются.</p>
    <div class="confirm-acts">
      <button type="button" class="btn-sec" id="updModalClose">Закрыть</button>
      ${u?.ready && !u?.dev_mode ? `<button type="button" class="btn-pri" id="updModalInstall">${ICON.ok}Обновить и перезапустить</button>` : ""}
    </div>
  </div>`;
  $("#modal").classList.remove("hidden");
  $("#updModalClose").onclick = closeModal;
  const go = $("#updModalInstall");
  if (go) go.onclick = async () => { closeModal(); await applyStagedUpdate(go); };
}

async function applyStagedUpdate(btn = null) {
  if (btn) btn.disabled = true;
  try {
    const r = await post("/api/update/apply", {});
    toast("ok", `Устанавливаю RealtyWatch ${r.version || ""}…`, "Резервная копия базы создана. Сейчас окно закроется и перезапустится с новой версией.");
    const bar = $("#updateBar");
    if (bar) bar.classList.add("hidden");
  } catch (e) {
    toast("warn", "Не удалось запустить обновление", e.message || String(e));
    if (btn) btn.disabled = false;
  }
}

function renderUpdateBar(u) {
  const bar = $("#updateBar");
  if (!bar) return;
  if (!u || !u.ready || u.dismissed || u.dev_mode || !u.available_version) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    return;
  }
  bar.innerHTML = `<span>🚀 Доступно обновление <b>RealtyWatch ${esc(u.available_version)}</b> — архив скачан и готов к установке (база, заметки и избранное сохранятся)</span>
    <span class="fresh-db-acts">
      <button type="button" class="btn-sec sm" id="btnUpdBarNotes">Что нового</button>
      <button type="button" class="btn-pri sm" id="btnUpdBarGo">${ICON.ok}Обновить и перезапустить</button>
      <button type="button" class="link sm" id="btnUpdBarHide">Позже</button>
    </span>`;
  bar.classList.remove("hidden");
  $("#btnUpdBarNotes").onclick = () => showUpdateNotesModal(u);
  $("#btnUpdBarGo").onclick = () => applyStagedUpdate($("#btnUpdBarGo"));
  $("#btnUpdBarHide").onclick = async () => {
    bar.classList.add("hidden");
    try { await post("/api/update/ack", { dismiss_banner: true }); } catch (e) {}
  };
}

function paintUpdateSettingsBox(st) {
  const box = $("#updStatusBox");
  if (!box || !st) return;
  const curLbl = st.current_version ? `v${esc(st.current_version)}` : (st.dev_mode ? "режим разработки (git)" : "не указана");
  const checkedLbl = st.last_checked ? `проверено ${dtt(st.last_checked)}` : "ещё не проверялось";
  let extra = "";
  if (st.checking || st.downloading) {
    extra = `<div class="upd-state-line">${st.downloading ? "Скачиваю и проверяю архив новой версии…" : "Проверяю релизы на GitHub…"}</div>`;
  } else if (st.ready && st.available_version) {
    const kb = st.size_kb ? ` (${fmt(st.size_kb)} КБ)` : "";
    extra = `<div class="upd-ready-box">
      <div><b>Готово к установке: RealtyWatch v${esc(st.available_version)}</b>${esc(kb)}</div>
      ${st.notes ? `<div class="upd-notes-preview">${esc(st.notes.slice(0, 280))}${st.notes.length > 280 ? "…" : ""}</div>` : ""}
      <div class="upd-ready-acts">
        ${!st.dev_mode ? `<button type="button" class="btn-pri sm" id="btnApplyUpdate">${ICON.ok}Обновить и перезапустить</button>` : `<span class="meta">В рабочей папке git автоперезапись отключена</span>`}
        <button type="button" class="btn-sec sm" id="btnUpdNotes">Описание релиза</button>
      </div>
    </div>`;
  } else if (st.last_error) {
    extra = `<div class="upd-err-line">⚠ ${esc(st.last_error)}</div>`;
  } else if (st.last_checked) {
    const latestTxt = st.latest_github_version && st.dev_mode ? ` (последний релиз на GitHub: v${esc(st.latest_github_version)})` : "";
    extra = `<div class="upd-ok-line">Установлена актуальная версия${latestTxt}</div>`;
  }
  box.innerHTML = `<div class="upd-cur-line">Текущая версия: <b>${curLbl}</b> · <span class="meta">${checkedLbl}</span></div>${extra}`;
  const goBtn = $("#btnApplyUpdate");
  if (goBtn) goBtn.onclick = () => applyStagedUpdate(goBtn);
  const notesBtn = $("#btnUpdNotes");
  if (notesBtn) notesBtn.onclick = () => showUpdateNotesModal(st);
}

async function loadUpdateStatus() {
  if (!$("#updStatusBox")) return;
  try {
    const st = await api("/api/update/status");
    paintUpdateSettingsBox(st);
  } catch (e) {
    if ($("#updStatusBox")) $("#updStatusBox").innerHTML = `<span class="meta">Не удалось получить статус обновлений</span>`;
  }
}

async function checkUpdatesManual(btn) {
  if (btn) btn.disabled = true;
  if ($("#updStatusBox")) {
    $("#updStatusBox").innerHTML = `<div class="upd-cur-line"><span class="meta">Обращаюсь к GitHub Releases…</span></div>`;
  }
  try {
    const st = await post("/api/update/check", {});
    UPD_SUM = st;
    renderUpdateBar(st);
    paintUpdateSettingsBox(st);
    if (st.ready && st.available_version) {
      toast("ok", `Доступна версия ${st.available_version}`, "Архив скачан и проверен — можно устанавливать",
        !st.dev_mode ? { label: "Обновить", fn: () => applyStagedUpdate() } : null);
    } else {
      const vTxt = st.current_version ? `v${st.current_version}` : (st.latest_github_version ? `последний релиз v${st.latest_github_version}` : "");
      toast("ok", "Обновлений нет", vTxt ? `У вас актуальная версия (${vTxt})` : "Новых релизов на GitHub не найдено");
    }
  } catch (e) {
    toast("warn", "Проверка обновлений не удалась", e.message || String(e));
    await loadUpdateStatus();
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------- статус сбора и журнал ----------
let LAST_STATUS = {};

async function continueRun({ source, run_id } = {}, btn = null) {
  if (btn) btn.disabled = true;
  try {
    const r = await post("/api/run/continue", { source, run_id });
    if (r.started && r.started.length) {
      const s = r.started[0];
      if (LAST_STATUS[s]) Object.assign(LAST_STATUS[s], { running: true, state: "running", can_continue: false });
      renderSources(LAST_STATUS);
      if (view === "runs") renderRuns();
      toast("ok", "Продолжаю сбор", `${SRC[s] || s} — данные будут записаны в текущий прогон`);
    } else {
      toast("warn", "Уже идёт", "Этот источник уже собирается");
    }
  } catch (e) {
    toast("warn", "Не удалось продолжить", e.message || String(e));
    if (view === "runs") renderRuns();
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderSources(status) {
  LAST_STATUS = status || LAST_STATUS;
  $("#sources").innerHTML = Object.keys(SRC).map(k => {
    const v = status[k] || LASTRUN[k], on = $(`.src-run[value=${k}]`)?.checked ?? true;
    const paused = v && v.running && v.paused_until;
    const state = v ? (paused ? "paused" : v.running ? "running" : v.state) : "";
    const pct = v && v.pages ? Math.round(100 * v.page / v.pages) : 0;
    const canCont = !!(v && !v.running && v.can_continue);
    const retryPart = v && v.running && v.retry ? ` · повтор ${v.retry}/${v.retry_max || 5}` : "";
    const meta = !v ? "ещё не запускался"
      : paused ? `⏸ ограничение IP — пауза до ${new Date(v.paused_until).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}, задержка ${v.delay ? v.delay.join("–") + " с" : "увеличена"}`
      : v.running ? `стр. ${v.page || 0} из ${v.pages || "?"}${retryPart} · найдено ${v.found}${v.details ? ` · карточек ${v.details}` : ""}`
      : v.message || "";
    const tip = `${SRC[k]}: переключатель включает сайт в сбор — и для кнопки «Запустить», и для автообновления.` +
      (v && v.finished ? `
Последний сбор завершён ${dtt(v.finished)}: ${v.message || STATE[v.state] || ""}` : "");
    const contBtn = canCont
      ? `<button type="button" class="src-cont" data-continue="${k}" data-run-id="${v.run_id || ""}" title="Продолжить незавершённый сбор с сохранённой позиции (все данные запишутся в текущий запуск)">↻ Продолжить</button>`
      : "";
    return `<label class="src-row ${state}" title="${esc(tip)}"><input type="checkbox" class="src-run" value="${k}" ${on ? "checked" : ""}><span class="switch"></span>
      <span class="src-name"><i class="sdot ${k}"></i>${SRC[k]}</span><span class="src-state ${state}">${v ? STATE[state] || state : ""}</span>
      <span class="src-meta">${esc(meta)}</span>${contBtn}<span class="meter"><i style="width:${v ? (v.running ? Math.max(pct, 4) : 100) : 0}%"></i></span></label>`;
  }).join("");
}

async function poll() {
  try {
    const s = await api(`/api/status?after=${jAfter}&lan_after=${lanAfter}`);
    LAST_STATUS = s.status || {};
    if (!$("#sources").matches(":hover")) renderSources(s.status);
    for (const [k, v] of Object.entries(s.status)) {
      const st = v.running ? "running" : v.state;
      if (lastStates[k] === "running" && st !== "running") {
        const act = v.can_continue ? { label: "Продолжить", fn: () => continueRun({ source: k, run_id: v.run_id }) } : null;
        toast(st === "done" ? "ok" : "warn", `${SRC[k]}: ${STATE[st] || st}`, v.message || "", act);
      }
      lastStates[k] = st;
    }
    if (s.journal.length) {
      const body = $("#jBody");
      s.journal.forEach(j => {
        const d = document.createElement("div");
        const warn = j.msg.includes("⚠"), ban = j.msg.includes("⛔"), human = j.msg.includes("НУЖНА РУЧНАЯ ПРОВЕРКА");
        d.className = "jl" + (warn || ban ? " warn" : "");
        if (ban) {
          toast("warn", `${SRC[j.src]}: ограничение по IP`, j.msg.replace(/^⛔\s*/, ""));
          $("#jDot").classList.remove("hidden");
          loadSettings();  // задержка в настройках увеличилась
        }
        d.innerHTML = `<span class="t">${j.t}</span><span class="s">${SRC[j.src] || (j.src === "db" ? "База" : j.src === "lan" ? "Сеть" : j.src)}</span>${esc(j.msg)}`;
        const keep = body.scrollTop > 4, h0 = body.scrollHeight;
        body.prepend(d);
        if (keep) body.scrollTop += body.scrollHeight - h0;
        jAfter = j.n;
        if (j.msg.startsWith("🔔")) {
          toast("ok", "Изменения на рынке", j.msg.replace(/^🔔\s*изменения на рынке\s*(?:\([^)]*\))?:\s*/, ""), { label: "Показать", fn: () => setView("changes") });
          loadData();
          updateChangesBadge();
        }
        if (j.msg.startsWith("сбор завершён")) loadData();
        // уведомление о капче — только когда нужен человек (пазл Авито программа проходит сама); прочие ⚠ — точка у журнала
        if (human) toast("warn", `${SRC[j.src]}: нужна проверка`, "Пройдите капчу в появившемся окне браузера — сбор продолжится сам.");
        if (warn) $("#jDot").classList.remove("hidden");
        if ((j.msg.startsWith("старт") || j.msg.startsWith("продолжение сбора")) && view === "runs") renderRuns();
        if (j.msg.startsWith("завершено") || j.msg.startsWith("фото: загружено") || j.msg.startsWith("объединение дублей")) {
          loadData();
          if (view === "settings") renderDbInfo();
          if (view === "runs") renderRuns();
        }
      });
      const last = s.journal[s.journal.length - 1];
      $("#jLast").textContent = `${last.t} · ${SRC[last.src] || (last.src === "db" ? "База" : last.src === "lan" ? "Сеть" : "")}: ${last.msg}`;
    }
    if (s.lan) {
      const prevInbox = LAN_SUM.inbox_count || 0;
      const prevDataRev = LAN_SUM.data_rev || 0;
      const wasInit = lanPollInit;
      const newInbox = s.lan.inbox_count || 0;
      if (s.lan.seq != null && s.lan.seq > lanAfter) lanAfter = s.lan.seq;
      syncLanIndicators(s.lan);
      if (wasInit && newInbox > prevInbox) {
        toast("ok", "Входящий запрос по LAN", `Получены новые данные по локальной сети (${newInbox})`, { label: "Открыть", fn: () => openLanModal() });
        if (view === "imports" && !IMP_BATCH_ID) renderImports();
      } else if (!wasInit && newInbox > 0 && view === "imports" && !IMP_BATCH_ID) {
        renderImports();
      }
      if (wasInit && (s.lan.data_rev || 0) !== prevDataRev) {
        await loadData();
        const openOid = $("#modal:not(.hidden) #modalBody")?.dataset?.oid;
        if (openOid && document.activeElement?.id !== "objNote") openObject(openOid);
      }
      for (const ev of (s.lan.events || [])) {
        if (ev.kind === "spotlight") {
          const freshEnough = wasInit || (ev.ts && (Date.now() - new Date(ev.ts).getTime()) < 15000);
          if (freshEnough) handleSpotlightEvent(ev);
        } else if (ev.kind === "live_fav" && wasInit) {
          handleLiveFavEvent(ev);
        } else if (ev.kind === "peer_state" && wasInit) {
          if (ev.online) toast("ok", "Устройство в сети", `«${ev.name || "Устройство"}» подключено автоматически`);
          else toast("warn", "Устройство отключилось", `«${ev.name || "Устройство"}» больше не в сети — работаем автономно`);
          const openModal = !$("#modal")?.classList.contains("hidden") && !!$("#lanModalWin");
          const modalWin = openModal ? $("#lanModalWin") : null;
          const busyInModal = modalWin && (modalWin.contains(document.activeElement) || !!$("#lanDiscBox .lan-quick-box:not(.hidden)"));
          if (modalWin && !busyInModal) {
            api("/api/lan/status").then(st => {
              syncLanIndicators(st);
              const stillOpen = !$("#modal")?.classList.contains("hidden") && !!$("#lanModalWin");
              if (stillOpen && !$("#lanDiscBox .lan-quick-box:not(.hidden)")) renderLanModalBody(st);
            }).catch(() => {});
          }
        }
      }
      lanPollInit = true;
    }
    if (s.update) {
      const prevReady = !!UPD_SUM.ready;
      const prevVer = UPD_SUM.available_version || "";
      UPD_SUM = s.update;
      renderUpdateBar(s.update);
      if (s.update.just_updated_to) {
        const uv = s.update.just_updated_to;
        s.update.just_updated_to = "";
        post("/api/update/ack", { clear_just_updated: true }).catch(() => {});
        toast("ok", `Обновлено до версии ${uv}`, "Все объявления, личные заметки, избранное и настройки сохранены");
      } else if (updPollInit && s.update.ready && (!prevReady || prevVer !== s.update.available_version) && !s.update.dev_mode) {
        toast("ok", `Доступно обновление v${s.update.available_version}`, "Архив скачан в фоне и готов к установке", {
          label: "Обновить",
          fn: () => applyStagedUpdate(),
        });
        if (view === "settings") loadUpdateStatus();
      }
      updPollInit = true;
    }
    const running = Object.values(s.status).filter(v => v.running);
    if (running.length) {
      const sig = running.map(v => `${v.found || 0}:${v.new || 0}:${v.changed || 0}:${v.details || 0}`).join("|");
      if (sig !== poll.lastSig && Date.now() - (poll.lastLoad || 0) > 15000) {
        poll.lastSig = sig;
        poll.lastLoad = Date.now();
        loadData();
      }
    } else {
      poll.lastSig = "";
    }
  } catch (e) { /* сервер ещё стартует */ }
  setTimeout(poll, 2000);
}

// ---------- выделение карточек и меню «Экспорт» ----------
function exportBtnTip(o) {
  const n = EXPORT_PICK.size;
  if (o && EXPORT_PICK.has(o.id)) {
    return `Убрать карточку из пакета экспорта (сейчас выбрано: ${n}).\nСнять все — Esc или панель сверху`;
  }
  return `Добавить карточку в пакет экспорта (.rwcards.zip или по сети)${n ? ` — сейчас выбрано: ${n}` : ""}`;
}

function exportBannerHtml() {
  const n = EXPORT_PICK.size;
  if (!n) return "";
  const likes = OBJS.filter(o => o.liked).length;
  return `<div class="export-bar">${ICON.share}<span class="exp-bar-txt">Выбрано для экспорта: <b>${n}</b> ${plural(n, "карточка", "карточки", "карточек")}</span>
    <span class="exp-bar-chips">
      <button type="button" class="chip exp-add-liked" ${likes ? "" : "disabled"} title="Добавить к выбранным все квартиры с сердечком ♥">+ Всё избранное (${likes})</button>
      <button type="button" class="chip exp-add-filter" title="Добавить все карточки, подходящие под текущие фильтры на экране">+ Все по фильтру</button>
      <button type="button" class="chip exp-clear" title="Снять выделение со всех карточек (Esc)">Снять все (Esc)</button>
    </span>
    <span class="exp-bar-acts">
      <button type="button" class="btn-sec sm exp-go-lan" title="Отправить выбранные карточки на другое устройство в локальной сети">По сети (LAN)</button>
      <button type="button" class="btn-pri sm exp-go-file" title="Сохранить выбранные карточки в переносимый файл .rwcards.zip">${ICON.down}В файл (.rwcards.zip)</button>
    </span></div>`;
}

function updateExportBtnUI() {
  const btn = $("#btnShare"), badge = $("#expBadge");
  if (!btn) return;
  const n = EXPORT_PICK.size;
  const likes = OBJS.filter(o => o.liked).length;
  btn.disabled = !OBJS.length && !n;
  btn.classList.toggle("has-pick", n > 0);
  if (badge) {
    badge.textContent = n > 0 ? String(n) : "";
    badge.classList.toggle("hidden", !n);
  }
  const fTitle = $("#expFileTitle"), hSub = $("#expHtmlSub"), hItem = $('#expMenu [data-exp="html"]');
  if (fTitle) fTitle.textContent = n ? `Экспортировать выбранные (${n}) в файл (.rwcards.zip)` : "Экспортировать в файл (.rwcards.zip)";
  if (hSub) hSub.textContent = likes ? `Избранное (${likes}) одним файлом для просмотра без программы` : "Отметьте квартиры сердечком, чтобы выгрузить HTML";
  if (hItem) hItem.disabled = !likes;
}

function syncExportPickUI(changedIds = []) {
  updateExportBtnUI();
  const byId = new Map(OBJS.map(o => [o.id, o]));
  const ids = new Set(changedIds.filter(Boolean));
  if (view !== "cards") $("#cardsList > .export-bar")?.remove();
  if (view !== "changes") $("#view-changes > .export-bar")?.remove();
  if (view !== "imports") $("#view-imports > .export-bar")?.remove();
  if (view === "cards") {
    const list = $("#cardsList");
    if (!list) return;
    if (!list.querySelector(".empty")) _syncExportBanner(list);
    else {
      const curBar = list.querySelector(":scope > .export-bar");
      if (EXPORT_PICK.size) {
        const html = exportBannerHtml();
        const mergeBar = list.querySelector(":scope > .merge-bar");
        if (curBar) curBar.outerHTML = html;
        else if (mergeBar) mergeBar.insertAdjacentHTML("afterend", html);
        else list.insertAdjacentHTML("afterbegin", html);
      } else if (curBar) curBar.remove();
    }
    ids.forEach(id => {
      const cardEl = list.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
      const on = EXPORT_PICK.has(id);
      if (cardEl) {
        cardEl.classList.toggle("exporting", on);
        cardEl.querySelector(".export-btn")?.classList.toggle("on", on);
      }
      const cached = CARD_DOM.get(id);
      if (cached && cached.el === cardEl && byId.has(id)) cached.sig = cardSig(byId.get(id));
    });
    list.querySelectorAll(".card").forEach(cardEl => {
      const o = byId.get(cardEl.dataset.id);
      if (o) _setTip(cardEl.querySelector(".export-btn"), exportBtnTip(o));
    });
    return;
  }
  if (view === "changes") {
    const root = $("#view-changes"), chBar = root?.querySelector(":scope > .ch-bar");
    if (!root || !chBar) return;
    const curBar = root.querySelector(":scope > .export-bar");
    if (EXPORT_PICK.size) {
      const html = exportBannerHtml();
      const anchor = root.querySelector(":scope > .merge-bar") || chBar;
      if (curBar) curBar.outerHTML = html;
      else anchor.insertAdjacentHTML("afterend", html);
      if (typeof bindExportBarActions === "function") bindExportBarActions(root);
    } else if (curBar) {
      curBar.remove();
    }
    ids.forEach(id => {
      const tile = root.querySelector(`.ch-tile[data-id="${CSS.escape(id)}"]`);
      const on = EXPORT_PICK.has(id);
      if (tile) {
        tile.classList.toggle("exporting", on);
        tile.querySelector(".export-btn")?.classList.toggle("on", on);
      }
    });
    root.querySelectorAll(".ch-tile[data-id]").forEach(tile => {
      const o = byId.get(tile.dataset.id);
      if (o) _setTip(tile.querySelector(".export-btn"), exportBtnTip(o));
    });
    return;
  }
  if (view === "imports" && IMP_BATCH_ID) {
    const root = $("#view-imports");
    const curBar = root?.querySelector(":scope > .export-bar");
    const impBar = root?.querySelector(":scope > .imp-bar");
    if (EXPORT_PICK.size && impBar) {
      const html = exportBannerHtml();
      if (curBar) curBar.outerHTML = html;
      else impBar.insertAdjacentHTML("afterend", html);
      bindExportBarActions(root);
    } else if (curBar) {
      curBar.remove();
    }
    ids.forEach(id => {
      const cardEl = root?.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
      const on = EXPORT_PICK.has(id);
      if (cardEl) {
        cardEl.classList.toggle("exporting", on);
        cardEl.querySelector(".export-btn")?.classList.toggle("on", on);
      }
    });
  }
}

function bindExportBarActions(root) {
  if (!root) return;
  root.querySelectorAll(".exp-add-liked").forEach(b => b.onclick = () => addLikedToExport());
  root.querySelectorAll(".exp-add-filter").forEach(b => b.onclick = () => addFilteredToExport());
  root.querySelectorAll(".exp-clear").forEach(b => b.onclick = () => clearExportPick());
  root.querySelectorAll(".exp-go-file").forEach(b => b.onclick = () => exportCardsFlow());
  root.querySelectorAll(".exp-go-lan").forEach(b => b.onclick = () => exportCardsToLan());
}

function toggleExportPick(id) {
  if (!id) return;
  $("#tip")?.classList.remove("on");
  if (EXPORT_PICK.has(id)) EXPORT_PICK.delete(id);
  else EXPORT_PICK.add(id);
  syncExportPickUI([id]);
}

function clearExportPick() {
  if (!EXPORT_PICK.size) return;
  const changed = [...EXPORT_PICK];
  EXPORT_PICK.clear();
  $("#tip")?.classList.remove("on");
  syncExportPickUI(changed);
}

function addLikedToExport() {
  const changed = [];
  for (const o of OBJS) {
    if (o.liked && !EXPORT_PICK.has(o.id)) { EXPORT_PICK.add(o.id); changed.push(o.id); }
  }
  syncExportPickUI(changed);
  if (changed.length) toast("ok", "Избранное добавлено в выборку", `Добавлено ${changed.length}, всего выбрано ${EXPORT_PICK.size}`);
  else toast("ok", "Всё избранное уже выбрано", `В выборке ${EXPORT_PICK.size} ${plural(EXPORT_PICK.size, "карточка", "карточки", "карточек")}`);
}

function addFilteredToExport() {
  let targetIds = [];
  if (view === "changes") {
    targetIds = $$("#view-changes .ch-tile[data-id]").map(el => el.dataset.id).filter(Boolean);
  } else if (view === "imports" && IMP_BATCH_ID && IMP_DETAIL) {
    targetIds = $$("#impGrid .card[data-id]").filter(el => !el.classList.contains("imp-stale")).map(el => el.dataset.id);
  } else {
    const rows = view === "cards" ? inArea(filtered()) : filtered();
    targetIds = rows.map(o => o.id);
  }
  const changed = [];
  for (const id of targetIds) {
    if (!EXPORT_PICK.has(id)) { EXPORT_PICK.add(id); changed.push(id); }
  }
  syncExportPickUI(changed);
  if (changed.length) toast("ok", "Добавлено по фильтру", `Добавлено ${changed.length}, всего выбрано ${EXPORT_PICK.size}`);
  else toast("ok", "Новых карточек нет", "Все показанные карточки уже выделены");
}

function closeExportMenu() {
  $("#expMenu")?.classList.add("hidden");
  $("#btnShare")?.setAttribute("aria-expanded", "false");
}

function initExportMenu() {
  const btn = $("#btnShare"), menu = $("#expMenu"), wrap = $("#expWrap");
  if (!btn || !menu || !wrap) return;
  btn.onclick = e => {
    e.stopPropagation();
    updateExportBtnUI();
    const opening = menu.classList.toggle("hidden") === false;
    btn.setAttribute("aria-expanded", opening ? "true" : "false");
  };
  document.addEventListener("click", e => {
    if (!menu.classList.contains("hidden") && !e.target.closest("#expWrap")) closeExportMenu();
  });
  menu.addEventListener("click", e => {
    const item = e.target.closest("[data-exp]");
    if (!item || item.disabled) return;
    const act = item.dataset.exp;
    closeExportMenu();
    if (act === "file") exportCardsFlow();
    else if (act === "lan") exportCardsToLan();
    else if (act === "html") shareLiked($("#btnShare"));
  });
  updateExportBtnUI();
}

function stopLanTimers() {
  if (lanPinTimer) { clearInterval(lanPinTimer); lanPinTimer = null; }
  if (lanDiscTimer) { clearInterval(lanDiscTimer); lanDiscTimer = null; }
}

function syncLanIndicators(lan) {
  if (!lan) return;
  if (Array.isArray(lan.peers)) LAN_CACHE = lan;
  const wasSpot = canSpotlight();
  LAN_SUM = {
    enabled: !!lan.enabled,
    listening: !!lan.listening,
    online_peers: lan.online_peers || 0,
    inbox_count: lan.inbox_count || 0,
    outbox_count: lan.outbox_count ?? LAN_SUM.outbox_count ?? 0,
    data_rev: lan.data_rev ?? LAN_SUM.data_rev ?? 0,
    newer_db_peer: lan.newer_db_peer !== undefined ? lan.newer_db_peer : LAN_SUM.newer_db_peer,
  };
  const nowSpot = canSpotlight();
  const onlineN = LAN_SUM.online_peers || 0;
  const led = $("#lanLed"), txt = $("#lanFootTxt"), lanBtn = $("#lanBtn"), footB = $("#lanFootBadge"), impB = $("#impBadge");
  if (led) {
    led.classList.toggle("on", LAN_SUM.enabled && onlineN > 0);
    led.classList.toggle("wait", LAN_SUM.enabled && onlineN === 0);
  }
  if (txt) {
    txt.textContent = !LAN_SUM.enabled ? "Сеть выкл" : (onlineN > 0 ? `Онлайн · ${onlineN}` : "Нет связи");
  }
  if (lanBtn) {
    lanBtn.classList.toggle("is-online", LAN_SUM.enabled && onlineN > 0);
    const onlinePeers = (Array.isArray(lan.peers) ? lan.peers : (LAN_CACHE?.peers || [])).filter(p => p.online);
    const tip = !LAN_SUM.enabled
      ? "Локальная сеть выключена — нажмите для подключения устройств"
      : onlineN > 0
        ? (onlinePeers.length
            ? "В сети: " + onlinePeers.map(p => p.rtt_ms != null ? `${p.name} (${p.rtt_ms} мс)` : p.name).join(", ")
            : `Локальная сеть: подключено устройств — ${onlineN}`)
        : "Локальная сеть включена — сопряжённые устройства сейчас не в сети";
    _setTip(lanBtn, tip);
  }
  for (const b of [footB, impB]) {
    if (!b) continue;
    b.textContent = LAN_SUM.inbox_count > 0 ? String(LAN_SUM.inbox_count) : "";
    b.classList.toggle("hidden", !(LAN_SUM.inbox_count > 0));
  }
  const impBtn = $("#btnImpLan");
  if (impBtn) {
    impBtn.textContent = LAN_SUM.enabled
      ? (LAN_SUM.online_peers ? `Сеть (LAN): ${LAN_SUM.online_peers} онлайн` : "Сеть (LAN): включена")
      : "Сеть (LAN): подключить…";
  }
  const sHint = $("#sLanHint");
  if (sHint) {
    sHint.textContent = LAN_SUM.enabled
      ? (LAN_SUM.online_peers ? `Онлайн устройств: ${LAN_SUM.online_peers}` : "LAN включена, нет связи с устройствами")
      : "Доступ по сети выключен";
  }
  renderFreshDbBar();
  if (wasSpot !== nowSpot) {
    CARD_DOM.clear();
    if (["cards", "table", "changes"].includes(view)) render();
    const openOid = $("#modal:not(.hidden) #modalBody")?.dataset?.oid;
    if (openOid && document.activeElement?.id !== "objNote") openObject(openOid);
  }
}

function renderFreshDbBar() {
  const bar = $("#freshDbBar");
  if (!bar) return;
  const np = LAN_SUM.newer_db_peer;
  const dKey = np ? `${np.device_id}:${np.latest_db_ts || ""}` : "";
  if (!LAN_SUM.enabled || !np || dKey === dismissedFreshDb) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    return;
  }
  const srcTxt = np.newer_sources && np.newer_sources.length ? ` (${np.newer_sources.join(", ")})` : "";
  bar.innerHTML = `<span class="fresh-db-txt">У устройства <b>${esc(np.name)}</b> есть более свежая база${esc(srcTxt)}.</span>
    <span class="fresh-db-acts">
      <button type="button" class="btn-pri sm" id="btnFreshPull">Забрать свежую базу по сети</button>
      <button type="button" class="btn-sec sm" id="btnFreshHide">Скрыть</button>
    </span>`;
  bar.classList.remove("hidden");
  $("#btnFreshPull").onclick = () => pullDbFromPeer(np.device_id, np.name);
  $("#btnFreshHide").onclick = () => { dismissedFreshDb = dKey; bar.classList.add("hidden"); };
}

function spotBrief(s) {
  if (!s) return "";
  const r = s.rooms === 0 ? "студия" : s.rooms != null ? `${s.rooms}-к` : "";
  return [r, s.area != null && `${s.area} м²`, s.price && `${fmt(s.price)} ₽`, s.address].filter(Boolean).join(", ");
}

function findObjectByMemberKeys(memberKeys) {
  if (!Array.isArray(memberKeys) || !memberKeys.length) return null;
  const set = new Set(memberKeys.map(k => `${k[0]}:${k[1]}`));
  return OBJS.find(o => o.items.some(i => set.has(`${i.source}:${i.source_id}`))) || null;
}

function clearSpotlight() {
  if (!SPOTLIGHT) return;
  const oldId = SPOTLIGHT.id;
  SPOTLIGHT = null;
  CARD_DOM.delete(oldId);
  $$(".spotlighted").forEach(el => el.classList.remove("spotlighted"));
  $$(".spot-note, .spot-tbl-tag").forEach(el => el.remove());
}

function jumpToSpotlightCard(id, fromName) {
  SPOTLIGHT = { id, from: fromName || "Партнёр" };
  CARD_DOM.delete(id);
  setView("cards");
  setTimeout(() => {
    const el = document.querySelector(`#cardsList .card[data-id="${CSS.escape(id)}"]`);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("spot-pulse");
      setTimeout(() => el.classList.remove("spot-pulse"), 2400);
    }
    highlight(id, true);
  }, 90);
}

async function importAndShowSpotlight(ev) {
  const mk = (ev.member_keys || [])[0];
  if (!mk) return toast("warn", "Не удалось загрузить", "Не переданы ключи объявления");
  try {
    const r = await post("/api/lan/spotlight/import", {
      from_device_id: ev.from_device_id,
      source: mk[0],
      source_id: mk[1],
    });
    await loadData();
    const o = findObjectByMemberKeys(r.member_keys || ev.member_keys) || OBJS.find(x => x.id === r.object_id);
    if (o) {
      jumpToSpotlightCard(o.id, ev.from_device_name);
      toast("ok", "Карточка импортирована", objBrief(o));
    } else {
      toast("warn", "Карточка импортирована", "Откройте вкладку «Импорт» для просмотра");
    }
  } catch (e) {
    toast("warn", "Не удалось импортировать карточку", e.message || String(e));
  }
}

function handleSpotlightEvent(ev) {
  const o = findObjectByMemberKeys(ev.member_keys);
  if (!o) {
    const text = spotBrief(ev.summary) || "объявление";
    toast("ok", `👁 ${ev.from_device_name} показывает квартиру`, `${text} — пока нет в вашей базе`, {
      label: "Импортировать и показать",
      fn: () => importAndShowSpotlight(ev),
    });
    return;
  }
  const prevId = SPOTLIGHT ? SPOTLIGHT.id : null;
  SPOTLIGHT = { id: o.id, from: ev.from_device_name || "Партнёр" };
  if (prevId) CARD_DOM.delete(prevId);
  CARD_DOM.delete(o.id);

  if (view === "cards") {
    render();
    setTimeout(() => {
      const el = document.querySelector(`#cardsList .card[data-id="${CSS.escape(o.id)}"]`);
      if (el) {
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        el.classList.add("spot-pulse");
        setTimeout(() => el.classList.remove("spot-pulse"), 2400);
      }
      highlight(o.id, true);
    }, 60);
    return;
  }
  if (view === "changes") {
    const tile = document.querySelector(`#view-changes .ch-tile[data-id="${CSS.escape(o.id)}"]`);
    if (tile) {
      paintChanges();
      setTimeout(() => {
        const el = document.querySelector(`#view-changes .ch-tile[data-id="${CSS.escape(o.id)}"]`);
        if (el) {
          el.scrollIntoView({ block: "center", behavior: "smooth" });
          el.classList.add("spot-pulse");
          setTimeout(() => el.classList.remove("spot-pulse"), 2400);
        }
      }, 60);
    } else {
      toast("ok", `👁 ${SPOTLIGHT.from} показывает квартиру`, objBrief(o), {
        label: "Показать в Подборке",
        fn: () => jumpToSpotlightCard(o.id, SPOTLIGHT.from),
      });
    }
    return;
  }
  if (view === "table") {
    render();
    const tr = document.querySelector(`#view-table tbody tr[data-id="${CSS.escape(o.id)}"]`);
    if (tr) {
      tr.scrollIntoView({ block: "center", behavior: "smooth" });
      tr.classList.add("spot-pulse");
      setTimeout(() => tr.classList.remove("spot-pulse"), 2400);
    } else {
      toast("ok", `👁 ${SPOTLIGHT.from} показывает квартиру`, objBrief(o), {
        label: "Показать в Подборке",
        fn: () => jumpToSpotlightCard(o.id, SPOTLIGHT.from),
      });
    }
    return;
  }
  if (view === "map") {
    const mk = MAIN && MAIN.markers && MAIN.markers[o.id];
    if (mk && MAIN.map) {
      MAIN.map.flyTo(mk.getLatLng(), Math.max(MAIN.map.getZoom(), 16), { duration: 0.6 });
      mk.openPopup();
      toast("ok", `👁 ${SPOTLIGHT.from} показывает на карте`, objBrief(o));
    } else {
      toast("ok", `👁 ${SPOTLIGHT.from} показывает квартиру`, objBrief(o), {
        label: "Показать в Подборке",
        fn: () => jumpToSpotlightCard(o.id, SPOTLIGHT.from),
      });
    }
    return;
  }
  toast("ok", `👁 ${SPOTLIGHT.from} показывает квартиру`, objBrief(o), {
    label: "Показать",
    fn: () => jumpToSpotlightCard(o.id, SPOTLIGHT.from),
  });
}

function handleLiveFavEvent(ev) {
  const title = ev.on
    ? `♥ ${ev.from_device_name}: добавлено в Избранное`
    : `${ev.from_device_name}: убрано из Избранного`;
  toast("ok", title, spotBrief(ev.summary) || "Квартира", {
    label: "Принять",
    fn: async () => {
      try {
        await post("/api/lan/live/favorite/apply", {
          from_device_id: ev.from_device_id,
          keys: ev.keys,
          on: !!ev.on,
        });
        await loadData();
        toast("ok", "Избранное обновлено", spotBrief(ev.summary));
      } catch (e) {
        toast("warn", "Не удалось применить", e.message || String(e));
      }
    },
  });
}

async function spotlightObject(objectId) {
  if (!objectId) return;
  let st;
  try {
    st = await api("/api/lan/status");
    syncLanIndicators(st);
  } catch (e) {
    return toast("warn", "Сеть (LAN) недоступна", e.message || String(e));
  }
  const online = (st.peers || []).filter(p => p.online);
  if (!online.length) return toast("warn", "Нет устройств в сети", "Подключите и выведите в онлайн хотя бы одного партнёра");
  if (online.length === 1) {
    try {
      await post("/api/lan/spotlight", { object_id: objectId, peer_ids: [online[0].device_id] });
      toast("ok", "Подсвечено партнёру", `Показано на экране «${online[0].name}»`);
    } catch (e) {
      toast("warn", "Не удалось подсветить", e.message || String(e));
    }
    return;
  }
  openSpotlightPickModal(objectId, online);
}

function openSpotlightPickModal(objectId, onlinePeers) {
  stopLanTimers();
  $("#modalBody").innerHTML = `<div class="confirm exp-choice">
    <h3>Кому подсветить квартиру?</h3>
    <p>На выбранном устройстве карточка прокрутится в центр экрана и выделится рамкой.</p>
    <div class="imp-mode-list" style="margin-bottom:12px">
      <button type="button" class="btn-pri" id="spotSendAll">${ICON.spot}Всем онлайн (${onlinePeers.length})</button>
      ${onlinePeers.map(p => `<button type="button" class="btn-sec spot-pick-one" data-pid="${esc(p.device_id)}">${esc(p.name)} (${esc(p.ip)})</button>`).join("")}
    </div>
    <div class="confirm-acts"><button type="button" class="btn-sec" data-c="no">Отмена</button></div>
  </div>`;
  $("#modal").classList.remove("hidden");
  $('#modalBody [data-c="no"]').onclick = closeModal;
  const sendTo = async ids => {
    try {
      const r = await post("/api/lan/spotlight", { object_id: objectId, peer_ids: ids });
      closeModal();
      const names = (r.sent_to || []).map(x => x.name).join(", ");
      toast("ok", "Подсвечено партнёру", names || "Отправлено");
    } catch (e) {
      toast("warn", "Не удалось подсветить", e.message || String(e));
    }
  };
  $("#spotSendAll").onclick = () => sendTo(onlinePeers.map(p => p.device_id));
  $$("#modalBody .spot-pick-one").forEach(b => b.onclick = () => sendTo([b.dataset.pid]));
}

async function sendCardToPeersFlow(objectId) {
  let st;
  try {
    st = await api("/api/lan/status");
    syncLanIndicators(st);
  } catch (e) {
    return toast("warn", "Сеть (LAN) недоступна", e.message || String(e));
  }
  const online = (st.peers || []).filter(p => p.online);
  if (!online.length) return toast("warn", "Нет устройств в сети", "Для отправки откройте окно LAN");
  if (online.length === 1) {
    try {
      const r = await post("/api/lan/send/cards", { peer_ids: [online[0].device_id], object_ids: [objectId] });
      if (!r.ok) throw new Error((r.results?.[0]?.error) || "получатель не ответил");
      toast("ok", "Карточка отправлена", `Получатель: ${online[0].name}`);
    } catch (e) {
      toast("warn", "Не удалось отправить", e.message || String(e));
    }
    return;
  }
  stopLanTimers();
  $("#modalBody").innerHTML = `<div class="confirm exp-choice">
    <h3>Отправить карточку партнёру</h3>
    <div class="imp-mode-list" style="margin-bottom:12px">
      <button type="button" class="btn-pri" id="cardSendAll">${ICON.share}Всем онлайн (${online.length})</button>
      ${online.map(p => `<button type="button" class="btn-sec card-pick-one" data-pid="${esc(p.device_id)}">${esc(p.name)}</button>`).join("")}
    </div>
    <div class="confirm-acts"><button type="button" class="btn-sec" data-c="no">Отмена</button></div>
  </div>`;
  $("#modal").classList.remove("hidden");
  $('#modalBody [data-c="no"]').onclick = closeModal;
  const sendTo = async ids => {
    try {
      const r = await post("/api/lan/send/cards", { peer_ids: ids, object_ids: [objectId] });
      if (!r.ok) throw new Error("ошибка доставки");
      closeModal();
      toast("ok", "Карточка отправлена по LAN", `${(r.results || []).filter(x => x.ok).map(x => x.name).join(", ")}`);
    } catch (e) {
      toast("warn", "Ошибка отправки", e.message || String(e));
    }
  };
  $("#cardSendAll").onclick = () => sendTo(online.map(p => p.device_id));
  $$("#modalBody .card-pick-one").forEach(b => b.onclick = () => sendTo([b.dataset.pid]));
}

async function pullDbFromPeer(peerId, peerName) {
  toast("ok", "Запрашиваю свежую базу…", `Ожидаю снимок от «${peerName || "устройства"}»`);
  try {
    const r = await post("/api/lan/pull/db", { peer_id: peerId });
    const st = await api("/api/lan/status").catch(() => null);
    if (st) syncLanIndicators(st);
    if (r.item) openLanAcceptModal(r.item);
    else openLanModal(st);
  } catch (e) {
    toast("warn", "Не удалось забрать базу", e.message || String(e));
  }
}

function lanReqKindTitle(req) {
  if (req.kind === "favorites") return `Избранное (${req.summary?.total || 0} карт.)`;
  if (req.kind === "db") return `Полная база (${fmt(req.sender_counts?.listings || 0)} объявл.)`;
  return `Подборка карточек (${req.summary?.total || 0} шт.)`;
}

function renderLanInboxHtml(inbox, compact = false) {
  if (!inbox || !inbox.length) {
    return compact ? "" : `<div class="meta lan-empty">Входящих запросов пока нет</div>`;
  }
  return `<div class="${compact ? "lan-inbox-panel panel" : "lan-inbox-list"}">
    ${compact ? `<div class="lan-inbox-hdr"><b>Входящие по локальной сети (${inbox.length})</b><span class="meta">Подтвердите приём, чтобы добавить данные на это устройство</span></div>` : ""}
    ${inbox.map(req => {
      const sub = req.kind === "db"
        ? `объявл. ${fmt(req.sender_counts?.listings || 0)} · фото ${fmt(req.images_received || 0)} догружено / ${fmt(req.images_total || 0)} всего`
        : `новых ${req.summary?.new || 0} · в базе ${req.summary?.matched || 0} · дублей ${req.summary?.duplicate || 0}${req.summary?.stale ? ` · неактуальных ${req.summary.stale}` : ""}`;
      return `<div class="lan-inbox-row" data-req="${esc(req.req_id)}">
        <div class="lan-inbox-info">
          <b>${esc(lanReqKindTitle(req))}</b>
          <span>От: <b>${esc(req.from_device_name || "Устройство")}</b> · ${dtt(req.received_at)} · ${esc(sub)}</span>
        </div>
        <div class="lan-inbox-acts">
          <button type="button" class="btn-pri sm lan-inbox-accept" data-req="${esc(req.req_id)}">Принять…</button>
          <button type="button" class="btn-sec sm lan-inbox-reject" data-req="${esc(req.req_id)}">Отклонить</button>
        </div>
      </div>`;
    }).join("")}
  </div>`;
}

function bindLanInboxEvents(root, inboxList, afterChange, inLanModal = false) {
  if (!root || !Array.isArray(inboxList)) return;
  const byId = new Map(inboxList.map(r => [r.req_id, r]));
  root.querySelectorAll(".lan-inbox-accept").forEach(b => b.onclick = () => {
    const req = byId.get(b.dataset.req);
    if (req) openLanAcceptModal(req, afterChange, inLanModal);
  });
  root.querySelectorAll(".lan-inbox-reject").forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      await post(`/api/lan/inbox/${encodeURIComponent(b.dataset.req)}/reject`);
      toast("ok", "Запрос отклонён", "Входящие данные удалены из очереди");
      if (typeof afterChange === "function") await afterChange();
    } catch (e) {
      toast("warn", "Не удалось отклонить", e.message || String(e));
      b.disabled = false;
    }
  });
}

function openLanAcceptModal(req, afterDone, inLanModal = false) {
  const fromLine = `<div class="imp-prev-from"><span>Отправитель: <b>${esc(req.from_device_name || "Устройство")}</b></span><span>Получено: <b>${dtt(req.received_at)}</b></span></div>`;
  if (req.kind === "cards") {
    const p = req.preview || {};
    const stat = (val, lbl, cls = "") => `<div class="imp-ps ${cls}"><b>${fmt(val)}</b><span>${lbl}</span></div>`;
    $("#modalBody").innerHTML = `<div class="imp-preview lan-accept">
      <h3>Принять карточки по локальной сети</h3>
      ${fromLine}
      <div class="imp-prev-stats">
        ${stat(p.total_cards || req.summary?.total || 0, "карточек")}
        ${stat(p.new_cards || 0, "новых", p.new_cards ? "good" : "")}
        ${stat(p.matched_cards || 0, "совпало с базой")}
        ${stat(p.duplicate_cards || 0, "дублей")}
        ${stat(p.stale_cards || 0, "неактуально", p.stale_cards ? "bad" : "")}
        ${stat(p.updated_notes || 0, "заметок")}
      </div>
      ${p.all_duplicates ? `<div class="imp-prev-dup">Все карточки уже были в базе без новых изменений — пустая партия не создастся.</div>` : ""}
      <div class="imp-prev-sec">
        <div class="imp-prev-lbl">Как поступить с Избранным (♥) для актуальных карточек:</div>
        <div class="imp-mode-list">
          ${IMP_LIKE_MODES.map(([val, title, sub], idx) => `<label class="imp-mode-opt">
            <input type="radio" name="lanLikesMode" value="${val}" ${val === "preserve" ? "checked" : idx === 0 && !IMP_LIKE_MODES.some(m => m[0] === "preserve") ? "checked" : ""}>
            <span><b>${title}</b><i>${sub}</i></span></label>`).join("")}
        </div>
      </div>
      <label class="imp-prev-chk"><input type="checkbox" id="lanKeepManual" checked><span>Сохранять ручные объединения отправителя</span></label>
      <div class="confirm-acts">
        <button type="button" class="btn-sec" data-c="no">Отмена</button>
        <button type="button" class="btn-pri" id="btnLanAcceptGo">${ICON.ok}Принять карточки</button>
      </div>
    </div>`;
    $("#modal").classList.remove("hidden");
    $('#modalBody [data-c="no"]').onclick = () => (inLanModal && afterDone ? afterDone() : closeModal());
    const go = $("#btnLanAcceptGo");
    go.onclick = async () => {
      const likes_mode = ($('#modalBody input[name="lanLikesMode"]:checked') || {}).value || "preserve";
      const apply_manual_merges = !!$("#lanKeepManual")?.checked;
      go.disabled = true; go.textContent = "Принимаю…";
      try {
        const r = await post(`/api/lan/inbox/${encodeURIComponent(req.req_id)}/accept`, { likes_mode, apply_manual_merges });
        closeModal();
        await loadData();
        const st = await api("/api/lan/status").catch(() => null);
        if (st) syncLanIndicators(st);
        if (r.skipped_empty) {
          toast("ok", "Новых изменений нет", r.message || "Все карточки уже были импортированы");
        } else {
          IMP_HIGHLIGHT_ID = r.batch_id; IMP_BATCH_ID = null;
          setView("imports");
          toast("ok", "Карточки приняты по LAN", `Новых ${r.new_cards}, совпало ${r.matched_cards}, неактуальных ${r.stale_cards}`,
            r.batch_id ? { label: "Открыть подборку", fn: () => { IMP_FILTER = "all"; setView("imports"); openImportBatch(r.batch_id); } } : undefined);
        }
        if (typeof afterDone === "function" && view === "imports") await afterDone(true);
      } catch (e) {
        toast("warn", "Не удалось принять карточки", e.message || String(e));
        go.disabled = false; go.textContent = "Принять карточки";
      }
    };
    return;
  }

  if (req.kind === "favorites") {
    const p = req.preview || {};
    $("#modalBody").innerHTML = `<div class="imp-preview lan-accept">
      <h3>Принять Избранное (♥) от ${esc(req.from_device_name || "устройства")}</h3>
      ${fromLine}
      <div class="imp-prev-stats">
        <div class="imp-ps"><b>${fmt(p.total_cards || req.summary?.total || 0)}</b><span>карточек</span></div>
        <div class="imp-ps good"><b>${fmt(p.new_cards || 0)}</b><span>новых</span></div>
        <div class="imp-ps bad"><b>${fmt(p.stale_cards || 0)}</b><span>неактуально</span></div>
      </div>
      <div class="imp-prev-sec">
        <div class="imp-prev-lbl">Режим обновления вашего Избранного:</div>
        <div class="imp-mode-list">
          <label class="imp-mode-opt"><input type="radio" name="lanFavMode" value="merge" checked>
            <span><b>Дополнить моё Избранное</b><i>Добавит входящие актуальные квартиры с ♥ к вашим текущим (ваши ♥ сохранятся)</i></span></label>
          <label class="imp-mode-opt"><input type="radio" name="lanFavMode" value="replace">
            <span><b>Заменить моё Избранное</b><i>Сохранит резервную копию базы, снимет текущие ♥ и поставит только актуальный список отправителя</i></span></label>
        </div>
      </div>
      <div class="confirm-acts">
        <button type="button" class="btn-sec" data-c="no">Отмена</button>
        <button type="button" class="btn-pri" id="btnLanAcceptGo">${ICON.heart}Принять Избранное</button>
      </div>
    </div>`;
    $("#modal").classList.remove("hidden");
    $('#modalBody [data-c="no"]').onclick = () => (inLanModal && afterDone ? afterDone() : closeModal());
    const go = $("#btnLanAcceptGo");
    go.onclick = async () => {
      const favorites_mode = ($('#modalBody input[name="lanFavMode"]:checked') || {}).value || "merge";
      go.disabled = true; go.textContent = "Принимаю…";
      try {
        const r = await post(`/api/lan/inbox/${encodeURIComponent(req.req_id)}/accept`, { favorites_mode });
        closeModal();
        await loadData();
        const st = await api("/api/lan/status").catch(() => null);
        if (st) syncLanIndicators(st);
        toast("ok", "Избранное обновлено по LAN", favorites_mode === "replace" ? `Заменено, в Избранном: ${r.liked}` : `Дополнено (активных в пакете: ${r.liked})`);
        if (typeof afterDone === "function" && view === "imports") await afterDone(true);
      } catch (e) {
        toast("warn", "Не удалось принять Избранное", e.message || String(e));
        go.disabled = false; go.textContent = "Принять Избранное";
      }
    };
    return;
  }

  // Полная передача базы (req.kind === "db")
  const sf = req.sender_freshness || {}, lf = req.local_freshness || {};
  const cmp = req.freshness_cmp || {};
  const srcComp = Object.keys(SRC).map(s => `<tr>
    <td><i class="sdot ${s}"></i>${SRC[s]}</td>
    <td>${sf[s] ? dtt(sf[s]) : "—"}</td>
    <td>${lf[s] ? dtt(lf[s]) : "—"}</td>
  </tr>`).join("");
  $("#modalBody").innerHTML = `<div class="imp-preview lan-accept">
    <h3>Принять полную базу от ${esc(req.from_device_name || "устройства")}</h3>
    ${fromLine}
    <div class="imp-prev-stats">
      <div class="imp-ps"><b>${fmt(req.sender_counts?.listings || 0)}</b><span>объявлений во входящей</span></div>
      <div class="imp-ps"><b>${fmt(req.local_counts?.listings || 0)}</b><span>у вас сейчас</span></div>
      <div class="imp-ps good"><b>${fmt(req.images_received || 0)}</b><span>новых фото (из ${fmt(req.images_total || 0)})</span></div>
    </div>
    <table class="lan-cmp-tbl"><thead><tr><th>Сайт</th><th>Во входящей базе</th><th>В вашей базе</th></tr></thead><tbody>${srcComp}</tbody></table>
    ${cmp.is_older ? `<div class="lan-warn-box">${ICON.warn}<span>${esc(cmp.warning || "Входящая база старее вашей текущей базы.")}</span></div>` : ""}
    ${req.busy ? `<div class="lan-warn-box">${ICON.warn}<span>Сейчас идёт сбор объявлений — дождитесь окончания, чтобы принять базу.</span></div>` : ""}
    <div class="lan-keep-note">Автоматически создастся резервная копия текущей базы. Ваши <b>Избранное (♥)</b>, <b>Скрытые (👎)</b>, <b>личные и импортированные заметки</b>, <b>журнал импортов</b> и <b>подключённые устройства</b> сохранятся.</div>
    <label class="imp-prev-chk"><input type="checkbox" id="lanCopyFilters"><span>Также перенять параметры поиска и геозону отправителя</span></label>
    <div class="confirm-acts">
      <button type="button" class="btn-sec" data-c="no">Отмена</button>
      <button type="button" class="btn-pri" id="btnLanAcceptGo" ${req.busy ? "disabled" : ""}>${ICON.ok}Принять базу</button>
    </div>
  </div>`;
  $("#modal").classList.remove("hidden");
  $('#modalBody [data-c="no"]').onclick = () => (inLanModal && afterDone ? afterDone() : closeModal());
  const go = $("#btnLanAcceptGo");
  go.onclick = async () => {
    const copy_filters_zone = !!$("#lanCopyFilters")?.checked;
    go.disabled = true; go.textContent = "Применяю базу…";
    try {
      const r = await post(`/api/lan/inbox/${encodeURIComponent(req.req_id)}/accept`, { copy_filters_zone });
      closeModal();
      await loadSettings();
      await loadData();
      if (view === "settings") renderSettings();
      const st = await api("/api/lan/status").catch(() => null);
      if (st) syncLanIndicators(st);
      toast("ok", "База синхронизирована по LAN",
        `Объявлений: ${r.listings}, объектов: ${r.objects}, новых фото: ${r.images_copied}${r.filters_copied ? ", фильтры обновлены" : ""}`);
      if (typeof afterDone === "function" && view === "imports") await afterDone(true);
    } catch (e) {
      toast("warn", "Не удалось принять базу", e.message || String(e));
      go.disabled = false; go.textContent = "Принять базу";
    }
  };
}

async function openLanModal(prefetched = null) {
  stopLanTimers();
  let st = prefetched;
  if (!st) {
    try { st = await api("/api/lan/status?refresh=true"); }
    catch (e) { toast("warn", "Сеть (LAN) недоступна", e.message || String(e)); return; }
  }
  LAN_CACHE = st;
  syncLanIndicators(st);
  renderLanModalBody(st);
}

function renderDiscoveredRowsHtml(discovered) {
  if (!discovered || !discovered.length) {
    return `<div class="meta lan-empty">Новых устройств рядом пока не найдено — нажмите «Автопоиск устройств» (программа должна быть открыта на обоих компьютерах).</div>`;
  }
  return `<div class="lan-disc-list">${discovered.map(d => `<div class="lan-disc-item" data-ip="${esc(d.ip)}" data-port="${d.port}" data-dname="${esc(d.device_name)}">
    <div class="lan-disc-row">
      <div class="lan-disc-title"><span class="lan-pdot on"></span><b>${esc(d.device_name)}</b><span class="meta">найдено в сети</span></div>
      <button type="button" class="btn-pri sm lan-quick-pair">Подключить</button>
    </div>
    <div class="lan-quick-box hidden">
      <div class="lan-quick-lbl">Введите 6-значный PIN с экрана «<b>${esc(d.device_name)}</b>»:</div>
      <div class="lan-quick-controls">
        <input type="text" class="lan-q-pin" placeholder="6 цифр PIN" maxlength="8" inputmode="numeric" autocomplete="off">
        <button type="button" class="btn-pri sm lan-q-go">Подключить</button>
        <button type="button" class="btn-sec sm lan-q-cancel">Отмена</button>
      </div>
    </div>
  </div>`).join("")}</div>`;
}

function bindDiscoveredEvents(boxEl, onPaired) {
  if (!boxEl) return;
  boxEl.querySelectorAll(".lan-quick-pair").forEach(btn => {
    btn.onclick = () => {
      boxEl.querySelectorAll(".lan-quick-box").forEach(x => x.classList.add("hidden"));
      const item = btn.closest(".lan-disc-item");
      const qb = item?.querySelector(".lan-quick-box");
      const inp = qb?.querySelector(".lan-q-pin");
      if (qb) qb.classList.remove("hidden");
      if (inp) { inp.value = ""; inp.focus(); }
    };
  });
  boxEl.querySelectorAll(".lan-q-cancel").forEach(btn => {
    btn.onclick = () => {
      const qb = btn.closest(".lan-quick-box");
      const inp = qb?.querySelector(".lan-q-pin");
      if (inp) inp.value = "";
      if (qb) qb.classList.add("hidden");
    };
  });
  boxEl.querySelectorAll(".lan-disc-item").forEach(item => {
    const go = item.querySelector(".lan-q-go");
    const inp = item.querySelector(".lan-q-pin");
    const submitQuick = async () => {
      const pinVal = (inp?.value || "").trim();
      const ip = item.dataset.ip || "";
      const port = +(item.dataset.port || 8766);
      if (!pinVal) { inp?.focus(); return; }
      go.disabled = true; go.textContent = "Подключаю…";
      try {
        const r = await post("/api/lan/pair", { ip, port, pin: pinVal });
        toast("ok", "Устройство подключено", r.name || item.dataset.dname);
        if (typeof onPaired === "function") await onPaired();
      } catch (e) {
        toast("warn", "Не удалось подключить", e.message || String(e));
        go.disabled = false; go.textContent = "Подключить";
        inp?.focus();
      }
    };
    if (go) go.onclick = submitQuick;
    if (inp) inp.onkeydown = e => {
      if (e.key === "Enter") { e.preventDefault(); submitQuick(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); item.querySelector(".lan-q-cancel")?.click(); }
    };
  });
}

function renderLanModalBody(st) {
  stopLanTimers();
  LAN_CACHE = st;
  const ips = (st.local_ips && st.local_ips.length ? st.local_ips : ["127.0.0.1"]).map(ip => `${ip}:${st.port}`);
  const pin = st.pin || {};
  const peersHtml = st.peers.length
    ? st.peers.map(p => {
        const lc = p.live_cfg || {};
        return `<div class="lan-peer" data-pid="${esc(p.device_id)}">
          <div class="lan-peer-top">
            <div class="lan-peer-main">
              <span class="lan-pdot ${p.online ? "on" : ""}" title="${p.online ? "В сети" : (p.error || "Не в сети")}"></span>
              <div class="lan-peer-meta">
                <b>${esc(p.name)}</b>
                <span>${esc(p.ip)}:${p.port} · ${p.online ? `онлайн${p.rtt_ms != null ? ` (${p.rtt_ms} мс)` : ""}` : "не в сети"}${p.last_db_ts ? ` · база ${dtt(p.last_db_ts)}` : ""}${p.has_newer_db ? " · <b class=\"lan-newer-tag\">есть свежее сбор!</b>" : ""}</span>
              </div>
            </div>
            <div class="lan-peer-acts">
              ${p.has_newer_db ? `<button type="button" class="btn-pri sm lan-pull-db" data-pid="${esc(p.device_id)}" data-pname="${esc(p.name)}">Забрать базу</button>` : ""}
              <button type="button" class="btn-sec sm lan-send-db" data-pid="${esc(p.device_id)}" ${!p.online ? "title=\"Устройство не ответило на последний опрос\"" : ""}>Передать базу</button>
              <button type="button" class="btn-sec sm lan-send-fav" data-pid="${esc(p.device_id)}">Передать Избранное</button>
              <button type="button" class="btn-sec sm lan-live-btn" data-pid="${esc(p.device_id)}">Live-режим ▾</button>
              <button type="button" class="icon-btn sm lan-del-peer" data-pid="${esc(p.device_id)}" data-pname="${esc(p.name)}" title="Удалить устройство">${ICON.trash}</button>
            </div>
          </div>
          <div class="lan-live-panel hidden" data-live-pid="${esc(p.device_id)}">
            <div class="lan-live-grid">
              <label class="lan-live-chk"><input type="checkbox" data-lk="accept_spotlight" ${lc.accept_spotlight !== false ? "checked" : ""}><span>Принимать подсветку от партнёра (👁)</span></label>
              <label class="lan-live-chk"><input type="checkbox" data-lk="live_notes" ${lc.live_notes ? "checked" : ""}><span>Live-синхронизация заметок</span></label>
              <label class="lan-live-chk"><input type="checkbox" data-lk="outbox_enabled" ${lc.outbox_enabled !== false ? "checked" : ""}><span>Офлайн-очередь доставки (sync_outbox)</span></label>
              <label class="lan-live-chk"><input type="checkbox" data-lk="live_merges" ${lc.live_merges ? "checked" : ""}><span>Синхронизация ручных объединений</span></label>
              <label class="lan-live-sel"><span>Live-Избранное (отправка)</span>
                <select data-lk="fav_send">
                  <option value="off" ${lc.fav_send === "off" || !lc.fav_send ? "selected" : ""}>Выкл</option>
                  <option value="add_only" ${lc.fav_send === "add_only" ? "selected" : ""}>Только добавление (♥+)</option>
                  <option value="add_remove" ${lc.fav_send === "add_remove" ? "selected" : ""}>Добавление и удаление (♥+/♥−)</option>
                </select>
              </label>
              <label class="lan-live-sel"><span>Live-Избранное (приём)</span>
                <select data-lk="fav_recv">
                  <option value="ask" ${lc.fav_recv === "ask" || !lc.fav_recv ? "selected" : ""}>Спрашивать (уведомление)</option>
                  <option value="auto_add" ${lc.fav_recv === "auto_add" ? "selected" : ""}>Автодобавление (♥+)</option>
                  <option value="auto_sync" ${lc.fav_recv === "auto_sync" ? "selected" : ""}>Полная синхронизация (♥+/♥−)</option>
                </select>
              </label>
            </div>
          </div>
        </div>`;
      }).join("")
    : `<div class="meta lan-empty">Сопряжённых устройств пока нет — сгенерируйте PIN на одном компьютере и введите его на другом.</div>`;

  const discHtml = renderDiscoveredRowsHtml(st.discovered);

  $("#modalBody").innerHTML = `<div class="lan-win" id="lanModalWin">
    <div class="lan-head">
      <div><h3>Локальная сеть и устройства</h3><div class="meta">ID: <code>${esc(st.device_id)}</code></div></div>
      <button type="button" class="btn-sec sm" id="btnLanRefresh">${ICON.refresh}Обновить</button>
    </div>

    <div class="lan-sec">
      <div class="lan-toggle-row">
        <label class="lan-sw-lbl">${sw("lanOnSw", st.enabled)}<span>Разрешить обмен по локальной сети (порт ${st.port})</span></label>
        <span class="st-badge ${st.listening ? "done" : ""}">${st.listening ? "порт открыт" : "выключен"}</span>
      </div>
      <div class="lan-dev-row">
        <label><span>Имя этого устройства</span><input type="text" id="lanNameInp" value="${esc(st.device_name)}" maxlength="80"></label>
        <button type="button" class="btn-sec sm" id="btnLanSaveName">Сохранить имя</button>
      </div>
      <div class="lan-addr-row">
        <span>Адреса для подключения: ${ips.map(a => `<code class="lan-addr">${esc(a)}</code>`).join(" ")}</span>
        <button type="button" class="chip" id="btnLanCopyAddr">Копировать ${esc(ips[0])}</button>
        <button type="button" class="btn-pri sm" id="btnLanGenPin" ${st.enabled ? "" : "disabled"}>Показать PIN для подключения</button>
      </div>
      <div class="lan-pin-box${pin.active ? "" : " hidden"}" id="lanPinBox">
        <span class="meta">Одноразовый PIN (сообщите другому устройству):</span>
        <b class="lan-pin-code" id="lanPinCode">${pin.active ? esc(String(pin.pin).replace(/(\d{3})(\d{3})/, "$1 $2")) : ""}</b>
        <span class="lan-pin-ttl" id="lanPinTtl">${pin.active ? `действует ${pin.remaining_sec} с` : ""}</span>
      </div>
    </div>

    <div class="lan-sec">
      <h4>Входящие запросы ${st.inbox_count ? `<span class="lan-cnt">${st.inbox_count}</span>` : ""}</h4>
      <div id="lanInboxWrap">${renderLanInboxHtml(st.inbox, false)}</div>
    </div>

    <div class="lan-sec">
      <div class="lan-sec-head">
        <h4>Подключить новое устройство</h4>
        <button type="button" class="btn-pri sm" id="btnLanDiscover">${ICON.refresh}Автопоиск устройств</button>
      </div>
      <div class="meta">Нажмите «Показать PIN для подключения» на втором компьютере, затем выберите его здесь и введите 6 цифр PIN. После первого сопряжения устройства находят друг друга автоматически.</div>
      <div id="lanDiscBox">${discHtml}</div>
      <details class="lan-manual-pair">
        <summary>Подключить вручную по IP-адресу</summary>
        <div class="lan-pair-form">
          <input type="text" id="lanPairIp" placeholder="IP-адрес (напр. 192.168.1.25)">
          <input type="number" id="lanPairPort" value="8766" min="1024" max="65534" title="LAN-порт второго устройства">
          <input type="text" id="lanPairPin" placeholder="6-значный PIN" maxlength="8" inputmode="numeric">
          <button type="button" class="btn-pri sm" id="btnLanPair">Сопрячь</button>
        </div>
      </details>
    </div>

    <div class="lan-sec">
      <h4>Сопряжённые устройства (${st.peers.length})</h4>
      ${st.outbox_count ? `<div class="meta lan-outbox-note">В офлайн-очереди ожидает доставки: <b>${st.outbox_count}</b> событий (отправятся при появлении устройства в сети).</div>` : ""}
      <div class="lan-peers">${peersHtml}</div>
    </div>
  </div>`;
  $("#modal").classList.remove("hidden");

  if (pin.active && pin.remaining_sec > 0) {
    let left = pin.remaining_sec;
    lanPinTimer = setInterval(() => {
      left -= 1;
      const ttlEl = $("#lanPinTtl"), boxEl = $("#lanPinBox");
      if (!ttlEl || !boxEl) { stopLanTimers(); return; }
      if (left <= 0) {
        if (lanPinTimer) { clearInterval(lanPinTimer); lanPinTimer = null; }
        boxEl.classList.add("hidden");
      } else {
        ttlEl.textContent = `действует ${left} с`;
      }
    }, 1000);
  }

  const isLanModalOpen = () => !$("#modal")?.classList.contains("hidden") && !!$("#lanModalWin");
  const refreshModal = async () => {
    const fresh = await api("/api/lan/status?refresh=true");
    syncLanIndicators(fresh);
    if (isLanModalOpen()) renderLanModalBody(fresh);
  };
  bindDiscoveredEvents($("#lanDiscBox"), refreshModal);

  if (st.enabled) {
    lanDiscTimer = setInterval(async () => {
      if (!isLanModalOpen() || !$("#lanDiscBox")) { stopLanTimers(); return; }
      try {
        const cur = await api("/api/lan/status");
        syncLanIndicators(cur);
        const curBox = $("#lanDiscBox");
        if (!curBox || !isLanModalOpen()) return;
        const quickOpen = !!curBox.querySelector(".lan-quick-box:not(.hidden)");
        const typingHere = curBox.contains(document.activeElement);
        if (!quickOpen && !typingHere) {
          curBox.innerHTML = renderDiscoveredRowsHtml(cur.discovered);
          bindDiscoveredEvents(curBox, refreshModal);
        }
      } catch (e) { /* игнорируем кратковременный сбой опроса */ }
    }, 3500);
  }

  const discBtn = $("#btnLanDiscover");
  if (discBtn) {
    discBtn.onclick = async () => {
      discBtn.disabled = true;
      discBtn.textContent = "Ищу устройства в сети…";
      try {
        const fresh = await post("/api/lan/discover", { auto_enable: true, wait_sec: 1.5 });
        syncLanIndicators(fresh);
        if (isLanModalOpen()) renderLanModalBody(fresh);
        const foundN = (fresh.discovered || []).length;
        const onN = fresh.online_peers || 0;
        if (foundN) toast("ok", "Найдены устройства", `Доступно для подключения: ${foundN}`);
        else if (onN) toast("ok", "Поиск завершён", `Сопряжённые устройства уже в сети (${onN})`);
        else toast("ok", "Поиск завершён", "Новых несопряжённых устройств рядом не обнаружено");
      } catch (e) {
        toast("warn", "Не удалось выполнить поиск", e.message || String(e));
        discBtn.disabled = false;
        discBtn.innerHTML = `${ICON.refresh}Автопоиск устройств`;
      }
    };
  }
  $("#btnLanRefresh").onclick = refreshModal;
  $("#lanOnSw").onchange = async () => {
    try {
      const fresh = await post("/api/lan/config", { enabled: !!$("#lanOnSw").checked });
      syncLanIndicators(fresh);
      renderLanModalBody(fresh);
      toast("ok", fresh.enabled ? "Локальная сеть включена" : "Локальная сеть выключена",
        fresh.enabled ? `Слушаю порт ${fresh.port}` : "Входящие LAN-подключения остановлены");
    } catch (e) {
      toast("warn", "Ошибка настройки сети", e.message || String(e));
      $("#lanOnSw").checked = !!st.enabled;
    }
  };
  $("#btnLanSaveName").onclick = async () => {
    const name = ($("#lanNameInp").value || "").trim();
    if (!name) return toast("warn", "Пустое имя", "Введите название устройства");
    try {
      const fresh = await post("/api/lan/config", { device_name: name });
      renderLanModalBody(fresh);
      toast("ok", "Имя устройства сохранено", fresh.device_name);
    } catch (e) {
      toast("warn", "Не удалось сохранить имя", e.message || String(e));
    }
  };
  $("#btnLanCopyAddr").onclick = async () => {
    try {
      await navigator.clipboard.writeText(ips[0]);
      toast("ok", "Адрес скопирован", ips[0]);
    } catch { toast("ok", "Адрес этого устройства", ips[0]); }
  };
  $("#btnLanGenPin").onclick = async () => {
    try {
      const p = await post("/api/lan/pin", {});
      st.pin = { active: true, ...p };
      renderLanModalBody(st);
    } catch (e) { toast("warn", "Не удалось создать PIN", e.message || String(e)); }
  };
  $("#btnLanPair").onclick = async () => {
    const btn = $("#btnLanPair");
    const ip = ($("#lanPairIp").value || "").trim();
    const port = +($("#lanPairPort").value || 8766);
    const pinVal = ($("#lanPairPin").value || "").trim();
    btn.disabled = true; btn.textContent = "Сопрягаю…";
    try {
      const r = await post("/api/lan/pair", { ip, port, pin: pinVal });
      toast("ok", "Устройство сопряжено", `${r.name} (${r.ip}:${r.port})`);
      await refreshModal();
    } catch (e) {
      toast("warn", "Сопряжение не удалось", e.message || String(e));
      btn.disabled = false; btn.textContent = "Сопрячь";
    }
  };
  $$(".lan-send-db").forEach(b => b.onclick = async () => {
    const pid = b.dataset.pid;
    b.disabled = true; const prev = b.textContent; b.textContent = "Отправляю…";
    try {
      const r = await post("/api/lan/send/db", { peer_id: pid });
      toast("ok", `База отправлена на «${r.peer.name}»`,
        `Объявлений: ${r.listings}. Фото: передано ${r.sent_images} новых, пропущено ${r.skipped_images} (уже есть). На получателе нужно подтвердить приём.`);
    } catch (e) {
      toast("warn", "Не удалось передать базу", e.message || String(e));
    } finally { b.disabled = false; b.textContent = prev; }
  });
  $$(".lan-send-fav").forEach(b => b.onclick = async () => {
    const pid = b.dataset.pid;
    b.disabled = true; const prev = b.textContent; b.textContent = "Отправляю…";
    try {
      const r = await post("/api/lan/send/favorites", { peer_ids: [pid] });
      const first = (r.results || [])[0] || {};
      if (!r.ok) throw new Error(first.error || "получатель не ответил");
      toast("ok", "Избранное отправлено", `Карточек: ${r.cards}. На получателе нужно подтвердить приём.`);
    } catch (e) {
      toast("warn", "Не удалось передать Избранное", e.message || String(e));
    } finally { b.disabled = false; b.textContent = prev; }
  });
  $$(".lan-pull-db").forEach(b => b.onclick = () => pullDbFromPeer(b.dataset.pid, b.dataset.pname));
  $$(".lan-live-btn").forEach(b => b.onclick = () => {
    const pan = $(`.lan-live-panel[data-live-pid="${CSS.escape(b.dataset.pid)}"]`);
    if (pan) pan.classList.toggle("hidden");
  });
  $$(".lan-live-panel").forEach(pan => {
    const pid = pan.dataset.livePid;
    pan.addEventListener("change", async e => {
      const el = e.target;
      const k = el.dataset.lk;
      if (!k) return;
      const val = el.type === "checkbox" ? !!el.checked : el.value;
      try {
        await post(`/api/lan/peers/${encodeURIComponent(pid)}/live-config`, { [k]: val });
        toast("ok", "Настройки Live-режима сохранены", "");
      } catch (err) {
        toast("warn", "Не удалось сохранить", err.message || String(err));
      }
    });
  });
  $$(".lan-del-peer").forEach(b => b.onclick = () => {
    const pid = b.dataset.pid, pname = b.dataset.pname || pid;
    confirmBox("Удалить сопряжённое устройство?",
      `<p>Удалить <b>${esc(pname)}</b> из списка доверенных устройств?</p><p class="meta">Чтобы снова обмениваться данными, потребуется повторно ввести PIN.</p>`,
      "Удалить", async () => {
        await post(`/api/lan/peers/${encodeURIComponent(pid)}/delete`);
        toast("ok", "Устройство удалено", pname);
        openLanModal();
      });
  });
  bindLanInboxEvents($("#lanInboxWrap"), st.inbox || [], refreshModal, true);
}

async function exportCardsToLan() {
  closeExportMenu();
  let st;
  try {
    st = await api("/api/lan/status?refresh=true");
    syncLanIndicators(st);
  } catch (e) {
    toast("warn", "Сеть (LAN) недоступна", e.message || String(e));
    return;
  }
  if (!st.enabled || !st.peers.length) {
    openLanModal(st);
    toast("warn", "Нет подключённых устройств", !st.enabled
      ? "Включите доступ по локальной сети и сопрягите устройство по PIN"
      : "Подключите второе устройство по PIN в этом окне");
    return;
  }
  openLanSendCardsModal(st);
}

function openLanSendCardsModal(st) {
  stopLanTimers();
  const nPick = EXPORT_PICK.size;
  const likes = OBJS.filter(o => o.liked).length;
  const onlineCnt = st.peers.filter(p => p.online).length;
  const peersChk = st.peers.map(p => `<label class="imp-mode-opt">
    <input type="checkbox" class="lan-send-peer" value="${esc(p.device_id)}" ${p.online || st.peers.length === 1 ? "checked" : ""}>
    <span><b>${esc(p.name)}</b><i>${esc(p.ip)}:${p.port} · ${p.online ? "онлайн" : "не в сети"}</i></span>
  </label>`).join("");

  const whatHtml = nPick > 0
    ? `<p>Будет отправлено выделенных карточек: <b>${nPick}</b>.</p>`
    : `<p>Сейчас карточки вручную не выделены. Вы можете сразу отправить всё <b>Избранное (${likes})</b> или отметить нужные карточки на экране.</p>
       <div class="btn-row" style="margin-bottom:10px"><button type="button" class="btn-sec sm" id="lanSendPickScreen">${ICON.share}Выделить карточки на экране</button></div>`;

  $("#modalBody").innerHTML = `<div class="imp-preview lan-send-cards">
    <h3>Отправить по локальной сети (LAN)</h3>
    ${whatHtml}
    <div class="imp-prev-lbl">Устройства-получатели (${onlineCnt} онлайн):</div>
    <div class="imp-mode-list">${peersChk}</div>
    <div class="confirm-acts">
      <button type="button" class="btn-sec" data-c="no">Отмена</button>
      <button type="button" class="btn-pri" id="btnLanSendGo" ${!nPick && !likes ? "disabled" : ""}>${ICON.share}${nPick ? `Отправить карточки (${nPick})` : `Отправить Избранное (${likes})`}</button>
    </div>
  </div>`;
  $("#modal").classList.remove("hidden");
  $('#modalBody [data-c="no"]').onclick = closeModal;
  if ($("#lanSendPickScreen")) {
    $("#lanSendPickScreen").onclick = () => {
      closeModal();
      if (!["cards", "changes"].includes(view)) setView("cards");
      toast("ok", "Выберите карточки", "Нажмите значок экспорта в правом нижнем углу нужных карточек");
    };
  }
  const go = $("#btnLanSendGo");
  go.onclick = async () => {
    const peerIds = $$("#modalBody .lan-send-peer:checked").map(i => i.value);
    if (!peerIds.length) return toast("warn", "Выберите устройство", "Отметьте хотя бы одного получателя");
    go.disabled = true; go.textContent = "Отправляю…";
    try {
      const r = nPick > 0
        ? await post("/api/lan/send/cards", { peer_ids: peerIds, object_ids: [...EXPORT_PICK], liked_only: false })
        : await post("/api/lan/send/favorites", { peer_ids: peerIds });
      if (!r.ok) {
        const errs = (r.results || []).filter(x => !x.ok).map(x => `${x.name}: ${x.error}`).join("; ");
        throw new Error(errs || "получатели не ответили");
      }
      closeModal();
      const okNames = (r.results || []).filter(x => x.ok).map(x => x.name).join(", ");
      toast("ok", `Отправлено по LAN (${r.cards} карт.)`, `Получатели: ${okNames}. Подтвердите приём на втором устройстве.`);
    } catch (e) {
      toast("warn", "Ошибка отправки по LAN", e.message || String(e));
      go.disabled = false;
      go.textContent = nPick ? `Отправить карточки (${nPick})` : `Отправить Избранное (${likes})`;
    }
  };
}

async function runCardsExport({ object_ids = null, liked_only = false, kind = "cards" }) {
  const btn = $("#btnShare");
  if (btn) btn.disabled = true;
  try {
    const apiDesk = desktop();
    let r;
    if (apiDesk && typeof apiDesk.export_cards === "function") {
      r = await apiDesk.export_cards(object_ids, liked_only, kind);
    } else {
      r = await post("/api/cards/export", { object_ids, liked_only, kind });
    }
    if (!r || r.cancelled) return;
    if (r.error) { toast("warn", "Экспорт не выполнен", r.error); return; }
    const name = fileName(r.path);
    const act = !(apiDesk && typeof apiDesk.export_cards === "function") && name
      ? { label: "Скачать файл", fn: () => {
          const a = document.createElement("a");
          a.href = `/api/exports/download?name=${encodeURIComponent(name)}`;
          a.download = name;
          a.click();
        } }
      : undefined;
    toast("ok", `Выгружено ${r.cards} ${plural(r.cards, "карточка", "карточки", "карточек")}`,
      `${r.path} (объявлений ${r.listings}, фото ${r.images})`, act);
  } catch (e) {
    toast("warn", "Не удалось экспортировать", e.message || String(e));
  } finally {
    updateExportBtnUI();
  }
}

function exportCardsFlow() {
  if (EXPORT_PICK.size > 0) {
    return runCardsExport({ object_ids: [...EXPORT_PICK], liked_only: false, kind: "cards" });
  }
  openExportChoiceModal();
}

function openExportChoiceModal() {
  const likes = OBJS.filter(o => o.liked).length;
  $("#modalBody").innerHTML = `<div class="confirm exp-choice">
    <h3>Что выгрузить в .rwcards.zip?</h3>
    <p>Карточки сейчас не выделены. Вы можете сразу выгрузить всё Избранное или отметить нужные квартиры вручную значком экспорта на карточках.</p>
    <div class="exp-choice-btns">
      <button type="button" class="btn-pri" id="expChoiceFav" ${likes ? "" : "disabled"}>${ICON.heart}Выгрузить всё Избранное (${likes})</button>
      <button type="button" class="btn-sec" id="expChoicePick">${ICON.share}Выделить карточки на экране</button>
    </div>
    <div class="confirm-acts"><button type="button" class="btn-sec" data-c="no">Отмена</button></div>
  </div>`;
  $("#modal").classList.remove("hidden");
  $('#modalBody [data-c="no"]').onclick = closeModal;
  $("#expChoiceFav").onclick = () => { closeModal(); runCardsExport({ liked_only: true, kind: "favorites" }); };
  $("#expChoicePick").onclick = () => {
    closeModal();
    if (!["cards", "changes"].includes(view)) setView("cards");
    toast("ok", "Выберите карточки", "Нажмите значок экспорта в правом нижнем углу нужных карточек (в «Подборке» или «Изменениях»)");
  };
}

// ---------- страница «Импорт» ----------
const IMP_LIKE_MODES = [
  ["all", "Добавить все актуальные в Избранное (♥)", "Удобно, если коллега прислал проверенную подборку для совместного отбора"],
  ["preserve", "Оставить как при экспорте (♥ отправителя)", "Поставит ♥ только тем, у кого стояло ♥ у отправителя; ваши существующие ♥ сохранятся"],
  ["none", "Не добавлять в Избранное", "Квартиры появятся в базе и в истории импорта без автоматических отметок ♥ (ваши ♥ не тронутся)"],
];

function impSourceLabel(b) {
  return b.channel === "lan"
    ? `LAN · ${b.origin_name || "Устройство"}`
    : `Файл · ${b.origin_name || b.source_file || "Пакет"}`;
}

async function renderImports() {
  const el = $("#view-imports");
  if (!el) return;
  let batches, lanSt = null;
  try {
    [batches, lanSt] = await Promise.all([
      api("/api/imports?limit=200"),
      api("/api/lan/status").catch(() => null),
    ]);
  } catch (e) {
    el.innerHTML = `<div class="empty"><b>Не удалось загрузить историю импортов</b>${esc(e.message || String(e))}</div>`;
    return;
  }
  if (lanSt) {
    LAN_CACHE = lanSt;
    syncLanIndicators(lanSt);
  }
  const lanLabel = lanSt?.enabled
    ? (lanSt.online_peers ? `Сеть (LAN): ${lanSt.online_peers} онлайн` : "Сеть (LAN): включена")
    : "Сеть (LAN): подключить…";
  const topBar = `<div class="imp-bar panel">
    <button type="button" class="btn-pri" id="btnImportFile">${ICON.up}Импортировать файл…</button>
    <button type="button" class="btn-sec" id="btnImpLan" title="Подключение к устройствам в локальной сети (LAN)">${esc(lanLabel)}</button>
    <span class="meta imp-bar-meta">${batches.length ? `Партий импорта: ${batches.length}` : "Импортированных подборок пока нет"}</span>
  </div>`;
  const inboxHtml = lanSt?.inbox?.length ? renderLanInboxHtml(lanSt.inbox, true) : "";
  if (!batches.length) {
    el.innerHTML = `${topBar}${inboxHtml}<div class="empty"><b>Импортов ещё не было</b>Нажмите «Импортировать файл…», чтобы загрузить пакет <code>.rwcards.zip</code></div>${aboutTab("imports")}`;
    bindImportListEvents(el, lanSt);
    return;
  }
  const num = (label, v, cls = "") => v ? `<span class="rp-num">${label} <b${cls ? ` class="${cls}"` : ""}>${fmt(v)}</b></span>` : "";
  const cardsHtml = batches.map(b => {
    const st = b.stats || {}, bySrc = st.by_source || {};
    const freshCls = IMP_HIGHLIGHT_ID === b.id ? " imp-fresh" : "";
    const badges = [
      b.new_cards ? `<span class="st-badge done">+${b.new_cards} новых</span>` : "",
      b.matched_cards ? `<span class="st-badge running">совпало ${b.matched_cards}</span>` : "",
      b.duplicate_cards ? `<span class="st-badge">дублей ${b.duplicate_cards}</span>` : "",
      b.stale_cards ? `<span class="st-badge partial">неактуально ${b.stale_cards}</span>` : "",
      b.updated_notes ? `<span class="st-badge">заметок ${b.updated_notes}</span>` : "",
    ].filter(Boolean).join(" ");
    const srcRows = Object.keys(SRC).filter(s => (bySrc[s] && bySrc[s].listings > 0)).map(s => {
      const r = bySrc[s];
      return `<div class="rp-r">
        <span class="rp-src"><i class="sdot ${s}"></i>${SRC[s]}</span>
        ${num("объявлений", r.listings)}${num("новых", r.new)}${num("в базе", r.matched)}${num("дублей", r.duplicate)}${num("неактуально", r.stale, r.stale ? "bad" : "")}
      </div>`;
    }).join("");
    const lModeLabel = b.likes_mode === "preserve" ? "♥ как у отправителя" : b.likes_mode === "none" ? "без ♥" : "все в ♥";
    return `<div class="rp-g${freshCls}" data-batch="${b.id}">
      <div class="rp-gh" data-open-batch="${b.id}" title="Открыть подборку этого импорта">
        <span class="rp-when"><b>${dtt(b.ts)} · ${esc(impSourceLabel(b))}</b>
          <i>${esc(b.source_file || "")}${b.source_file ? " · " : ""}${b.total_listings} объявл. → <b>${b.total_cards}</b> ${plural(b.total_cards, "карточка", "карточки", "карточек")} · ${lModeLabel}</i></span>
        <span class="imp-badges">${badges}</span>
        <span class="imp-gh-acts">
          <button type="button" class="btn-sec sm imp-open-btn" data-open-batch="${b.id}">Открыть подборку →</button>
          <button type="button" class="icon-btn sm imp-del-btn" data-del-batch="${b.id}" title="Удалить запись из журнала «Импорт» (квартиры останутся в базе)">${ICON.trash}</button>
        </span>
      </div>
      ${srcRows}
      <div class="rp-r imp-sum-row"><span class="meta">Склейка в карточки: ${st.multi_site_cards ? `на 2+ сайтах ${st.multi_site_cards}` : "без мультисайтовых склеек"}${st.manual_merged_cards ? ` · ручных объединений ${st.manual_merged_cards}` : ""}</span></div>
    </div>`;
  }).join("");
  el.innerHTML = `${topBar}${inboxHtml}<div class="runs-groups imp-groups">${cardsHtml}</div>`;
  bindImportListEvents(el, lanSt);
  if (IMP_HIGHLIGHT_ID) {
    setTimeout(() => { el.querySelector(".rp-g.imp-fresh")?.classList.remove("imp-fresh"); IMP_HIGHLIGHT_ID = null; }, 2200);
  }
}

function bindImportListEvents(el, lanSt = null) {
  const fBtn = $("#btnImportFile");
  if (fBtn) fBtn.onclick = () => pickAndPreviewCardsImport();
  const lBtn = $("#btnImpLan");
  if (lBtn) lBtn.onclick = () => openLanModal();
  if (lanSt?.inbox?.length) {
    bindLanInboxEvents(el.querySelector(".lan-inbox-panel"), lanSt.inbox, () => renderImports());
  }
  el.querySelectorAll("[data-del-batch]").forEach(b => b.onclick = e => {
    e.stopPropagation();
    const id = +b.dataset.delBatch;
    confirmBox("Удалить запись импорта?",
      "<p>Партия исчезнет из списка «Импорт».</p><p class=\"meta\">Уже загруженные в базу квартиры, избранное (♥) и заметки останутся нетронутыми.</p>",
      "Удалить из журнала", async () => {
        await post(`/api/imports/${id}/delete`);
        if (IMP_BATCH_ID === id) { IMP_BATCH_ID = null; IMP_DETAIL = null; }
        await renderImports();
        toast("ok", "Запись удалена", "Партия убрана из журнала импортов");
      });
  });
  el.querySelectorAll("[data-open-batch]").forEach(b => b.onclick = e => {
    if (e.target.closest("[data-del-batch]")) return;
    openImportBatch(+b.dataset.openBatch);
  });
}

async function openImportBatch(id) {
  IMP_BATCH_ID = +id;
  try {
    IMP_DETAIL = await api(`/api/imports/${IMP_BATCH_ID}`);
  } catch (e) {
    IMP_BATCH_ID = null;
    IMP_DETAIL = null;
    toast("warn", "Партия не найдена", e.message || String(e));
    return renderImports();
  }
  renderHeader([]);
  paintImportDetail();
}

function impSnapshotCard(c) {
  const imgItem = (c.items || []).find(i => i.photo_url) || (c.items || [])[0];
  const imgUrl = imgItem && imgItem.photo_url ? imgItem.photo_url : null;
  const mediaHtml = imgUrl
    ? `<div class="media"><img src="${esc(imgUrl)}" loading="lazy" alt="" onerror="this.parentNode.classList.add('noimg');this.remove()">`
    : `<div class="media noimg" title="Фото не сохранено">`;
  const rooms = c.rooms === 0 ? "студия" : c.rooms ?? "—";
  const offersHtml = `<div class="offers">${(c.items || []).map(i => `<a class="offer ${i.active ? "" : "gone"}" href="${esc(i.url || "#")}" target="_blank" rel="noopener" title="${esc(i.stale_reason || "Открыть на сайте и проверить вручную")}">
    <i class="sdot ${i.source}"></i><span>${SRC[i.source] || i.source}</span>
    <span><b>${fmt(i.price)} ₽</b></span>${ICON.ext}</a>`).join("")}</div>`;
  const noteCard = { imported_notes: c.imported_notes || [] };
  const n = (c.items || []).length;
  const linksRow = (c.items || []).filter(i => i.url).slice(0, 1).map(i =>
    `<a class="btn-sec sm" href="${esc(i.url)}" target="_blank" rel="noopener" title="Открыть объявление на сайте">${ICON.ext}Проверить на сайте</a>`).join("");
  return `<article class="card ${c.is_stale ? "imp-stale" : ""} ${c.excluded ? "excl" : ""}" data-snap="${esc(c.export_card_id)}">
    ${c.is_stale ? `<div class="stale-banner" title="${esc(c.stale_reason || "")}">${ICON.warn}<span>Неактуально</span></div>` : ""}
    ${mediaHtml}
      <div class="media-top"><span class="left"><span class="pill zone-${esc(c.zone || "B")}">${ZONE_SHORT[c.zone] || c.zone || "—"}</span>
        ${c.is_stale ? '<span class="pill stale-pill">неактуально</span>' : ""}</span></div>
      <div class="media-bottom"><div><div class="price">${fmt(c.price)} ₽</div>
        <div class="ppm">${fmt(c.price_m2)} ₽/м²</div></div></div>
    </div>
    <div class="body">
      <div class="facts">
        <div class="fact"><b>${rooms}</b><span>${c.rooms === 0 ? "" : "комнат"}</span></div>
        <div class="fact"><b>${c.area ?? "—"}</b><span>м² общая</span></div>
        <div class="fact"><b>${c.floor ?? "?"}<small>/${c.floors ?? "?"}</small></b><span>этаж</span></div>
      </div>
      <div class="addr">${ICON.pin}<span>${esc(c.address || "адрес не указан")}</span></div>
      ${c.stale_reason ? `<div class="stale-reason">${esc(c.stale_reason)}</div>` : ""}
      ${cardImpNotesHtml(noteCard)}
      ${offersHtml}
    </div>
    <div class="card-foot"><span>${c.last_seen ? `обновл. ${dt(c.last_seen)}` : ""} · ${n} ${plural(n, "объявление", "объявления", "объявлений")}</span>
      <span class="acts">${linksRow}</span></div>
  </article>`;
}

function openSnapshotModal(c) {
  const imgItem = (c.items || []).find(i => i.photo_url) || (c.items || [])[0];
  const imgUrl = imgItem && imgItem.photo_url ? imgItem.photo_url : null;
  const byId = new Map(OBJS.map(o => [o.id, o]));
  const live = byId.get(c.object_id);
  const impNotes = c.imported_notes || [];
  const descs = (c.items || []).filter(i => i.sender_description || i.description);
  $("#modalBody").innerHTML = `
    <div class="m-head">
      <div class="m-img">${imgUrl ? `<img src="${esc(imgUrl)}" alt="">` : ""}</div>
      <div class="m-info">
        <div class="m-title">${c.rooms === 0 ? "Студия" : `${c.rooms ?? "—"}-комнатная`}, ${c.area ?? "—"} м², этаж ${c.floor ?? "?"} из ${c.floors ?? "?"}</div>
        <div class="addr">${ICON.pin}<span>${esc(c.address || "адрес не указан")}</span></div>
        <div class="m-price">${fmt(c.price)} ₽ <span class="meta">${fmt(c.price_m2)} ₽/м²</span></div>
        ${c.stale_reason ? `<div class="stale-reason modal-ver">${ICON.warn}<div><b>Почему считается неактуальной:</b> ${esc(c.stale_reason)}</div></div>` : ""}
        <div class="offers">${(c.items || []).map(i => `<a class="offer ${i.active ? "" : "gone"}" href="${esc(i.url || "#")}" target="_blank" rel="noopener">
          <i class="sdot ${i.source}"></i><span>${SRC[i.source] || i.source}</span><span><b>${fmt(i.price)} ₽</b></span>${ICON.ext}</a>`).join("")}</div>
        ${impNotes.length ? `<details class="m-imp-notes" open><summary>Импортированные заметки (${impNotes.length})</summary>
          <div class="min-list">${impNotes.map(n => `<div class="min-item"><div class="min-head"><b>💬 ${esc(n.origin_name || "Импорт")}</b>${n.ts ? `<span>${tsLabel(n.ts)}</span>` : ""}</div><div class="min-text">${esc(n.text)}</div></div>`).join("")}</div>
        </details>` : ""}
        ${live ? `<div class="confirm-acts"><button type="button" class="btn-sec sm" id="snapOpenLive">Открыть карточку в базе</button></div>` : ""}
      </div>
    </div>
    ${descs.length ? `<details class="m-desc" open><summary>Описание из импорта</summary><p>${esc(descs.map(i => i.sender_description || i.description).join("\n\n"))}</p></details>` : ""}`;
  $("#modal").classList.remove("hidden");
  const liveBtn = $("#snapOpenLive");
  if (liveBtn && live) liveBtn.onclick = () => openObject(live.id);
}

function paintImportDetail() {
  const el = $("#view-imports");
  if (!el || !IMP_DETAIL) return;
  const { batch: b, cards, counts } = IMP_DETAIL;
  const chips = [
    ["all", "Все", counts.all],
    ["relevant", "Актуальные", counts.relevant],
    ["stale", "Неактуальные", counts.stale],
    ["with_notes", "С заметками", counts.with_notes],
    ["duplicate", "Пропущенные дубли", counts.duplicate],
  ];
  const filteredCards = cards.filter(c => {
    if (IMP_FILTER === "relevant") return !c.is_stale;
    if (IMP_FILTER === "stale") return c.is_stale;
    if (IMP_FILTER === "with_notes") return !!(c.note || (c.imported_notes && c.imported_notes.length) || c.sender_note);
    if (IMP_FILTER === "duplicate") return c.status === "duplicate";
    return true;
  });
  const byId = new Map(OBJS.map(o => [o.id, o]));
  const bar = `<div class="imp-bar panel detail">
    <button type="button" class="btn-sec sm" id="btnImpBack">← Все импорты</button>
    <div class="imp-head-meta"><b>${dtt(b.ts)} · ${esc(impSourceLabel(b))}</b>
      <span>${esc(b.source_file || "")}</span></div>
    <div class="seg small imp-chips">${chips.map(([k, lbl, cnt]) => `<label><input type="radio" name="impChip" value="${k}" ${IMP_FILTER === k ? "checked" : ""}><span>${lbl} <em>${cnt || 0}</em></span></label>`).join("")}</div>
  </div>`;
  const gridHtml = filteredCards.length
    ? `<div class="cards flat" id="impGrid">${filteredCards.map(c => {
        const live = !c.is_stale ? byId.get(c.object_id) : null;
        return live ? card(live) : impSnapshotCard(c);
      }).join("")}</div>`
    : `<div class="empty"><b>Карточек по этому фильтру нет</b>Выберите другой фильтр сверху</div>`;
  el.innerHTML = bar + exportBannerHtml() + gridHtml;
  bindExportBarActions(el);
  $("#btnImpBack").onclick = () => { IMP_BATCH_ID = null; IMP_DETAIL = null; renderHeader([]); renderImports(); };
  el.querySelectorAll("input[name=impChip]").forEach(r => r.onchange = () => { IMP_FILTER = r.value; paintImportDetail(); });
  const grid = $("#impGrid");
  if (grid) {
    const snapMap = new Map(filteredCards.map(c => [c.export_card_id, c]));
    grid.addEventListener("click", e => {
      const snapEl = e.target.closest(".card[data-snap]");
      if (snapEl) {
        if (e.target.closest("a, button, input, select, summary, .card-imp-notes")) return;
        const sc = snapMap.get(snapEl.dataset.snap);
        if (sc) openSnapshotModal(sc);
        return;
      }
      const c = e.target.closest(".card[data-id]");
      if (!c) return;
      const eb = e.target.closest(".export-btn");
      if (eb) { e.preventDefault(); toggleExportPick(c.dataset.id); return; }
      const mark = e.target.closest(".heart");
      if (mark) { e.preventDefault(); (mark.classList.contains("dis") ? toggleDislike : toggleLike)(c.dataset.id, mark); return; }
      const um = e.target.closest(".unmerge-btn");
      if (um) { e.preventDefault(); unmergeObject(c.dataset.id, um); return; }
      const mb = e.target.closest(".merge-btn");
      if (mb) { e.preventDefault(); toggleMerge(c.dataset.id, mb); return; }
      if (e.target.closest(".note-btn")) { e.preventDefault(); openObject(c.dataset.id, true); return; }
      if (e.target.closest(".spotlight-btn")) { e.preventDefault(); spotlightObject(c.dataset.id); return; }
      if (e.target.closest("a, button, input, select, summary, .card-imp-notes")) return;
      openObject(c.dataset.id);
    });
  }
}

async function pickAndPreviewCardsImport() {
  const apiDesk = desktop();
  if (apiDesk && typeof apiDesk.pick_cards_import === "function") {
    const r = await apiDesk.pick_cards_import();
    if (!r || r.cancelled || !r.path) return;
    try {
      const prev = await post("/api/imports/preview", { path: r.path });
      openImportPreviewModal(r.path, prev);
    } catch (e) {
      toast("warn", "Не удалось открыть пакет", e.message || String(e));
    }
    return;
  }
  const inp = $("#impFileInput");
  if (inp) { inp.value = ""; inp.click(); }
}

function openImportPreviewModal(path, prev) {
  const stat = (val, lbl, cls = "") => `<div class="imp-ps ${cls}"><b>${fmt(val)}</b><span>${lbl}</span></div>`;
  $("#modalBody").innerHTML = `<div class="imp-preview">
    <h3>Предпросмотр импорта карточек</h3>
    <div class="imp-prev-from">
      <span>Отправитель: <b>${esc(prev.origin_name || "Неизвестно")}</b></span>
      ${prev.exported_at ? `<span>Создан: <b>${dtt(prev.exported_at)}</b></span>` : ""}
      ${prev.source_file ? `<span class="meta">${esc(prev.source_file)}</span>` : ""}
    </div>
    <div class="imp-prev-stats">
      ${stat(prev.total_cards, "карточек")}
      ${stat(prev.new_cards, "новых", prev.new_cards ? "good" : "")}
      ${stat(prev.matched_cards, "совпало с базой")}
      ${stat(prev.duplicate_cards, "дублей")}
      ${stat(prev.stale_cards, "неактуально", prev.stale_cards ? "bad" : "")}
      ${stat(prev.updated_notes, "заметок")}
    </div>
    ${prev.all_duplicates ? `<div class="imp-prev-dup">Все ${prev.total_cards} карточек уже были импортированы ранее без новых изменений — пустая партия создаваться не будет.</div>` : ""}
    <div class="imp-prev-sec">
      <div class="imp-prev-lbl">Как поступить с Избранным (♥) для актуальных карточек:</div>
      <div class="imp-mode-list">
        ${IMP_LIKE_MODES.map(([val, title, sub], idx) => `<label class="imp-mode-opt">
          <input type="radio" name="impLikesMode" value="${val}" ${idx === 0 ? "checked" : ""}>
          <span><b>${title}</b><i>${sub}</i></span></label>`).join("")}
      </div>
    </div>
    <label class="imp-prev-chk"><input type="checkbox" id="impKeepManual" checked><span>Сохранять ручные объединения отправителя</span></label>
    <div class="confirm-acts">
      <button type="button" class="btn-sec" data-c="no">Отмена</button>
      <button type="button" class="btn-pri" id="btnApplyImport">${ICON.up}Импортировать</button>
    </div>
  </div>`;
  $("#modal").classList.remove("hidden");
  $('#modalBody [data-c="no"]').onclick = closeModal;
  const go = $("#btnApplyImport");
  go.onclick = async () => {
    const mode = ($('#modalBody input[name="impLikesMode"]:checked') || {}).value || "all";
    const keepManual = !!$("#impKeepManual")?.checked;
    go.disabled = true;
    go.textContent = "Импортирую…";
    try {
      const r = await post("/api/imports/apply", { path, likes_mode: mode, channel: "file", apply_manual_merges: keepManual });
      closeModal();
      if (r.skipped_empty) {
        toast("ok", "Новых изменений нет", r.message || "Все карточки уже импортированы");
        if (view === "imports" && !IMP_BATCH_ID) await renderImports();
        return;
      }
      IMP_HIGHLIGHT_ID = r.batch_id;
      IMP_BATCH_ID = null;
      await loadData();
      setView("imports");
      toast("ok", "Импорт завершён", `Новых ${r.new_cards}, совпало ${r.matched_cards}, дублей ${r.duplicate_cards}, неактуальных ${r.stale_cards}`, {
        label: "Открыть подборку",
        fn: () => { IMP_FILTER = "all"; setView("imports"); openImportBatch(r.batch_id); },
      });
    } catch (e) {
      go.disabled = false;
      go.innerHTML = `${ICON.up}Импортировать`;
      toast("warn", "Импорт не выполнен", e.message || String(e));
    }
  };
}

// ---------- навигация и события ----------
// Панель фильтров должна оставаться в одну строку: на 1920×1080 с подписями она занимает около 1800 px
// и расползается на два-три ряда, съедая высоту экрана. Сворачиваем по шагам, пока строка не поместится:
// сначала «Тип», «Статус» и «Сортировка» становятся квадратными значками, потом «Ремонт», потом подписи
// у кнопок справа и у «без координат». Что выбрано в свёрнутом фильтре, видно по кружку и подсказке.
const TB_FOLD = [["fNewBox", "fStateBox", "fSortBox"], ["fRenoBox"]];

function fitToolbar() {
  const [top, row] = $$("#filters .tb-row");
  if (!row || !row.clientWidth) return;  // панель скрыта — мерить нечего
  const sig = `${row.clientWidth}|${top.clientWidth}|${$("#zc-A").textContent.length}|${$("#zc-B").textContent.length}|${$("#zc-C").textContent.length}|${$("#likeCount").textContent.length}|${$("#hideCount").textContent.length}|${$("#hideNew").hidden ? 0 : $("#hideNew").textContent.length}|${$("#fSortBox").classList.contains("hidden") ? 1 : 0}|${String(EXPORT_PICK.size).length}`;
  if (fitToolbar._sig === sig) return;
  fitToolbar._sig = sig;
  const over = r => {
    const gap = parseFloat(getComputedStyle(r).columnGap) || 10;
    return [...r.children].reduce((s, k) => s + k.getBoundingClientRect().width, gap * (r.children.length - 1)) > r.clientWidth + 1;
  };
  TB_FOLD.flat().forEach(id => $(`#${id}`).classList.remove("ico"));
  row.classList.remove("tight");
  for (const group of TB_FOLD) {
    if (!over(row)) break;
    group.forEach(id => $(`#${id}`).classList.add("ico"));
  }
  row.classList.toggle("tight", over(row));
  top.classList.remove("tight");          // в верхней строке сворачивается подпись «Избранное»
  top.classList.toggle("tight", over(top));
}

// Кружок на свёрнутом фильтре, если он не в положении по умолчанию, и выбранное — в подсказку:
// у свёрнутого фильтра значения на экране не видно (подсказки живут в data-tip, см. initTips)
function markFilters() {
  $$("#filters .sel").forEach(l => {
    const s = l.querySelector("select");
    if (!s) return;
    l.classList.toggle("on", s.value !== s.options[0].value);
    const base = l.dataset.base || (l.dataset.base = l.getAttribute("title") || l.dataset.tip || "");
    const text = `${l.querySelector("span").textContent}: ${s.options[s.selectedIndex].text} — ${base}`;
    if (l.hasAttribute("title")) l.title = text; else l.dataset.tip = text;
  });
}

addEventListener("resize", () => { fitToolbar._sig = ""; fitToolbar(); });

function setView(v) {
  if (ZE && v !== view) { endZoneEdit(); toast("warn", "Изменение круга отменено", "Круг остался прежним"); }
  view = v;
  if (v !== "cards") PICK = null;
  $$(".nav button").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach(el => el.classList.add("hidden"));
  $(`#view-${v}`).classList.remove("hidden");
  $("#viewTitle").textContent = VIEW_TITLE[v];
  const lists = ["cards", "table", "map", "changes"].includes(v);
  if (v === "changes") markChangesSeen();
  $("#filters").classList.toggle("hidden", !lists);
  $("#fSortBox").classList.toggle("hidden", v === "table");  // в таблице сортируют кликом по заголовку колонки
  if (lists) { markFilters(); fitToolbar._sig = ""; requestAnimationFrame(fitToolbar); }  // панель только что показали — теперь её можно измерить
  $("#btnSide").classList.toggle("hidden", v !== "cards");
  $("#btnGroup").classList.toggle("hidden", v !== "cards");
  $(".main").classList.toggle("inner-scroll", v === "cards");
  if (v === "dashboard") { renderHeader([]); renderDashboard(); }
  else if (v === "changes") { renderHeader([]); renderChanges(); }
  else if (v === "runs") { renderHeader([]); renderRuns(); }
  else if (v === "imports") { renderHeader([]); IMP_BATCH_ID ? openImportBatch(IMP_BATCH_ID) : renderImports(); }
  else if (v === "settings") { renderHeader([]); renderSettings(); }
  else render();
  $(".main").scrollTop = 0;
  $("#cardsList").scrollTop = 0;
}

function serializeFilters() {
  return {
    src: checked("src"), zones: checked("zone"), rooms: checked("rooms"),
    pmin: $("#fPmin").value, pmax: $("#fPmax").value, amin: $("#fAmin").value,
    kmin: $("#fKmin").value, kmax: $("#fKmax").value,
    ymin: $("#fYmin").value, ymax: $("#fYmax").value,
    reno: $("#fReno").value, nb: $("#fNew").value, st: $("#fState").value,
    active: $("#fActive").checked, noCoords: $("#fNoCoords").checked, manual: $("#fManual").checked,
    liked: $("#fLiked").checked, hiddenOnly: $("#fHidden").checked, noDis: $("#fNoDis").checked,
    text: $("#fText").value, sort: $("#fSort").value,
  };
}

function applyFilterState(s) {
  if (!s || typeof s !== "object") return;
  if (Array.isArray(s.src)) $$('input[name="src"]').forEach(i => i.checked = s.src.includes(i.value));
  if (Array.isArray(s.zones)) $$('input[name="zone"]').forEach(i => i.checked = s.zones.includes(i.value));
  if (Array.isArray(s.rooms)) $$('input[name="rooms"]').forEach(i => i.checked = s.rooms.includes(i.value));
  $("#fPmin").value = s.pmin ?? "";
  $("#fPmax").value = s.pmax ?? "";
  $("#fAmin").value = s.amin ?? "";
  $("#fKmin").value = s.kmin ?? "";
  $("#fKmax").value = s.kmax ?? "";
  $("#fYmin").value = s.ymin ?? "";
  $("#fYmax").value = s.ymax ?? "";
  $("#fText").value = s.text ?? "";
  for (const [id, keyName, def] of [["fReno", "reno", ""], ["fNew", "nb", ""], ["fState", "st", "pass"], ["fSort", "sort", "new"]]) {
    const el = $(`#${id}`), val = s[keyName] ?? def;
    el.value = [...el.options].some(o => o.value === val) ? val : def;
  }
  $("#fActive").checked = s.active !== false;
  $("#fNoCoords").checked = !!s.noCoords;
  $("#fManual").checked = !!s.manual;
  $("#fHidden").checked = !!s.hiddenOnly;
  $("#fLiked").checked = !s.hiddenOnly && !!s.liked;
  $("#fNoDis").checked = s.noDis !== false;
}

function saveFilterState() {
  store.set("rw.filters", JSON.stringify(serializeFilters()));
}

function restoreFilters() {
  try {
    const raw = store.get("rw.filters", "");
    if (raw) applyFilterState(JSON.parse(raw));
  } catch (e) { /* повреждённый JSON игнорируем */ }
}

function loadPresets() {
  try {
    const raw = store.get("rw.presets", "[]");
    const list = JSON.parse(raw);
    return Array.isArray(list) ? list.filter(p => p && typeof p.name === "string" && p.state && typeof p.state === "object") : [];
  } catch (e) { return []; }
}

function savePresetsList(list) {
  store.set("rw.presets", JSON.stringify(list.slice(0, 8)));
}

function sameFilterState(a, b) {
  const norm = s => JSON.stringify({
    src: [...(s.src || [])].sort(), zones: [...(s.zones || [])].sort(), rooms: [...(s.rooms || [])].sort(),
    pmin: String(s.pmin || ""), pmax: String(s.pmax || ""), amin: String(s.amin || ""),
    kmin: String(s.kmin || ""), kmax: String(s.kmax || ""),
    ymin: String(s.ymin || ""), ymax: String(s.ymax || ""),
    reno: String(s.reno || ""), nb: String(s.nb || ""), st: String(s.st || "pass"),
    active: s.active !== false, noCoords: !!s.noCoords, manual: !!s.manual, liked: !!s.liked, hiddenOnly: !!s.hiddenOnly,
    noDis: s.noDis !== false, text: String(s.text || "").trim(), sort: String(s.sort || "new"),
  });
  return norm(a) === norm(b);
}

function suggestPresetName(s) {
  const parts = [];
  if (s.liked) parts.push("Избранное");
  if (s.hiddenOnly) parts.push("Скрытые");
  if (s.manual) parts.push("Объединённые");
  if (s.zones && s.zones.length && s.zones.length < 3) parts.push(s.zones.map(z => ZONE_SHORT[z]).join("+"));
  if (s.rooms && s.rooms.length && s.rooms.length < 3) parts.push(s.rooms.map(r => `${r}к`).join(", "));
  if (s.pmax) parts.push(`до ${s.pmax} млн`);
  else if (s.pmin) parts.push(`от ${s.pmin} млн`);
  if (s.amin) parts.push(`от ${s.amin} м²`);
  if (s.kmin && s.kmax) parts.push(`кухня ${s.kmin}–${s.kmax} м²`);
  else if (s.kmin) parts.push(`кухня от ${s.kmin} м²`);
  else if (s.kmax) parts.push(`кухня до ${s.kmax} м²`);
  if (s.ymin && s.ymax) parts.push(`${s.ymin}–${s.ymax} г.`);
  else if (s.ymin) parts.push(`от ${s.ymin} г.`);
  else if (s.ymax) parts.push(`до ${s.ymax} г.`);
  if (s.reno) parts.push($("#fReno").options[$("#fReno").selectedIndex]?.text || "");
  if (s.text) parts.push(s.text.trim());
  return (parts.filter(Boolean).join(" · ") || "Мой фильтр").slice(0, 32);
}

let presetFormOpen = false;
function renderPresets() {
  const bar = $("#presetBar");
  if (!bar) return;
  const presets = loadPresets(), cur = serializeFilters();
  const chips = presets.map((p, idx) => {
    const active = sameFilterState(cur, p.state);
    return `<span class="preset-chip ${active ? "on" : ""}">
      <button type="button" class="preset-go" data-apply="${idx}" title="Применить набор фильтров «${esc(p.name)}»">${esc(p.name)}</button>
      <button type="button" class="preset-x" data-del="${idx}" title="Удалить набор «${esc(p.name)}»">×</button></span>`;
  }).join("");
  const right = presetFormOpen
    ? `<span class="preset-form"><input type="text" id="presetName" maxlength="32" value="${esc(suggestPresetName(cur))}" placeholder="Название набора…">
      <button type="button" class="chip on" id="presetSave">Сохранить</button>
      <button type="button" class="link sm" id="presetCancel">Отмена</button></span>`
    : (presets.length < 8
      ? `<button type="button" class="chip preset-add" id="btnPresetNew" title="Сохранить текущее сочетание фильтров как быструю кнопку">+ Сохранить фильтр</button>`
      : `<span class="meta">максимум 8 наборов</span>`);
  bar.innerHTML = `<span class="preset-lbl">Наборы:</span>${chips}${right}`;
  bar.querySelectorAll("[data-apply]").forEach(b => b.onclick = () => {
    const p = loadPresets()[+b.dataset.apply];
    if (!p) return;
    applyFilterState(p.state);
    saveFilterState();
    renderPresets();
    render();
  });
  bar.querySelectorAll("[data-del]").forEach(b => b.onclick = () => {
    const list = loadPresets(), idx = +b.dataset.del, removed = list.splice(idx, 1)[0];
    if (!removed) return;
    savePresetsList(list);
    renderPresets();
    toast("ok", "Набор удалён", removed.name, {
      label: "Вернуть",
      fn: () => { const curList = loadPresets(); curList.splice(idx, 0, removed); savePresetsList(curList); renderPresets(); },
    });
  });
  const addBtn = $("#btnPresetNew");
  if (addBtn) addBtn.onclick = () => { presetFormOpen = true; renderPresets(); const inp = $("#presetName"); if (inp) { inp.focus(); inp.select(); } };
  const saveBtn = $("#presetSave"), cancelBtn = $("#presetCancel"), nameInp = $("#presetName");
  const commitPreset = () => {
    const name = (nameInp?.value || "").trim().slice(0, 32) || suggestPresetName(cur);
    const list = loadPresets().filter(p => p.name !== name);
    list.push({ name, state: cur });
    savePresetsList(list);
    presetFormOpen = false;
    renderPresets();
  };
  if (saveBtn) saveBtn.onclick = commitPreset;
  if (cancelBtn) cancelBtn.onclick = () => { presetFormOpen = false; renderPresets(); };
  if (nameInp) nameInp.onkeydown = e => {
    if (e.key === "Enter") { e.preventDefault(); commitPreset(); }
    if (e.key === "Escape") { e.preventDefault(); presetFormOpen = false; renderPresets(); }
  };
}

function resetFilters() {
  clearTimeout(_filterTimer);
  // по умолчанию всё включено, кроме посёлков, «без координат», «объединённых», «Избранного» и «Скрытых»; «исключить дизлайки» — включено
  $$("#filters input[type=checkbox]").forEach(i => i.checked = !(i.value === "C" || i.id === "fManual" || ["fNoCoords", "fLiked", "fHidden"].includes(i.id)));
  $("#fPmin").value = $("#fPmax").value = $("#fAmin").value = $("#fKmin").value = $("#fKmax").value = $("#fYmin").value = $("#fYmax").value = $("#fText").value = "";
  $("#fReno").value = $("#fNew").value = ""; $("#fState").value = "pass"; $("#fSort").value = "new";
  saveFilterState();
  renderPresets();
  render();
}

function setTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  store.set("rw.theme", t);
  $$("#theme button").forEach(b => b.classList.toggle("on", b.dataset.t === t));
  if (view === "map" || sideOn()) render();
  // Цвета графиков берутся при отрисовке — после смены темы их надо нарисовать заново
  if (view === "dashboard" && typeof drawDashCharts === "function") drawDashCharts();
}

$$(".nav button").forEach(b => b.onclick = () => {
  if (b.dataset.view === "imports" && view === "imports") { IMP_BATCH_ID = null; IMP_DETAIL = null; }
  setView(b.dataset.view);
});
// «Скрытые» и «Избранное» не включаются вместе: квартира не бывает и той, и другой. Слушатели на самих
// переключателях срабатывают раньше общего на панели, поэтому список рисуется уже с одним из них
$("#fHidden").addEventListener("input", () => { if ($("#fHidden").checked) $("#fLiked").checked = false; });
$("#fLiked").addEventListener("input", () => { if ($("#fLiked").checked) $("#fHidden").checked = false; });
let _filterTimer = null;
$("#filters").addEventListener("input", e => {
  if (e.target.closest("#presetBar")) return;
  saveFilterState();
  renderPresets();
  clearTimeout(_filterTimer);
  if (["text", "search", "number"].includes(e.target.type)) _filterTimer = setTimeout(render, 120);
  else render();
});
$("#btnReset").onclick = resetFilters;
$("#sources").addEventListener("mouseleave", () => renderSources(LAST_STATUS));
$("#sources").addEventListener("click", e => {
  const btn = e.target.closest("[data-continue]");
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const rid = btn.dataset.runId ? +btn.dataset.runId : undefined;
  continueRun({ source: btn.dataset.continue, run_id: rid }, btn);
});
$("#btnRun").onclick = async () => {
  const sources = $$(".src-run:checked").map(i => i.value);
  if (!sources.length) return toast("warn", "Не выбраны сайты", "Включите хотя бы один источник");
  const r = await post("/api/run", { sources });
  if (r.started.length) {
    Object.values(LAST_STATUS).forEach(st => { if (st && !st.running) st.can_continue = false; });
    Object.values(LASTRUN).forEach(st => { if (st) st.can_continue = false; });
    renderSources(LAST_STATUS);
    if (view === "runs") renderRuns();
    toast("ok", "Сбор запущен", r.started.map(k => SRC[k]).join(", "));
  } else toast("warn", "Уже идёт", "Выбранные сайты уже собираются");
};
$("#btnStop").onclick = () => { post("/api/stop", {}); toast("warn", "Останавливаю сбор", "Текущая страница будет дочитана"); };
$("#jToggle").onclick = () => { $("#journal").classList.toggle("collapsed"); $("#jDot").classList.add("hidden"); };
if ($("#lanBtn")) $("#lanBtn").onclick = () => openLanModal();
$("#jClose").onclick = () => $("#journal").classList.add("collapsed");
$("#btnSide").onclick = () => setSide(!sideOn());
$("#btnGroup").onclick = () => setGroup(!groupOn());
initExportMenu();
$("#brand").onclick = aboutBox;
$$("#theme button").forEach(b => b.onclick = () => setTheme(b.dataset.t));
$("#cardsList").addEventListener("mouseover", e => {
  const c = e.target.closest(".card");
  if (c && c.dataset.id !== hl) highlight(c.dataset.id, false);
});
$("#cardsList").addEventListener("click", e => {
  if (e.target.closest(".merge-cancel")) { e.preventDefault(); cancelMerge(); return; }
  if (e.target.closest(".exp-add-liked")) { e.preventDefault(); addLikedToExport(); return; }
  if (e.target.closest(".exp-add-filter")) { e.preventDefault(); addFilteredToExport(); return; }
  if (e.target.closest(".exp-clear")) { e.preventDefault(); clearExportPick(); return; }
  if (e.target.closest(".exp-go-file")) { e.preventDefault(); exportCardsFlow(); return; }
  if (e.target.closest(".exp-go-lan")) { e.preventDefault(); exportCardsToLan(); return; }
  const c = e.target.closest(".card");
  if (!c) return;
  if (e.target.closest(".spot-x")) { e.preventDefault(); clearSpotlight(); return; }
  if (c.classList.contains("picked")) { PICK = null; CARD_DOM.delete(c.dataset.id); c.classList.remove("picked"); c.querySelector(".pick-note")?.remove(); }
  const eb = e.target.closest(".export-btn");
  if (eb) { e.preventDefault(); toggleExportPick(c.dataset.id); return; }
  if (e.target.closest(".spotlight-btn")) { e.preventDefault(); spotlightObject(c.dataset.id); return; }
  const mark = e.target.closest(".heart");
  if (mark) { e.preventDefault(); (mark.classList.contains("dis") ? toggleDislike : toggleLike)(c.dataset.id, mark); return; }
  if (e.target.closest(".keep-hidden")) { e.preventDefault(); keepHidden(c.dataset.id); return; }
  const um = e.target.closest(".unmerge-btn");
  if (um) { e.preventDefault(); unmergeObject(c.dataset.id, um); return; }
  const mb = e.target.closest(".merge-btn");
  if (mb) { e.preventDefault(); toggleMerge(c.dataset.id, mb); return; }
  if (e.target.closest(".note-btn")) { e.preventDefault(); openObject(c.dataset.id, true); return; }
  if (e.target.closest(".pin")) {
    if (!sideOn()) setSide(true);
    highlight(c.dataset.id, true);
    return;
  }
  if (e.target.closest("a, button, input, select, summary, .card-imp-notes")) return;
  if (c.classList.contains("spotlighted")) clearSpotlight();
  openObject(c.dataset.id);  // окно объекта: история цен по каждому сайту
});

// Таблица и изменения → карточка в подборке: открыть, прокрутить и подсветить
function goToCard(id) {
  PICK = id;
  setView("cards");
  setTimeout(() => {
    const el = document.querySelector(`.card[data-id="${CSS.escape(id)}"]`);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
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
      LASTRUN[r.source] = { state: r.status, message: r.message, finished: r.finished, running: false, can_continue: !!r.can_continue, run_id: r.id };
  } catch (e) { /* нет прогонов */ }
  renderSources({});
  restoreFilters();
  renderPresets();
  setView("dashboard");
  await loadData();
  initAuto();
  initSound();
  updateChangesBadge();
  const impInp = $("#impFileInput");
  if (impInp) impInp.onchange = async () => {
    const file = impInp.files && impInp.files[0];
    if (!file) return;
    try {
      const res = await api(`/api/imports/upload?name=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
        body: file,
      });
      openImportPreviewModal(res.path, res.preview);
    } catch (e) {
      toast("warn", "Не удалось открыть пакет", e.message || String(e));
    } finally {
      impInp.value = "";
    }
  };
  poll();
});
