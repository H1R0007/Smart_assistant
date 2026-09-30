# core/patterns/export.py

"""
Экспорт и импорт обнаруженных паттернов.

Позволяет:
- Сохранить паттерны перед переустановкой
- Перенести на другой компьютер
- Ускорить холодный старт на новой машине
  (импортированные паттерны как seed)

Формат: JSON, человекочитаемый.
"""

import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_EXPORT_PATH = "data/patterns_export.json"


def export_patterns(
    temporal_patterns: list,
    sequence_patterns: list,
    contextual_patterns: list = None,
    path: str = DEFAULT_EXPORT_PATH,
) -> str:
    """
    Экспорт паттернов в JSON-файл.

    Returns: путь к файлу.
    """
    data = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M"),
        "version": 1,
        "temporal": [
            {
                "target": p.target,
                "peak_minute": p.peak_minute,
                "peak_time": p.peak_time_str,
                "bandwidth": p.bandwidth_minutes,
                "regularity": p.regularity,
                "confidence": p.confidence,
                "day_type": p.day_type,
                "sample_count": p.sample_count,
                "days_observed": p.days_observed,
            }
            for p in temporal_patterns
            if p.confidence >= 0.3
        ],
        "sequence": [
            {
                "antecedent": p.antecedent,
                "consequent": p.consequent,
                "confidence": p.confidence,
                "lift": p.lift,
                "occurrences": p.occurrences,
                "support": p.support,
            }
            for p in sequence_patterns
            if p.is_strong
        ],
        "contextual": [
            {
                "antecedent": p.antecedent,
                "consequent": p.consequent,
                "time_slot": p.time_slot,
                "confidence": p.confidence,
                "context_boost": p.context_boost,
                "occurrences": p.occurrences,
            }
            for p in (contextual_patterns or [])
            if p.is_strong
        ],
    }

    Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = (
        len(data["temporal"])
        + len(data["sequence"])
        + len(data["contextual"])
    )
    logger.info(
        "Экспортировано %d паттернов в %s", total, path,
    )
    return path


def import_patterns(
    path: str = DEFAULT_EXPORT_PATH,
) -> dict | None:
    """
    Импорт паттернов из JSON-файла.

    Returns: словарь с данными или None если файл
    не найден / повреждён.
    """
    if not Path(path).exists():
        logger.debug("Файл экспорта не найден: %s", path)
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        total = (
            len(data.get("temporal", []))
            + len(data.get("sequence", []))
            + len(data.get("contextual", []))
        )
        logger.info(
            "Импортировано %d паттернов из %s "
            "(экспорт от %s)",
            total, path, data.get("exported_at", "?"),
        )
        return data

    except Exception as e:
        logger.warning(
            "Ошибка импорта паттернов из %s: %s",
            path, e,
        )
        return None