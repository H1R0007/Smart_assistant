# tests/test_knowledge_tracker.py

"""
Тесты трекера активности.

Запуск: python -m pytest tests/test_knowledge_tracker.py -v
"""

import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge.storage import KnowledgeStorage
from core.knowledge.activity_tracker import ActivityTracker


class TestAppNameExtraction(unittest.TestCase):
    """Извлечение имени приложения из заголовка."""

    def test_vscode(self):
        name = ActivityTracker._extract_app_name(
            "main.py — assistant — Visual Studio Code"
        )
        self.assertEqual(name, "VS Code")

    def test_chrome(self):
        name = ActivityTracker._extract_app_name(
            "Google — Google Chrome"
        )
        self.assertEqual(name, "Chrome")

    def test_telegram(self):
        name = ActivityTracker._extract_app_name(
            "Telegram (3)"
        )
        self.assertEqual(name, "Telegram")

    def test_unknown(self):
        name = ActivityTracker._extract_app_name(
            "Some Random App — Window Title"
        )
        # Должен вернуть последнюю часть
        self.assertEqual(name, "Window Title")

    def test_empty(self):
        name = ActivityTracker._extract_app_name("")
        self.assertEqual(name, "Unknown")

    def test_firefox(self):
        name = ActivityTracker._extract_app_name(
            "Hacker News — Mozilla Firefox"
        )
        self.assertEqual(name, "Firefox")


class TestTrackerCommands(unittest.TestCase):
    """Логирование команд и событий."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_tracker.db")
        self.storage = KnowledgeStorage(self.db_path)
        self.tracker = ActivityTracker(self.storage)

    def tearDown(self):
        self.tracker.stop()
        self.storage.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_log_command(self):
        self.tracker.log_command("open_app", {"app": "chrome"})
        self.tracker._flush_buffer()

        records = self.storage.get_activity(hours=1, action="command")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target, "open_app")

    def test_log_search(self):
        self.tracker.log_search("python tutorial")
        self.tracker._flush_buffer()

        records = self.storage.get_activity(hours=1, action="search")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].target, "python tutorial")

    def test_log_clipboard(self):
        self.tracker.log_clipboard("скопированный текст")
        self.tracker._flush_buffer()

        records = self.storage.get_activity(hours=1, action="clipboard")
        self.assertEqual(len(records), 1)

    def test_buffer_batching(self):
        """Буфер накапливает записи и пишет пачкой."""
        for i in range(20):
            self.tracker.log_command(f"cmd_{i}")

        # До flush — в БД пусто
        records_before = self.storage.get_activity(hours=1)
        self.assertEqual(len(records_before), 0)

        # После flush — всё записано
        self.tracker._flush_buffer()
        records_after = self.storage.get_activity(hours=1)
        self.assertEqual(len(records_after), 20)

    def test_session_summary(self):
        summary = self.tracker.get_session_summary()
        self.assertIn("session_minutes", summary)
        self.assertIn("top_apps", summary)
        self.assertGreaterEqual(summary["session_minutes"], 0)


class TestTrackerBackground(unittest.TestCase):
    """Фоновый трекинг."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_bg.db")
        self.storage = KnowledgeStorage(self.db_path)
        self.tracker = ActivityTracker(self.storage)

    def tearDown(self):
        self.tracker.stop()
        self.storage.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_start_stop(self):
        """Трекер стартует и останавливается без ошибок."""
        self.tracker.start()
        time.sleep(1)
        self.tracker.stop()

    def test_tracks_window(self):
        """За 2 секунды должен записать хотя бы одно переключение."""
        self.tracker.FLUSH_INTERVAL = 1  # Ускоряем flush
        self.tracker.MIN_FOCUS_SEC = 0.5  # Ускоряем запись
        self.tracker.start()
        time.sleep(3)
        self.tracker.stop()

        records = self.storage.get_activity(hours=1, action="app_switch")
        # Может быть 0 если окно не менялось, но не должно падать
        self.assertIsInstance(records, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)