"""Капча Авито: какой вид показан и автоматическое прохождение пазла с ползунком (GeeTest v4).

Авито показывает проверку двумя способами:
- отдельная страница «Доступ ограничен» (код 429). Страница сама выбирает проверку (запрос
  /web/5/firewallCaptcha/get): GeeTest — окно с заданием по кнопке «Пройти проверку» (новая версия страницы
  открывает его сама), hCaptcha — галочка, или картинка с символами;
- окно «Доступ ограничен: проблема с IP» поверх обычной страницы (выдача приходит с кодом 439) с кнопкой
  «Продолжить», которая открывает то же окно GeeTest.
Сама программа проходит только GeeTest «пазл с ползунком»: находит на картинке вырез под кусочек и тянет
ползунок мышью, как человек (app.human). Задание и картинки берутся из ответов, которые браузер получил
с загрузки страницы (net.Browser запоминает их всё время: задание может прийти ещё до нажатия кнопки), —
лишних запросов нет. Другие виды (иконки, галочка, символы) и неудачные попытки — человеку:
звук и уведомление Windows (net.Browser)."""
import io
import json
import random
import time
from urllib.parse import urlsplit

from PIL import Image, ImageChops, ImageFilter

FIREWALL_KIND = {"geetest": "GeeTest", "modal": "GeeTest в окне поверх страницы", "hcaptcha": "hCaptcha (галочка)",
                 "image": "картинка с символами", "pending": "вид не определился", "": "вид не распознан"}
GEETEST_KIND = {"slide": "пазл с ползунком", "icon": "иконки по порядку", "word": "слова по порядку",
                "phrase": "слова по порядку", "match": "три в ряд", "winlinze": "пять в ряд", "gobang": "пять в ряд",
                "nine": "картинки из сетки", "ai": "без задания"}

# Окно «Доступ ограничен» поверх обычной страницы Авито (диалог сайта); по HTML его не отличить от заготовки
OVERLAY_JS = """() => [...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].some(d => {
  const r = d.getBoundingClientRect(), s = getComputedStyle(d);
  return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > 0
    && (d.textContent || '').indexOf('Доступ ограничен') >= 0; })"""

# Открыто окно GeeTest с заданием
_POPUP_JS = """() => [...document.querySelectorAll('[class*="geetest_box"]')].some(el => {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return r.width > 100 && r.height > 100 && s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > 0; })"""

# Вид проверки: 'geetest' (окно GeeTest открыто или его открывает «Пройти проверку»), 'modal' (окно поверх
# страницы с «Продолжить»), 'hcaptcha', 'image', '' (проверки нет); пока страница не выбрала — 'pending'
_KIND_JS = """() => {
  const vis = el => { if (!el) return false; const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  if ((%s)()) return 'geetest';
  if (!document.querySelector('.js-firewall-form')) return (%s)() ? 'modal' : '';
  if (vis(document.getElementById('h-captcha'))) return 'hcaptcha';
  const img = document.querySelector('#inner-captcha .js-form-captcha-image');
  if (vis(document.getElementById('inner-captcha')) && img && img.getAttribute('src')) return 'image';
  if (vis(document.getElementById('geetest_captcha'))) return 'geetest';
  return 'pending';
}""" % (_POPUP_JS, OVERLAY_JS)

# Кнопки, которые открывают окно GeeTest
_OPEN_BUTTON = {"geetest": 'button[name="submit"]',
                "modal": '[aria-modal="true"] button[type="submit"], [role="dialog"] button[type="submit"]'}

# Окно GeeTest: картинка-фон и кусочек пазла (по адресам картинок из задания) и ручка ползунка
_FIND_JS = """(n) => {
  const box = el => { if (!el) return null; const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0
      ? {x: r.left, y: r.top, width: r.width, height: r.height} : null; };
  const src = el => el.tagName === 'IMG' ? (el.currentSrc || el.src || '') : (getComputedStyle(el).backgroundImage || '');
  let bg = null, slice = null;
  for (const el of document.querySelectorAll('div, img, span, canvas')) {
    const s = src(el); if (!s || s === 'none') continue;
    if (!bg && s.includes(n.bg) && box(el)) bg = el;
    else if (!slice && s.includes(n.slice) && box(el)) slice = el;
  }
  let btn = null, best = 1e9;
  for (const el of document.querySelectorAll('[class*="geetest_btn"], [class*="geetest_slider_button"], [class*="geetest_drag"]')) {
    const b = box(el); if (!b) continue;
    const a = b.width * b.height; if (a > 200 && a < best) { best = a; btn = el; }
  }
  if (!btn && bg) {  // запасной способ: «хваталка» под картинкой
    const g = box(bg);
    for (const el of document.querySelectorAll('[class*="geetest"] *')) {
      const b = box(el); if (!b || b.y < g.y + g.height - 2 || b.y > g.y + g.height + 120) continue;
      const c = getComputedStyle(el).cursor;
      if (/pointer|grab|move/.test(c) && b.width >= 24 && b.width <= 90 && b.height >= 24 && b.height <= 90 && (!btn || b.x < box(btn).x)) btn = el;
    }
  }
  return {bg: box(bg), slice: box(slice), btn: box(btn)};
}"""

