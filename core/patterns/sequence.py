# core/patterns/sequence.py

"""
Анализ последовательностей действий.

Находит паттерны "после A часто делают B" и "A → B → C".

Алгоритм:
  1. Разбить activity_log на сессии (gap > 5 мин → новая)
  2. В каждой сессии — скользящее окно для пар (A→B)
  3. Посчитать support, confidence, lift
  4. Тройки — только из сильных пар (отсекает 95% кандидатов)

Почему не PrefixSpan:
  50 приложений × длина 5 = миллионы кандидатов.
  Пары + тройки покрывают 95% полезных предложений.

Почему скользящее окно в 4 шага:
  Между "VS Code" и "Chrome для документации" может быть
  2–3 промежуточных переключения (терминал, проводник).
"""

import time
import logging
from dataclasses import dataclass
from collections import defaultdict, Counter

logger = logging.getLogger(__name__)


@dataclass
class SequencePattern:
    """Паттерн последовательности действий."""
    antecedent: list[str]       # ["Chrome"] или ["VS Code", "Chrome"]
    consequent: str             # "Stack Overflow"
    action: str                 # "app_switch"
    support: float              # Доля сессий с паттерном (0–1)
    confidence: float           # P(consequent | antecedent) (0–1)
    lift: float                 # confidence / P(consequent). >1 = связь
    occurrences: int            # Абсолютное число
    avg_gap_seconds: float      # Среднее время между A и B

    @property
    def description(self) -> str:
        return " → ".join(self.antecedent) + f" → {self.consequent}"

    @property
    def is_strong(self) -> bool:
        return (
            self.confidence >= 0.5
            and self.lift >= 1.5
            and self.occurrences >= 3
        )


