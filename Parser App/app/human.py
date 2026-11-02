"""Мышь и прокрутка «как у человека» для окна браузера.

Курсор идёт к цели по плавной дуге (кривая Безье со случайным изгибом): разгоняется, у цели замедляется,
на длинных движениях иногда чуть проскакивает и поправляется. Время движения растёт с расстоянием и
уменьшается с размером цели (закон Фиттса). Ползунок тянется с быстрым началом и медленным концом, с лёгким
дрожанием по вертикали; в конце рука «доводит» его по тому, что видит. Прокрутка — неровными сериями
щелчков колёсика с паузами, будто человек просматривает выдачу, изредка немного назад.
События уходят по одному, 40–70 в секунду, — как от настоящей мыши. Чаще не выходит: одно событие
Windows отдаёт браузеру за 15–30 мс, и если просить больше, движение просто растягивается во времени."""
import math
import random
import time

WHEEL_NOTCH = 100  # прокрутка одним щелчком колёсика, px
TIME_SCALE = 1.0   # множитель всех пауз и длительностей движений (тесты ускоряют)


def _ease(t):
    """Плавный разгон и торможение (минимальный рывок) — так двигается рука."""
    return t * t * t * (10 - 15 * t + 6 * t * t)


def _bezier(p0, p1, p2, p3, t):
    u = 1 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def point_in(box):
    """Случайная точка внутри прямоугольника, чаще ближе к середине — куда обычно нажимает человек."""
    w, h = box["width"], box["height"]
    return (box["x"] + w / 2 + max(-w * 0.35, min(w * 0.35, random.gauss(0, w / 6))),
            box["y"] + h / 2 + max(-h * 0.35, min(h * 0.35, random.gauss(0, h / 6))))


