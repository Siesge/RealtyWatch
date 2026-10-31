"""Значок в области уведомлений: сворачивание в трей, меню и уведомления Windows (клик — открыть нужный экран)."""
import threading

NIN_BALLOONUSERCLICK = 0x0405  # клик по всплывающему уведомлению


class Tray:
    def __init__(self, window, icon_path, title, on_update):
        import pystray
        from PIL import Image

        self.window = window
        self.click_view = "changes"
        self.hint_shown = False
        tray = self

        class _Icon(pystray.Icon):
            def _on_notify(self, wparam, lparam):
                if lparam == NIN_BALLOONUSERCLICK:
                    tray.show(tray.click_view)
                else:
                    super()._on_notify(wparam, lparam)

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
                self.window.evaluate_js(f"window.setView && setView({view!r})")
        except Exception:
            pass

    def hide(self):
        self.window.hide()
        if not self.hint_shown:
            self.hint_shown = True
            self.click_view = None
            self.notify("RealtyWatch свёрнут в трей", "Сбор и автообновление продолжают работать. Двойной клик по значку — открыть.")

    def notify(self, title, text, view=None):
        self.click_view = view
        try:
            self.icon.notify(text, title)
        except Exception:
            pass

    def quit(self):
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
