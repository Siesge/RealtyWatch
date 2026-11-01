r"""Тесты RealtyWatch. Запуск: tests\run_tests.bat (или runtime\python\python.exe -m unittest discover -s tests -t .)"""
import logging

# Журнал тестов не пишется в logs/app.log: обработчик ставится раньше, чем любой тест импортирует app.runner
# (runlog.setup_logging не трогает уже настроенный журнал)
logging.getLogger().addHandler(logging.NullHandler())
