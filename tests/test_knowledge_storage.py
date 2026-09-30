"""
Тесты хранилища базы знаний.

Запуск: python -m pytest tests/test_knowledge_storage.py -v
Или без pytest: python tests/test_knowledge_storage.py
"""

import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge.storage import (
    KnowledgeStorage, Entry, vector_to_bytes, bytes_to_vector
)
import numpy as np


class TestVectorSerialization(unittest.TestCase):
    """Сериализация embeddings."""

    def test_roundtrip(self):
        vec = np.random.randn(384).astype(np.float32)
        blob = vector_to_bytes(vec)
        restored = bytes_to_vector(blob)
        np.testing.assert_array_almost_equal(vec, restored, decimal=6)

    def test_different_dimensions(self):
        for dim in [128, 384, 768, 1024]:
            vec = np.random.randn(dim).astype(np.float32)
            restored = bytes_to_vector(vector_to_bytes(vec))
            self.assertEqual(restored.shape[0], dim)

    def test_zeros(self):
        vec = np.zeros(384, dtype=np.float32)
        restored = bytes_to_vector(vector_to_bytes(vec))
        np.testing.assert_array_equal(vec, restored)


class TestStorage(unittest.TestCase):
    """Основные операции хранилища."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test.db")
        self.storage = KnowledgeStorage(self.db_path)

    def tearDown(self):
        self.storage.close()
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmp)
        except Exception:
            pass

    def test_add_and_get(self):
        entry_id = self.storage.add_entry(
            content="купить молоко",
            entry_type="note",
            tags="покупки",
        )
        self.assertGreater(entry_id, 0)

        entry = self.storage.get_entry(entry_id)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.content, "купить молоко")
        self.assertEqual(entry.tags, "покупки")
        self.assertEqual(entry.entry_type, "note")

    def test_get_recent(self):
        self.storage.add_entry("первая")
        time.sleep(0.01)
        self.storage.add_entry("вторая")
        time.sleep(0.01)
        self.storage.add_entry("третья")

        recent = self.storage.get_recent(2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0].content, "третья")
        self.assertEqual(recent[1].content, "вторая")

    def test_get_recent_by_type(self):
        self.storage.add_entry("заметка", entry_type="note")
        self.storage.add_entry("факт", entry_type="fact")
        self.storage.add_entry("ещё заметка", entry_type="note")

        notes = self.storage.get_recent(10, entry_type="note")
        self.assertEqual(len(notes), 2)
        for n in notes:
            self.assertEqual(n.entry_type, "note")

    def test_archive(self):
        entry_id = self.storage.add_entry("удали меня")
        self.storage.archive_entry(entry_id)

        entry = self.storage.get_entry(entry_id)
        self.assertIsNone(entry)

        # Не должна появляться в recent
        recent = self.storage.get_recent(100)
        ids = [e.id for e in recent]
        self.assertNotIn(entry_id, ids)

    def test_update(self):
        entry_id = self.storage.add_entry("старый текст")
        self.storage.update_entry(entry_id, "новый текст")

        entry = self.storage.get_entry(entry_id)
        self.assertEqual(entry.content, "новый текст")

    def test_tags(self):
        self.storage.add_entry("задача 1", tags="работа,важно")
        self.storage.add_entry("задача 2", tags="работа")
        self.storage.add_entry("покупки", tags="дом")

        work = self.storage.get_by_tag("работа")
        self.assertEqual(len(work), 2)

    def test_entry_count(self):
        self.assertEqual(self.storage.entry_count, 0)
        self.storage.add_entry("раз")
        self.storage.add_entry("два")
        self.assertEqual(self.storage.entry_count, 2)

    def test_entry_age_display(self):
        entry = Entry(
            content="test",
            created_at=time.time() - 30 * 60,  # 30 минут назад
        )
        self.assertIn("минут", entry.age_display)

        entry2 = Entry(
            content="test",
            created_at=time.time() - 5 * 3600,  # 5 часов
        )
        self.assertIn("часов", entry2.age_display)


class TestFTS(unittest.TestCase):
    """Полнотекстовый поиск."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_fts.db")
        self.storage = KnowledgeStorage(self.db_path)

        # Наполняем тестовыми данными
        self.storage.add_entry("купить молоко и хлеб")
        self.storage.add_entry("встреча с Сашей по деплою в пятницу")
        self.storage.add_entry("сервер на 192.168.1.100 порт 8080")
        self.storage.add_entry("идея: сделать бота для телеграма")
        self.storage.add_entry("python asyncio event loop")

    def tearDown(self):
        self.storage.close()
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmp)
        except Exception:
            pass

    def test_simple_search(self):
        results = self.storage.search_fts("молоко")
        self.assertGreater(len(results), 0)
        self.assertIn("молоко", results[0].entry.content)

    def test_multiple_words(self):
        results = self.storage.search_fts("встреча деплой")
        self.assertGreater(len(results), 0)
        self.assertIn("Сашей", results[0].entry.content)

    def test_no_results(self):
        results = self.storage.search_fts("динозавры")
        self.assertEqual(len(results), 0)

    def test_archived_not_found(self):
        entry_id = self.storage.add_entry("секретная заметка")
        results_before = self.storage.search_fts("секретная")
        self.assertGreater(len(results_before), 0)

        self.storage.archive_entry(entry_id)
        results_after = self.storage.search_fts("секретная")
        self.assertEqual(len(results_after), 0)

    def test_special_characters(self):
        """FTS не падает на спецсимволах."""
        results = self.storage.search_fts('test"OR"1=1')
        # Не должно бросить исключение
        self.assertIsInstance(results, list)

    def test_cyrillic(self):
        results = self.storage.search_fts("сервер")
        self.assertGreater(len(results), 0)

    def test_english(self):
        results = self.storage.search_fts("python")
        self.assertGreater(len(results), 0)