class Mouse:
    """Курсор одной вкладки. Playwright не сообщает, где курсор, поэтому положение помним сами.
    stop — событие «остановить прогон», stopped — исключение, которое тогда бросается."""

    def __init__(self, page, stop=None, stopped=None):
        self.page = page
        self.stop = stop
        self.stopped = stopped or RuntimeError
        self.x, self.y = random.uniform(400, 900), random.uniform(250, 550)

    def _check(self):
        if self.stop is not None and self.stop.is_set():
            raise self.stopped()

    def wait(self, s):
        """Пауза, как у человека (в секундах)."""
        self._check()
        if s > 0:
            time.sleep(s * TIME_SCALE)

    def _until(self, t):
        """Темп движения: дождаться момента t (time.monotonic())."""
        self._check()
        d = t - time.monotonic()
        if d > 0:
            time.sleep(d)

    def _emit(self, x, y):
        self.page.mouse.move(round(x, 2), round(y, 2))
        self.x, self.y = x, y

    def _view(self):
        try:
            w, h = self.page.evaluate("() => [innerWidth, innerHeight]")
            return w, h
        except Exception:
            return 1280, 800

    def move_to(self, x, y, size=40.0):
        """Ведёт курсор в точку (x, y); size — размер цели: к маленькой цели рука подходит медленнее."""
        dist = math.hypot(x - self.x, y - self.y)
        if dist > 150 and random.random() < 0.3:  # проскочить мимо и поправиться
            k = random.uniform(0.03, 0.07)
            ox = max(-25.0, min(25.0, (x - self.x) * k))
            oy = max(-25.0, min(25.0, (y - self.y) * k))
            self._path(x + ox + random.uniform(-3, 3), y + oy + random.uniform(-3, 3), size * 2)
            self.wait(random.uniform(0.05, 0.15))
        self._path(x, y, size)

    def _path(self, x, y, size):
        x0, y0 = self.x, self.y
        dist = math.hypot(x - x0, y - y0)
        if dist < 2:
            self._emit(x, y)
            return
        dur = (0.1 + 0.12 * math.log2(dist / max(size, 4.0) + 1)) * random.uniform(0.85, 1.2)
        steps = max(6, int(dur / random.uniform(0.014, 0.024)))
        dur *= TIME_SCALE
        nx, ny = -(y - y0) / dist, (x - x0) / dist  # поперёк пути — туда изгибается дуга
        bend = random.uniform(-0.2, 0.2) * min(dist, 400)
        k1, k2 = random.uniform(0.2, 0.4), random.uniform(0.6, 0.85)
        p1 = (x0 + (x - x0) * k1 + nx * bend, y0 + (y - y0) * k1 + ny * bend)
        p2 = (x0 + (x - x0) * k2 + nx * bend * 0.5, y0 + (y - y0) * k2 + ny * bend * 0.5)
        t0 = time.monotonic()
        for i in range(1, steps + 1):
            px, py = _bezier((x0, y0), p1, p2, (x, y), _ease(i / steps))
            if i < steps:  # рука немного дрожит, конечная точка — точно в цель
                px, py = px + random.uniform(-0.5, 0.5), py + random.uniform(-0.5, 0.5)
            self._emit(px, py)
            self._until(t0 + dur * i / steps)

    def click(self, box):
        """Нажатие в случайную точку цели (ближе к середине), с паузами как у человека."""
        x, y = point_in(box)
        self.move_to(x, y, min(box["width"], box["height"]))
        self.wait(random.uniform(0.06, 0.22))
        self.page.mouse.down()
        self.wait(random.uniform(0.05, 0.13))
        self.page.mouse.up()

    def wheel(self, notches):
        """Серия щелчков колёсика: notches > 0 — вниз, < 0 — вверх."""
        for _ in range(abs(notches)):
            self.page.mouse.wheel(0, WHEEL_NOTCH if notches > 0 else -WHEEL_NOTCH)
            self.wait(random.uniform(0.03, 0.09))

    def wander(self):
        """Курсор немного смещается — человек водит мышью, пока смотрит страницу."""
        w, h = self._view()
        self.move_to(max(40.0, min(w - 40.0, self.x + random.uniform(-220, 220))),
                     max(90.0, min(h - 60.0, self.y + random.uniform(-140, 140))), 80)

    def read_down(self, at_bottom, max_bursts=80):
        """Просматривает страницу сверху вниз сериями прокрутки с паузами. at_bottom() — дошли ли до конца."""
        for i in range(max_bursts):
            if i and random.random() < 0.07:  # вернулся взглянуть ещё раз
                self.wheel(-random.randint(1, 2))
                self.wait(random.uniform(0.4, 1.0))
            self.wheel(random.choice((1, 2, 2, 3, 3, 4, 5)))
            if random.random() < 0.3:
                self.wander()
            self.wait(random.uniform(0.35, 1.3) + (random.uniform(1.5, 3.5) if random.random() < 0.08 else 0))
            if at_bottom():
                return

    def drag(self, box, dx, position=None, target=None):
        """Тянет ползунок box вправо на dx пикселей и отпускает — одним уверенным движением за полсекунды-секунду,
        как человек. position() — где сейчас то, что двигает ползунок (кусочек пазла), target — где оно должно
        оказаться: в конце рука доводит его по тому, что видит. Возвращает, на сколько сдвинута ручка."""
        x, y = point_in({"x": box["x"] + box["width"] * 0.25, "y": box["y"] + box["height"] * 0.2,
                         "width": box["width"] * 0.5, "height": box["height"] * 0.6})
        self.move_to(x, y, min(box["width"], box["height"]))
        self.wait(random.uniform(0.15, 0.45))
        start = position() if position else None
        self.page.mouse.down()
        self.wait(random.uniform(0.07, 0.18))
        sx, sy = self.x, self.y
        over = random.uniform(2.0, 6.0) if random.random() < 0.6 else 0.0
        total = dx + over
        dur = random.uniform(0.3, 0.5) + abs(total) / random.uniform(500, 850)
        steps = max(14, int(dur / random.uniform(0.018, 0.028)))
        dur *= TIME_SCALE
        yoff, yvel, paused = 0.0, random.uniform(-0.05, 0.05), False
        t0 = time.monotonic()
        for i in range(1, steps + 1):
            t = i / steps
            yvel = 0.85 * yvel + random.gauss(0, 0.06)
            yoff = max(-3.0, min(3.0, yoff + yvel))
            # быстрое начало, плавное торможение к цели
            self._emit(sx + total * _ease(t ** 0.8) + random.uniform(-0.3, 0.3), sy + yoff)
            if not paused and 0.35 < t < 0.7 and random.random() < 0.04:
                paused, t0 = True, t0 + random.uniform(0.04, 0.12) * TIME_SCALE  # рука на миг замедлилась
            self._until(t0 + dur * t)
        moved = total
        if over:
            self.wait(random.uniform(0.06, 0.18))
            moved = self._nudge(sx, sy + yoff, moved, -over)
        if position and target is not None and start is not None:
            for _ in range(3):
                cur = self._settled(position)
                if cur is None or abs(target - cur) <= 0.6:
                    break
                ratio = (cur - start) / moved if abs(moved) > 5 else 1.0  # кусочек может ехать не 1:1 с ручкой
                moved = self._nudge(sx, sy + yoff, moved, (target - cur) / max(0.5, min(1.5, ratio or 1.0)))
        self.wait(random.uniform(0.08, 0.25))
        self.page.mouse.up()
        return moved

    def _settled(self, position):
        """Где то, что двигает ползунок, когда оно остановилось: сайт может доводить его сам, с задержкой."""
        cur = position()
        for _ in range(4):
            self.wait(random.uniform(0.05, 0.09))
            nxt = position()
            if nxt is None or cur is None or abs(nxt - cur) < 0.3:
                return nxt
            cur = nxt
        return cur

    def _nudge(self, sx, y, moved, delta):
        """Небольшая поправка ручки ползунка на delta пикселей."""
        steps, dur = random.randint(4, 8), random.uniform(0.07, 0.16) * TIME_SCALE
        t0 = time.monotonic()
        for i in range(1, steps + 1):
            self._emit(sx + moved + delta * _ease(i / steps) + random.uniform(-0.2, 0.2), y + random.uniform(-0.4, 0.4))
            self._until(t0 + dur * i / steps)
        return moved + delta
