# core/patterns/drift_detector.py

"""
Обнаружение смены привычек.

Сравнивает паттерны первой и второй половины периода
наблюдения. Если confidence упал > 50% — привычка
сломалась. Если появился новый паттерн — новая привычка.
Если пик сместился > 60 мин — привычка сдвинулась.

Применение:
- Голосовая команда "какие у меня привычки?" →
  дополнительно: "за последнюю неделю заметила изменения..."
- Ускорение затухания устаревших предложений
- Информирование пользователя о трендах
"""

import logging
from dataclasses import dataclass

from core.patterns.temporal import TemporalAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class HabitDrift:
    """Изменение привычки."""
    target: str
    drift_type: str       # "broken", "new", "shifted"
    old_time: str         # "09:00" или ""
    new_time: str         # "10:30" или ""
    old_confidence: float
    new_confidence: float
    day_type: str         # "weekday" или "weekend"
    message: str          # Человекочитаемое описание


def detect_drift(
    storage,
    days: int = 14,
    min_confidence: float = 0.4,
) -> list[HabitDrift]:
    """
    Сравнить паттерны первой и второй половины периода.

    Алгоритм:
    1. Анализируем весь период (14 дней) → "старые" паттерны
    2. Анализируем вторую половину (7 дней) → "свежие" паттерны
    3. Сравниваем:
       - Был в старых, нет в свежих → "broken"
       - Нет в старых, есть в свежих → "new"
       - Пик сместился > 60 мин → "shifted"

    Args:
        storage: KnowledgeStorage
        days: общий период анализа
        min_confidence: минимальная confidence для
            рассмотрения паттерна

    Returns: список обнаруженных изменений
    """
    analyzer = TemporalAnalyzer(storage)

    half = days // 2

    # "Старые" = весь период (включает начало, где
    # могли быть старые привычки)
    old_patterns = analyzer.analyze(days=days)

    # "Свежие" = вторая половина (текущее поведение)
    new_patterns = analyzer.analyze(days=half)

    # Индексируем по (target, day_type)
    old_by_key = {
        (p.target, p.day_type): p
        for p in old_patterns
        if p.confidence >= min_confidence
    }
    new_by_key = {
        (p.target, p.day_type): p
        for p in new_patterns
    }

    drifts: list[HabitDrift] = []

    # ── Сломанные привычки ──
    # Были в old с высоким confidence, нет в new
    for key, old_p in old_by_key.items():
        new_p = new_by_key.get(key)

        if new_p is None or new_p.confidence < 0.2:
            # Привычка исчезла
            drifts.append(HabitDrift(
                target=old_p.target,
                drift_type="broken",
                old_time=old_p.peak_time_str,
                new_time="",
                old_confidence=old_p.confidence,
                new_confidence=(
                    new_p.confidence if new_p else 0
                ),
                day_type=old_p.day_type,
                message=(
                    f"Раньше ты открывал {old_p.target} "
                    f"около {old_p.peak_time_str}, "
                    f"но последнюю неделю — нет."
                ),
            ))

        elif new_p and new_p.confidence >= min_confidence:
            # Привычка существует — проверяем сдвиг
            dist = TemporalAnalyzer._circular_distance(
                old_p.peak_minute, new_p.peak_minute,
            )
            if dist > 60:
                drifts.append(HabitDrift(
                    target=old_p.target,
                    drift_type="shifted",
                    old_time=old_p.peak_time_str,
                    new_time=new_p.peak_time_str,
                    old_confidence=old_p.confidence,
                    new_confidence=new_p.confidence,
                    day_type=old_p.day_type,
                    message=(
                        f"{old_p.target}: раньше в "
                        f"{old_p.peak_time_str}, "
                        f"теперь в {new_p.peak_time_str}."
                    ),
                ))

    # ── Новые привычки ──
    # Есть в new с высоким confidence, нет в old
    for key, new_p in new_by_key.items():
        if new_p.confidence < 0.5:
            continue

        old_p = old_by_key.get(key)
        if old_p is None or old_p.confidence < 0.2:
            drifts.append(HabitDrift(
                target=new_p.target,
                drift_type="new",
                old_time="",
                new_time=new_p.peak_time_str,
                old_confidence=(
                    old_p.confidence if old_p else 0
                ),
                new_confidence=new_p.confidence,
                day_type=new_p.day_type,
                message=(
                    f"Новая привычка: ты стал "
                    f"открывать {new_p.target} "
                    f"около {new_p.peak_time_str}."
                ),
            ))

    if drifts:
        logger.info(
            "Drift detector: %d изменений привычек",
            len(drifts),
        )

    return drifts