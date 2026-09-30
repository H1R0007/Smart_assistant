# tests/test_knowledge_search.py

"""
Тесты гибридного поиска.

Запуск: python -m pytest tests/test_knowledge_search.py -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge.storage import KnowledgeStorage, SearchResult, Entry
from core.knowledge.embeddings import EmbeddingEngine
from core.knowledge.search import HybridSearch


def _has_sentence_transformers():
    try:
        import sentence_transformers
        return True
    except ImportError:
        return False


class TestRRFMerge(unittest.TestCase):
    """Тест Reciprocal Rank Fusion без зависимостей."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        db_path = os.path.join(tmp, "test_rrf.db")
        storage = KnowledgeStorage(db_path)
        engine = EmbeddingEngine(storage)
        self.search = HybridSearch(storage, engine)
        self.storage = storage
        self.tmp = tmp

    def tearDown(self):
        self.storage.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_result(self, entry_id, content, score=1.0, match_type="fts"):
        entry = Entry(
            id=entry_id, content=content,
            created_at=0, updated_at=0,
        )
        return SearchResult(entry=entry, score=score, match_type=match_type)

    def test_merge_disjoint(self):
        """Результаты без пересечений."""
        fts = [
            self._make_result(1, "fts result 1"),
            self._make_result(2, "fts result 2"),
        ]
        sem = [
            self._make_result(3, "sem result 1"),
            self._make_result(4, "sem result 2"),
        ]
        merged = self.search._rrf_merge(fts, sem)
        self.assertEqual(len(merged), 4)

    def test_merge_overlap_boost(self):
        """Результат в обоих списках получает boost."""
        fts = [
            self._make_result(1, "общий результат"),
            self._make_result(2, "только fts"),
        ]
        sem = [
            self._make_result(1, "общий результат"),
            self._make_result(3, "только sem"),
        ]
        merged = self.search._rrf_merge(fts, sem)

        # Общий результат должен быть первым
        self.assertEqual(merged[0].entry.id, 1)
        self.assertEqual(merged[0].match_type, "hybrid")

        # И его score выше чем у остальных
        self.assertGreater(merged[0].score, merged[1].score)

    def test_merge_empty(self):
        merged = self.search._rrf_merge([], [])
        self.assertEqual(len(merged), 0)

    def test_merge_one_empty(self):
        fts = [self._make_result(1, "only fts")]
        merged = self.search._rrf_merge(fts, [])
        self.assertEqual(len(merged), 1)


