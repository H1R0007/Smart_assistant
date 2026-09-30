# core/patterns/temporal.py

"""
Анализ временных паттернов пользователя.

Алгоритм:
1. Собираем timestamps действия X за N дней
2. Переводим в "минуты от полуночи" (0–1439)
3. Строим KDE с циркулярным гауссовым ядром (bandwidth=30 мин)
4. Находим пики — кандидаты в привычки
5. Для каждого пика считаем:
   - regularity: доля дней где действие было вблизи пика
   - recency:    экспоненциально затухающий вес (свежие важнее)
   - confidence: итоговая уверенность

Почему KDE, а не слоты:
  Слот 9:00–10:00 "разрезает" привычку 9:45–10:15.
  KDE автоматически находит центр и ширину пика.

Почему bandwidth=30 мин:
  < 15 → шумные пики от случайных совпадений
  > 60 → "утро" и "обед" сливаются
  30 ≈ естественная вариативность ("обычно в 9, иногда в 9:20")

Почему экспоненциальное затухание:
  Привычки меняются. Если пользователь перестал вставать в 7
  и встаёт в 9, линейное среднее скажет "8:00".
  Экспоненциальное (decay=0.1, полупериод ≈7 дней) — "ближе к 9".
"""

import math
import time
import logging
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class TemporalPattern:
    """Обнаруженный временной паттерн."""
    action: str                 # "app_switch", "command"
    target: str                 # "Chrome", "telegram"
    peak_minute: int            # Пик: минуты от полуночи (0–1439)
    bandwidth_minutes: float    # Ширина пика (σ)
    regularity: float           # Доля дней с действием вблизи пика (0–1)
    recency: float              # Свежесть (1.0=активно, 0.1=затухает)
    confidence: float           # Итоговая уверенность (0–1)
    day_type: str               # "weekday", "weekend"
    sample_count: int           # Событий в пике
    days_observed: int          # За сколько дней наблюдали

    @property
    def peak_time_str(self) -> str:
        h, m = divmod(self.peak_minute, 60)
        return f"{h:02d}:{m:02d}"

    @property
    def window_str(self) -> str:
        return f"{self.peak_time_str} ± {int(self.bandwidth_minutes)} мин."

    def is_active_now(self, tolerance_minutes: float = 15) -> bool:
        """Попадает ли текущий момент в окно паттерна."""
        now = time.localtime()
        now_minutes = now.tm_hour * 60 + now.tm_min
        diff = abs(now_minutes - self.peak_minute)
        diff = min(diff, 1440 - diff)  # циркулярное расстояние
        return diff <= self.bandwidth_minutes + tolerance_minutes


