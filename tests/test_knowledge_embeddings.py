# tests/test_knowledge_embeddings.py

"""
Тесты embedding-движка.

Требует: pip install sentence-transformers
Первый запуск скачает модель (~100MB).

Запуск: python -m pytest tests/test_knowledge_embeddings.py -v
"""

import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from core.knowledge.storage import KnowledgeStorage
from core.knowledge.embeddings import EmbeddingEngine


def _has_sentence_transformers():
    try:
        import sentence_transformers
        return True
    except ImportError:
        return False


@unittest.skipUnless(
    _has_sentence_transformers(),
    "sentence-transformers не установлен"
)
class TestEmbeddingEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Один раз загружаем модель для всех тестов."""
        cls.tmp = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmp, "test_emb.db")
        cls.storage = KnowledgeStorage(cls.db_path)
        cls.engine = EmbeddingEngine(cls.storage)
        # Принудительная загрузка модели
        cls.engine._ensure_model()

    @classmethod
    def tearDownClass(cls):
        cls.storage.close()
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_embed_text(self):
        vec = self.engine.embed_text("привет мир")
        self.assertIsNotNone(vec)
        self.assertEqual(vec.dtype, np.float32)
        self.assertGreater(vec.shape[0], 0)

    def test_embed_query(self):
        vec = self.engine.embed_query("что купить в магазине")
        self.assertIsNotNone(vec)
        self.assertGreater(vec.shape[0], 0)

    def test_normalized(self):
        """Вектор должен быть нормализован (длина ~1)."""
        vec = self.engine.embed_text("тестовый текст")
        norm = np.linalg.norm(vec)
        self.assertAlmostEqual(norm, 1.0, places=3)

    def test_similar_texts_close(self):
        """Похожие тексты должны иметь высокую cosine similarity."""
        v1 = self.engine.embed_text("купить молоко в магазине")
        v2 = self.engine.embed_text("надо зайти в магазин за молоком")
        v3 = self.engine.embed_text("квантовая механика и физика")

        sim_12 = float(v1 @ v2)  # нормализованы → dot = cosine
        sim_13 = float(v1 @ v3)

        self.assertGreater(sim_12, sim_13,
            f"Молоко-магазин ({sim_12:.3f}) должно быть ближе "
            f"чем молоко-физика ({sim_13:.3f})"
        )
        self.assertGreater(sim_12, 0.5)

    def test_batch_embed(self):
        texts = [
            "первый текст",
            "второй текст",
            "третий текст",
        ]
        vectors = self.engine.embed_batch(texts)
        self.assertEqual(len(vectors), 3)
        for v in vectors:
            self.assertEqual(v.dtype, np.float32)

    def test_embed_speed(self):
        """Один embed < 100мс."""
        start = time.time()
        for _ in range(10):
            self.engine.embed_text("тестовая фраза для замера")
        elapsed = (time.time() - start) / 10
        self.assertLess(elapsed, 0.1,
            f"Embed слишком медленный: {elapsed:.3f}s"
        )

    def test_index_entry(self):
        entry_id = self.storage.add_entry("индексируемая запись")
        success = self.engine.index_entry(entry_id, "индексируемая запись")
        self.assertTrue(success)

        # Должен появиться в кэше
        self.assertIn(entry_id, self.engine._cache)

    def test_search_semantic(self):
        # Добавляем и индексируем записи
        entries = [
            "встреча с Сашей по деплою в пятницу",
            "купить молоко хлеб и масло",
            "настроить nginx на сервере",
            "позвонить маме вечером",
            "баг в модуле авторизации",
        ]
        for text in entries:
            eid = self.storage.add_entry(text)
            self.engine.index_entry(eid, text)

        # Поиск
        results = self.engine.search("что надо из магазина")
        self.assertGreater(len(results), 0)
        # Молоко должно быть в топе
        top_content = results[0].entry.content
        self.assertIn("молоко", top_content.lower())

    def test_search_russian_paraphrase(self):
        """Перефразирование на русском."""
        entries = [
            "деплой production сервера в пятницу в 15:00",
            "рецепт борща с мясом",
            "день рождения жены 15 марта",
        ]
        for text in entries:
            eid = self.storage.add_entry(text)
            self.engine.index_entry(eid, text)

        # Перефразированный запрос
        results = self.engine.search("когда выкатываем на прод")
        self.assertGreater(len(results), 0)
        self.assertIn("деплой", results[0].entry.content.lower())

    def test_index_pending(self):
        """Батчевая индексация."""
        for i in range(10):
            self.storage.add_entry(f"неиндексированная запись {i}")

        count = self.engine.index_pending()
        self.assertGreater(count, 0)

        # Все должны быть проиндексированы
        pending = self.storage.get_entries_without_embeddings()
        self.assertEqual(len(pending), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)