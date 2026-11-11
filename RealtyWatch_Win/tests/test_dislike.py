"""Дизлайк («не нравится») и «Скрытые»: отметка хранится по объявлениям, взаимоисключающая с ♥, переживает
сбор и очищается вместе с базой; в интерфейсе по умолчанию скрытые не показываются нигде.
База — во временной папке, данные синтетические."""
import unittest

from app import db, paths, purge, server
from tests.support import Case

KEYS = ["cian:111", "avito:222"]


def add(source, sid):
    con = db.connect()
    con.execute("INSERT INTO listings(source, source_id, url, title, price, first_seen, last_seen, active, missed, object_id) "
                "VALUES(?,?,?,?,?,?,?,1,0,?)", (source, sid, f"https://example.test/{sid}", "2-к. квартира", 10_000_000,
                                               db.now(), db.now(), "obj:1"))
    con.commit()
    con.close()


def marks():
    rows = {(r["source"], r["source_id"]): (r["liked"], r["disliked"]) for r in server.listings()}
    return rows


class Dislike(Case):
    def setUp(self):
        super().setUp()
        add("cian", "111")
        add("avito", "222")

    def test_dislike_marks_every_listing_of_the_object(self):
        server.dislike({"keys": KEYS, "on": True})
        self.assertEqual(marks(), {("cian", "111"): (False, True), ("avito", "222"): (False, True)})
        server.dislike({"keys": KEYS, "on": False})
        self.assertEqual(marks(), {("cian", "111"): (False, False), ("avito", "222"): (False, False)}, "вернули из «Скрытых»")

    def test_like_and_dislike_exclude_each_other(self):
        server.like({"keys": KEYS, "on": True})
        server.dislike({"keys": KEYS, "on": True})
        self.assertEqual(set(marks().values()), {(False, True)}, "👎 снимает ♥")
        server.like({"keys": KEYS, "on": True})
        self.assertEqual(set(marks().values()), {(True, False)}, "♥ снимает 👎")

    def test_clear_all_removes_dislikes(self):
        server.dislike({"keys": KEYS, "on": True})
        purge.clear_all()
        con = db.connect()
        self.assertEqual(con.execute("SELECT COUNT(*) FROM dislikes").fetchone()[0], 0)
        con.close()


class HiddenInterface(unittest.TestCase):
    """Проводка в интерфейсе: «Скрытые» слева от «Избранного», «исключить дизлайки» включено по умолчанию
    в подборке, таблице и на карте (общий фильтр), на дашборде и в «Изменениях»."""

    @classmethod
    def setUpClass(cls):
        cls.html = (paths.WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (paths.WEB / "app.js").read_text(encoding="utf-8")
        cls.dash = (paths.WEB / "dash.js").read_text(encoding="utf-8")
        cls.css = (paths.WEB / "app.css").read_text(encoding="utf-8")

    def test_hidden_button_left_of_favourites(self):
        self.assertLess(self.html.index('id="fHidden"'), self.html.index('id="fLiked"'))

    def test_exclude_dislikes_on_by_default(self):
        self.assertIn('id="fNoDis" checked', self.html, "подборка, таблица, карта")
        self.assertIn('store.get("rw.dashNoDis", "1")', self.dash, "дашборд")
        self.assertIn('store.get("rw.chNoDis", "1")', self.dash, "изменения")

    def test_filter_uses_both_switches(self):
        self.assertIn("(hiddenOnly ? o.disliked : !(noDis && o.disliked))", self.js)

    def test_reset_keeps_dislikes_hidden(self):
        """«Сбросить» выключает «Скрытые», а «исключить дизлайки» оставляет включённым."""
        self.assertIn('["fNoCoords", "fLiked", "fHidden"].includes(i.id)', self.js)

    def test_price_chart_starts_from_zero_by_default(self):
        self.assertIn('store.get("rw.objChartAbs", "1")', self.dash)
        self.assertIn("if (OBJ_ABS) opt.scales.y.min = 0;", self.dash)

    def test_badge_never_splits(self):
        self.assertRegex(self.css, r"\.tag \{[^}]*white-space: nowrap;")


if __name__ == "__main__":
    unittest.main()