class TestActivity(unittest.TestCase):
    """Лог активности."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_activity.db")
        self.storage = KnowledgeStorage(self.db_path)

    def tearDown(self):
        self.storage.close()
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmp)
        except Exception:
            pass

    def test_add_and_get(self):
        self.storage.add_activity(
            action="command",
            target="open_app",
            details='{"app": "chrome"}',
        )
        self.storage.add_activity(
            action="app_switch",
            target="Chrome",
            duration_sec=120.5,
        )

        records = self.storage.get_activity(hours=1)
        self.assertEqual(len(records), 2)

    def test_filter_by_action(self):
        self.storage.add_activity(action="command", target="timer")
        self.storage.add_activity(action="app_switch", target="Chrome")
        self.storage.add_activity(action="command", target="weather")

        commands = self.storage.get_activity(hours=1, action="command")
        self.assertEqual(len(commands), 2)

    def test_summary(self):
        self.storage.add_activity(
            action="app_switch", target="Chrome",
            duration_sec=3600,
        )
        self.storage.add_activity(
            action="app_switch", target="VS Code",
            duration_sec=7200,
        )
        self.storage.add_activity(action="command", target="timer")

        summary = self.storage.get_activity_summary(hours=24)
        self.assertEqual(summary["total_commands"], 1)
        self.assertGreater(len(summary["top_apps"]), 0)

        # VS Code должен быть первым (больше времени)
        self.assertEqual(summary["top_apps"][0][0], "VS Code")

    def test_old_records_not_returned(self):
        # Добавляем запись "из прошлого"
        conn = self.storage._get_connection()
        old_time = time.time() - 48 * 3600  # 2 дня назад
        conn.execute(
            """INSERT INTO activity_log
               (action, target, timestamp, duration_sec)
               VALUES (?, ?, ?, ?)""",
            ("app_switch", "OldApp", old_time, 100)
        )
        conn.commit()

        records = self.storage.get_activity(hours=24)
        targets = [r.target for r in records]
        self.assertNotIn("OldApp", targets)


class TestEmbeddingStorage(unittest.TestCase):
    """Хранение embeddings."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_emb.db")
        self.storage = KnowledgeStorage(self.db_path)

    def tearDown(self):
        self.storage.close()
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmp)
        except Exception:
            pass

    def test_save_and_load(self):
        entry_id = self.storage.add_entry("тест embedding")
        vec = np.random.randn(384).astype(np.float32)
        self.storage.save_embedding(entry_id, vec, "test-model")

        all_embs = self.storage.get_all_embeddings()
        self.assertEqual(len(all_embs), 1)
        self.assertEqual(all_embs[0][0], entry_id)
        np.testing.assert_array_almost_equal(all_embs[0][1], vec, decimal=6)

    def test_entries_without_embeddings(self):
        id1 = self.storage.add_entry("с embedding")
        id2 = self.storage.add_entry("без embedding")

        vec = np.random.randn(384).astype(np.float32)
        self.storage.save_embedding(id1, vec, "test")

        pending = self.storage.get_entries_without_embeddings()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, id2)

    def test_cascade_delete(self):
        """Embedding удаляется при архивации записи."""
        entry_id = self.storage.add_entry("удалимся")
        vec = np.random.randn(384).astype(np.float32)
        self.storage.save_embedding(entry_id, vec, "test")

        self.assertEqual(len(self.storage.get_all_embeddings()), 1)

        # Архивация НЕ удаляет embedding (мягкое удаление)
        # Но get_all_embeddings фильтрует archived
        self.storage.archive_entry(entry_id)
        self.assertEqual(len(self.storage.get_all_embeddings()), 0)