_SLICE_X_JS = """(name) => {
  for (const el of document.querySelectorAll('div, img, span, canvas')) {
    const s = el.tagName === 'IMG' ? (el.currentSrc || el.src || '') : (getComputedStyle(el).backgroundImage || '');
    if (s && s.includes(name)) { const r = el.getBoundingClientRect(); if (r.width > 2) return r.left; }
  }
  return null;
}"""


def _jsonp(text):
    """Ответ GeeTest: geetest_123({...}) или просто {...}."""
    text = text or ""
    i, j = text.find("("), text.rfind(")")
    body = text[i + 1:j] if 0 <= i < j and not text.lstrip().startswith("{") else text
    try:
        return json.loads(body)
    except ValueError:
        return None


def _is_gt(url, part):
    p = urlsplit(url)
    return p.path.endswith(part) and "captcha_id=" in p.query


def _tail(path):
    """Конец адреса картинки задания: папка и файл. У фона и кусочка имя файла одно и то же —
    различаются только папки (…/bg/abc.png и …/slice/abc.png)."""
    parts = [p for p in (path or "").split("/") if p]
    return "/".join(parts[-2:])


# ---------- где вырез под кусочек ----------

def _edges(gray):
    """Сила перепадов яркости (оператор Собеля): |dx| + |dy|, обе стороны каждого перепада."""
    out = None
    for kern in ((-1, 0, 1, -2, 0, 2, -1, 0, 1), (-1, -2, -1, 0, 0, 0, 1, 2, 1)):
        for sign in (1, -1):
            e = gray.filter(ImageFilter.Kernel((3, 3), [sign * v for v in kern], scale=1, offset=0))
            out = e if out is None else ImageChops.add(out, e)
    return out


def _thin(points, limit):
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit)]


