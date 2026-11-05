"""Значок в области уведомлений: сворачивание в трей, меню и уведомления Windows (клик — открыть нужный экран)."""
import threading

NIN_BALLOONUSERCLICK = 0x0405  # клик по всплывающему уведомлению
NIIF_NOSOUND = 0x10            # уведомление без звука


class Tray:
    def __init__(self, window, icon_path, title, on_update):
        import pystray
        from PIL import Image

        self.window = window
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
            item("Открыть RealtyWatch", lambda icon, it: self.show(), default=True),
            item("Изменения на рынке", lambda icon, it: self.show("changes")),
            item("Обновить сейчас", lambda icon, it: on_update()),
            pystray.Menu.SEPARATOR,
            item("Выход", lambda icon, it: self.quit()),
        )
        self.icon = _Icon("RealtyWatch", Image.open(icon_path), title, menu)
        self.icon.run_detached()

    def show(self, view=None):
        try:
            self.window.show()
            self.window.restore()
            if view:
                self.window.evaluate_js(f"window.openView && openView({view!r})")
        except Exception:
            pass

    def on_close(self):
        """Нажата кнопка «Закрыть». Если в настройках включено «сворачивать в трей по закрытию»,
        окно уходит в трей, а закрытие отменяется (False); иначе программа закрывается как обычно.
        Настройку читаем в момент нажатия — галочка действует сразу, без перезапуска."""
        from . import settings
        if self.quitting or not settings.load()["ui"].get("tray_on_close", True):
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
                "Сбор и автообновление продолжают работать. Двойной клик по значку — открыть.")).start()

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