class TestLinks(unittest.TestCase):
    """Связи между записями."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_links.db")
        self.storage = KnowledgeStorage(self.db_path)

    def tearDown(self):
        self.storage.close()
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmp)
        except Exception:
            pass

    def test_link_and_get_related(self):
        id1 = self.storage.add_entry("деплой сервера")
        id2 = self.storage.add_entry("настройка nginx")
        id3 = self.storage.add_entry("покупки")

        self.storage.link_entries(id1, id2, "related")

        related = self.storage.get_related(id1)
        self.assertEqual(len(related), 1)
        self.assertEqual(related[0].id, id2)

        # Связь двусторонняя
        related2 = self.storage.get_related(id2)
        self.assertEqual(len(related2), 1)
        self.assertEqual(related2[0].id, id1)

        # id3 не связан
        related3 = self.storage.get_related(id3)
        self.assertEqual(len(related3), 0)


class TestBackup(unittest.TestCase):
    """Бэкап."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_bak.db")
        self.storage = KnowledgeStorage(self.db_path)

    def tearDown(self):
        self.storage.close()
        import shutil
        try:
            shutil.rmtree(self.tmp)
        except Exception:
            pass

    def test_backup_creates_file(self):
        self.storage.add_entry("важные данные")
        backup_path = self.storage.backup()

        self.assertTrue(os.path.exists(backup_path))

        # Бэкап должен содержать данные
        backup_storage = KnowledgeStorage(backup_path)
        self.assertEqual(backup_storage.entry_count, 1)
        backup_storage.close()


class TestConcurrency(unittest.TestCase):
    """Потокобезопасность."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "test_concurrent.db")
        self.storage = KnowledgeStorage(self.db_path)

    def tearDown(self):
        self.storage.close()
        try:
            os.remove(self.db_path)
            os.rmdir(self.tmp)
        except Exception:
            pass

    def test_concurrent_writes(self):
        """Параллельная запись из нескольких потоков."""
        import threading

        errors = []
        count_per_thread = 50

        def writer(thread_id):
            try:
                for i in range(count_per_thread):
                    self.storage.add_entry(
                        f"thread_{thread_id}_entry_{i}"
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(t,))
            for t in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Ошибки: {errors}")
        self.assertEqual(
            self.storage.entry_count,
            4 * count_per_thread
        )

    def test_concurrent_read_write(self):
        """Параллельное чтение и запись."""
        import threading

        # Наполняем
        for i in range(100):
            self.storage.add_entry(f"запись {i}")

        errors = []
        reads = []

        def writer():
            try:
                for i in range(50):
                    self.storage.add_entry(f"новая {i}")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(50):
                    result = self.storage.search_fts("запись")
                    reads.append(len(result))
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Ошибки: {errors}")
        self.assertGreater(len(reads), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)