def find_gap(bg_bytes, piece_bytes, top=None):
    """Где на фоне вырез под кусочек пазла. Возвращает (x, уверенность): x — левый край кусочка в пикселях
    картинки фона, уверенность — насколько лучший вариант выделяется среди остальных (≥ 2 — уверенно).
    top — верх кусочка на фоне (по окну GeeTest), если известен; иначе ищется и по высоте.
    Сравниваются контур кусочка с перепадами яркости фона и яркость внутри выреза с яркостью вокруг."""
    bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
    piece = Image.open(io.BytesIO(piece_bytes)).convert("RGBA")
    W, H = bg.size
    alpha = piece.getchannel("A")
    bbox = alpha.point(lambda a: 255 if a >= 128 else 0).getbbox()
    if not bbox:
        raise ValueError("кусочек пазла пустой")
    if piece.size[1] >= H * 0.9:  # кусочек во всю высоту фона — его верх уже на своём месте
        top = 0
    mask = alpha.point(lambda a: 255 if a >= 128 else 0)
    inner = mask.filter(ImageFilter.MinFilter(7))
    outer = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(9)), mask)
    m, pin, pout = mask.load(), inner.load(), outer.load()
    w, h = piece.size
    edge_pts, in_pts, out_pts = [], [], []
    for y in range(h):
        for x in range(w):
            if m[x, y]:
                if any(not (0 <= x + dx < w and 0 <= y + dy < h) or not m[x + dx, y + dy] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    edge_pts.append((x, y))
                if pin[x, y]:
                    in_pts.append((x, y))
            elif pout[x, y]:
                out_pts.append((x, y))
    edge_pts, in_pts, out_pts = _thin(edge_pts, 240), _thin(in_pts, 120), _thin(out_pts, 120)
    gray = bg.convert("L")
    E, L = _edges(gray).load(), gray.load()
    x_max = W - w
    spread = 1  # верх кусочка по окну GeeTest известен с точностью до пикселя
    if top is None:  # грубо по высоте, потом точно
        ys = range(0, max(1, H - h + 1), 3)
        coarse = _thin(edge_pts, 120)
        _, top = max(((sum(E[x + px, y + py] for px, py in coarse), y) for y in ys for x in range(0, x_max + 1, 2)))
        spread = 2
    top = int(round(top))
    ys = [y for y in range(top - spread, top + spread + 1) if 0 <= y <= H - h] or [max(0, min(H - h, top))]
    best = None
    for y in ys:
        e_sc, c_sc = [], []
        for x in range(0, x_max + 1):
            e_sc.append(sum(E[x + px, y + py] for px, py in edge_pts) / len(edge_pts))
            if in_pts and out_pts:
                li = sum(L[x + px, y + py] for px, py in in_pts) / len(in_pts)
                lo = sum(L[x + px, y + py] for px, py in out_pts if 0 <= x + px < W and 0 <= y + py < H) / len(out_pts)
                c_sc.append(abs(li - lo))
            else:
                c_sc.append(0.0)
        score = [a + b for a, b in zip(_z(e_sc), [0.6 * v for v in _z(c_sc)])]
        i = max(range(len(score)), key=score.__getitem__)
        if best is None or score[i] > best[0]:
            others = [s for k, s in enumerate(score) if abs(k - i) > w / 2]
            best = (score[i], i, score[i] - max(others) if others else 9.0)
    return best[1], round(best[2], 2)


def _z(v):
    n = len(v)
    mu = sum(v) / n
    sd = (sum((a - mu) ** 2 for a in v) / n) ** 0.5 or 1.0
    return [(a - mu) / sd for a in v]


# ---------- прохождение ----------

class _Recorder:
    """Ответы GeeTest текущей вкладки, которые браузер запомнил с загрузки страницы: задание, картинки, итог."""

    def __init__(self, b):
        self.b = b
        self.used = set()

    def _tick(self, ms=250):
        self.b.page.wait_for_timeout(ms)  # пока ждём, браузер передаёт события (ответы сайта)

    def wait_json(self, part, timeout):
        """Самый свежий ещё не прочитанный ответ GeeTest /load или /verify (данные JSON) или None.
        Задание, пришедшее ещё до нажатия кнопки, тоже годится."""
        end = time.time() + timeout
        while True:
            fresh = [r for r in list(self.b.gt_responses()) if id(r) not in self.used and _is_gt(r.url, part)]
            self.used.update(id(r) for r in fresh)
            for r in reversed(fresh):
                try:
                    j = _jsonp(r.text())
                except Exception:
                    j = None
                if isinstance(j, dict):
                    return j.get("data") if isinstance(j.get("data"), dict) else j
            if time.time() >= end:
                return None
            self._tick()

    def body(self, path, timeout=8):
        """Картинка задания по её адресу (из уже полученных браузером) или None."""
        tail, end = _tail(path), time.time() + timeout
        while tail:
            for r in reversed(list(self.b.gt_responses())):
                if urlsplit(r.url).path.endswith(tail):
                    try:
                        return r.body()
                    except Exception:
                        break
            if time.time() >= end:
                return None
            self._tick()
        return None


class AvitoCaptcha:
    """Капча Авито («Доступ ограничен» — страница или окно поверх страницы): вид и прохождение пазла GeeTest."""

    TRIES = 3          # попыток собрать пазл в одном окне GeeTest (после неудачи GeeTest сам даёт новый)
    LOAD_WAIT_S = 15   # сколько ждать задание GeeTest после открытия окна

    def __init__(self, enabled=True):
        self.enabled = enabled

    def kind(self, page, wait_s=10):
        """'geetest' | 'modal' | 'hcaptcha' | 'image' | 'pending' (страница так и не выбрала) | '' (проверки нет)."""
        end, k = time.time() + wait_s, "pending"
        while True:
            try:
                k = page.evaluate(_KIND_JS)
            except Exception:
                k = "pending"
            if k != "pending" or time.time() >= end:
                return k
            page.wait_for_timeout(500)

    def describe(self, page):
        return FIREWALL_KIND.get(self.kind(page), "вид не распознан")

    @staticmethod
    def _popup(page):
        try:
            return bool(page.evaluate(_POPUP_JS))
        except Exception:
            return False

    def _open_popup(self, b, kind):
        """Открыть окно GeeTest: «Пройти проверку» на странице проверки, «Продолжить» в окне поверх страницы.
        Сайт может открыть окно и сам — тогда кнопку не трогаем (повторное нажатие создало бы второе окно)."""
        for _ in range(2):
            if self._popup(b.page):
                return True
            btn = b.box(_OPEN_BUTTON.get(kind, _OPEN_BUTTON["geetest"]))
            if btn is None:
                return False
            b.mouse.click(btn)
            end = time.time() + 10
            while time.time() < end:
                if self._popup(b.page):
                    return True
                b.page.wait_for_timeout(300)
            # «Продолжить» могло открыть отдельную страницу проверки — там своя кнопка
            kind = self.kind(b.page, wait_s=5)
            if kind not in ("geetest", "modal"):
                return False
        return self._popup(b.page)

    def solve(self, b):
        """Пробует пройти капчу сам; b — net.Browser. Возвращает (итог, вид): 'solved' — GeeTest принял ответ,
        сайт сейчас откроет страницу; 'unsupported' — такой вид проходит человек; 'failed' — не получилось."""
        kind = self.kind(b.page)
        what = FIREWALL_KIND.get(kind, "вид не распознан")
        if kind not in ("geetest", "modal"):
            return "unsupported", what
        if not self.enabled:
            return "unsupported", f"{what} (автопрохождение выключено в настройках)"
        rec = _Recorder(b)
        if not self._open_popup(b, kind):
            if b.left_firewall():
                return "solved", f"{what}: без задания"  # GeeTest пропустил по поведению
            return "failed", f"{what}: окно с заданием не открылось"
        for attempt in range(1, self.TRIES + 1):
            task = rec.wait_json("/load", self.LOAD_WAIT_S)
            if task is None:
                if b.left_firewall():
                    return "solved", f"{what}: без задания"
                return "failed", f"{what}: задание не пришло"
            gt = task.get("captcha_type")
            what = f"GeeTest — {GEETEST_KIND.get(gt, gt or 'неизвестное задание')}"
            if gt == "ai":  # проверка без задания: GeeTest решает по поведению сам
                verdict = rec.wait_json("/verify", 10)
                ok = (verdict or {}).get("result") == "success" or b.left_firewall()
                return ("solved" if ok else "failed"), what
            if gt != "slide":
                return "unsupported", what
            res = self._slide(b, rec, task)
            if res == "success" or b.left_firewall():
                return "solved", what
            b.log(f"пазл не принят ({attempt} из {self.TRIES}){'' if res == 'fail' else ': ' + res}")
            if res != "fail":  # не нашлось окно или картинки — новое задание само не придёт
                return "failed", what
            b.mouse.wait(random.uniform(0.8, 2.0))  # GeeTest показывает «не удалось» и меняет картинку
        return "failed", what

    def _slide(self, b, rec, task):
        """Одна попытка собрать пазл: 'success' | 'fail' | причина, почему попытки не было."""
        bg, piece = rec.body(task.get("bg")), rec.body(task.get("slice"))
        if not bg or not piece:
            return "картинки задания не получены"
        names = {"bg": _tail(task.get("bg")), "slice": _tail(task.get("slice"))}
        # окно должно успокоиться: GeeTest показывает, куда тянуть, — ручка сама ездит туда-обратно
        el, prev, still, end = None, None, None, time.time() + 8
        while time.time() < end:
            el = b.page.evaluate(_FIND_JS, names)
            if el and el.get("bg") and el.get("slice") and el.get("btn"):
                pos = (el["btn"]["x"], el["slice"]["x"])
                if prev and abs(pos[0] - prev[0]) < 0.5 and abs(pos[1] - prev[1]) < 0.5:
                    still = still or time.time()
                    if time.time() - still >= 0.6:
                        break
                else:
                    still = None
                prev = pos
            b.page.wait_for_timeout(200)
        if not el or not el.get("bg") or not el.get("btn") or not el.get("slice"):
            return "не найдено окно с пазлом"
        bw, bh = Image.open(io.BytesIO(bg)).size
        sx, sy = el["bg"]["width"] / bw, el["bg"]["height"] / bh
        top = (el["slice"]["y"] - el["bg"]["y"]) / sy if sy else task.get("ypos")
        gap, conf = find_gap(bg, piece, top=top if top is not None and top >= 0 else task.get("ypos"))
        target = el["bg"]["x"] + gap * sx
        b.mouse.wait(random.uniform(0.6, 1.6))  # человек сначала смотрит на картинку
        b.mouse.drag(el["btn"], target - el["slice"]["x"],
                     position=lambda: b.page.evaluate(_SLICE_X_JS, names["slice"]), target=target)
        verdict = rec.wait_json("/verify", 10)
        result = (verdict or {}).get("result")
        b.note(f"пазл: вырез x={gap} (уверенность {conf}), GeeTest ответил: {result or 'нет ответа'}")
        return "success" if result == "success" else "fail" if result == "fail" or verdict is None else str(result)
