# core/integrations/__init__.py

"""
Утилиты для интеграций с приложениями.

Общие компоненты:
- Симуляция медиа-клавиш (через core.input_sim)
- Поиск процессов по имени
- Фокус окна по заголовку
"""

import ctypes
import ctypes.wintypes
import logging

from core.input_sim import send_key, VK

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# ═══════════════════════════════════════════
#  Медиа-клавиши (оборачиваем send_key)
# ═══════════════════════════════════════════

def media_play_pause() -> bool:
    """Play/Pause текущего медиаплеера."""
    send_key(VK.MEDIA_PLAY_PAUSE)
    return True

def media_next() -> bool:
    """Следующий трек."""
    send_key(VK.MEDIA_NEXT)
    return True

def media_prev() -> bool:
    """Предыдущий трек."""
    send_key(VK.MEDIA_PREV)
    return True

def media_stop() -> bool:
    """Остановить воспроизведение."""
    send_key(VK.MEDIA_STOP)
    return True

# ═══════════════════════════════════════════
#  Поиск процессов и окон (БЕЗ ИЗМЕНЕНИЙ)
# ═══════════════════════════════════════════

def find_window_by_title(substring: str) -> int | None:
    """Найти окно по подстроке в заголовке."""
    result = []

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if substring.lower() in buf.value.lower():
            result.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )
    user32.EnumWindows(WNDENUMPROC(callback), 0)

    return result[0] if result else None

def get_window_title(hwnd: int) -> str:
    """Получить заголовок окна по HWND."""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value

def focus_window(hwnd: int) -> bool:
    """Поставить окно в фокус."""
    try:
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as e:
        logger.debug("Focus window error: %s", e)
        return False

def is_process_running(process_name: str) -> bool:
    """Проверить, запущен ли процесс."""
    try:
        import psutil
        for proc in psutil.process_iter(['name']):
            if proc.info['name'] and (
                process_name.lower()
                in proc.info['name'].lower()
            ):
                return True
    except Exception:
        pass
    return False