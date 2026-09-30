# core/knowledge/embeddings.py

"""
Embedding-движок для семантического поиска.

Архитектура:
- sentence-transformers для генерации embeddings
- Модель multilingual-e5-small: 384 dims, ~100MB, русский + английский
- Батчевая обработка для эффективности
- Кэш embeddings в RAM (numpy matrix)
- Фоновая индексация новых записей
- Cosine similarity через numpy (без внешних библиотек)

Почему multilingual-e5-small:
- 384 dims vs 768 у base → в 2 раза меньше RAM и быстрее поиск
- Качество на русском достаточное для заметок
- Инференс ~5мс на CPU (не блокирует голос)
- Prefix "query:" / "passage:" улучшает качество поиска
"""

import time
import logging
import threading
import numpy as np
from typing import Optional

from core.knowledge.storage import KnowledgeStorage, SearchResult, Entry

logger = logging.getLogger(__name__)


class EmbeddingEngine:
    """
    Генерация и поиск по embeddings.
    
    Потокобезопасный. Модель загружается лениво при первом запросе.
    Фоновый поток индексирует новые записи.
    
    Жизненный цикл:
    1. __init__: сохраняет ссылку на storage, НЕ загружает модель
    2. Первый вызов search/embed: загрузка модели (~2 сек)
    3. start_background_indexer: фоновая индексация
    4. stop: остановка фоновых задач
    """

    DEFAULT_MODEL = "intfloat/multilingual-e5-small"
    BATCH_SIZE = 32
    INDEX_INTERVAL = 30  # секунд между проверками новых записей
    SIMILARITY_THRESHOLD = 0.3  # минимальная схожесть для результата

    def __init__(
        self,
        storage: KnowledgeStorage,
        model_name: str = DEFAULT_MODEL,
    ):
        self._storage = storage
        self._model_name = model_name

        # Ленивая загрузка
        self._model = None
        self._model_lock = threading.Lock()
        self._model_loaded = False

        # RAM-кэш embeddings: {entry_id: np.ndarray}
        self._cache: dict[int, np.ndarray] = {}
        self._cache_lock = threading.RLock()
        self._cache_loaded = False

        # Предвычисленная матрица для batch cosine similarity
        self._matrix: Optional[np.ndarray] = None
        self._matrix_ids: list[int] = []
        self._matrix_dirty = True

        # Фоновый индексатор
        self._indexer_stop = threading.Event()
        self._indexer_thread: Optional[threading.Thread] = None

    # ══════════════════════════════════════════════
    #  Загрузка модели
    # ══════════════════════════════════════════════

    def _ensure_model(self) -> None:
        """Загрузить модель если ещё не загружена."""
        if self._model_loaded:
            return

        with self._model_lock:
            if self._model_loaded:
                return

            logger.info("Загрузка embedding модели: %s...", self._model_name)
            start = time.time()

            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    self._model_name,
                    device="cpu",
                )
                elapsed = time.time() - start
                logger.info(
                    "Embedding модель загружена за %.1f сек (dim=%d)",
                    elapsed, self._model.get_sentence_embedding_dimension()
                )
                self._model_loaded = True
            except ImportError:
                logger.error(
                    "sentence-transformers не установлен. "
                    "pip install sentence-transformers"
                )
            except Exception as e:
                logger.error("Ошибка загрузки embedding модели: %s", e)

    def _ensure_cache(self) -> None:
        """Загрузить кэш embeddings из БД."""
        if self._cache_loaded:
            return

        with self._cache_lock:
            if self._cache_loaded:
                return

            start = time.time()
            all_embeddings = self._storage.get_all_embeddings()

            self._cache = {eid: vec for eid, vec in all_embeddings}
            self._rebuild_matrix()
            self._cache_loaded = True

            logger.info(
                "Embedding кэш: %d векторов за %.2f сек",
                len(self._cache), time.time() - start
            )

    # ══════════════════════════════════════════════
    #  Генерация embeddings
    # ══════════════════════════════════════════════

    def embed_text(self, text: str) -> Optional[np.ndarray]:
        """
        Сгенерировать embedding для текста.
        
        Добавляет prefix "query:" для вопросов
        и "passage:" для документов (e5 protocol).
        """
        self._ensure_model()
        if not self._model:
            return None

        try:
            # e5 формат: "passage: <text>" для документов
            prefixed = f"passage: {text}"
            vec = self._model.encode(
                prefixed,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return vec.astype(np.float32)
        except Exception as e:
            logger.error("Embed error: %s", e)
            return None

    def embed_query(self, query: str) -> Optional[np.ndarray]:
        """
        Embedding для поискового запроса.
        Prefix "query:" для e5.
        """
        self._ensure_model()
        if not self._model:
            return None

        try:
            prefixed = f"query: {query}"
            vec = self._model.encode(
                prefixed,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return vec.astype(np.float32)
        except Exception as e:
            logger.error("Query embed error: %s", e)
            return None

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Батчевая генерация embeddings."""
        self._ensure_model()
        if not self._model:
            return []

        try:
            prefixed = [f"passage: {t}" for t in texts]
            vecs = self._model.encode(
                prefixed,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=self.BATCH_SIZE,
            )
            return [v.astype(np.float32) for v in vecs]
        except Exception as e:
            logger.error("Batch embed error: %s", e)
            return []

    # ══════════════════════════════════════════════
    #  Семантический поиск
    # ══════════════════════════════════════════════

    def search(
        self,
        query: str,
        limit: int = 10,
        threshold: float = None,
    ) -> list[SearchResult]:
        """
        Семантический поиск по embeddings.
        
        Использует cosine similarity через матричное умножение.
        При 10000 записей: <1мс.
        
        Args:
            query: текст запроса
            limit: максимум результатов
            threshold: минимальная схожесть (0..1)
        """
        self._ensure_cache()

        if not self._cache:
            return []

        if threshold is None:
            threshold = self.SIMILARITY_THRESHOLD

        query_vec = self.embed_query(query)
        if query_vec is None:
            return []

        return self._search_by_vector(query_vec, limit, threshold)

    def _search_by_vector(
        self,
        query_vec: np.ndarray,
        limit: int,
        threshold: float,
    ) -> list[SearchResult]:
        """Поиск по предвычисленному вектору."""
        with self._cache_lock:
            if self._matrix_dirty or self._matrix is None:
                self._rebuild_matrix()

            if self._matrix is None or len(self._matrix_ids) == 0:
                return []

            # Cosine similarity через матричное умножение
            # Векторы уже нормализованы → dot product = cosine sim
            similarities = self._matrix @ query_vec

        # Топ-N
        if len(similarities) <= limit:
            top_indices = np.argsort(similarities)[::-1]
        else:
            # partial sort для эффективности
            top_indices = np.argpartition(
                similarities, -limit
            )[-limit:]
            top_indices = top_indices[
                np.argsort(similarities[top_indices])[::-1]
            ]

        results = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score < threshold:
                continue

            entry_id = self._matrix_ids[idx]
            entry = self._storage.get_entry(entry_id)
            if entry:
                results.append(SearchResult(
                    entry=entry,
                    score=score,
                    match_type="semantic",
                ))

        return results

    def _rebuild_matrix(self) -> None:
        """Пересобрать матрицу из кэша."""
        if not self._cache:
            self._matrix = None
            self._matrix_ids = []
            self._matrix_dirty = False
            return

        ids = list(self._cache.keys())
        vectors = [self._cache[eid] for eid in ids]

        self._matrix = np.vstack(vectors)
        self._matrix_ids = ids
        self._matrix_dirty = False

    # ══════════════════════════════════════════════
    #  Индексация
    # ══════════════════════════════════════════════

    def index_entry(self, entry_id: int, content: str) -> bool:
        """Проиндексировать одну запись."""
        vec = self.embed_text(content)
        if vec is None:
            return False

        self._storage.save_embedding(entry_id, vec, self._model_name)

        with self._cache_lock:
            self._cache[entry_id] = vec
            self._matrix_dirty = True

        return True

    def index_pending(self) -> int:
        """
        Проиндексировать все записи без embeddings.
        
        Returns: количество проиндексированных
        """
        pending = self._storage.get_entries_without_embeddings()
        if not pending:
            return 0

        logger.info("Индексация %d записей...", len(pending))
        start = time.time()

        # Батчевая генерация
        texts = [e.content for e in pending]
        vectors = self.embed_batch(texts)

        if len(vectors) != len(pending):
            logger.error(
                "Mismatch: %d entries, %d vectors",
                len(pending), len(vectors)
            )
            return 0

        count = 0
        for entry, vec in zip(pending, vectors):
            self._storage.save_embedding(
                entry.id, vec, self._model_name
            )
            with self._cache_lock:
                self._cache[entry.id] = vec
            count += 1

        with self._cache_lock:
            self._matrix_dirty = True

        elapsed = time.time() - start
        logger.info(
            "Проиндексировано %d записей за %.1f сек (%.0f/сек)",
            count, elapsed, count / max(elapsed, 0.001)
        )
        return count

    # ══════════════════════════════════════════════
    #  Фоновый индексатор
    # ══════════════════════════════════════════════

    def start_background_indexer(self) -> None:
        """Запустить фоновую индексацию новых записей."""
        if self._indexer_thread and self._indexer_thread.is_alive():
            return

        self._indexer_stop.clear()
        self._indexer_thread = threading.Thread(
            target=self._indexer_loop,
            daemon=True,
            name="embedding-indexer",
        )
        self._indexer_thread.start()
        logger.info("Фоновый индексатор запущен")

    def _indexer_loop(self) -> None:
        """Основной цикл фоновой индексации."""
        # Загрузка модели при старте (не при первом запросе)
        self._ensure_model()

        # Первичная загрузка кэша
        self._ensure_cache()

        # Первичная индексация
        self.index_pending()

        while not self._indexer_stop.is_set():
            self._indexer_stop.wait(timeout=self.INDEX_INTERVAL)
            if self._indexer_stop.is_set():
                break

            try:
                count = self.index_pending()
                if count > 0:
                    logger.debug("Фоновая индексация: %d записей", count)
            except Exception as e:
                logger.error("Ошибка фоновой индексации: %s", e)

    def stop(self) -> None:
        """Остановить фоновый индексатор."""
        self._indexer_stop.set()
        if self._indexer_thread:
            self._indexer_thread.join(timeout=5)
        logger.info("Индексатор остановлен")

    # ══════════════════════════════════════════════
    #  Утилиты
    # ══════════════════════════════════════════════

    @property
    def cache_size(self) -> int:
        with self._cache_lock:
            return len(self._cache)

    @property
    def is_ready(self) -> bool:
        return self._model_loaded and self._cache_loaded