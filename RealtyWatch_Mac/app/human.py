"""Мышь и прокрутка «как у человека» для окна браузера.

Курсор идёт к цели по плавной дуге (кривая Безье со случайным изгибом): разгоняется, у цели замедляется,
на длинных движениях иногда чуть проскакивает и поправляется. Время движения растёт с расстоянием и
уменьшается с размером цели (закон Фиттса). Ползунок тянется с быстрым началом и медленным концом, с лёгким
дрожанием по вертикали; в конце рука «доводит» его по тому, что видит. Перед нажатием курсор так же
сверяется с тем, что видно: если цель уехала из-под него (страница переложилась, пока рука шла), он
поправляется и только потом нажимает. Прокрутка — неровными сериями щелчков колёсика с паузами, будто
человек просматривает выдачу, изредка немного назад.
События уходят по одному, 40–70 в секунду, — как от настоящей мыши. Чаще не выходит: одно событие
система отдаёт браузеру за 15–30 мс, и если просить больше, движение просто растягивается во времени."""
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


def _inside(box, x, y):
    """Точка (x, y) — внутри прямоугольника?"""
    return box["x"] <= x <= box["x"] + box["width"] and box["y"] <= y <= box["y"] + box["height"]


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

    def idle(self, s):
        """Пауза, которая ждёт страницу, а не изображает человека, — поэтому не ускоряется в тестах."""
        self._check()
        if s > 0:
            time.sleep(s)

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

    FIXES = 2  # столько раз поправляемся, если цель уехала из-под курсора, пока рука к ней шла

    def reachable(self, box):
        """По цели можно нажать — она целиком видна в окне."""
        w, h = self._view()
        return box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= w and box["y"] + box["height"] <= h

    def click(self, box, recheck=None):
        """Нажатие в случайную точку цели (ближе к середине), с паузами как у человека.
        recheck() — где цель сейчас (прямоугольник или None). Пока рука идёт к цели (это почти секунда),
        страница успевает переложиться — дозагрузились картинки карточек, довернулась плавная прокрутка, —
        и ссылка уезжает из-под курсора. Человек в этот миг не бьёт по пустому месту: он видит, что цель
        сместилась, и поправляется. Без этого нажатие уходит в пустоту, и переход приходится делать по адресу,
        то есть заметнее для сайта.
        Возвращает True, если нажатие сделано. Если цель пропала или ушла за край окна, нажатия не будет:
        бить наугад нельзя — попали бы в чужую ссылку. Прокрутить и прицелиться заново — дело вызывающего."""
        x, y = point_in(box)
        self.move_to(x, y, min(box["width"], box["height"]))
        self.wait(random.uniform(0.06, 0.22))
        for _ in range(self.FIXES if recheck is not None else 0):
            b = recheck()
            if b is None or not self.reachable(b):
                return False
            if _inside(b, self.x, self.y):
                break
            x, y = point_in(b)
            self.move_to(x, y, min(b["width"], b["height"]))
            self.wait(random.uniform(0.05, 0.12))
        self.page.mouse.down()
        self.wait(random.uniform(0.05, 0.13))
        self.page.mouse.up()
        return True

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

    TRIES = 3       # коротких доводящих движений после броска (как у человека: два-три, реже четыре)
    SETTLE_S = 4.0  # сколько ждать, пока кусочек доедет за ручкой: на занятой машине страница рисует реже
    AIM_S = 14.0    # и сколько всего отведено на доводку, если кусочек так и не останавливается

    def drag(self, box, dx, position=None, target=None):
        """Тянет ползунок box вправо примерно на dx пикселей и отпускает — как человек: быстрый бросок почти
        до места, резкое торможение, потом два-три коротких движения с паузами, каждый раз сверяясь с тем,
        что видно на экране. Всё вместе — около секунды.
        position() — где сейчас то, что двигает ползунок (кусочек пазла), target — где оно должно оказаться.
        Возвращает (где кусочек оказался перед тем, как рука отпустила, доехал ли он к этому моменту);
        где он оказался — None, если это неизвестно."""
        x, y = point_in({"x": box["x"] + box["width"] * 0.25, "y": box["y"] + box["height"] * 0.2,
                         "width": box["width"] * 0.5, "height": box["height"] * 0.6})
        self.move_to(x, y, min(box["width"], box["height"]))
        self.wait(random.uniform(0.15, 0.45))
        start = position() if position else None
        self.page.mouse.down()
        self.wait(random.uniform(0.07, 0.18))
        sx, sy = self.x, self.y
        moved = self._burst(sx, sy, 0.0, dx * random.uniform(0.78, 0.9), fling=True)  # бросок почти до места
        moved = self._burst(sx, sy, moved, dx * random.uniform(0.94, 0.99) - moved)   # и короткое движение следом
        cur, still = None, True
        if position and target is not None and start is not None:
            end = time.monotonic() + self.AIM_S
            for _ in range(self.TRIES):
                cur, still = self._settled(position, target)  # кусочек едет за ручкой с запозданием — ждём его
                if cur is None or time.monotonic() >= end or (still and abs(target - cur) <= 0.5):
                    break
                if not still:
                    continue  # кусочек ещё едет: поправка по такому замеру уедет дальше цели — ждём ещё
                ratio = (cur - start) / moved if abs(moved) > 5 else 1.0  # кусочек может ехать не 1:1 с ручкой
                moved = self._burst(sx, sy, moved, (target - cur) / max(0.5, min(1.5, ratio or 1.0)))
            else:  # поправки кончились, а кусочек ещё едет: отпускать ползунок на ходу — не по-человечески
                cur, still = self._settled(position, target)
        self.wait(random.uniform(0.12, 0.3))  # рука замерла на месте, прежде чем отпустить
        self.page.mouse.up()
        return cur, still

    def _burst(self, sx, sy, moved, delta, fling=False):
        """Одно движение руки по ползунку: разгон, торможение, лёгкое дрожание — и пауза после.
        fling — бросок почти до цели (быстрый), иначе короткая доводка. Возвращает новый сдвиг ручки."""
        if abs(delta) < 0.05:
            return moved
        dur = (0.16 + abs(delta) / random.uniform(700, 1100)) if fling else min(0.3, 0.07 + abs(delta) / random.uniform(200, 400))
        steps = max(3, int(dur / random.uniform(0.018, 0.028)))
        dur *= TIME_SCALE
        yoff, yvel = 0.0, random.uniform(-0.05, 0.05)
        t0 = time.monotonic()
        for i in range(1, steps + 1):
            t = i / steps
            yvel = 0.85 * yvel + random.gauss(0, 0.06)
            yoff = max(-3.0, min(3.0, yoff + yvel))
            self._emit(sx + moved + delta * _ease(t ** 0.85) + random.uniform(-0.3, 0.3), sy + yoff)
            self._until(t0 + dur * t)
        self.wait(random.uniform(0.06, 0.16))  # рука на миг остановилась — и смотрит, куда попала
        return moved + delta

    STILL = 0.05   # на столько кусочек может сдвинуться за CALM_S и всё равно считаться доехавшим
    CALM_S = 0.35  # и столько он должен простоять на месте
    ROUGH = 0.4    # пока до цели далеко, запас больше: по грубому замеру делается поправка, и её проверит
    #                следующий замер — зато ждать полной остановки, которой ещё долго ждать, не нужно
    SHARE = 0.03   # «далеко» — это доля остатка пути: чем ближе цель, тем строже запас, вплоть до STILL

    def _settled(self, position, target=None):
        """Где то, что двигает ползунок, и доехало ли оно. Кусочек пазла едет за ручкой с запозданием:
        если поверить замеру, пока он ещё едет, поправка уедет дальше цели (на Авито — ровно на 4 пикселя).
        «Медленно» — это не «доехал»: страница подтягивает кусочек к ручке понемногу с каждым нарисованным
        кадром, и когда машина занята и кадров мало, он за замер сдвигается еле-еле, хотя ехать ему ещё
        далеко. Поэтому стояние на месте считается по времени: сдвинулся меньше чем на STILL за CALM_S —
        доехал. Сравнивать замер с предыдущим нельзя: при редких кадрах два замера попадают в один и тот же
        кадр, и «ничего не изменилось» значит «страница не рисовала», а вовсе не «кусочек доехал».
        target — куда кусочек должен приехать: пока до цели далеко, замер берётся погрубее, иначе на медленной
        странице одно такое ожидание съело бы все отведённые на доводку секунды. Последний замер, по которому
        ползунок отпускают, всегда точный — цель к тому времени рядом.
        Эти паузы ждут не «как человек», а пока страница дорисует, поэтому в тестах они не ускоряются."""
        cur = ref = position()
        since = time.monotonic()
        end = since + self.SETTLE_S
        while time.monotonic() < end:
            self.idle(random.uniform(0.07, 0.11))
            cur = position()
            if cur is None or ref is None:
                return cur, False
            # чем ближе кусочек к цели, тем точнее нужен замер: у последнего, перед отпусканием, запас строгий
            tol = self.STILL if target is None else max(self.STILL, min(self.ROUGH, abs(target - cur) * self.SHARE))
            if abs(cur - ref) >= tol:
                ref, since = cur, time.monotonic()
            elif time.monotonic() - since >= self.CALM_S:
                return cur, True
        return cur, False
