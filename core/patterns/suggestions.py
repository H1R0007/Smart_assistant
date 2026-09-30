# core/patterns/suggestions.py

"""
Управление предложениями пользователю v2.

Ключевые улучшения:
1. Взвешенное сравнение контекстов (не бинарное)
2. Экспоненциальный штраф с накоплением similarity
3. Двухуровневые объяснения (brief + detailed)
4. Поддержка ignored-статуса
5. Параметризованный порог confidence (для idle-режима)
6. Снимок контекста при создании и в feedback

Принципы:
- Precision > Recall — лучше промолчать, чем предложить не то
- Адаптивность — каждый отказ в контексте снижает future confidence
- Объяснимость — "почему ты это предложил?" (два уровня)

Антипаттерны:
- Clippy: предлагать очевидное
- Alert fatigue: слишком часто → пользователь игнорирует ВСЁ
- Context-blind: предлагать то же после отказа в том же контексте
"""

import json
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from core.patterns.session_classifier import (
    classify_current_session,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Типы и модели
# ═══════════════════════════════════════════

class SuggestionType(Enum):
    ROUTINE = "routine"
    SEQUENCE = "sequence"
    AUTOMATION = "automation"


class SuggestionStatus(Enum):
    PENDING = "pending"
    SHOWN = "shown"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IGNORED = "ignored"


@dataclass
class Suggestion:
    """Предложение пользователю."""
    id: str
    type: SuggestionType
    text: str                         # Основной вопрос для озвучки
    brief_explanation: str = ""       # Краткое "почему" (1-й уровень)
    explanation: str = ""             # Детальная статистика (2-й уровень)
    action: str = ""                  # open_app, run_macro, create_macro
    action_params: dict = field(default_factory=dict)
    confidence: float = 0.0
    source_pattern_id: str = ""
    status: SuggestionStatus = SuggestionStatus.PENDING
    created_at: float = 0.0
    shown_at: float = 0.0
    resolved_at: float = 0.0
    context_snapshot: dict = field(default_factory=dict)


# ═══════════════════════════════════════════
#  Утилиты контекста
# ═══════════════════════════════════════════

def _get_time_slot(hour: int) -> str:
    """
    Временной слот для сравнения контекстов.

    4 слота по 5-6 часов — достаточно грубо,
    чтобы утренний отказ не блокировал дневное предложение,
    но достаточно точно для паттернов.
    """
    if 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 22:
        return "evening"
    else:
        return "night"


_TIME_SLOT_LABELS = {
    "morning": "утром",
    "afternoon": "днём",
    "evening": "вечером",
    "night": "ночью",
}


# ═══════════════════════════════════════════
#  Менеджер предложений
# ═══════════════════════════════════════════

class SuggestionManager:
    """
    Генерация, фильтрация и throttling предложений.

    Использование:
        mgr = SuggestionManager(storage)

        # После команды или при idle
        suggestion = mgr.check(action, target, temporal, sequence)

        if suggestion:
            speak(suggestion.text)
            # "почему?" → suggestion.brief_explanation
            # "подробнее?" → suggestion.explanation
            mgr.record_feedback(suggestion.id, accepted=True)
    """

    # ── Лимиты ──
    MAX_PER_HOUR = 2
    MIN_INTERVAL = 300           # 5 мин между предложениями
    REJECTION_COOLDOWN = 86400   # 24ч после отказа
    IGNORE_COOLDOWN = 3600       # 1ч после игнора

    # ── Confidence ──
    MIN_SHOW_CONFIDENCE = 0.5    # Порог по умолчанию
    TIMING_BONUS = 0.15          # Бонус для temporal паттернов

    # ── Контекстные отказы ──
    CONTEXT_REJECTION_HOURS = 24
    CONTEXT_PENALTY_FACTOR = 0.5  # База экспоненциального штрафа
    CONTEXT_REJECTION_LIMIT = 3.0 # Порог accumulated similarity для блока

    def __init__(self, storage=None):
        self._storage = storage
        self._recent: list[Suggestion] = []
        self._last_shown_time: float = 0

        # pattern_id → unix timestamp истечения cooldown'а
        self._cooldowns: dict[str, float] = {}

        # Трекинг последнего активного приложения
        self._last_active_app: str = ""

        self._load_cooldowns()

    # ══════════════════════════════════════════════
    #  Инициализация
    # ══════════════════════════════════════════════

    def _load_cooldowns(self) -> None:
        """Загрузить активные cooldown'ы из БД при старте."""
        if not self._storage:
            return
        try:
            now = time.time()
            rejected = self._storage.get_rejected_patterns(
                hours=self.REJECTION_COOLDOWN / 3600,
            )
            for pid, ts in rejected:
                expiry = ts + self.REJECTION_COOLDOWN
                if expiry > now:
                    self._cooldowns[pid] = expiry

            if self._cooldowns:
                logger.debug(
                    "Loaded %d active cooldowns", len(self._cooldowns),
                )
        except Exception as e:
            logger.debug("Could not load cooldowns: %s", e)

    # ══════════════════════════════════════════════
    #  Главный метод
    # ══════════════════════════════════════════════

    def check(
        self,
        current_action: str,
        current_target: str,
        temporal_patterns: list = None,
        sequence_patterns: list = None,
        contextual_patterns: list = None,
        macro_generator=None,
        min_confidence: float | None = None,
    ) -> Suggestion | None:
        """
        Проверить, стоит ли что-то предложить.

        Порядок:
        1. Захват контекста
        2. Сбор кандидатов (cooldown-фильтрация)
        3. Контекстная фильтрация (штраф confidence)
        4. Выбор лучшего
        5. Порог confidence
        6. Rate limit (только для показа)
        7. Показ

        Args:
            min_confidence: Переопределить MIN_SHOW_CONFIDENCE.
                Используется PatternEngine.check_idle()
                для повышенного порога.
        """
        now = time.time()
        threshold = (
            min_confidence
            if min_confidence is not None
            else self.MIN_SHOW_CONFIDENCE
        )

        # Обновляем трекинг активного приложения
        if current_action == "app_switch" and current_target:
            self._last_active_app = current_target

        # 1. Контекст
        context = self._capture_context(current_action, current_target)

        # 2. Кандидаты
        candidates: list[Suggestion] = []

        # Последовательности
        if sequence_patterns:
            for sp in sequence_patterns:
                if not sp.is_strong:
                    continue
                if sp.antecedent[-1] != current_target:
                    continue
                pid = f"seq_{sp.description}"
                if self._is_on_cooldown(pid):
                    continue
                suggestion = self._make_sequence(
                    sp, pid, context,
                )
                if suggestion is not None:
                    candidates.append(suggestion)

        # Временные
        if temporal_patterns:
            for tp in temporal_patterns:
                if not tp.is_active_now():
                    continue
                if tp.target == current_target:
                    continue
                pid = f"tmp_{tp.target}_{tp.peak_minute}"
                if self._is_on_cooldown(pid):
                    continue
                suggestion = self._make_temporal(
                    tp, pid, context,
                )
                if suggestion is not None:
                    candidates.append(suggestion)
        
        # Контекстно-зависимые последовательности
        if contextual_patterns:
            for cp in contextual_patterns:
                if not cp.is_strong:
                    continue
                if not cp.is_active_now():
                    continue
                if cp.antecedent[-1] != current_target:
                    continue
                pid = f"ctx_{cp.description}"
                if self._is_on_cooldown(pid):
                    continue
                suggestion = self._make_contextual(
                    cp, pid, context,
                )
                if suggestion is not None:
                    candidates.append(suggestion)

        # Автоматизация
        if macro_generator and sequence_patterns:
            auto_candidate = macro_generator.find_candidate(
                sequence_patterns, temporal_patterns,
            )
            if auto_candidate:
                candidates.append(Suggestion(
                    id=f"auto_{int(now)}",
                    type=SuggestionType.AUTOMATION,
                    text=auto_candidate["proposal_text"],
                    brief_explanation=(
                        "Я заметила повторяющуюся "
                        "последовательность действий"
                    ),
                    explanation=auto_candidate["explanation"],
                    action="create_macro",
                    action_params={"macro": auto_candidate["macro"]},
                    confidence=auto_candidate["confidence"],
                    source_pattern_id=auto_candidate["pattern_id"],
                    created_at=now,
                    context_snapshot=context,
                ))

        if not candidates:
            return None

        # 3. Контекстная фильтрация
        for candidate in candidates:
            penalty = self._get_contextual_penalty(
                candidate.source_pattern_id, context,
            )
            candidate.confidence *= penalty

        # 4. Лучший кандидат
        candidates.sort(key=lambda s: s.confidence, reverse=True)
        best = candidates[0]

        # 5. Порог confidence
        if best.confidence < threshold:
            return None

        # 6. Rate limit (только для показа)
        if now - self._last_shown_time < self.MIN_INTERVAL:
            return None

        max_per_hour = self._get_dynamic_max_per_hour()
        recent_shown = sum(
            1 for s in self._recent
            if s.shown_at > 0 and now - s.shown_at < 3600
        )
        if recent_shown >= max_per_hour:
            return None

        # 7. Показ
        best.status = SuggestionStatus.SHOWN
        best.shown_at = now
        self._recent.append(best)
        self._last_shown_time = now

        # Чистим старые (> 24ч)
        self._recent = [
            s for s in self._recent if now - s.created_at < 86400
        ]

        logger.info(
            "Suggestion [%.2f]: %s", best.confidence, best.text,
        )
        return best

    # ══════════════════════════════════════════════
    #  Контекст
    # ══════════════════════════════════════════════

    def _capture_context(
        self, current_action: str, current_target: str,
    ) -> dict:
        """
        Снимок текущего контекста.

        Используется:
        - При создании предложения (сохраняется в context_snapshot)
        - При записи feedback (сохраняется в БД)
        - При сравнении с прошлыми отказами
        """
        now = time.localtime()

        last_command = ""
        last_command_target = ""
        try:
            from core.context import get_context
            ctx = get_context().get()
            if ctx.is_fresh:
                last_command = ctx.action
                last_command_target = ctx.target
        except Exception:
            pass

        return {
            "timestamp": time.time(),
            "hour": now.tm_hour,
            "minute": now.tm_min,
            "weekday": now.tm_wday,
            "time_slot": _get_time_slot(now.tm_hour),
            "last_active_app": self._last_active_app,
            "current_action": current_action,
            "current_target": current_target,
            "last_command": last_command,
            "last_command_target": last_command_target,
        }

    # ══════════════════════════════════════════════
    #  Контекстные отказы (взвешенный подход)
    # ══════════════════════════════════════════════

    def _get_contextual_penalty(
        self, pattern_id: str, current_context: dict,
    ) -> float:
        """
        Штраф confidence на основе прошлых отказов
        в похожем контексте.

        Алгоритм:
        1. Загружаем контексты прошлых отказов для pattern_id
        2. Для каждого считаем similarity (0.0 - 1.0)
        3. Суммируем similarity → accumulated
        4. penalty = FACTOR ^ accumulated

        Примеры:
        - 1 полное совпадение (1.0)    → 0.5^1.0 = 0.50
        - 1 частичное (0.5)            → 0.5^0.5 ≈ 0.71
        - 2 полных (2.0)               → 0.5^2.0 = 0.25
        - 2 частичных (1.0)            → 0.5^1.0 = 0.50
        - 3 полных (3.0)               → заблокировано (0.0)

        Returns: 1.0 (без штрафа) ... 0.0 (заблокировано)
        """
        if not self._storage:
            return 1.0

        try:
            rejection_contexts = self._storage.get_contextual_rejections(
                pattern_id=pattern_id,
                hours=self.CONTEXT_REJECTION_HOURS,
            )
        except Exception as e:
            logger.debug("Contextual rejection query error: %s", e)
            return 1.0

        if not rejection_contexts:
            return 1.0

        # Накопление взвешенной похожести
        accumulated = 0.0
        for ctx_json in rejection_contexts:
            try:
                prev_ctx = json.loads(ctx_json) if ctx_json else {}
            except (json.JSONDecodeError, TypeError):
                continue

            similarity = self._context_similarity(
                current_context, prev_ctx,
            )
            accumulated += similarity

        if accumulated >= self.CONTEXT_REJECTION_LIMIT:
            logger.debug(
                "Pattern '%s' blocked: accumulated similarity %.2f >= %.1f",
                pattern_id, accumulated, self.CONTEXT_REJECTION_LIMIT,
            )
            return 0.0

        if accumulated > 0:
            penalty = self.CONTEXT_PENALTY_FACTOR ** accumulated
            logger.debug(
                "Pattern '%s' penalized: %.3f "
                "(accumulated similarity %.2f)",
                pattern_id, penalty, accumulated,
            )
            return penalty

        return 1.0

    @staticmethod
    def _context_similarity(current: dict, previous: dict) -> float:
        """
        Взвешенная похожесть двух контекстов.

        Три фактора сравнения:
        - time_slot: morning/afternoon/evening/night
        - last_active_app: имя приложения
        - weekday_type: будни/выходные (NEW)

        Completeness weight: если доступен только 1 фактор
        из 3 возможных, similarity домножается на коэффициент
        неполноты. Это предотвращает ложную блокировку
        в idle-режиме, где last_active_app часто пуст.

        Weights:
        - 1 фактор: × 0.4
        - 2 фактора: × 0.7
        - 3 фактора: × 1.0

        Примеры:
          morning/VS Code/wd  vs  morning/VS Code/wd  → 1.0
          morning/VS Code/wd  vs  morning/Chrome/wd   → 0.47
          morning/""/wd       vs  morning/""/wd        → 0.70
          morning/""          vs  morning/""           → 0.40
        """
        if not previous:
            return 0.0

        factors: list[float] = []

        # Фактор 1: временной слот
        cur_slot = current.get("time_slot", "")
        prev_slot = previous.get("time_slot", "")
        if cur_slot and prev_slot:
            factors.append(
                1.0 if cur_slot == prev_slot else 0.0
            )

        # Фактор 2: последнее активное приложение
        cur_app = current.get("last_active_app", "")
        prev_app = previous.get("last_active_app", "")
        if cur_app and prev_app:
            factors.append(
                1.0 if cur_app == prev_app else 0.0
            )

        # Фактор 3: тип дня (будни/выходные)
        cur_wd = current.get("weekday", -1)
        prev_wd = previous.get("weekday", -1)
        if cur_wd >= 0 and prev_wd >= 0:
            cur_type = "weekday" if cur_wd < 5 else "weekend"
            prev_type = (
                "weekday" if prev_wd < 5 else "weekend"
            )
            factors.append(
                1.0 if cur_type == prev_type else 0.0
            )

        if not factors:
            return 0.0

        # Completeness weight: чем меньше факторов
        # доступно, тем менее уверены в сравнении.
        _COMPLETENESS = {1: 0.4, 2: 0.7, 3: 1.0}
        completeness = _COMPLETENESS.get(
            len(factors), 0.3,
        )

        raw_similarity = sum(factors) / len(factors)
        return raw_similarity * completeness

    # ══════════════════════════════════════════════
    #  Формирование предложений
    # ══════════════════════════════════════════════

    def _make_sequence(
        self, pattern, pid: str, context: dict,
    ) -> Suggestion:
        # Не предлагать открыть приложение, которое
        # пользователь только что сам открыл
        if (context.get("last_active_app", "").lower()
                == pattern.consequent.lower()):
            return None

        ant = " и ".join(pattern.antecedent)
        return Suggestion(
            id=f"{pid}_{int(time.time())}",
            type=SuggestionType.SEQUENCE,
            text=(
                f"Обычно после {ant} ты открываешь "
                f"{pattern.consequent}. Открыть?"
            ),
            brief_explanation=(
                f"Потому что обычно после {ant} "
                f"ты переходишь к {pattern.consequent}"
            ),
            explanation=(
                f"За последние 2 недели ты {pattern.occurrences} раз "
                f"открывал {pattern.consequent} после {ant} "
                f"(в {int(pattern.confidence * 100)}% случаев)."
            ),
            action="open_app",
            action_params={"target": pattern.consequent},
            confidence=pattern.confidence * min(pattern.lift / 3, 1.0),
            source_pattern_id=pid,
            created_at=time.time(),
            context_snapshot=context,
        )

    def _make_temporal(
        self, pattern, pid: str, context: dict,
    ) -> Suggestion:
        # Не предлагать открыть то, что уже активно
        if (context.get("last_active_app", "").lower()
                == pattern.target.lower()):
            return None

        conf = min(pattern.confidence + self.TIMING_BONUS, 1.0)
        time_slot = context.get("time_slot", "")
        slot_label = _TIME_SLOT_LABELS.get(time_slot, "в это время")

        return Suggestion(
            id=f"{pid}_{int(time.time())}",
            type=SuggestionType.ROUTINE,
            text=(
                f"Обычно около {pattern.peak_time_str} ты открываешь "
                f"{pattern.target}. Открыть?"
            ),
            brief_explanation=(
                f"Потому что обычно {slot_label} "
                f"ты работаешь в {pattern.target}"
            ),
            explanation=(
                f"За 2 недели ты открывал {pattern.target} "
                f"около {pattern.peak_time_str} "
                f"в {pattern.days_observed} днях "
                f"({pattern.day_type})."
            ),
            action="open_app",
            action_params={"target": pattern.target},
            confidence=conf,
            source_pattern_id=pid,
            created_at=time.time(),
            context_snapshot=context,
        )
    
    def _make_contextual(
        self, pattern, pid: str, context: dict,
    ) -> Suggestion | None:
        """Создать предложение из контекстного паттерна."""
        # Не предлагать то, что уже открыто
        if (context.get("last_active_app", "").lower()
                == pattern.consequent.lower()):
            return None

        slot_label = pattern.slot_label
        ant = " и ".join(pattern.antecedent)

        # confidence = slot_confidence × boost_factor
        boost_factor = min(pattern.context_boost / 3, 1.0)
        conf = pattern.confidence * boost_factor

        return Suggestion(
            id=f"{pid}_{int(time.time())}",
            type=SuggestionType.SEQUENCE,
            text=(
                f"Обычно {slot_label} после {ant} "
                f"ты открываешь {pattern.consequent}. "
                f"Открыть?"
            ),
            brief_explanation=(
                f"Потому что {slot_label} после {ant} "
                f"ты обычно переходишь "
                f"к {pattern.consequent}"
            ),
            explanation=(
                f"За 2 недели ты {pattern.occurrences} раз "
                f"открывал {pattern.consequent} после "
                f"{ant} именно {slot_label} "
                f"(в {int(pattern.confidence * 100)}% "
                f"случаев, что в {pattern.context_boost}x "
                f"чаще чем в среднем)."
            ),
            action="open_app",
            action_params={"target": pattern.consequent},
            confidence=conf,
            source_pattern_id=pid,
            created_at=time.time(),
            context_snapshot=context,
        )

    # ══════════════════════════════════════════════
    #  Обратная связь
    # ══════════════════════════════════════════════

    def record_feedback(
        self,
        suggestion_id: str,
        *,
        accepted: bool = False,
        ignored: bool = False,
    ) -> None:
        """
        Записать реакцию пользователя.

        Три исхода:
        - accepted=True  → cooldown снимается
        - accepted=False → cooldown 24ч, контекст в БД
        - ignored=True   → cooldown 1ч
        """
        now = time.time()

        for s in self._recent:
            if s.id != suggestion_id:
                continue

            s.resolved_at = now

            if ignored:
                s.status = SuggestionStatus.IGNORED
                status_str = "ignored"
                self._cooldowns[s.source_pattern_id] = (
                    now + self.IGNORE_COOLDOWN
                )
                logger.info("Ignored: %s", s.text)

            elif accepted:
                s.status = SuggestionStatus.ACCEPTED
                status_str = "accepted"
                self._cooldowns.pop(s.source_pattern_id, None)
                logger.info("Accepted: %s", s.text)

            else:
                s.status = SuggestionStatus.REJECTED
                status_str = "rejected"
                self._cooldowns[s.source_pattern_id] = (
                    now + self.REJECTION_COOLDOWN
                )
                logger.info("Rejected: %s", s.text)

            if self._storage:
                try:
                    self._storage.add_suggestion_feedback(
                        suggestion_id=s.id,
                        suggestion_type=s.type.value,
                        pattern_id=s.source_pattern_id,
                        status=status_str,
                        confidence=s.confidence,
                        context=json.dumps(
                            s.context_snapshot, ensure_ascii=False,
                        ),
                        shown_at=s.shown_at,
                    )
                except Exception as e:
                    logger.debug("Feedback save error: %s", e)

            break

    # ══════════════════════════════════════════════
    #  Cooldown
    # ══════════════════════════════════════════════

    def _get_dynamic_max_per_hour(self) -> int:
        """
        Адаптивный rate limit на основе типа сессии.

        focus     → 1/час (не прерывать глубокую работу)
        ritual    → 3/час (ритуал — хорошее время для предложений)
        browse    → 3/час (пассивный просмотр, можно предложить)
        comm      → 2/час (общение, средний приоритет)
        multitask → 2/час (стандарт)
        нет данных → MAX_PER_HOUR (дефолт)
        """
        if not self._storage:
            return self.MAX_PER_HOUR

        try:
            profile = classify_current_session(self._storage)
            if profile is None:
                return self.MAX_PER_HOUR

            limits = {
                "focus": 1,
                "ritual": 3,
                "browse": 3,
                "communication": 2,
                "multitask": 2,
                "unknown": self.MAX_PER_HOUR,
            }
            return limits.get(
                profile.session_type, self.MAX_PER_HOUR,
            )

        except Exception:
            return self.MAX_PER_HOUR

    def _is_on_cooldown(self, pattern_id: str) -> bool:
        """Проверить cooldown. Автоочистка истёкших."""
        expiry = self._cooldowns.get(pattern_id, 0)
        if time.time() >= expiry:
            self._cooldowns.pop(pattern_id, None)
            return False
        return True
