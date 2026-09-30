# core/patterns/temporal_sequence.py

"""
Контекстно-зависимые последовательности.

Обычный SequenceAnalyzer находит:
  "После Chrome часто Telegram" (confidence 0.33)
  — слабый паттерн, потому что Chrome→VS Code днём
    и Chrome→Discord вечером "разбавляют".

TemporalSequenceAnalyzer находит:
  "После Chrome УТРОМ — Telegram" (confidence 0.80)
  "После Chrome ДНЁМ — VS Code" (confidence 0.75)
  "После Chrome ВЕЧЕРОМ — Discord" (confidence 0.70)

Алгоритм:
1. Разбиваем сессии на временные слоты (morning/afternoon/...)
2. Считаем пары ВНУТРИ каждого слота
3. Сравниваем с глобальным confidence
4. Если контекстный >> глобального → контекстный паттерн

Метрика context_boost = slot_confidence / global_confidence.
Паттерн интересен если boost >= 1.5 (слот важен).
"""

import time
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _time_slot(ts: float) -> str:
    """Временной слот для timestamp."""
    hour = time.localtime(ts).tm_hour
    if 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 22:
        return "evening"
    return "night"


_SLOT_LABELS = {
    "morning": "утром",
    "afternoon": "днём",
    "evening": "вечером",
    "night": "ночью",
}


@dataclass
class ContextualPattern:
    """Паттерн, привязанный к временному контексту."""
    antecedent: list[str]     # ["Chrome"]
    consequent: str           # "Telegram"
    time_slot: str            # "morning"
    confidence: float         # Внутри слота
    global_confidence: float  # Без учёта слота
    lift: float               # Внутри слота
    occurrences: int          # Сколько раз в этом слоте
    context_boost: float      # confidence / global_confidence

    @property
    def description(self) -> str:
        slot = _SLOT_LABELS.get(self.time_slot, self.time_slot)
        chain = " → ".join(self.antecedent)
        return f"{chain} → {self.consequent} ({slot})"

    @property
    def slot_label(self) -> str:
        return _SLOT_LABELS.get(self.time_slot, self.time_slot)

    @property
    def is_strong(self) -> bool:
        return (
            self.confidence >= 0.5
            and self.context_boost >= 1.5
            and self.occurrences >= 3
        )

    def is_active_now(self) -> bool:
        """Текущее время попадает в слот паттерна."""
        return _time_slot(time.time()) == self.time_slot


class TemporalSequenceAnalyzer:
    """
    Обнаружение контекстно-зависимых последовательностей.

    Использование:
        analyzer = TemporalSequenceAnalyzer(storage)
        patterns = analyzer.analyze(days=14)
    """

    SEQUENCE_WINDOW = 4
    SESSION_GAP = 300         # 5 мин
    MIN_CONFIDENCE = 0.4      # Внутри слота
    MIN_CONTEXT_BOOST = 1.5   # Слот должен быть важен
    MIN_OCCURRENCES = 3

    def __init__(self, storage):
        self._storage = storage

    def analyze(
        self,
        days: int = 14,
        action: str = "app_switch",
    ) -> list[ContextualPattern]:
        """Найти контекстно-зависимые паттерны."""
        records = self._storage.get_activity(
            hours=days * 24, action=action, limit=50000,
        )
        if len(records) < 10:
            return []

        sessions = self._split_sessions(records)
        if not sessions:
            return []

        # Аннотируем сессии временным слотом
        # (по первой записи сессии)
        slotted: dict[str, list[list]] = defaultdict(list)
        for session in sessions:
            slot = _time_slot(session[0].timestamp)
            slotted[slot].append(session)

        # Глобальные пары (для сравнения)
        global_pairs = self._count_pairs(sessions)

        patterns: list[ContextualPattern] = []

        for slot, slot_sessions in slotted.items():
            if len(slot_sessions) < self.MIN_OCCURRENCES:
                continue

            slot_pairs = self._count_pairs(slot_sessions)

            # P(B) в этом слоте — для lift
            total_slot = len(slot_sessions)
            target_sessions: dict[str, int] = defaultdict(int)
            for s in slot_sessions:
                seen = set()
                for r in s:
                    if r.target and r.target not in seen:
                        target_sessions[r.target] += 1
                        seen.add(r.target)

            for (a, b), slot_data in slot_pairs.items():
                if slot_data["count"] < self.MIN_OCCURRENCES:
                    continue

                slot_conf = (
                    slot_data["count"]
                    / slot_data["ant_count"]
                    if slot_data["ant_count"] else 0
                )

                if slot_conf < self.MIN_CONFIDENCE:
                    continue

                # Глобальный confidence
                global_data = global_pairs.get((a, b))
                global_conf = (
                    global_data["count"]
                    / global_data["ant_count"]
                    if global_data
                    and global_data["ant_count"]
                    else 0.01
                )

                boost = (
                    slot_conf / global_conf
                    if global_conf > 0 else 10.0
                )

                if boost < self.MIN_CONTEXT_BOOST:
                    continue

                # Lift
                p_b = (
                    target_sessions.get(b, 0) / total_slot
                    if total_slot else 0.01
                )
                lift = slot_conf / p_b if p_b > 0 else 0

                patterns.append(ContextualPattern(
                    antecedent=[a],
                    consequent=b,
                    time_slot=slot,
                    confidence=round(slot_conf, 3),
                    global_confidence=round(
                        global_conf, 3,
                    ),
                    lift=round(lift, 2),
                    occurrences=slot_data["count"],
                    context_boost=round(boost, 2),
                ))

        patterns.sort(
            key=lambda p: p.confidence * p.context_boost,
            reverse=True,
        )

        logger.info(
            "TemporalSequenceAnalyzer: %d контекстных "
            "паттернов за %d дней",
            len(patterns), days,
        )
        return patterns

    def _count_pairs(
        self, sessions: list[list],
    ) -> dict[tuple, dict]:
        """Подсчёт пар с antecedent counts (R2-safe)."""
        pairs: dict[tuple, dict] = {}
        ant_counts: Counter = Counter()

        for session in sessions:
            targets = [r.target for r in session]
            for i, a in enumerate(targets):
                if not a:
                    continue
                end = min(
                    i + 1 + self.SEQUENCE_WINDOW,
                    len(targets),
                )
                seen = set()
                found = False
                for j in range(i + 1, end):
                    b = targets[j]
                    if not b or b == a or b in seen:
                        continue
                    seen.add(b)
                    found = True
                    key = (a, b)
                    if key not in pairs:
                        pairs[key] = {
                            "count": 0,
                            "ant_count": 0,
                        }
                    pairs[key]["count"] += 1
                if found:
                    ant_counts[a] += 1

        for key in pairs:
            pairs[key]["ant_count"] = ant_counts[key[0]]

        return pairs

    def _split_sessions(self, records: list) -> list[list]:
        """Разбить на сессии по временным паузам."""
        sorted_recs = sorted(
            records, key=lambda r: r.timestamp,
        )
        sessions = []
        current = [sorted_recs[0]]
        for i in range(1, len(sorted_recs)):
            gap = (
                sorted_recs[i].timestamp
                - sorted_recs[i - 1].timestamp
            )
            if gap > self.SESSION_GAP:
                if len(current) >= 2:
                    sessions.append(current)
                current = []
            current.append(sorted_recs[i])
        if len(current) >= 2:
            sessions.append(current)
        return sessions