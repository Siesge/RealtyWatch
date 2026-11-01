"""Пазл-капча Авито (GeeTest): поиск выреза на картинке и прохождение «мышью, как человек».
Сайт — локальная имитация страницы «Доступ ограничен» с окном пазла: кусочек едет медленнее ручки
(как бывает у GeeTest), первая попытка может быть отклонена — программа берёт новое задание и пробует снова.
Капча другого вида (иконки) — программа зовёт человека: звук и уведомление, затем ждёт его."""
import io
import json
import random
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

from app import alerts, captcha
from app.net import Blocked
from tests.support import LocalBrowser, fast_timing, has_browser

W, H, P, SHOWN_W = 340, 226, 68, 300  # картинка 340×226, в окне — 300 px шириной


def _texture(rnd):
    img = Image.new("RGB", (W, H), tuple(rnd.randint(60, 200) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x0, y0 = rnd.randint(-40, W), rnd.randint(-40, H)
        box = (x0, y0, x0 + rnd.randint(20, 120), y0 + rnd.randint(20, 90))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)(box, fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    return ImageChops.add(img, Image.effect_noise((W, H), 18).convert("RGB"), offset=-128)


def _mask():
    m = Image.new("L", (P, P), 0)
    d, k = ImageDraw.Draw(m), P // 6
    d.rounded_rectangle((k, k + 6, P - k - 6, P - k), radius=6, fill=255)
    d.ellipse((P // 2 - 9, 2, P // 2 + 9, k + 14), fill=255)       # выступ сверху
    d.ellipse((P - k - 14, P // 2 - 9, P - 2, P // 2 + 9), fill=255)  # выступ справа
    return m


def _png(im):
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def make_puzzle(seed, light=False):
    """Картинка с вырезом и кусочек пазла, как у GeeTest. Возвращает (фон, кусочек, x выреза, y выреза)."""
    rnd = random.Random(seed)
    bg, mask = _texture(rnd), _mask()
    gx, gy = rnd.randint(P + 20, W - P - 4), rnd.randint(4, H - P - 4)
    piece = bg.crop((gx, gy, gx + P, gy + P)).convert("RGBA")
    piece.putalpha(mask)
    region = bg.crop((gx, gy, gx + P, gy + P))
    hole = Image.composite(ImageEnhance.Brightness(region).enhance(1.6 if light else 0.45), region, mask)
    rim = ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(3)), mask.filter(ImageFilter.MinFilter(3)))
    bg.paste(Image.composite(Image.new("RGB", (P, P), (235, 235, 235)), hole, rim), (gx, gy))
    return _png(bg), _png(piece), gx, gy


class GapFinder(unittest.TestCase):
    def test_finds_cutout(self):
        for seed in range(8):
            for light in (False, True):
                bg, piece, gx, gy = make_puzzle(seed, light)
                x, _ = captcha.find_gap(bg, piece, top=gy)
                self.assertLessEqual(abs(x - gx), 2, f"картинка {seed}: найдено x={x}, вырез x={gx}")

    def test_finds_cutout_without_known_top(self):
        for seed in (40, 41):
            bg, piece, gx, gy = make_puzzle(seed)
            x, _ = captcha.find_gap(bg, piece)
            self.assertLessEqual(abs(x - gx), 2)


# ---------- имитация страницы Авито «Доступ ограничен» с окном GeeTest ----------

FIREWALL = """<html><head><title>Доступ ограничен: проблема с IP</title></head><body>
<form class="js-firewall-form"><div id="h-captcha" style="display: none">галочка</div>
<fieldset id="inner-captcha" style="display: none"><img class="js-form-captcha-image" alt=""></fieldset>
<div id="geetest_captcha" style="display: none"><p>Иногда IP выглядит необычно, поэтому нужно пройти простую проверку.</p></div>
<div class="form-action"><button type="button" name="submit">Пройти проверку</button></div></form>
<script>
function showRedirectMessage() { document.querySelector('.form-action').innerHTML = '<p>Проверка пройдена, перенаправление...</p>'; }
const RATIO = 0.9;  // кусочек едет медленнее ручки
let LOT = null, start = null, btnX = 0;
function jsonp(url) { return new Promise(res => { const cb = 'geetest_' + Date.now() + Math.floor(Math.random() * 1000);
  window[cb] = d => { delete window[cb]; res(d); }; const s = document.createElement('script'); s.src = url + '&callback=' + cb; document.head.appendChild(s); }); }
async function load() { const r = await jsonp('/gt/load?captcha_id=test'); LOT = r.data.lot_number; render(r.data); }
function place() { document.querySelector('.geetest_btn').style.left = btnX + 'px'; document.querySelector('.geetest_slice').style.left = (btnX * RATIO) + 'px'; }
function render(d) {
  let wrap = document.querySelector('.geetest_box_wrap');
  if (!wrap) {
    wrap = document.createElement('div'); wrap.className = 'geetest_box_wrap';
    wrap.innerHTML = '<div class="geetest_box" style="position:fixed;left:260px;top:120px;width:300px;padding:20px;background:#fff">' +
      '<div class="geetest_wrap" style="position:relative;width:300px;height:200px;overflow:hidden">' +
      '<div class="geetest_bg" style="position:absolute;left:0;top:0;width:300px;height:200px;background-size:100% 100%"></div>' +
      '<div class="geetest_slice" style="position:absolute;left:0;top:0"><div class="geetest_slice_bg" style="background-size:100% 100%"></div></div></div>' +
      '<div class="geetest_track" style="position:relative;width:300px;height:44px;margin-top:12px;background:#eee">' +
      '<div class="geetest_btn" style="position:absolute;left:0;top:2px;width:52px;height:40px;background:#39f;cursor:pointer"></div></div></div>';
    document.body.appendChild(wrap);
    wrap.querySelector('.geetest_btn').addEventListener('mousedown', e => { start = e.clientX - btnX; e.preventDefault(); });
    document.addEventListener('mousemove', e => { if (start === null) return; btnX = Math.max(0, Math.min(248, e.clientX - start)); place(); });
    document.addEventListener('mouseup', () => { if (start === null) return; start = null; verify(); });
  }
  if (d.captcha_type !== 'slide') { wrap.querySelector('.geetest_wrap').innerHTML = '<p>Нажмите на иконки по порядку</p>'; return; }
  const k = 300 / d.bg_w, sl = wrap.querySelector('.geetest_slice'), sb = sl.querySelector('.geetest_slice_bg');
  wrap.querySelector('.geetest_bg').style.backgroundImage = 'url(/' + d.bg + ')';
  sb.style.backgroundImage = 'url(/' + d.slice + ')';
  sb.style.width = sb.style.height = (d.slice_w * k) + 'px';
  sl.style.top = (d.ypos * k) + 'px';
  btnX = 0; place();
}
async function verify() {
  const x = parseFloat(document.querySelector('.geetest_slice').style.left);
  const r = await jsonp('/gt/verify?captcha_id=test&lot_number=' + LOT + '&x=' + x.toFixed(1));
  if (r.data.result === 'success') { showRedirectMessage(); document.cookie = 'st=ok; path=/'; setTimeout(() => location.reload(), 300); }
  else setTimeout(load, 700);  // как GeeTest: после неудачи — новое задание
}
setTimeout(() => { document.getElementById('geetest_captcha').style.display = 'inline-block'; }, 500);
document.querySelector('button[name=submit]').addEventListener('click', load);
</script></body></html>"""
CONTENT = '<html><body><span data-marker="page-title/count">1</span><div data-marker="item">1</div></body></html>'


class Site(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        st, u = self.state, urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/search":
            return self._send(CONTENT if "st=ok" in (self.headers.get("Cookie") or "") else FIREWALL)
        if u.path == "/gt/load":
            st["loads"] += 1
            lot = f"lot{st['loads']}"
            bg, piece, gx, gy = make_puzzle(st["loads"] + 7)
            st["puzzles"][lot] = (bg, piece, gx)
            data = {"lot_number": lot, "captcha_type": st["type"], "bg": f"pictures/bg_{lot}.png",
                    "slice": f"pictures/slice_{lot}.png", "ypos": gy, "bg_w": W, "slice_w": P}
            return self._send(f'{q["callback"]}({json.dumps({"status": "success", "data": data})})', "application/javascript")
        if u.path == "/gt/verify":
            st["verifies"] += 1
            gx = st["puzzles"][q["lot_number"]][2]
            ok = abs(float(q["x"]) * W / SHOWN_W - gx) <= 4 and not (st["fail_first"] and st["verifies"] == 1)
            st["errors"].append(round(float(q["x"]) * W / SHOWN_W - gx, 1))
            return self._send(f'{q["callback"]}({{"status": "success", "data": {{"result": "{"success" if ok else "fail"}"}}}})',
                              "application/javascript")
        if u.path.startswith("/pictures/"):
            name = u.path.rsplit("/", 1)[-1]
            lot = name.split("_", 1)[1].rsplit(".", 1)[0]
            bg, piece, _ = st["puzzles"][lot]
            return self._send(bg if name.startswith("bg_") else piece, "image/png")
        self.send_response(404)
        self.end_headers()


@unittest.skipUnless(has_browser(), "Chromium не установлен (setup.bat)")
class SliderCaptcha(unittest.TestCase):
    def setUp(self):
        fast_timing(self)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.asked = []
        orig = alerts.human_needed
        alerts.human_needed = lambda title, text: self.asked.append(text)
        self.addCleanup(setattr, alerts, "human_needed", orig)
        self.log = []

    def browser(self, wait_min=1.0):
        b = LocalBrowser("avito", lambda html, url: "js-firewall-form" in html, self.log.append, None,
                         captcha_wait_min=wait_min, is_ready=lambda html, url: 'data-marker="item"' in html,
                         label="Авито", solver=captcha.AvitoCaptcha())
        self.addCleanup(b.close)
        return b

    def open(self, b):
        return b.goto(f"http://127.0.0.1:{self.srv.server_port}/search", settle=(0.3, 0.3))

    def test_solves_slider_itself(self):
        Site.state = {"type": "slide", "loads": 0, "verifies": 0, "fail_first": True, "puzzles": {}, "errors": []}
        b = self.browser()
        html = self.open(b)
        self.assertIn('data-marker="item"', html, "после пазла должна открыться выдача")
        self.assertEqual((b.captchas, b.solved), (1, 1))
        self.assertEqual(self.asked, [], "пазл пройден сам — человека звать не нужно")
        self.assertIn("капча: GeeTest", self.log)
        self.assertTrue(any("пазл не принят (1 из 3)" in m for m in self.log), "после отказа — новая попытка")
        self.assertTrue(any("пройдена автоматически" in m for m in self.log))
        self.assertLessEqual(abs(Site.state["errors"][-1]), 4, f"кусочек не довели до выреза: {Site.state['errors']}")

    def test_other_kind_goes_to_human(self):
        Site.state = {"type": "icon", "loads": 0, "verifies": 0, "fail_first": False, "puzzles": {}, "errors": []}
        b = self.browser(wait_min=0.05)
        with self.assertRaises(Blocked):
            self.open(b)
        self.assertEqual(b.solved, 0)
        self.assertEqual(len(self.asked), 1, "капчу другого вида проходит человек: звук и уведомление")
        self.assertIn("иконки по порядку", self.asked[0])
        self.assertTrue(any("такую программа сама не проходит" in m for m in self.log))
        self.assertTrue(any("НУЖНА РУЧНАЯ ПРОВЕРКА" in m for m in self.log))