class TemporalAnalyzer:
    BANDWIDTH_MINUTES = 30
    RESOLUTION_MINUTES = 5
    MIN_REGULARITY = 0.4
    MIN_CONFIDENCE = 0.3
    MIN_EVENTS_IN_PEAK = 3
    RECENCY_DECAY = 0.1
    MIN_PEAK_RATIO = 0.15

    # Bayesian smoothing для regularity.
    # При малом количестве наблюдений стягивает regularity
    # к REGULARITY_PRIOR (агностичный 0.5).
    # При большом — приближается к чистой частоте hits/total.
    #
    # Формула: (hits + PRIOR_STRENGTH × PRIOR) / (N + PRIOR_STRENGTH)
    #
    # PRIOR_STRENGTH = 2:
    #   1 день:  1/1 → (1+1)/(1+2) = 0.67 (демпфирован)
    #   10 дней: 7/10 → (7+1)/(10+2) = 0.67 (почти без изменений)
    REGULARITY_PRIOR_STRENGTH = 2
    REGULARITY_PRIOR = 0.5

    def __init__(self, storage):
        self._storage = storage

    def analyze(
        self,
        days: int = 14,
        actions: list[str] | None = None,
    ) -> list[TemporalPattern]:
        """
        Найти временные паттерны за период.

        Returns:
            Паттерны, отсортированные по confidence DESC.
        """
        if actions is None:
            actions = ["app_switch", "command"]

        all_patterns = []

        for action in actions:
            records = self._storage.get_activity(
                hours=days * 24, action=action, limit=10000,
            )
            if not records:
                continue

            # Группируем по target
            by_target: dict[str, list] = defaultdict(list)
            for r in records:
                if r.target:
                    by_target[r.target].append(r)

            for target, target_records in by_target.items():
                weekday_recs = [
                    r for r in target_records if self._is_weekday(r.timestamp)
                ]
                weekend_recs = [
                    r for r in target_records if not self._is_weekday(r.timestamp)
                ]

                if len(weekday_recs) >= self.MIN_EVENTS_IN_PEAK:
                    patterns = self._find_patterns(
                        action, target, weekday_recs, "weekday", days,
                        all_records_for_action=records,
                    )
                    all_patterns.extend(patterns)

                if len(weekend_recs) >= self.MIN_EVENTS_IN_PEAK:
                    patterns = self._find_patterns(
                        action, target, weekend_recs, "weekend", days,
                        all_records_for_action=records,
                    )
                    all_patterns.extend(patterns)

        all_patterns.sort(key=lambda p: p.confidence, reverse=True)
        logger.info(
            "TemporalAnalyzer: %d паттернов за %d дней", len(all_patterns), days,
        )
        return all_patterns

    def _find_patterns(
        self, action: str, target: str, records: list,
        day_type: str, total_days: int,
        all_records_for_action: list = None,
    ) -> list[TemporalPattern]:
        """Найти пики KDE для конкретного (action, target, day_type)."""
        minutes = [self._timestamp_to_minutes(r.timestamp) for r in records]
        timestamps = [r.timestamp for r in records]

        if not minutes:
            return []

        # KDE
        density = self._kde(minutes)

        # Пики
        peaks = self._find_peaks(density)
        if not peaks:
            return []

        # Метрики для каждого пика
        now = time.time()

        # Адаптивный eligible_days: считаем фактические дни
        # с данными трекера, а не теоретические.
        # all_records_for_action — ВСЕ записи этого action
        # (не только target), чтобы определить "дни когда
        # трекер работал".
        source = all_records_for_action or records
        eligible_days = self._count_observed_days(source, day_type)
        patterns = []

        for peak_minute in peaks:
            window = self.BANDWIDTH_MINUTES * 2  # ±2σ

            events_near = [
                (m, ts) for m, ts in zip(minutes, timestamps)
                if self._circular_distance(m, peak_minute) <= window
            ]

            if len(events_near) < self.MIN_EVENTS_IN_PEAK:
                continue

            # Regularity: уникальные дни с событием вблизи пика
            days_with = set()
            for _, ts in events_near:
                days_with.add(time.strftime("%Y-%m-%d", time.localtime(ts)))

            regularity = self._adaptive_regularity(
                len(days_with), eligible_days,
            )
            if regularity < self.MIN_REGULARITY:
                continue

            # Recency: свежие события весят больше
            recency_weights = [
                math.exp(-self.RECENCY_DECAY * (now - ts) / 86400)
                for _, ts in events_near
            ]
            recency = sum(recency_weights) / len(recency_weights)

            # Volume: лог-масштаб (3 и 30 событий — не 10x разница)
            volume_factor = min(1.0, math.log2(len(events_near) + 1) / 5)

            confidence = regularity * recency * volume_factor
            if confidence < self.MIN_CONFIDENCE:
                continue

            # Уточняем bandwidth по реальному разбросу
            if len(events_near) >= 3:
                near_minutes = [m for m, _ in events_near]
                actual_std = self._circular_std(near_minutes, peak_minute)
            else:
                actual_std = float(self.BANDWIDTH_MINUTES)

            patterns.append(TemporalPattern(
                action=action,
                target=target,
                peak_minute=peak_minute,
                bandwidth_minutes=round(actual_std, 1),
                regularity=round(regularity, 3),
                recency=round(recency, 3),
                confidence=round(confidence, 3),
                day_type=day_type,
                sample_count=len(events_near),
                days_observed=len(days_with),
            ))

        return patterns

    # ══════════════════════════════════════════════
    #  KDE и поиск пиков
    # ══════════════════════════════════════════════

    def _kde(self, minutes: list[int]) -> list[float]:
        """
        Kernel Density Estimation с циркулярным гауссовым ядром.

        Циркулярность: 23:50 и 0:10 — расстояние 20 мин, не 1430.
        """
        n_bins = 1440 // self.RESOLUTION_MINUTES
        density = [0.0] * n_bins
        sigma = self.BANDWIDTH_MINUTES / self.RESOLUTION_MINUTES

        for minute in minutes:
            center_bin = minute / self.RESOLUTION_MINUTES
            radius = int(3 * sigma) + 1

            for offset in range(-radius, radius + 1):
                bin_idx = int(center_bin + offset) % n_bins
                weight = math.exp(-0.5 * (offset / sigma) ** 2)
                density[bin_idx] += weight

        total = sum(density)
        if total > 0:
            density = [d / total for d in density]

        return density

    def _find_peaks(self, density: list[float]) -> list[int]:
        """Локальные максимумы выше порога."""
        if not density:
            return []

        n = len(density)
        max_density = max(density)
        if max_density == 0:
            return []

        threshold = max_density * self.MIN_PEAK_RATIO
        peaks = []

        for i in range(n):
            if density[i] < threshold:
                continue
            prev_val = density[(i - 1) % n]
            next_val = density[(i + 1) % n]
            if density[i] >= prev_val and density[i] >= next_val:
                # Не добавлять пики слишком близко друг к другу
                peak_min = i * self.RESOLUTION_MINUTES
                if not peaks or self._circular_distance(peak_min, peaks[-1]) > self.BANDWIDTH_MINUTES:
                    peaks.append(peak_min)

        return peaks

    # ══════════════════════════════════════════════
    #  Утилиты
    # ══════════════════════════════════════════════

    @staticmethod
    def _timestamp_to_minutes(ts: float) -> int:
        lt = time.localtime(ts)
        return lt.tm_hour * 60 + lt.tm_min

    @staticmethod
    def _is_weekday(ts: float) -> bool:
        return time.localtime(ts).tm_wday < 5

    @staticmethod
    def _circular_distance(a: int, b: int) -> int:
        diff = abs(a - b)
        return min(diff, 1440 - diff)

    @staticmethod
    def _circular_std(minutes: list[int], center: int) -> float:
        diffs = [
            TemporalAnalyzer._circular_distance(m, center)
            for m in minutes
        ]
        if not diffs:
            return 30.0
        mean_diff = sum(diffs) / len(diffs)
        variance = sum((d - mean_diff) ** 2 for d in diffs) / len(diffs)
        return math.sqrt(max(variance, 1.0))
    
    def _count_observed_days(
        self, records: list, day_type: str,
    ) -> int:
        """
        Фактические дни с данными трекера.

        Считает уникальные даты, когда была ЛЮБАЯ запись
        данного action (не обязательно конкретного target).
        Отвечает на вопрос "сколько дней трекер работал?",
        а не "сколько дней пользователь открывал Chrome?".

        Без этого метода:
          total_days=14, eligible = int(14*5/7) = 10
          3 дня данных → regularity = 3/10 = 0.30 → отброшен

        С этим методом:
          3 дня данных → eligible = 3
          regularity = adaptive(3, 3) = 0.80 → проходит
        """
        observed = set()
        for r in records:
            lt = time.localtime(r.timestamp)
            is_wd = lt.tm_wday < 5
            if (day_type == "weekday" and is_wd) or \
               (day_type == "weekend" and not is_wd):
                observed.add(time.strftime("%Y-%m-%d", lt))
        return max(1, len(observed))

    def _adaptive_regularity(
        self, hits: int, total: int,
    ) -> float:
        """
        Regularity с Bayesian (Laplace) smoothing.

        При малом total: стягивает к PRIOR (0.5).
        При большом: приближается к hits/total.

        Формула:
          posterior = (hits + α×β) / (total + α)
          где α = PRIOR_STRENGTH, β = PRIOR

        Примеры (α=2, β=0.5):
          1/1  → 0.67    3/3  → 0.80    5/5  → 0.86
          3/10 → 0.33    7/10 → 0.67    10/10 → 0.92
        """
        return (
            (hits + self.REGULARITY_PRIOR_STRENGTH
                  * self.REGULARITY_PRIOR)
            / (total + self.REGULARITY_PRIOR_STRENGTH)
        )

    @staticmethod
    def _count_days(total_days: int, day_type: str) -> int:
        if day_type == "weekday":
            return max(1, int(total_days * 5 / 7))
        elif day_type == "weekend":
            return max(1, int(total_days * 2 / 7))
        return total_days