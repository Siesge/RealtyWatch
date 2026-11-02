"""Звуковые сигналы и уведомления. Когда сайт требует пройти капчу, которую программа не проходит сама,
она подаёт звук, показывает уведомление Windows и повторяет звук, пока капча на экране, — чтобы человек
заметил. Режим «без звука» (перечёркнутый колокольчик у кнопки «Запустить») выключает все звуки программы,
включая звук уведомлений Windows, — для работы ночью (само уведомление показывается беззвучно).
Настройка относится к этому компьютеру: data/sound.json, с базой не переносится."""
import json
import threading

from .paths import DATA

FILE = DATA / "sound.json"
REPEAT_S = 30  # как часто повторять сигнал, пока капча ждёт человека
_lock = threading.Lock()
_notifiers = []  # уведомления Windows: f(title, text); добавляет main.py, когда есть значок в трее


def muted():
    try:
        return bool(json.loads(FILE.read_text(encoding="utf-8")).get("muted"))
    except (OSError, ValueError):
        return False


def set_muted(on):
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps({"muted": bool(on)}), encoding="utf-8")
    return bool(on)


def _play(notes):
    try:
        import winsound
        for freq, ms in notes:
            winsound.Beep(freq, ms)
    except Exception:  # нет звуковой карты, не Windows — молча
        pass


def chime(notes=((880, 140), (1175, 140), (1568, 240))):
    """Короткий сигнал в фоне (если звук не выключен)."""
    if muted():
        return False
    threading.Thread(target=_play, args=(notes,), daemon=True, name="chime").start()
    return True


def captcha():
    """Нужна капча: заметный сигнал."""
    return chime(((988, 160), (1319, 160), (988, 160), (1319, 260)))


def on_human_needed(fn):
    _notifiers.append(fn)


def human_needed(title, text):
    """Нужен человек (капча, которую программа не прошла сама): сигнал и уведомление Windows."""
    captcha()
    for fn in list(_notifiers):
        try:
            fn(title, text)
        except Exception:
            pass
