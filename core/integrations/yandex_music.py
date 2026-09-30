# core/integrations/yandex_music.py

"""
Интеграция с Яндекс Музыкой.

Стратегия управления:
1. Системные медиа-клавиши (play/pause/next/prev)
   — работают с десктопным приложением И web-версией
2. Определение текущего трека из заголовка окна
3. Открытие через URL/приложение

Не требует API-ключей — всё через системные средства.

Заголовки окон:
- Десктоп: "Исполнитель — Название трека"
- Браузер: "Название трека — Исполнитель • Яндекс Музыка — Google Chrome"
"""

import os
import subprocess
import logging
import webbrowser

from core.integrations import (
    media_play_pause,
    media_next,
    media_prev,
    media_stop,
    find_window_by_title,
    get_window_title,
    focus_window,
    is_process_running,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Константы
# ═══════════════════════════════════════════

YANDEX_MUSIC_URL = "https://music.yandex.ru"
YM_PROCESS_NAME = "Яндекс Музыка"
YM_WINDOW_HINTS = [
    "яндекс музыка", "yandex music",
    "music.yandex",
]


# ═══════════════════════════════════════════
#  Управление воспроизведением
# ═══════════════════════════════════════════

def play_pause() -> bool:
    """Play/Pause. Работает с любым активным плеером."""
    ok = media_play_pause()
    logger.debug("Yandex Music: play/pause → %s", ok)
    return ok


def next_track() -> bool:
    """Следующий трек."""
    ok = media_next()
    logger.debug("Yandex Music: next → %s", ok)
    return ok


def prev_track() -> bool:
    """Предыдущий трек."""
    ok = media_prev()
    logger.debug("Yandex Music: prev → %s", ok)
    return ok


def stop() -> bool:
    """Остановить воспроизведение."""
    ok = media_stop()
    logger.debug("Yandex Music: stop → %s", ok)
    return ok


# ═══════════════════════════════════════════
#  Определение текущего трека
# ═══════════════════════════════════════════

def get_current_track() -> dict | None:
    """
    Определить что сейчас играет.

    Парсит заголовок окна Яндекс Музыки.

    Returns:
        {"artist": "...", "title": "...", "source": "desktop|browser"}
        или None
    """
    # Десктопное приложение
    for hint in YM_WINDOW_HINTS:
        hwnd = find_window_by_title(hint)
        if hwnd:
            title = get_window_title(hwnd)
            parsed = _parse_track_title(title)
            if parsed:
                parsed["source"] = "desktop"
                return parsed

    return None


def _parse_track_title(title: str) -> dict | None:
    """
    Извлечь исполнителя и название из заголовка.

    Паттерны:
    - "Исполнитель — Название" (десктоп)
    - "Название — Исполнитель • Яндекс Музыка" (веб)
    - "Яндекс Музыка" (ничего не играет)
    """
    if not title:
        return None

    # Убираем браузерную часть
    for indicator in [
        " — Google Chrome", " — Mozilla Firefox",
        " — Microsoft Edge", " — Яндекс Браузер",
    ]:
        if indicator in title:
            title = title.replace(indicator, "")

    # Убираем "• Яндекс Музыка"
    if "• Яндекс Музыка" in title:
        title = title.split("• Яндекс Музыка")[0].strip()
    if "• Yandex Music" in title:
        title = title.split("• Yandex Music")[0].strip()

    # Если осталось просто "Яндекс Музыка" — ничего не играет
    if title.lower() in (
        "яндекс музыка", "yandex music", "",
    ):
        return None

    # Парсим "Исполнитель — Название" или "Название — Исполнитель"
    for sep in [" — ", " - ", " – "]:
        if sep in title:
            parts = title.split(sep, 1)
            return {
                "artist": parts[0].strip(),
                "title": parts[1].strip(),
            }

    # Без разделителя — только название
    return {"artist": "", "title": title.strip()}


# ═══════════════════════════════════════════
#  Запуск и навигация
# ═══════════════════════════════════════════

def open_yandex_music() -> bool:
    """
    Открыть Яндекс Музыку.

    Приоритет: десктоп → веб.
    """
    # Если десктоп запущен — фокусируем
    for hint in YM_WINDOW_HINTS:
        hwnd = find_window_by_title(hint)
        if hwnd:
            focus_window(hwnd)
            logger.info("Яндекс Музыка: фокус на окно")
            return True

    # Пробуем запустить десктоп
    try:
        # Стандартный путь установки
        app_path = os.path.expandvars(
            r"%LOCALAPPDATA%\Programs\YandexMusic\Яндекс Музыка.exe"
        )
        if os.path.exists(app_path):
            subprocess.Popen(
                [app_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Яндекс Музыка: запуск десктоп")
            return True
    except Exception:
        pass

    # Fallback: веб-версия
    webbrowser.open(YANDEX_MUSIC_URL)
    logger.info("Яндекс Музыка: открыта веб-версия")
    return True


def search_yandex_music(query: str) -> bool:
    """Поиск в Яндекс Музыке (через веб)."""
    from urllib.parse import quote_plus
    url = f"{YANDEX_MUSIC_URL}/search?text={quote_plus(query)}"
    webbrowser.open(url)
    return True


def is_yandex_music_active() -> bool:
    """Запущена ли Яндекс Музыка."""
    for hint in YM_WINDOW_HINTS:
        if find_window_by_title(hint):
            return True
    return False