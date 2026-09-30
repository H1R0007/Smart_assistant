# core/knowledge/search.py

"""
Гибридный поиск: FTS5 + Embeddings + LLM.

Архитектура:
- Два этапа поиска параллельно (FTS + semantic)
- Reciprocal Rank Fusion для объединения результатов
- LLM для ответа на вопрос по найденным фрагментам (RAG)
- Кэш частых запросов

Почему Reciprocal Rank Fusion (RRF):
- Не требует нормализации скоров из разных систем
- FTS5 rank и cosine similarity имеют разные шкалы
- RRF устойчив к выбросам
- Формула: score = Σ 1/(k + rank_i), k=60 (стандарт)

Почему два этапа:
- FTS мгновенный, ловит точные совпадения ("купить молоко")
- Semantic ловит перефразирования ("что надо из магазина")
- Комбинация > каждый по отдельности
"""

import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from core.knowledge.storage import KnowledgeStorage, SearchResult, Entry
from core.knowledge.embeddings import EmbeddingEngine

logger = logging.getLogger(__name__)


class HybridSearch:
    """
    Гибридный поиск по базе знаний.
    
    Поддерживает три режима:
    1. search() — быстрый поиск (FTS + semantic)
    2. ask() — RAG: поиск + ответ LLM по найденным фрагментам
    3. find_similar() — похожие записи для конкретной записи
    """

    RRF_K = 60  # параметр RRF (стандартное значение)
    MAX_CONTEXT_ENTRIES = 5  # максимум записей в контекст для LLM
    MAX_CONTEXT_CHARS = 2000  # максимум символов контекста

    def __init__(
        self,
        storage: KnowledgeStorage,
        embeddings: EmbeddingEngine,
        llm=None,
    ):
        self._storage = storage
        self._embeddings = embeddings
        self._llm = llm
        self._executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="search",
        )

    # ══════════════════════════════════════════════
    #  Гибридный поиск
    # ══════════════════════════════════════════════

    def search(
        self,
        query: str,
        limit: int = 10,
        source: Optional[str] = None,
        since: Optional[float] = None,
    ) -> list[SearchResult]:
        """
        Гибридный поиск: FTS + semantic + RRF.
        
        Время: 5-15мс (FTS <1мс, semantic 5-10мс).
        Если embedding движок не готов — только FTS.
        
        Args:
            query:  Поисковый запрос
            limit:  Максимум результатов
            source: Фильтр по источнику записи (None = все).
                    Например, source='tracker' — только события трекера.
            since:  Unix timestamp. Только записи новее этого момента.
        """
        start = time.time()

        # FTS поддерживает source/since нативно через SQL
        fts_future = self._executor.submit(
            self._storage.search_fts, query, limit * 2, source, since
        )

        if self._embeddings.is_ready:
            # Semantic search не поддерживает фильтры,
            # поэтому запрашиваем больше и фильтруем post-hoc.
            # Множитель x4 при наличии фильтров, чтобы после
            # отсева осталось достаточно результатов.
            sem_limit = limit * 4 if (source or since) else limit * 2
            sem_future = self._executor.submit(
                self._embeddings.search, query, sem_limit
            )
        else:
            sem_future = None

        # Собираем результаты
        fts_results = fts_future.result(timeout=5)
        sem_results = sem_future.result(timeout=10) if sem_future else []

        # ── Post-filter семантических результатов ──
        # EmbeddingEngine ищет по всем векторам в памяти,
        # фильтрация по source/since невозможна на уровне cosine similarity.
        # Фильтруем после: это допустимо, т.к. семантический поиск
        # и так является «вторым мнением» к FTS.
        if sem_results and (source or since):
            sem_results = [
                r for r in sem_results
                if (source is None or r.entry.source == source)
                and (since is None or r.entry.created_at >= since)
            ]

        # Объединяем через RRF
        merged = self._rrf_merge(fts_results, sem_results)

        elapsed = time.time() - start
        logger.debug(
            "Hybrid search: '%s' → %d results "
            "(fts=%d, sem=%d, source=%s) in %.3fs",
            query[:30], len(merged), len(fts_results),
            len(sem_results), source, elapsed
        )

        return merged[:limit]

    def _rrf_merge(
        self,
        fts_results: list[SearchResult],
        sem_results: list[SearchResult],
    ) -> list[SearchResult]:
        """
        Reciprocal Rank Fusion.
        
        Каждый результат получает score = sum(1 / (k + rank))
        по всем спискам где он встречается.
        """
        scores: dict[int, float] = {}
        entries: dict[int, SearchResult] = {}

        for rank, result in enumerate(fts_results):
            eid = result.entry.id
            rrf_score = 1.0 / (self.RRF_K + rank)
            scores[eid] = scores.get(eid, 0) + rrf_score
            if eid not in entries:
                entries[eid] = result

        for rank, result in enumerate(sem_results):
            eid = result.entry.id
            rrf_score = 1.0 / (self.RRF_K + rank)
            scores[eid] = scores.get(eid, 0) + rrf_score
            if eid not in entries:
                entries[eid] = result
            else:
                # Был в обоих — помечаем как hybrid
                entries[eid] = SearchResult(
                    entry=entries[eid].entry,
                    score=scores[eid],
                    match_type="hybrid",
                )

        # Сортируем по RRF score
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        results = []
        for eid in sorted_ids:
            result = entries[eid]
            result.score = scores[eid]
            results.append(result)

        return results

    # ══════════════════════════════════════════════
    #  RAG: вопрос-ответ по базе знаний
    # ══════════════════════════════════════════════

    def ask(
        self,
        question: str,
        source: Optional[str] = None,
        since: Optional[float] = None,
    ) -> Optional[str]:
        """
        Ответить на вопрос используя базу знаний.
        
        Пайплайн:
        1. Поиск релевантных записей (с фильтрами)
        2. Формирование контекста
        3. Запрос к LLM с контекстом
        
        Args:
            question: Вопрос пользователя
            source:   Ограничить контекст записями из этого источника
            since:    Ограничить контекст записями новее этого момента
        """
        if not self._llm:
            return None

        # 1. Поиск с фильтрами
        results = self.search(
            question,
            limit=self.MAX_CONTEXT_ENTRIES,
            source=source,
            since=since,
        )

        if not results:
            return None

        # 2. Формирование контекста
        context = self._build_context(results)
        if not context:
            return None

        # 3. Запрос к LLM
        prompt = self._build_rag_prompt(question, context)
        answer = self._llm.ask(prompt)

        return answer

    def _build_context(self, results: list[SearchResult]) -> str:
        """
        Сформировать контекст из результатов поиска.
        
        Формат:
        [1] (2 часа назад, заметка) Купить молоко и хлеб
        [2] (вчера, факт) Встреча с Сашей в 15:00 по деплою
        """
        parts = []
        total_chars = 0

        for i, result in enumerate(results, 1):
            entry = result.entry
            age = entry.age_display
            line = f"[{i}] ({age}, {entry.entry_type}) {entry.content}"

            if total_chars + len(line) > self.MAX_CONTEXT_CHARS:
                break

            parts.append(line)
            total_chars += len(line)

        return "\n".join(parts)

    @staticmethod
    def _build_rag_prompt(question: str, context: str) -> str:
        """Промпт для RAG."""
        return (
            "Ты отвечаешь на вопрос используя ТОЛЬКО мои записи ниже.\n"
            "ПРАВИЛА:\n"
            "- Ответь одним предложением\n"
            "- Используй ТОЛЬКО факты из записей\n"
            "- Если ответа нет в записях — скажи 'В записях не нашёл'\n"
            "- НЕ придумывай, НЕ дополняй\n"
            "- Укажи когда была сделана запись если это важно\n\n"
            f"ЗАПИСИ:\n{context}\n\n"
            f"ВОПРОС: {question}\n"
            "ОТВЕТ:"
        )

    # ══════════════════════════════════════════════
    #  Поиск похожих
    # ══════════════════════════════════════════════

    def find_similar(
        self,
        entry_id: int,
        limit: int = 5,
    ) -> list[SearchResult]:
        """
        Найти записи похожие на данную.
        
        Используется для:
        - "Что ещё я записывал по этой теме?"
        - Автоматическое связывание записей
        """
        entry = self._storage.get_entry(entry_id)
        if not entry:
            return []

        results = self._embeddings.search(
            entry.content,
            limit=limit + 1,  # +1 потому что найдёт саму себя
        )

        # Убираем саму запись из результатов
        return [r for r in results if r.entry.id != entry_id][:limit]