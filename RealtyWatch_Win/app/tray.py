"""Значок в области уведомлений: сворачивание в трей, меню и уведомления Windows (клик — открыть нужный экран)."""
import threading

NIN_BALLOONUSERCLICK = 0x0405  # клик по всплывающему уведомлению
NIIF_NOSOUND = 0x10            # уведомление без звука
SW_RESTORE = 9                 # вернуть свёрнутое окно в состояние до сворачивания


def unminimize(title):
    """Окно свёрнуто в панель задач — вернуть его таким, каким его свернули: развёрнутым на весь экран
    или на прежнем месте прежнего размера. Это помнит сама Windows, и SW_RESTORE возвращает именно туда.
    Не свёрнутое окно не трогаем. restore() из pywebview для этого не годится: он ставит окну обычный
    размер всегда, и развёрнутое окно после него становится маленьким."""
    try:
        import ctypes

        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p
        hwnd = u.FindWindowW(None, title)
        if hwnd and u.IsIconic(ctypes.c_void_p(hwnd)):
            u.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
            u.SetForegroundWindow(ctypes.c_void_p(hwnd))
            return True
    except Exception:
        pass
    return False


def window_open(title):
    """Окно на экране: показано и не свёрнуто в панель задач. Не нашли окно — считаем, что его нет."""
    try:
        import ctypes

        u = ctypes.windll.user32
        u.FindWindowW.restype = ctypes.c_void_p
        hwnd = u.FindWindowW(None, title)
        return bool(hwnd) and bool(u.IsWindowVisible(ctypes.c_void_p(hwnd))) and not u.IsIconic(ctypes.c_void_p(hwnd))
    except Exception:
        return False


def wanted():
    """Нужен ли значок в трее. Он есть только с галочкой «сворачивать в трей по закрытию»:
    без неё «Закрыть» закрывает программу, а значит, и уведомлениям Windows неоткуда взяться."""
    from . import settings
    return bool(settings.load()["ui"].get("tray_on_close", True))


class Tray:
    def __init__(self, window, icon_path, title, on_update):
        import pystray
        from PIL import Image

        self.window = window
        self.title = title
        self.click_view = "changes"
        self.hint_shown = False
        self.quitting = False
        tray = self

        class _Icon(pystray.Icon):
            def _on_notify(self, wparam, lparam):
                if lparam == NIN_BALLOONUSERCLICK:
                    tray.show(tray.click_view)
                else:
                    super()._on_notify(wparam, lparam)

            def _notify(self, message, title=None):
                # «Без звука»: уведомление Windows показывается беззвучно (NIIF_NOSOUND)
                from pystray._util import win32
                from . import alerts
                self._message(win32.NIM_MODIFY, win32.NIF_INFO, szInfo=message, szInfoTitle=title or self.title or "",
                              dwInfoFlags=NIIF_NOSOUND if alerts.muted() else 0)

        item = pystray.MenuItem
        menu = pystray.Menu(
            # Клик левой кнопкой по значку: окно открыто — убрать в трей, нет — открыть. В меню этот пункт
            # не виден, клик pystray передаёт пункту с default=True
            item("Показать или скрыть", lambda icon, it: self.toggle(), default=True, visible=False),
            item("Открыть RealtyWatch", lambda icon, it: self.show()),
            item("Изменения на рынке", lambda icon, it: self.show("changes")),
            item("Обновить сейчас", lambda icon, it: on_update()),
            pystray.Menu.SEPARATOR,
            item("Выход", lambda icon, it: self.quit()),
        )
        self.icon = _Icon("RealtyWatch", Image.open(icon_path), title, menu)
        self.icon.run_detached()

    def show(self, view=None):
        """Открыть окно из трея таким, каким его убрали: развёрнутым — развёрнутым, обычным — на том же
        месте и того же размера. Скрытие и показ этого не меняют (Windows держит положение окна сама),
        поэтому после show() окно трогаем, только если оно было свёрнуто в панель задач."""
        try:
            self.window.show()
            unminimize(self.title)
            if view:
                self.window.evaluate_js(f"window.openView && openView({view!r})")
        except Exception:
            pass

    def toggle(self):
        """Клик по значку: открытое окно уходит в трей, скрытое или свёрнутое — открывается."""
        if window_open(self.title):
            self.hide()
        else:
            self.show()

    def on_close(self):
        """Нажата кнопка «Закрыть». Пока значок в трее есть, окно уходит в трей, а закрытие
        отменяется (False); без галочки программа закрывается как обычно. Настройку читаем в момент
        нажатия: «Выход» в меню значка и снятая галочка закрытие не перехватывают."""
        if self.quitting or not wanted():
            return True
        self.hide()
        return False

    def hide(self):
        self.window.hide()
        if not self.hint_shown:
            self.hint_shown = True
            # уведомление — отдельным потоком: по «Закрыть» этот метод держит поток окна
            threading.Thread(target=self.notify, daemon=True, args=(
                "RealtyWatch свёрнут в трей",
                "Сбор и автообновление продолжают работать. Клик по значку — открыть.")).start()

    def notify(self, title, text, view=None):
        self.click_view = view
        try:
            self.icon.notify(text, title)
        except Exception:
            pass

    def quit(self):
        self.quitting = True  # «Выход» в меню значка: закрытие окна больше не перехватываем
        threading.Thread(target=self._quit, daemon=True).start()

    def _quit(self):
        try:
            self.icon.stop()
        finally:
            self.window.destroy()

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass
