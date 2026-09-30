# core/input_sim.py

"""
Единый модуль симуляции ввода через SendInput.

Заменяет разрозненные keybd_event вызовы в:
- window_control.py
- media.py
- clipboard_actions.py
- macros.py

Архитектура:
- SendInput вместо keybd_event (современный API)
- Атомарные операции (down+up неразрывны)
- Именованные комбинации для удобства
- Поддержка extended keys (медиа-клавиши)
- Задержки между нажатиями при необходимости
"""

import ctypes
import ctypes.wintypes
import time
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Win32 структуры
# ═══════════════════════════════════════════

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT),
    ]


# ═══════════════════════════════════════════
#  Virtual Key Codes
# ═══════════════════════════════════════════

class VK:
    """Все виртуальные коды клавиш в одном месте."""

    # Модификаторы
    CONTROL = 0x11
    ALT = 0x12
    SHIFT = 0x10
    WIN = 0x5B

    # Буквы
    A = 0x41
    C = 0x43
    D = 0x44
    V = 0x56
    X = 0x58

    # Функциональные
    F4 = 0x73
    TAB = 0x09
    ESCAPE = 0x1B
    RETURN = 0x0D
    SPACE = 0x20

    # Стрелки
    LEFT = 0x25
    UP = 0x26
    RIGHT = 0x27
    DOWN = 0x28

    # Медиа (extended)
    MEDIA_PLAY_PAUSE = 0xB3
    MEDIA_NEXT = 0xB0
    MEDIA_PREV = 0xB1
    MEDIA_STOP = 0xB2
    VOLUME_MUTE = 0xAD
    VOLUME_DOWN = 0xAE
    VOLUME_UP = 0xAF

    # Медиа-клавиши требуют extended flag
    EXTENDED_KEYS = {
        0xB0, 0xB1, 0xB2, 0xB3,
        0xAD, 0xAE, 0xAF,
    }


# ═══════════════════════════════════════════
#  Низкоуровневые функции
# ═══════════════════════════════════════════

def send_key(vk_code: int) -> None:
    """
    Нажать и отпустить одну клавишу.

    Для extended keys (медиа) автоматически добавляет
    KEYEVENTF_EXTENDEDKEY.
    """
    is_extended = vk_code in VK.EXTENDED_KEYS
    flags_down = KEYEVENTF_EXTENDEDKEY if is_extended else 0
    flags_up = flags_down | KEYEVENTF_KEYUP

    inputs = (INPUT * 2)()

    inputs[0].type = INPUT_KEYBOARD
    inputs[0]._input.ki.wVk = vk_code
    inputs[0]._input.ki.dwFlags = flags_down

    inputs[1].type = INPUT_KEYBOARD
    inputs[1]._input.ki.wVk = vk_code
    inputs[1]._input.ki.dwFlags = flags_up

    ctypes.windll.user32.SendInput(
        2, ctypes.byref(inputs), ctypes.sizeof(INPUT)
    )


def send_hotkey(*vk_codes: int) -> None:
    """
    Нажать комбинацию клавиш.

    Все клавиши нажимаются по порядку,
    затем все отпускаются в обратном порядке.

    Пример:
        send_hotkey(VK.CONTROL, VK.C)   # Ctrl+C
        send_hotkey(VK.ALT, VK.F4)      # Alt+F4
        send_hotkey(VK.WIN, VK.D)        # Win+D
    """
    count = len(vk_codes) * 2
    inputs = (INPUT * count)()

    # Down
    for i, vk in enumerate(vk_codes):
        is_extended = vk in VK.EXTENDED_KEYS
        inputs[i].type = INPUT_KEYBOARD
        inputs[i]._input.ki.wVk = vk
        inputs[i]._input.ki.dwFlags = (
            KEYEVENTF_EXTENDEDKEY if is_extended else 0
        )

    # Up (reverse)
    for i, vk in enumerate(reversed(vk_codes)):
        idx = len(vk_codes) + i
        is_extended = vk in VK.EXTENDED_KEYS
        inputs[idx].type = INPUT_KEYBOARD
        inputs[idx]._input.ki.wVk = vk
        inputs[idx]._input.ki.dwFlags = (
            (KEYEVENTF_EXTENDEDKEY if is_extended else 0)
            | KEYEVENTF_KEYUP
        )

    ctypes.windll.user32.SendInput(
        count, ctypes.byref(inputs), ctypes.sizeof(INPUT)
    )


def send_keys_sequence(*vk_codes: int, delay: float = 0.05) -> None:
    """
    Нажать несколько клавиш последовательно (не одновременно).

    Используется для повторных нажатий:
        send_keys_sequence(VK.VOLUME_UP, VK.VOLUME_UP, VK.VOLUME_UP)
    """
    for vk in vk_codes:
        send_key(vk)
        if delay > 0:
            time.sleep(delay)


# ═══════════════════════════════════════════
#  Именованные действия
# ═══════════════════════════════════════════

def copy() -> None:
    """Ctrl+C"""
    send_hotkey(VK.CONTROL, VK.C)


def paste() -> None:
    """Ctrl+V"""
    send_hotkey(VK.CONTROL, VK.V)


def cut() -> None:
    """Ctrl+X"""
    send_hotkey(VK.CONTROL, VK.X)


def select_all() -> None:
    """Ctrl+A"""
    send_hotkey(VK.CONTROL, VK.A)


def undo() -> None:
    """Ctrl+Z"""
    send_hotkey(VK.CONTROL, 0x5A)


def redo() -> None:
    """Ctrl+Y"""
    send_hotkey(VK.CONTROL, 0x59)


def close_window() -> None:
    """Alt+F4"""
    send_hotkey(VK.ALT, VK.F4)


def switch_window() -> None:
    """Alt+Tab"""
    send_hotkey(VK.ALT, VK.TAB)


def minimize_all() -> None:
    """Win+D (показать рабочий стол)"""
    send_hotkey(VK.WIN, VK.D)


def maximize() -> None:
    """Win+Up"""
    send_hotkey(VK.WIN, VK.UP)


def snap_left() -> None:
    """Win+Left"""
    send_hotkey(VK.WIN, VK.LEFT)


def snap_right() -> None:
    """Win+Right"""
    send_hotkey(VK.WIN, VK.RIGHT)


# Медиа
def play_pause() -> None:
    send_key(VK.MEDIA_PLAY_PAUSE)


def next_track() -> None:
    send_key(VK.MEDIA_NEXT)


def prev_track() -> None:
    send_key(VK.MEDIA_PREV)


def stop_media() -> None:
    send_key(VK.MEDIA_STOP)


def volume_up(steps: int = 1) -> None:
    """Увеличить громкость (1 step ≈ 2%)."""
    for _ in range(steps):
        send_key(VK.VOLUME_UP)


def volume_down(steps: int = 1) -> None:
    """Уменьшить громкость."""
    for _ in range(steps):
        send_key(VK.VOLUME_DOWN)


def mute_toggle() -> None:
    send_key(VK.VOLUME_MUTE)