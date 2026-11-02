"""Всё, что зависит от операционной системы, в одном месте.

Это форк для macOS: рабочая ветка здесь — darwin. Ветки для Windows оставлены, чтобы тот же код
запускался и на Windows (на нём удобно прогонять тесты, не относящиеся к системе). Ничего из этого
не должно ронять программу: не вышло подать звук или поднять окно — значит, не вышло.
"""
import os
import subprocess
import sys
from pathlib import Path

MAC = sys.platform == "darwin"
WIN = sys.platform.startswith("win")

# Звуки macOS: короткий сигнал и тревожный (капча ждёт человека)
_SOUNDS = Path("/System/Library/Sounds")


def _osascript(script, timeout=10):
    """Выполняет AppleScript. Возвращает вывод или None."""
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def message(text, title="RealtyWatch"):
    """Сообщение пользователю, когда окна программы ещё нет (не запустился сервер, уже запущена копия)."""
    if MAC:
        if _osascript(f'display alert {_q(title)} message {_q(text)}') is not None:
            return
    elif WIN:
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
            return
        except Exception:
            pass
    print(text)


def _q(s):
    """Строка для AppleScript."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def single_instance(lock_file):
    """Блокирует файл, пока программа работает. None — копия уже запущена (две писали бы в одну базу)."""
    Path(lock_file).parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_file, "a+")
    try:
        if WIN:
            import msvcrt

            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def light_theme():
    """Светлая ли тема у системы — цвет фона окна до загрузки интерфейса."""
    if MAC:
        # ключ есть только в тёмной теме; в светлой команда возвращает ошибку
        return _osascript('tell application "System Events" to tell appearance preferences to get dark mode') != "true"
    if WIN:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
        except OSError:
            return True
    return True


def open_folder(path):
    """Открывает папку в файловом менеджере."""
    path = str(path)
    try:
        if MAC:
            subprocess.run(["open", path], check=False)
        elif WIN:
            os.startfile(path)  # noqa: S606 — только свои папки
        else:
            subprocess.run(["xdg-open", path], check=False)
    except OSError:
        pass


def beep(notes):
    """Короткий сигнал. notes — пары (частота, мс) из alerts; на macOS частоты не задать,
    поэтому звучит системный звук столько раз, сколько нот."""
    try:
        if MAC:
            snd = _SOUNDS / "Glass.aiff"
            for _ in notes:
                if snd.exists():
                    subprocess.run(["afplay", str(snd)], check=False, timeout=10)
                else:
                    _osascript("beep")
        elif WIN:
            import winsound

            for freq, ms in notes:
                winsound.Beep(freq, ms)
    except Exception:  # нет звуковой карты, нет afplay — молча
        pass


def notify(title, text):
    """Уведомление системы. True — показали."""
    if MAC:
        return _osascript(f"display notification {_q(text)} with title {_q(title)}") is not None
    return False


def raise_app(bundle_or_pid):
    """Поднимает чужое окно (браузер с капчей) поверх окна программы.
    bundle_or_pid — путь к .app браузера или его pid."""
    if not MAC:
        return False
    try:
        if isinstance(bundle_or_pid, int):
            return _osascript("tell application \"System Events\" to set frontmost of "
                              f"(first process whose unix id is {bundle_or_pid}) to true") is not None
        subprocess.run(["open", "-a", str(bundle_or_pid)], check=False, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
