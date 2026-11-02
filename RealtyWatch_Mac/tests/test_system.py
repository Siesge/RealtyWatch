"""Системные вызовы форка: то из app.system, что можно проверить на любой системе.
Звуков не издаёт, уведомлений не показывает, чужих папок не открывает."""
import tempfile
import unittest
from pathlib import Path

from app import system
from tests.support import TMP_ROOT


class System(unittest.TestCase):
    def test_single_instance_blocks_second_copy(self):
        """Вторая копия программы не должна открыться: две писали бы в одну базу."""
        lock = Path(tempfile.mkdtemp(dir=TMP_ROOT)) / "app.lock"
        first = system.single_instance(lock)
        self.assertIsNotNone(first, "первая копия должна запускаться")
        self.assertIsNone(system.single_instance(lock), "вторая копия не должна запускаться")
        first.close()
        again = system.single_instance(lock)
        self.assertIsNotNone(again, "после выхода замок свободен")
        again.close()

    def test_lock_file_folder_is_created(self):
        lock = Path(tempfile.mkdtemp(dir=TMP_ROOT)) / "нет" / "такой" / "папки" / "app.lock"
        f = system.single_instance(lock)
        self.addCleanup(f.close)
        self.assertTrue(lock.exists())

    def test_light_theme_answers(self):
        self.assertIn(system.light_theme(), (True, False))

    def test_quiet_calls_do_not_raise(self):
        """Ничего из этого не должно ронять программу, даже когда система отказала."""
        system.beep([])                      # пустой список — тишина на любой системе
        system.open_folder(TMP_ROOT)
        self.assertFalse(system.raise_app(0) and not system.MAC)

    def test_one_platform_is_chosen(self):
        self.assertEqual(sum((system.MAC, system.WIN)), 1, "форк рассчитан на macOS и Windows")
