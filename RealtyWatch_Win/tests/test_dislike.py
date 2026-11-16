"""Дизлайк («не нравится») и «Скрытые»: отметка хранится по объявлениям, взаимоисключающая с ♥, переживает
сбор и очищается вместе с базой; в интерфейсе по умолчанию скрытые не показываются нигде.
База — во временной папке, данные синтетические."""
import unittest

from app import db, paths, purge, server
from tests.support import Case

KEYS = ["cian:111", "avito:222"]


def add(source, sid, first_seen=None, obj="obj:1"):
    con = db.connect()
    con.execute("INSERT INTO listings(source, source_id, url, title, price, first_seen, last_seen, active, missed, object_id) "
                "VALUES(?,?,?,?,?,?,?,1,0,?)", (source, sid, f"https://example.test/{sid}", "2-к. квартира", 10_000_000,
                                               first_seen or db.now(), db.now(), obj))
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


def fresh(since=None):
    con = db.connect()
    try:
        return db.hidden_fresh(con, since)
    finally:
        con.close()


class FreshInHidden(Case):
    """В скрытую карточку после скрытия добавилось объявление (новая квартира в том же доме или та же
    на другом сайте): дизлайка у него нет — по этому его и видно."""

    def setUp(self):
        super().setUp()
        add("cian", "111", "2026-11-01T10:00:00")
        add("avito", "222", "2026-11-01T10:00:00")
        server.dislike({"keys": KEYS, "on": True})

    def test_new_listing_in_hidden_card_is_counted(self):
        self.assertEqual(fresh(), 0, "всё, что было в карточке, скрыто вместе с ней")
        add("cian", "333", "2026-11-11T18:23:00")
        add("yandex", "444", "2026-11-11T18:23:00", obj="obj:2")
        self.assertEqual(fresh(), 1, "одна скрытая карточка; объявление в чужой карточке не считается")
        self.assertEqual(fresh("2026-11-11T18:00:00"), 1, "появилось за этот сбор — для уведомления")
        self.assertEqual(fresh("2026-11-11T19:00:00"), 0, "в следующем сборе уже не новость")

    def test_keep_hidden_and_like_clear_it(self):
        add("cian", "333")
        server.dislike({"keys": ["cian:333"], "on": True})
        self.assertEqual(fresh(), 0, "«Оставить скрытой» ставит дизлайк новому объявлению")
        add("domclick", "555")
        server.like({"keys": ["domclick:555"], "on": True})
        self.assertEqual(fresh(), 0, "квартира с ♥ не скрытая")

    def test_notification_after_run(self):
        """Уведомление после сбора называет скрытые квартиры с новыми объявлениями и по клику ведёт в «Скрытые»."""
        from app import main
        con = db.connect()
        rid = con.execute("INSERT INTO runs(source, started, status) VALUES('cian', '2026-11-11T18:22:00', 'done')").lastrowid
        con.commit()
        con.close()
        add("cian", "333", "2026-11-11T18:23:00")
        sent = []

        class Icon:
            def notify(self, title, text, view=None):
                sent.append((title, text, view))
        main._notify_hook(lambda: Icon())([rid], ["cian"], False)
        self.assertEqual(len(sent), 1, sent)
        self.assertIn("в 1 скрытую квартиру добавились объявления", sent[0][1])


class HiddenInterface(unittest.TestCase):
    """Проводка в интерфейсе: «Скрытые» слева от «Избранного», «исключить дизлайки» включено по умолчанию
    в подборке, таблице и на карте (общий фильтр), на дашборде и в «Изменениях»."""

    @classmethod
    def setUpClass(cls):
        cls.html = (paths.WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (paths.WEB / "app.js").read_text(encoding="utf-8")
        cls.dash = (paths.WEB / "dash.js").read_text(encoding="utf-8")
        cls.css = (paths.WEB / "app.css").read_text(encoding="utf-8")

    def test_fresh_badge_and_highlight(self):
        """Кружок с числом на «Скрытых», подсветка новых объявлений и «Оставить скрытой»."""
        self.assertIn('<i id="hideNew" class="new-badge" hidden></i>', self.html)
        self.assertIn("return o.disliked ? o.items.filter(i => !i.disliked) : [];", self.js)
        self.assertIn('if (f.hiddenOnly) rows.sort((a, b) => (b.fresh.length > 0) - (a.fresh.length > 0));', self.js)
        self.assertIn('await post("/api/dislike", { keys, on: true });', self.js)
        self.assertIn('if (name === "cards" && arg === "hidden")', self.dash, "клик по уведомлению")
        self.assertIn(".offer.fresh", self.css)

    def test_hidden_button_left_of_favourites(self):
        self.assertLess(self.html.index('id="fHidden"'), self.html.index('id="fLiked"'))

    def test_exclude_dislikes_on_by_default(self):
        self.assertIn('id="fNoDis" checked', self.html, "подборка, таблица, карта и изменения")
        self.assertIn('store.get("rw.dashNoDis", "1")', self.dash, "дашборд")
        self.assertIn("matchObject(o, f,", self.dash, "изменения берут общий фильтр дизлайков")

    def test_changes_uses_unified_filters_and_local_time(self):
        self.assertIn('const lists = ["cards", "table", "map", "changes"].includes(v);', self.js)
        self.assertIn("readFilters()", self.dash)
        self.assertIn("allowRemoved", self.dash, "снятые события видны при включённой галочке «активные»")
        self.assertIn("renderHeader([], [...zoneMap.values()])", self.dash)
        self.assertNotIn('id="chPass"', self.dash)
        self.assertNotIn('id="chNoDis"', self.dash)
        self.assertIn("const today = () => localIso().slice(0, 10);", self.js)
        self.assertIn('store.set("rw.chSeen", localIso(Date.now() + 60000));', self.dash)

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
