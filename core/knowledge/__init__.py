# core/knowledge/__init__.py

"""
Персональная база знаний.

Единая точка инициализации.
Все компоненты доступны через get_knowledge().
"""

import logging
from typing import Optional
import time

from core.knowledge.storage import KnowledgeStorage
from core.knowledge.embeddings import EmbeddingEngine
from core.knowledge.search import HybridSearch
from core.knowledge.activity_tracker import ActivityTracker
from core.patterns import PatternEngine
from core.macros.auto_generator import MacroAutoGenerator

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    Фасад для всех компонентов базы знаний.

    Использование:
        kb = KnowledgeBase(llm=llm)
        kb.start()

        kb.remember("купить молоко", tags="покупки")
        results = kb.search("что купить")
        answer = kb.ask("что мне надо из магазина?")

        kb.stop()
    """

    def __init__(
        self,
        db_path: str = "data/knowledge.db",
        llm=None,
        embedding_model: str = EmbeddingEngine.DEFAULT_MODEL,
    ):
        self.storage = KnowledgeStorage(db_path)
        self.embeddings = EmbeddingEngine(self.storage, embedding_model)
        self.search_engine = HybridSearch(self.storage, self.embeddings, llm)
        self.tracker = ActivityTracker(self.storage)

        # Движок паттернов — анализирует привычки
        self.patterns = PatternEngine(self.storage)
        self.macro_generator = MacroAutoGenerator(
            self.storage, llm=llm,
        )
        self.patterns.set_macro_generator(self.macro_generator)
        # Связываем трекер и паттерны:
        # при каждом новом действии проверяем,
        # не пора ли что-то предложить.
        self.tracker.set_activity_callback(self._on_activity)

        self._llm = llm
        self._started = False
        self._pending_suggestion = None

    def start(self) -> None:
        """Запустить фоновые сервисы."""
        if self._started:
            return

        self.tracker.start()
        self.embeddings.start_background_indexer()
        self.patterns.start()
        self._started = True
        logger.info("KnowledgeBase запущена")

        # Автоархивация старых записей трекера при старте.
        # Только entries с source='tracker' старше 90 дней.
        # activity_log НЕ затрагивается (нужен для паттернов).
        try:
            self._auto_archive_old_tracker_entries()
        except Exception as e:
            logger.debug("Auto-archive error: %s", e)

    def stop(self) -> None:
        """Остановить всё."""
        self.patterns.stop()
        self.tracker.stop()
        self.embeddings.stop()
        self.storage.close()
        self._started = False
        logger.info("KnowledgeBase остановлена")

    # ══════════════════════════════════════════════
    #  Хук для проактивных предложений
    # ══════════════════════════════════════════════

    def _on_activity(self, record: dict) -> None:
        """
        Вызывается трекером при каждом действии.

        Проверяет, не пора ли что-то предложить.
        Вызов O(1) — только просмотр кэшированных паттернов.
        """
        try:
            suggestion = self.patterns.on_user_action(
                record.get("action", ""),
                record.get("target", ""),
            )
            if suggestion:
                self._pending_suggestion = suggestion
        except Exception as e:
            logger.debug("Pattern check error: %s", e)

    def get_pending_suggestion(self):
        """
        Получить предложение (если есть).

        Вызывается ProactiveManager
        между распознаваниями. None = молчим.
        """
        s = self._pending_suggestion
        self._pending_suggestion = None
        return s

    # ══════════════════════════════════════════════
    #  Статистика
    # ══════════════════════════════════════════════

    @property
    def stats(self) -> dict:
        """Статистика базы знаний (включая паттерны)."""
        return {
            "entries": self.storage.entry_count,
            "activities": self.storage.activity_count,
            "embeddings": self.embeddings.cache_size,
            "patterns": self.patterns.pattern_count,
        }

    # ══════════════════════════════════════════════
    #  Удобные методы
    # ══════════════════════════════════════════════

    def remember(
        self,
        content: str,
        entry_type: str = "note",
        tags: str = "",
        importance: int = 0,
        source: str = "voice",
    ) -> int:
        """
        Запомнить что-то.

        1. Сохраняет запись
        2. Фоновый индексатор создаст embedding
        3. После индексации — ищет похожие и создаёт связи

        Returns: ID записи
        """
        entry_id = self.storage.add_entry(
            content=content,
            entry_type=entry_type,
            source=source,
            tags=tags,
            importance=importance,
        )

        # Автосвязывание в фоне (после индексации)
        if self.embeddings.is_ready:
            import threading
            threading.Thread(
                target=self._auto_link,
                args=(entry_id, content),
                daemon=True,
                name=f"autolink-{entry_id}",
            ).start()

        return entry_id

    def _auto_link(self, entry_id: int, content: str) -> None:
        """Найти похожие записи и связать."""
        try:
            # Сначала индексируем
            self.embeddings.index_entry(entry_id, content)

            # Ищем похожие
            similar = self.search_engine.find_similar(entry_id, limit=2)

            for result in similar:
                if result.score > 0.5:
                    self.storage.link_entries(
                        entry_id, result.entry.id, "related"
                    )
                    logger.debug(
                        "Auto-linked #%d ↔ #%d (%.2f)",
                        entry_id, result.entry.id, result.score
                    )
        except Exception as e:
            logger.debug("Auto-link error: %s", e)

    # ══════════════════════════════════════════════
    #  Активность — поиск и аналитика
    # ══════════════════════════════════════════════

    def search_activity(
        self,
        query: str,
        hours: float = 24,
        limit: int = 10,
    ) -> list:
        """
        Поиск по записям трекера активности.

        Использует гибридный поиск (FTS + semantic) с фильтром
        source='tracker'. Ограничивает временное окно.
        """
        since = time.time() - (hours * 3600)
        return self.search_engine.search(
            query, limit, source="tracker", since=since
        )

    def ask_activity(self, question: str, hours: float = 24) -> Optional[str]:
        """
        Задать вопрос по активности через RAG.

        Контекст ограничен записями трекера за указанный период.
        """
        since = time.time() - (hours * 3600)
        return self.search_engine.ask(
            question, source="tracker", since=since
        )

    def get_activity_summary(self, hours: float = 8) -> dict:
        """
        Агрегированная сводка активности за период.

        Работает по СЫРЫМ данным из activity_log (не из entries).
        """
        return self.storage.get_activity_summary(hours)

    def get_recent_activities(
        self,
        hours: float = 24,
        action: Optional[str] = None,
        limit: int = 50,
    ) -> list:
        """
        Получить последние записи из лога активности.

        Обёртка над storage.get_activity().
        """
        return self.storage.get_activity(hours, action, limit)
    
    def get_app_duration_stats(
        self, hours: float = 168,
    ) -> dict:
        """
        Статистика длительности по приложениям за период.

        Обёртка над storage.get_app_duration_stats().
        """
        return self.storage.get_app_duration_stats(hours)

    def search(self, query: str, limit: int = 5) -> list:
        """Поиск по базе знаний."""
        return self.search_engine.search(query, limit)

    def ask(self, question: str) -> Optional[str]:
        """Задать вопрос по базе знаний (RAG)."""
        return self.search_engine.ask(question)

    def forget(self, entry_id: int) -> None:
        """Удалить запись."""
        self.storage.archive_entry(entry_id)
    
    def _auto_archive_old_tracker_entries(
        self, max_age_days: int = 90,
    ) -> int:
        """
        Архивировать записи трекера старше N дней.

        Только source='tracker', не трогает ручные заметки.
        Сырой activity_log НЕ архивируется (нужен для паттернов).

        Эффект:
        - Освобождает RAM (embeddings удаляются из кэша
          при следующей пересборке)
        - Ускоряет FTS/semantic поиск
        - НЕ влияет на PatternEngine (работает с activity_log)
        """
        cutoff = time.time() - max_age_days * 86400

        conn = self.storage._get_connection()
        old_entries = conn.execute(
            """
            SELECT id FROM entries
            WHERE source = 'tracker'
                AND is_archived = 0
                AND created_at < ?
            """,
            (cutoff,),
        ).fetchall()

        if not old_entries:
            return 0

        count = 0
        for row in old_entries:
            self.storage.archive_entry(row["id"])
            count += 1

        if count > 0:
            logger.info(
                "Архивировано %d записей трекера "
                "старше %d дней",
                count, max_age_days,
            )

        return count