class SequenceAnalyzer:
    """
    Обнаружение последовательных паттернов.

    Использование:
        analyzer = SequenceAnalyzer(storage)
        patterns = analyzer.analyze(days=14)
    """

    SESSION_GAP = 300           # 5 мин — порог новой сессии
    SEQUENCE_WINDOW = 4         # Окно поиска связей (шаги)
    MIN_SUPPORT = 0.1           # Мин. доля сессий с паттерном
    MIN_CONFIDENCE = 0.3        # Мин. P(B|A)
    MIN_LIFT = 1.2              # Мин. lift (1 = случайность)
    MIN_OCCURRENCES = 3         # Мин. вхождений
    MAX_UNIQUE_TARGETS = 100    # Защита от комбинаторного взрыва

    def __init__(self, storage):
        self._storage = storage

    def analyze(
        self,
        days: int = 14,
        action: str = "app_switch",
        max_sequence_length: int = 2,
    ) -> list[SequencePattern]:
        """
        Найти последовательные паттерны.

        Args:
            days: период
            action: тип действия
            max_sequence_length: макс. длина antecedent (1=пары, 2=тройки)
        """
        records = self._storage.get_activity(
            hours=days * 24, action=action, limit=50000,
        )

        if len(records) < self.MIN_OCCURRENCES * 2:
            return []

        sessions = self._split_into_sessions(records)
        logger.debug(
            "SequenceAnalyzer: %d записей → %d сессий",
            len(records), len(sessions),
        )

        if not sessions:
            return []

        # Ограничиваем уникальные targets
        sessions = self._limit_targets(sessions)
        sessions = [s for s in sessions if len(s) >= 2]

        if not sessions:
            return []

        total_sessions = len(sessions)
        target_freq = self._target_frequencies(sessions, total_sessions)

        # Пары
        patterns = self._find_pairs(sessions, total_sessions, target_freq, action)

        # Тройки (только из сильных пар — оптимизация)
        if max_sequence_length >= 2 and patterns:
            triples = self._find_triples(
                sessions, total_sessions, target_freq, action,
            )
            patterns.extend(triples)

        # Фильтрация
        patterns = [
            p for p in patterns
            if (p.confidence >= self.MIN_CONFIDENCE
                and p.lift >= self.MIN_LIFT
                and p.occurrences >= self.MIN_OCCURRENCES)
        ]

        patterns.sort(key=lambda p: p.confidence * p.lift, reverse=True)
        logger.info("SequenceAnalyzer: %d паттернов для '%s'", len(patterns), action)
        return patterns

    # ══════════════════════════════════════════════
    #  Сессии
    # ══════════════════════════════════════════════

    def _split_into_sessions(self, records: list) -> list[list]:
        """Разбить записи на сессии по временным разрывам."""
        if not records:
            return []

        # get_activity возвращает DESC → переворачиваем
        sorted_recs = sorted(records, key=lambda r: r.timestamp)

        sessions = []
        current = [sorted_recs[0]]

        for i in range(1, len(sorted_recs)):
            gap = sorted_recs[i].timestamp - sorted_recs[i - 1].timestamp
            if gap > self.SESSION_GAP:
                if len(current) >= 2:
                    sessions.append(current)
                current = []
            current.append(sorted_recs[i])

        if len(current) >= 2:
            sessions.append(current)

        return sessions

    def _limit_targets(self, sessions: list[list]) -> list[list]:
        """Оставить только топ-N targets по частоте."""
        all_targets = Counter(
            r.target for session in sessions for r in session if r.target
        )
        if len(all_targets) <= self.MAX_UNIQUE_TARGETS:
            return sessions

        top = {t for t, _ in all_targets.most_common(self.MAX_UNIQUE_TARGETS)}
        return [
            [r for r in session if r.target in top]
            for session in sessions
        ]

    @staticmethod
    def _target_frequencies(
        sessions: list[list], total: int,
    ) -> dict[str, float]:
        """P(target) = в какой доле сессий встречается."""
        by_target: dict[str, set] = defaultdict(set)
        for i, session in enumerate(sessions):
            for r in session:
                if r.target:
                    by_target[r.target].add(i)
        return {
            t: len(sids) / total for t, sids in by_target.items()
        }

    # ══════════════════════════════════════════════
    #  Поиск пар (A → B)
    # ══════════════════════════════════════════════

    def _find_pairs(
        self, sessions, total_sessions, target_freq, action,
    ) -> list[SequencePattern]:
        """Найти пары с помощью скользящего окна."""
        pair_counts: Counter = Counter()
        pair_sessions: dict[tuple, set] = defaultdict(set)
        antecedent_counts: Counter = Counter()
        pair_gaps: dict[tuple, list] = defaultdict(list)

        for sess_idx, session in enumerate(sessions):
            targets = [r.target for r in session]
            timestamps = [r.timestamp for r in session]

            for i, a in enumerate(targets):
                if not a:
                    continue

                end = min(i + 1 + self.SEQUENCE_WINDOW, len(targets))
                seen = set()
                found_any = False

                for j in range(i + 1, end):
                    b = targets[j]
                    if not b or b == a or b in seen:
                        continue
                    seen.add(b)
                    found_any = True

                    pair = (a, b)
                    pair_counts[pair] += 1
                    pair_sessions[pair].add(sess_idx)
                    pair_gaps[pair].append(timestamps[j] - timestamps[i])

                # Считаем antecedent только если в окне были
                # кандидаты. Без этого элементы в конце сессии
                # (где нет следующих) завышают знаменатель
                # confidence, занижая её на 15-20%.
                if found_any:
                    antecedent_counts[a] += 1

        patterns = []
        for (a, b), count in pair_counts.items():
            support = len(pair_sessions[(a, b)]) / total_sessions
            if support < self.MIN_SUPPORT:
                continue

            ant_total = antecedent_counts[a]
            confidence = count / ant_total if ant_total else 0
            p_b = target_freq.get(b, 0.01)
            lift = confidence / p_b if p_b > 0 else 0

            gaps = pair_gaps[(a, b)]
            avg_gap = sum(gaps) / len(gaps) if gaps else 0

            patterns.append(SequencePattern(
                antecedent=[a], consequent=b, action=action,
                support=round(support, 3),
                confidence=round(confidence, 3),
                lift=round(lift, 2),
                occurrences=count,
                avg_gap_seconds=round(avg_gap, 1),
            ))

        return patterns

    # ══════════════════════════════════════════════
    #  Поиск троек (A → B → C)
    # ══════════════════════════════════════════════

    def _find_triples(
        self, sessions, total_sessions, target_freq, action,
    ) -> list[SequencePattern]:
        """Тройки только из сильных пар — отсекает 95% кандидатов."""
        # Сначала находим сильные пары
        pairs = self._find_pairs(sessions, total_sessions, target_freq, action)
        strong_pairs = {
            (p.antecedent[0], p.consequent)
            for p in pairs
            if p.confidence >= self.MIN_CONFIDENCE * 0.8
        }

        if not strong_pairs:
            return []

        triple_counts: Counter = Counter()
        triple_sessions: dict[tuple, set] = defaultdict(set)
        ab_counts: Counter = Counter()

        for sess_idx, session in enumerate(sessions):
            targets = [r.target for r in session]

            for i in range(len(targets)):
                a = targets[i]
                if not a:
                    continue

                end_j = min(i + 1 + self.SEQUENCE_WINDOW, len(targets))
                for j in range(i + 1, end_j):
                    b = targets[j]
                    if not b or b == a or (a, b) not in strong_pairs:
                        continue

                    ab_counts[(a, b)] += 1

                    end_k = min(j + 1 + self.SEQUENCE_WINDOW, len(targets))
                    seen = set()
                    for k in range(j + 1, end_k):
                        c = targets[k]
                        if not c or c in (a, b) or c in seen:
                            continue
                        seen.add(c)

                        triple = (a, b, c)
                        triple_counts[triple] += 1
                        triple_sessions[triple].add(sess_idx)

        patterns = []
        for (a, b, c), count in triple_counts.items():
            support = len(triple_sessions[(a, b, c)]) / total_sessions
            if support < self.MIN_SUPPORT:
                continue

            ab_total = ab_counts.get((a, b), 1)
            confidence = count / ab_total
            p_c = target_freq.get(c, 0.01)
            lift = confidence / p_c if p_c > 0 else 0

            patterns.append(SequencePattern(
                antecedent=[a, b], consequent=c, action=action,
                support=round(support, 3),
                confidence=round(confidence, 3),
                lift=round(lift, 2),
                occurrences=count,
                avg_gap_seconds=0,
            ))

        return patterns