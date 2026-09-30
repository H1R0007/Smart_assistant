# core/patterns/session_classifier.py

"""
Классификация сессий по типу активности.

Типы:
- "focus": 1-2 приложения, доминирующее > 70% времени
- "multitask": 3+ приложений, частые переключения
- "browse": преимущественно браузер
- "communication": мессенджеры + почта
- "ritual": короткая сессия (< 5 мин), чёткая последовательность

Применение:
- Умный rate limit: не прерывать focus-сессии
- Статистика: "сегодня 4ч фокуса, 2ч мультитаскинга"
- Поиск ритуалов для автоматизации
"""

from dataclasses import dataclass
from collections import Counter


# Наборы приложений по категориям.
# Имена должны совпадать с ActivityTracker._extract_app_name().
BROWSER_APPS = frozenset({
    "chrome", "firefox", "edge",
    "яндекс браузер", "safari", "opera",
})
COMM_APPS = frozenset({
    "telegram", "discord", "slack", "teams",
    "outlook", "whatsapp", "skype",
})
IDE_APPS = frozenset({
    "vs code", "pycharm", "intellij", "vim",
    "emacs", "sublime", "блокнот", "notepad++",
})


@dataclass
class SessionProfile:
    """Профиль сессии."""
    session_type: str         # focus/multitask/browse/comm/ritual
    duration_min: float       # Длительность в минутах
    unique_apps: int          # Уникальных приложений
    dominant_app: str         # Самое частое приложение
    dominant_share: float     # Доля доминирующего (0-1)
    switch_rate: float        # Переключений в минуту


def classify_session(records: list) -> SessionProfile:
    """
    Классифицировать одну сессию по типу активности.

    Args:
        records: список ActivityRecord одной сессии,
                 отсортированный по timestamp.

    Returns: SessionProfile с типом и метриками.
    """
    if not records:
        return SessionProfile(
            "unknown", 0, 0, "", 0, 0,
        )

    targets = [r.target for r in records if r.target]
    if not targets:
        return SessionProfile(
            "unknown", 0, 0, "", 0, 0,
        )

    # Базовые метрики
    duration = (
        (records[-1].timestamp - records[0].timestamp) / 60
        if len(records) > 1 else 0
    )

    unique = set(t.lower() for t in targets)
    counts = Counter(t.lower() for t in targets)
    dominant, dominant_count = counts.most_common(1)[0]
    dominant_share = dominant_count / len(targets)
    switch_rate = (
        len(targets) / max(duration, 0.1)
    )

    # ── Ритуал: короткая + последовательная ──
    # < 5 мин и 2+ приложений = быстрый ритуал запуска
    if duration < 5 and len(targets) >= 2:
        return SessionProfile(
            "ritual", round(duration, 1),
            len(unique), dominant,
            round(dominant_share, 2),
            round(switch_rate, 1),
        )

    # ── Фокус: 1-2 приложения, одно доминирует ──
    if len(unique) <= 2 and dominant_share > 0.7:
        return SessionProfile(
            "focus", round(duration, 1),
            len(unique), dominant,
            round(dominant_share, 2),
            round(switch_rate, 1),
        )

    # ── Браузинг: > 60% в браузере ──
    browser_share = sum(
        c for t, c in counts.items()
        if t in BROWSER_APPS
    ) / len(targets)
    if browser_share > 0.6:
        return SessionProfile(
            "browse", round(duration, 1),
            len(unique), dominant,
            round(dominant_share, 2),
            round(switch_rate, 1),
        )

    # ── Коммуникация: > 50% в мессенджерах ──
    comm_share = sum(
        c for t, c in counts.items()
        if t in COMM_APPS
    ) / len(targets)
    if comm_share > 0.5:
        return SessionProfile(
            "communication", round(duration, 1),
            len(unique), dominant,
            round(dominant_share, 2),
            round(switch_rate, 1),
        )

    # ── Мультитаскинг: всё остальное ──
    return SessionProfile(
        "multitask", round(duration, 1),
        len(unique), dominant,
        round(dominant_share, 2),
        round(switch_rate, 1),
    )


def classify_current_session(
    storage, session_gap: int = 300,
) -> SessionProfile | None:
    """
    Классифицировать текущую (последнюю) сессию.

    Загружает последний час активности и определяет
    границу последней сессии по паузе >= session_gap.

    Returns: SessionProfile или None если нет данных.
    """
    records = storage.get_activity(
        hours=1.0, action="app_switch", limit=200,
    )
    if len(records) < 2:
        return None

    # Сортируем ASC
    sorted_recs = sorted(
        records, key=lambda r: r.timestamp,
    )

    # Находим начало последней сессии
    session_start = 0
    for i in range(len(sorted_recs) - 1, 0, -1):
        gap = (
            sorted_recs[i].timestamp
            - sorted_recs[i - 1].timestamp
        )
        if gap > session_gap:
            session_start = i
            break

    session = sorted_recs[session_start:]
    if len(session) < 2:
        return None

    return classify_session(session)