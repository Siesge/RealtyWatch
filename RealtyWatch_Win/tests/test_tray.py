"""Галочка «сворачивать в трей по закрытию»: нужен ли значок в трее, что делает кнопка «Закрыть»
и где стоит сама галочка.
Настоящий значок в трее (pystray) не создаётся — проверяется решение Tray.on_close и разметка настроек.
Настройки — во временной папке, реальный data/settings.json не затрагивается."""
import time
import unittest

from app import paths, server, settings, tray
from tests.support import Case


class FakeWindow:
    def __init__(self):
        self.hidden = 0
        self.shown = 0
        self.restored = 0

    def hide(self):
        self.hidden += 1

    def show(self):
        self.shown += 1

    def restore(self):
        self.restored += 1

    def evaluate_js(self, code):
        pass


class FakeTray:
    """Значок в трее без pystray: оставлено только то, чем пользуются on_close, hide и show."""
    hide = tray.Tray.hide
    on_close = tray.Tray.on_close
    show = tray.Tray.show
    toggle = tray.Tray.toggle

    def __init__(self):
        self.window = FakeWindow()
        self.title = "RealtyWatch — тест"
        self.quitting = False
        self.hint_shown = False
        self.notified = []

    def notify(self, title, text, view=None):
        self.notified.append(title)


class CloseButton(Case):
    def test_tray_on_close_by_default(self):
        self.assertIs(settings.load()["ui"]["tray_on_close"], True, "по умолчанию «Закрыть» убирает окно в трей")

    def test_with_tick_window_goes_to_tray(self):
        t = FakeTray()
        self.assertIs(t.on_close(), False, "закрытие окна отменяется")
        self.assertEqual(t.window.hidden, 1, "окно ушло в трей")

    def test_no_icon_without_tick(self):
        self.assertIs(tray.wanted(), True, "с галочкой значок в трее нужен")
        cfg = settings.load()
        cfg["ui"]["tray_on_close"] = False
        settings.save(cfg)
        self.assertIs(tray.wanted(), False, "без галочки значка в трее нет вовсе")

    def test_saving_wakes_listeners(self):
        """Значок появляется и убирается сразу: сохранение настроек будит слушателей."""
        seen = []

        def listener(cfg):
            seen.append(cfg["ui"]["tray_on_close"])

        settings.on_change(listener)
        self.addCleanup(settings._watchers.remove, listener)
        cfg = settings.load()
        cfg["ui"]["tray_on_close"] = False
        settings.save(cfg)
        self.assertEqual(seen, [False], "слушателю сказали, что галочку сняли")

    def test_without_tick_program_closes(self):
        cfg = settings.load()
        cfg["ui"]["tray_on_close"] = False
        settings.save(cfg)
        t = FakeTray()
        self.assertIs(t.on_close(), True, "окно закрывается как обычно")
        self.assertEqual(t.window.hidden, 0, "в трей не уходит")

    def test_exit_from_tray_menu_closes(self):
        t = FakeTray()
        t.quitting = True  # «Выход» в меню значка: окно закрывают сами, перехватывать нельзя
        self.assertIs(t.on_close(), True)
        self.assertEqual(t.window.hidden, 0)

    def test_hint_once_per_session(self):
        t = FakeTray()
        t.hide()
        t.hide()
        deadline = time.time() + 2  # уведомление уходит отдельным потоком
        while not t.notified and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(t.notified, ["RealtyWatch свёрнут в трей"], "уведомление — только в первый раз за запуск")
        self.assertEqual(t.window.hidden, 2, "окно уходит в трей каждый раз")

    def test_open_from_tray_keeps_window_as_it_was(self):
        """Открытие из трея не меняет размер окна: развёрнутое остаётся развёрнутым, обычное — на своём месте.
        restore() из pywebview ставит обычный размер всегда, поэтому звать его нельзя; свёрнутое в панель
        задач окно возвращает сама Windows (unminimize), в то состояние, из которого его свернули."""
        calls = []
        orig = tray.unminimize
        tray.unminimize = lambda title: calls.append(title)
        self.addCleanup(setattr, tray, "unminimize", orig)
        t = FakeTray()
        t.hide()
        t.show()
        self.assertEqual(t.window.shown, 1, "окно показано")
        self.assertEqual(t.window.restored, 0, "restore() не вызывается — он сжимал развёрнутое окно")
        self.assertEqual(calls, [t.title], "свёрнутое окно возвращает Windows, по заголовку окна")

    def test_click_on_icon_toggles_window(self):
        """Клик левой кнопкой по значку: открытое окно уходит в трей, скрытое — открывается."""
        state = {"open": True}
        orig_open, orig_un = tray.window_open, tray.unminimize
        tray.window_open = lambda title: state["open"]
        tray.unminimize = lambda title: None
        self.addCleanup(setattr, tray, "window_open", orig_open)
        self.addCleanup(setattr, tray, "unminimize", orig_un)
        t = FakeTray()
        t.toggle()
        self.assertEqual((t.window.hidden, t.window.shown), (1, 0), "открытое окно — в трей")
        state["open"] = False
        t.toggle()
        self.assertEqual((t.window.hidden, t.window.shown), (1, 1), "скрытое или свёрнутое — открыть")

    def test_click_is_the_default_menu_action(self):
        """pystray передаёт клик по значку пункту меню с default=True — это должен быть невидимый «показать
        или скрыть», а не «Открыть»: иначе пункт «Открыть» в меню прятал бы открытое окно."""
        src = (paths.ROOT / "app" / "tray.py").read_text(encoding="utf-8")
        self.assertIn('self.toggle(), default=True, visible=False)', src)
        self.assertIn('item("Открыть RealtyWatch", lambda icon, it: self.show()),', src)

    def test_setting_saved_like_the_others(self):
        server.set_settings({"ui": {"tray_on_close": False}})
        self.assertIs(settings.load()["ui"]["tray_on_close"], False)
        self.assertEqual(settings.load()["filters"]["rooms"], settings.DEFAULTS["filters"]["rooms"],
                         "остальные разделы настроек не затронуты")


class InterfaceCard(unittest.TestCase):
    """Карточка «Интерфейс» — в правом столбце под «Очисткой данных»."""

    def test_card_in_place(self):
        js = (paths.WEB / "app.js").read_text(encoding="utf-8")
        css = (paths.WEB / "app.css").read_text(encoding="utf-8")
        self.assertIn('class="s-card s-ui"', js)
        self.assertIn('sw("sTray"', js)
        self.assertIn('$("#sTray").onchange', js, "галочка сохраняется сразу, без кнопки «Сохранить»")
        self.assertIn('"collect purge" "collect ui"', css, "блок стоит справа, под очисткой данных")
        self.assertIn(".s-ui { grid-area: ui; }", css)

    def test_icon_follows_the_tick(self):
        """Переключение без перезапуска: окно подписано на сохранение настроек."""
        main = (paths.ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("settings.on_change(", main, "значок заводится и убирается при сохранении настроек")
        self.assertIn("tray.stop()", main, "снятая галочка убирает значок")


if __name__ == "__main__":
    unittest.main()
