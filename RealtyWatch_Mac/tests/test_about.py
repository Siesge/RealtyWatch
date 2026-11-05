"""Окно «О программе»: автор на месте, и в списке лицензий есть каждый пакет из requirements.txt
(иначе список незаметно отстанет от того, что реально установлено рядом с программой)."""
import re
import unittest

from app import about, paths


class About(unittest.TestCase):
    def test_author_and_name(self):
        d = about.info()
        self.assertEqual(d["author"], "Анатолий Никитин")
        self.assertEqual(d["name"], "RealtyWatch")
        self.assertTrue(d["about"].strip(), "короткое описание программы не пустое")

    def test_every_requirement_has_license(self):
        req = {re.split(r"[=<>~\[;]", line, 1)[0].strip().lower()
               for line in (paths.ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.startswith("#")}
        listed = {n.lower() for n, _, _ in about.PY_LIBS}
        self.assertEqual(req - listed, set(), "в окне «О программе» нет лицензии для этих пакетов")
        self.assertEqual(listed - req, set(), "в окне «О программе» есть пакеты, которых нет в requirements.txt")

    def test_groups_filled(self):
        groups = {g["title"]: g["items"] for g in about.info()["groups"]}
        self.assertEqual(set(groups), {"Библиотеки Python", "В интерфейсе", "Среда", "Данные"})
        for title, items in groups.items():
            self.assertTrue(items, f"раздел «{title}» пустой")
            for i in items:
                self.assertTrue(i["name"] and i["license"] and i["what"], f"в разделе «{title}» неполная запись: {i}")
        env = {i["name"]: i for i in groups["Среда"]}
        self.assertTrue(env["Python"]["version"], "версия Python берётся с работающей среды")
        self.assertIn("OpenStreetMap", {i["name"] for i in groups["Данные"]}, "атрибуция карты обязательна")


if __name__ == "__main__":
    unittest.main()
