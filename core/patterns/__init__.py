# core/patterns/__init__.py

"""
Движок обнаружения паттернов.

Фасад, объединяющий TemporalAnalyzer, SequenceAnalyzer
и SuggestionManager. Запускает фоновый анализ.
"""

import time
import logging
import threading
from typing import Optional

from core.patterns.temporal import TemporalAnalyzer, TemporalPattern
from core.patterns.sequence import SequenceAnalyzer, SequencePattern
from core.patterns.suggestions import SuggestionManager, Suggestion
from core.patterns.drift_detector import detect_drift, HabitDrift
from core.patterns.temporal_sequence import (
    TemporalSequenceAnalyzer, ContextualPattern,
)
from core.patterns.export import export_patterns, import_patterns

logger = logging.getLogger(__name__)

__all__ = [
    "PatternEngine",
    "TemporalAnalyzer", "TemporalPattern",
    "SequenceAnalyzer", "SequencePattern",
    "TemporalSequenceAnalyzer", "ContextualPattern",
    "SuggestionManager", "Suggestion",
    "HabitDrift",
]


class PatternEngine:
    """
    Фасад для системы обнаружения паттернов.

    Использование:
        engine = PatternEngine(storage)
        engine.start()

        # При каждом действии
        suggestion = engine.on_user_action("app_switch", "Chrome")

        # В режиме бездействия (раз в 10 мин)
        suggestion = engine.check_idle()

        # По запросу
        habits = engine.get_habits()
        insights = engine.get_insights()

        engine.stop()
    """

    # Интервал полного пересчёта: 6 часов.
    # Паттерны не меняются быстрее.
    FULL_ANALYSIS_INTERVAL = 6 * 3600

    ANALYSIS_DAYS = 14

    # Повышенный порог для idle-предложений.
    # В idle нет явного триггера (пользователь ничего не делал),
    # поэтому confidence должен быть выше, чтобы не раздражать.
    IDLE_MIN_CONFIDENCE = 0.65

    def __init__(self, storage):
        self._storage = storage
        self._temporal = TemporalAnalyzer(storage)
        self._sequence = SequenceAnalyzer(storage)
        self._temporal_sequence = TemporalSequenceAnalyzer(storage)
        self._contextual_patterns: list[ContextualPattern] = []
        self._suggestions = SuggestionManager(storage)

        # Кэш результатов
        self._temporal_patterns: list[TemporalPattern] = []
        self._sequence_patterns: list[SequencePattern] = []
        self._last_analysis: float = 0

        # Фоновый поток
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._analysis_lock = threading.Lock()
        self._habit_drifts: list[HabitDrift] = []
        self._last_drift_check: float = 0

        self._macro_generator = None

        # Таблицы для feedback
        self._ensure_tables()

    def set_macro_generator(self, generator) -> None:
        """Подключить MacroAutoGenerator."""
        self._macro_generator = generator

    def _ensure_tables(self) -> None:
        """Создать таблицы для хранения feedback (если ещё нет)."""
        try:
            self._storage.ensure_pattern_tables()
        except Exception as e:
            logger.debug("Pattern tables init: %s", e)

    # ══════════════════════════════════════════════
    #  Управление
    # ══════════════════════════════════════════════

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop,
            daemon=True,
            name="pattern-engine",
        )
        self._thread.start()
        logger.info("PatternEngine запущен")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("PatternEngine остановлен")

    # ══════════════════════════════════════════════
    #  API для ассистента
    # ══════════════════════════════════════════════

    def on_user_action(
        self, action: str, target: str,
    ) -> Optional[Suggestion]:
        if (not self._temporal_patterns
                and not self._sequence_patterns
                and not self._contextual_patterns):
            return None

        return self._suggestions.check(
            current_action=action,
            current_target=target,
            temporal_patterns=self._temporal_patterns,
            sequence_patterns=self._sequence_patterns,
            contextual_patterns=self._contextual_patterns,
            macro_generator=self._macro_generator,
        )

    def check_idle(self) -> Optional[Suggestion]:
        """
        Проверка предложений в режиме бездействия.

        Отличия от on_user_action():
        - Только temporal паттерны (нет триггера "после X")
        - Повышенный порог confidence (IDLE_MIN_CONFIDENCE)
        - Не обновляет трекинг last_active_app

        Вызывается ProactiveManager раз в 5-10 минут,
        когда пользователь не взаимодействует с ассистентом.
        """
        if not self._temporal_patterns:
            return None

        return self._suggestions.check(
            current_action="idle",
            current_target="",
            temporal_patterns=self._temporal_patterns,
            sequence_patterns=None,
            macro_generator=None,
            min_confidence=self.IDLE_MIN_CONFIDENCE,
        )

    def record_feedback(
        self,
        suggestion_id: str,
        *,
        accepted: bool = False,
        ignored: bool = False,
    ) -> None:
        """
        Записать реакцию пользователя на предложение.

        Вызывается из ProactiveManager после ответа
        (или таймаута) пользователя.

        Args:
            suggestion_id: ID предложения
            accepted: Пользователь согласился
            ignored: Пользователь не ответил (таймаут)
        """
        self._suggestions.record_feedback(
            suggestion_id, accepted=accepted, ignored=ignored,
        )

    def get_habits(self) -> dict:
        """Для команды "какие у меня привычки?"."""
        self._ensure_analyzed()

        return {
            "temporal": [
                {
                    "target": p.target,
                    "when": p.window_str,
                    "day_type": p.day_type,
                    "confidence": p.confidence,
                    "regularity": f"{int(p.regularity * 100)}%",
                }
                for p in self._temporal_patterns[:10]
            ],
            "sequences": [
                {
                    "pattern": p.description,
                    "confidence": f"{int(p.confidence * 100)}%",
                    "lift": p.lift,
                }
                for p in self._sequence_patterns[:10]
                if p.is_strong
            ],
        }

    def get_insights(self) -> str:
        """Текстовая сводка для голоса."""
        self._ensure_analyzed()
        parts = []

        # Топ-3 временных
        top_t = [
            p for p in self._temporal_patterns
            if p.confidence >= 0.4
        ][:3]
        if top_t:
            items = [
                f"{p.target} около {p.peak_time_str}"
                for p in top_t
            ]
            parts.append(
                "Регулярные привычки: "
                + ", ".join(items) + "."
            )

        # Топ-3 последовательности
        top_s = [
            p for p in self._sequence_patterns
            if p.is_strong
        ][:3]
        if top_s:
            items = [p.description for p in top_s]
            parts.append(
                "Частые последовательности: "
                + ", ".join(items) + "."
            )

        # Топ-3 контекстных
        top_c = [
            p for p in self._contextual_patterns
            if p.is_strong
        ][:3]
        if top_c:
            items = [p.description for p in top_c]
            parts.append(
                "Контекстные связи: "
                + ", ".join(items) + "."
            )

        if not parts:
            return (
                "Пока недостаточно данных "
                "для анализа привычек."
            )

        return " ".join(parts)

    def get_habit_changes(self) -> list[str]:
        """
        Текстовые описания изменений привычек.

        Для голосовой команды 'habits'.
        """
        return [d.message for d in self._habit_drifts]
    
    def export(self, path: str = None) -> str:
        """
        Экспорт паттернов в файл.

        Для голосовой команды: "экспортируй мои привычки"
        """
        self._ensure_analyzed()
        kwargs = {
            "temporal_patterns": self._temporal_patterns,
            "sequence_patterns": self._sequence_patterns,
            "contextual_patterns": self._contextual_patterns,
        }
        if path:
            kwargs["path"] = path
        return export_patterns(**kwargs)

    def explain_suggestion(self, suggestion_id: str) -> str:
        for s in self._suggestions._recent:
            if s.id == suggestion_id:
                return s.explanation
        return "Не нашла информации об этом предложении."
    
    def is_unusual_action(
        self, action: str, target: str,
    ) -> str | None:
        """
        Проверить, является ли действие необычным
        для текущего контекста.

        Вызывается из on_user_action (опционально)
        или из голосовой команды "это нормально?".

        Проверяет две аномалии:
        1. Приложение используется в другой тип дня
           (Discord в будни, хотя обычно по выходным)
        2. Приложение открыто в нетипичное время
           (Discord в 9 утра, хотя обычно вечером)

        Returns: описание аномалии или None (всё нормально).
        """
        if not self._temporal_patterns:
            return None

        now = time.localtime()
        now_minutes = now.tm_hour * 60 + now.tm_min
        is_weekday = now.tm_wday < 5
        day_type = "weekday" if is_weekday else "weekend"

        # Ищем temporal паттерны для этого target
        target_patterns = [
            p for p in self._temporal_patterns
            if p.target.lower() == target.lower()
            and p.confidence >= 0.5
        ]

        if not target_patterns:
            return None  # Нет данных — не можем судить

        # Разделяем по типу дня
        current_day_patterns = [
            p for p in target_patterns
            if p.day_type == day_type
        ]
        other_day_patterns = [
            p for p in target_patterns
            if p.day_type != day_type
        ]

        # Аномалия 1: приложение типично для другого типа дня
        if not current_day_patterns and other_day_patterns:
            p = other_day_patterns[0]
            when = (
                "по будням"
                if p.day_type == "weekday"
                else "по выходным"
            )
            now_type = (
                "будний день" if is_weekday else "выходной"
            )
            return (
                f"{target} обычно используется {when}, "
                f"а сейчас {now_type}."
            )

        # Аномалия 2: нетипичное время суток
        if current_day_patterns:
            # Ищем ближайший пик
            min_dist = 1440  # максимум
            closest = None
            for p in current_day_patterns:
                dist = TemporalAnalyzer._circular_distance(
                    now_minutes, p.peak_minute,
                )
                if dist < min_dist:
                    min_dist = dist
                    closest = p

            # Если дальше 3 часов от любого пика — аномалия
            if closest and min_dist > 180:
                now_str = (
                    f"{now.tm_hour}:{now.tm_min:02d}"
                )
                return (
                    f"{target} обычно открывается около "
                    f"{closest.peak_time_str}, "
                    f"а сейчас {now_str}."
                )

        return None

    @property
    def pattern_count(self) -> int:
        return len(self._temporal_patterns) + len(self._sequence_patterns)

    @property
    def is_analyzed(self) -> bool:
        return self._last_analysis > 0

    # ══════════════════════════════════════════════
    #  Фоновый анализ
    # ══════════════════════════════════════════════

    def _background_loop(self) -> None:
        # Первый анализ сразу
        self._run_analysis()

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self.FULL_ANALYSIS_INTERVAL)
            if not self._stop_event.is_set():
                self._run_analysis()

    def _run_analysis(self) -> None:
        start = time.monotonic()

        try:
            with self._analysis_lock:
                temporal = self._temporal.analyze(
                    days=self.ANALYSIS_DAYS,
                )
                sequence = self._sequence.analyze(
                    days=self.ANALYSIS_DAYS,
                )
                contextual = self._temporal_sequence.analyze(
                    days=self.ANALYSIS_DAYS,
                )

                self._temporal_patterns = temporal
                self._sequence_patterns = sequence
                self._contextual_patterns = contextual
                self._last_analysis = time.time()

            elapsed = time.monotonic() - start

            # Расширенная статистика для мониторинга
            temporal_strong = [
                p for p in temporal if p.confidence >= 0.5
            ]
            sequence_strong = [
                p for p in sequence if p.is_strong
            ]

            avg_t_conf = (
                sum(p.confidence for p in temporal)
                / len(temporal) if temporal else 0
            )
            avg_s_conf = (
                sum(p.confidence for p in sequence)
                / len(sequence) if sequence else 0
            )

            logger.info(
                "Pattern analysis: "
                "%d temporal (%d strong, avg=%.2f) + "
                "%d sequence (%d strong, avg=%.2f) + "
                "%d contextual "
                "in %.1fs",
                len(temporal), len(temporal_strong),
                avg_t_conf,
                len(sequence), len(sequence_strong),
                avg_s_conf,
                len(contextual),
                elapsed,
            )

            # Проверка смены привычек — раз в сутки
            now = time.time()
            if now - self._last_drift_check > 86400:
                try:
                    self._habit_drifts = detect_drift(
                        self._storage,
                        days=self.ANALYSIS_DAYS,
                    )
                    self._last_drift_check = now
                except Exception as e:
                    logger.debug("Drift detection error: %s", e)

            # Топ-3 temporal для отладки
            for p in temporal_strong[:3]:
                logger.debug(
                    "  T: %s ~%s (%s) reg=%.2f "
                    "rec=%.2f conf=%.3f",
                    p.target, p.peak_time_str,
                    p.day_type, p.regularity,
                    p.recency, p.confidence,
                )

        except Exception as e:
            logger.error(
                "Pattern analysis error: %s",
                e, exc_info=True,
            )

    def _ensure_analyzed(self) -> None:
        if self._last_analysis == 0:
            self._run_analysis()