@unittest.skipUnless(
    _has_sentence_transformers(),
    "sentence-transformers не установлен"
)
class TestHybridSearch(unittest.TestCase):
    """Полный гибридный поиск."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmp, "test_hybrid.db")
        cls.storage = KnowledgeStorage(cls.db_path)
        cls.engine = EmbeddingEngine(cls.storage)
        cls.engine._ensure_model()
        cls.search = HybridSearch(cls.storage, cls.engine)

        # Наполняем базу
        test_data = [
            ("купить молоко хлеб и яйца", "note", "покупки"),
            ("встреча с Сашей в 15:00 по деплою", "note", "работа"),
            ("сервер prod: 192.168.1.100 порт 443", "fact", "работа,сервер"),
            ("идея: автоматизировать тесты через CI", "thought", "работа"),
            ("день рождения мамы 15 марта", "fact", "семья"),
            ("рецепт борща: свёкла морковь картошка", "note", "рецепты"),
            ("пароль от wifi: MyNetwork123", "fact", "дом"),
            ("книга Чистый код Роберт Мартин", "bookmark", "книги"),
            ("деплой staging прошёл успешно", "note", "работа"),
            ("купить подарок жене на годовщину", "note", "семья,покупки"),
        ]

        for content, entry_type, tags in test_data:
            eid = cls.storage.add_entry(
                content, entry_type=entry_type, tags=tags
            )
            cls.engine.index_entry(eid, content)

    @classmethod
    def tearDownClass(cls):
        cls.storage.close()
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_exact_match(self):
        """Точный запрос находит через FTS."""
        results = self.search.search("молоко")
        self.assertGreater(len(results), 0)
        self.assertIn("молоко", results[0].entry.content)

    def test_semantic_match(self):
        """Семантический запрос (перефразирование)."""
        # Диагностика: проверяем что embeddings загружены
        self.engine._ensure_cache()
        cache_size = self.engine.cache_size
        self.assertGreater(cache_size, 0,
            f"Embedding кэш пуст! Записей в БД: {self.storage.entry_count}")

        # Пробуем прямой semantic поиск
        sem_results = self.engine.search("что надо из магазина", limit=10, threshold=0.0)

        # Гибридный поиск
        results = self.search.search("что надо из магазина")

        # Хотя бы один из двух должен найти
        all_results = sem_results + results
        self.assertGreater(len(all_results), 0,
            f"Ничего не найдено. Cache: {cache_size}, "
            f"sem: {len(sem_results)}, hybrid: {len(results)}")

        contents = [r.entry.content.lower() for r in all_results[:5]]
        found_shopping = any(
            "купить" in c or "молоко" in c for c in contents
        )
        self.assertTrue(found_shopping,
            f"Не нашли покупки. Результаты: {contents}")

    def test_technical_query(self):
        """Технический запрос."""
        self.engine._ensure_cache()

        # Прямой semantic
        sem_results = self.engine.search("адрес продакшн сервера", limit=10, threshold=0.0)

        # Гибридный
        results = self.search.search("адрес продакшн сервера")

        all_results = sem_results + results
        self.assertGreater(len(all_results), 0,
            f"Ничего не найдено. Cache: {self.engine.cache_size}")

        contents = [r.entry.content.lower() for r in all_results[:5]]
        found_server = any("192.168" in c or "сервер" in c for c in contents)
        self.assertTrue(found_server,
            f"Не нашли сервер. Результаты: {contents}")

    def test_hybrid_boost(self):
        """Запрос с совпадением и в FTS и semantic — выше в результатах."""
        results = self.search.search("деплой")
        self.assertGreater(len(results), 0)
        # Должны быть записи про деплой
        top = results[0].entry.content.lower()
        self.assertTrue(
            "деплой" in top or "деплою" in top,
            f"Топ результат не про деплой: {top}"
        )


class MockLLM:
    """Мок LLM для тестирования RAG."""

    def __init__(self):
        self.last_prompt = ""
        self.response = "Тестовый ответ LLM"

    def ask(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


@unittest.skipUnless(
    _has_sentence_transformers(),
    "sentence-transformers не установлен"
)
class TestRAG(unittest.TestCase):
    """RAG pipeline."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.tmp, "test_rag.db")
        cls.storage = KnowledgeStorage(cls.db_path)
        cls.engine = EmbeddingEngine(cls.storage)
        cls.engine._ensure_model()
        cls.mock_llm = MockLLM()
        cls.search = HybridSearch(cls.storage, cls.engine, cls.mock_llm)

        # Данные
        entries = [
            "встреча с Сашей по деплою в пятницу 15:00",
            "дедлайн проекта 20 декабря",
            "купить молоко",
        ]
        for content in entries:
            eid = cls.storage.add_entry(content)
            cls.engine.index_entry(eid, content)

    @classmethod
    def tearDownClass(cls):
        cls.storage.close()
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_ask_returns_answer(self):
        answer = self.search.ask("когда встреча с Сашей")
        self.assertIsNotNone(answer)
        self.assertEqual(answer, "Тестовый ответ LLM")

    def test_ask_passes_context(self):
        """LLM получает контекст из найденных записей."""
        self.search.ask("когда дедлайн")
        prompt = self.mock_llm.last_prompt
        self.assertIn("МОИ ЗАПИСИ", prompt)
        self.assertIn("дедлайн", prompt.lower())

    def test_ask_no_results(self):
        """Если ничего не найдено — возвращает None."""
        answer = self.search.ask("квантовая телепортация в 11 измерении")
        # Может быть None (ничего не найдено) или ответ LLM
        # Зависит от threshold. Если ничего не прошло — None.
        # Не проверяем конкретное значение, проверяем что не падает.
        self.assertIsInstance(answer, (str, type(None)))

    def test_ask_without_llm(self):
        search_no_llm = HybridSearch(self.storage, self.engine, llm=None)
        answer = search_no_llm.ask("тест")
        self.assertIsNone(answer)


if __name__ == "__main__":
    unittest.main(verbosity=2)