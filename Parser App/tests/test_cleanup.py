"""Очистка кэшей при закрытии: кэш удаляется, cookies и настройки остаются.
Всё во временной папке — настоящие профили, база и журналы не затрагиваются."""
import tempfile
import unittest
from pathlib import Path

from app import cleanup
from tests.support import TMP_ROOT


def make(root, rel, size=1000):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    return p


class Cleanup(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=TMP_ROOT))
        self.junk = [make(self.d, r) for r in (
            "avito/browser/Default/Cache/data_1", "avito/browser/Default/Cache/x/y.bin",
            "avito/browser/Default/Code Cache/js/a", "avito/browser/GrShaderCache/b",
            "avito/browser/Default/Service Worker/CacheStorage/c", "avito/browser/BrowserMetrics-spare.pma",
            "cian/browser/__pycache__/x.pyc", "webview/EBWebView/Default/GPUCache/d",
        )]
        self.keep = [make(self.d, r) for r in (
            "avito/browser/Default/Cookies", "avito/browser/Default/Network/Cookies",
            "avito/browser/Default/Local Storage/leveldb/000003.log", "avito/browser/Default/Preferences",
            "avito/browser/Local State", "cian/http_cookies.json",
        )]

    def test_removes_caches_keeps_cookies(self):
        freed = cleanup.free_caches([self.d])
        self.assertEqual([p.name for p in self.junk if p.exists()], [], "кэш должен быть удалён")
        self.assertEqual([p.name for p in self.keep if not p.exists()], [], "cookies и настройки трогать нельзя")
        self.assertEqual(freed, 1000 * len(self.junk), "считается освобождённое место")
        self.assertTrue((self.d / "avito/browser/Default").is_dir(), "сам профиль остаётся на месте")

    def test_budget_stops_cleanup(self):
        """Закрытие программы не ждёт дольше отведённого времени."""
        self.assertEqual(cleanup.free_caches([self.d], budget_s=-1), 0)
        self.assertEqual([p.name for p in self.junk if not p.exists()], [])

    def test_places_are_inside_app_folder(self):
        """Чистится только папка программы (профили, окно, скомпилированный код)."""
        for p in cleanup.places():
            self.assertIn(cleanup.ROOT, p.parents, p)
        self.assertNotIn(cleanup.ROOT / "database", cleanup.places())
        self.assertNotIn(cleanup.ROOT / "logs", cleanup.places())
