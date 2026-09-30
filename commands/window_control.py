# commands/window_control.py

"""
Управление окнами Windows.

v3.1 — Полная переработка:
1. Управление КОНКРЕТНЫМ окном по имени
   "сверни блокнот", "закрой хром", "разверни телеграм"
2. Snap в 4 угла и 2 половины
   "окно в левый верхний угол"
3. Перемещение между мониторами
   "перенеси на второй монитор"
4. Список открытых окон
   "какие окна открыты"
5. Фокус на конкретное окно
   "переключись на хром"
6. Единый API через ctypes (user32)

Архитектура:
- WindowManager: низкоуровневая работа с окнами
- Команды: высокоуровневые голосовые действия
"""

import ctypes
import ctypes.wintypes
import logging
import re

from commands.base import BaseCommand, CommandResult, stem_russian
from core.input_sim import send_hotkey as _press_keys
from core.app_registry import get_app_registry
from core.window_utils import MONITORINFO, MonitorManager

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# ═══════════════════════════════════════════
#  Win32 константы и структуры
# ═══════════════════════════════════════════

SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_RESTORE = 9
SW_SHOW = 5
SW_HIDE = 0

SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_SHOWWINDOW = 0x0040

GW_HWNDNEXT = 2
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_VISIBLE = 0x10000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

WM_CLOSE = 0x0010
MONITOR_DEFAULTTONEAREST = 0x00000002

# ═══════════════════════════════════════════
#  WindowManager — работа с окнами
# ═══════════════════════════════════════════

class WindowManager:
    """
    Низкоуровневое управление окнами Windows.

    Все методы — статические, без состояния.
    Потокобезопасные (Win32 API thread-safe для UI).
    """


    @staticmethod
    def get_foreground_window() -> int:
        """Получить текущее активное окно."""
        return user32.GetForegroundWindow()

    @staticmethod
    def get_all_windows() -> list[dict]:
        """
        Получить список всех видимых окон приложений.

        Возвращает: [{"hwnd": int, "title": str, "process": str}]

        Фильтрует:
        - Невидимые окна
        - Tool windows (панели инструментов)
        - Окна без заголовка
        - Системные окна (Program Manager, Settings)
        """
        windows = []

        IGNORED_TITLES = {
            "", "program manager", "settings",
            "microsoft text input application",
            "windows input experience",
        }

        def _enum_callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True

            # Проверяем стиль окна
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex_style & WS_EX_TOOLWINDOW:
                return True

            # Получаем заголовок
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True

            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value

            if title.lower() in IGNORED_TITLES:
                return True

            windows.append({
                "hwnd": hwnd,
                "title": title,
            })
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )
        user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)

        return windows

    @staticmethod
    def find_window_by_name(name: str) -> int | None:
        """Найти окно по имени."""
        name_lower = name.lower().strip()
        name_stemmed = stem_russian(name_lower)

        windows = WindowManager.get_all_windows()
        if not windows:
            return None

        search_terms = [name_lower]

        # ИСПОЛЬЗУЕМ НОВЫЙ РЕЕСТР
        registry = get_app_registry()
        window_aliases = registry.get_window_aliases()
        
        for alias, terms in window_aliases.items():
            if name_lower == alias or name_stemmed == stem_russian(alias):
                search_terms.extend(terms)
                break

        # Ищем
        for window in windows:
            title_lower = window["title"].lower()
            for term in search_terms:
                if term in title_lower:
                    return window["hwnd"]

        # Fallback: стемминг заголовков
        for window in windows:
            title_stemmed = " ".join(
                stem_russian(w) for w in window["title"].lower().split()
            )
            if name_stemmed in title_stemmed:
                return window["hwnd"]

        return None

    @staticmethod
    def focus_window(hwnd: int) -> bool:
        """Поставить окно в фокус."""
        try:
            # Восстанавливаем если свёрнуто
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)

            user32.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    @staticmethod
    def minimize_window(hwnd: int) -> bool:
        """Свернуть окно."""
        try:
            user32.ShowWindow(hwnd, SW_MINIMIZE)
            return True
        except Exception:
            return False

    @staticmethod
    def maximize_window(hwnd: int) -> bool:
        """Развернуть окно на весь экран."""
        try:
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            return True
        except Exception:
            return False

    @staticmethod
    def restore_window(hwnd: int) -> bool:
        """Восстановить окно (из свёрнутого/развёрнутого)."""
        try:
            user32.ShowWindow(hwnd, SW_RESTORE)
            return True
        except Exception:
            return False

    @staticmethod
    def close_window(hwnd: int) -> bool:
        """Закрыть окно (послать WM_CLOSE)."""
        try:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return True
        except Exception:
            return False

    @staticmethod
    def get_work_area(hwnd: int = None) -> tuple[int, int, int, int]:
        """
        Получить рабочую область монитора.

        Если hwnd задан — монитор этого окна.
        Иначе — основной монитор.

        Возвращает (x, y, width, height).
        """
        if hwnd:
            monitor = user32.MonitorFromWindow(
                hwnd, MONITOR_DEFAULTTONEAREST
            )
        else:
            monitor = user32.MonitorFromWindow(
                user32.GetForegroundWindow(), MONITOR_DEFAULTTONEAREST
            )

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))

        work = info.rcWork
        return (
            work.left,
            work.top,
            work.right - work.left,
            work.bottom - work.top
        )

    @staticmethod
    def move_window(
        hwnd: int, x: int, y: int, width: int, height: int
    ) -> bool:
        """Переместить и изменить размер окна."""
        try:
            # Восстанавливаем из максимизированного состояния
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.MoveWindow(hwnd, x, y, width, height, True)
            return True
        except Exception:
            return False

    @staticmethod
    def snap_window(hwnd: int, position: str) -> bool:
        """
        Прикрепить окно к позиции.

        Позиции:
        - "left", "right" — половины экрана
        - "top-left", "top-right", "bottom-left", "bottom-right" — четверти
        - "center" — по центру (60% экрана)
        """
        wx, wy, ww, wh = WindowManager.get_work_area(hwnd)

        half_w = ww // 2
        half_h = wh // 2

        positions = {
            "left": (wx, wy, half_w, wh),
            "right": (wx + half_w, wy, half_w, wh),
            "top-left": (wx, wy, half_w, half_h),
            "top-right": (wx + half_w, wy, half_w, half_h),
            "bottom-left": (wx, wy + half_h, half_w, half_h),
            "bottom-right": (wx + half_w, wy + half_h, half_w, half_h),
            "center": (
                wx + ww // 5, wy + wh // 10,
                ww * 3 // 5, wh * 4 // 5
            ),
        }

        coords = positions.get(position)
        if not coords:
            return False

        return WindowManager.move_window(hwnd, *coords)


# ═══════════════════════════════════════════
#  Парсер целевого окна
# ═══════════════════════════════════════════

def _extract_window_target(text: str, action_words: list[str]) -> str:
    """
    Извлечь название целевого окна из текста.

    "сверни блокнот" → "блокнот"
    "закрой окно хрома" → "хрома"
    "окно влево блокнот" → "блокнот"
    """
    words = text.lower().split()

    # Слова которые нужно убрать (точное совпадение, НЕ подстрока)
    remove = set(action_words) | {
        "пожалуйста", "окно", "окна",
        "влево", "вправо", "вверх", "вниз",
        "полный", "экран", "экрана",
        "в", "на", "по", "весь",
        "левый", "правый", "верхний", "нижний", "угол",
        "центру", "центр", "половину",
    }

    # Фразы (удаляем до разбиения на слова)
    phrase_remove = [
        "на весь экран", "в полный экран",
        "на половину экрана",
        "в левый верхний угол", "в правый верхний угол",
        "в левый нижний угол", "в правый нижний угол",
        "по центру", "в центр",
    ]

    text_lower = text.lower()
    for phrase in phrase_remove:
        text_lower = text_lower.replace(phrase, "")

    words = text_lower.split()

    # Убираем слова по точному совпадению
    result = [w for w in words if w not in remove]

    return " ".join(result).strip()


# ═══════════════════════════════════════════
#  Команды
# ═══════════════════════════════════════════

class MinimizeWindowCommand(BaseCommand):
    """
    Свернуть окно.

    "сверни" — текущее окно
    "сверни блокнот" — конкретное окно
    "сверни всё" — все окна (Win+D)
    """
    name = "minimize_window"
    keywords = [
        "сверни", "свернуть", "минимизируй",
    ]
    priority = 10

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        # Проверяем "сверни всё/все"
        if any(w in text for w in ["всё", "все", "все окна"]):
            return {"target": "__all__"}

        target = _extract_window_target(text, self.keywords)
        return {"target": target}

    def execute(self, text: str, params: dict) -> CommandResult:
        target = params.get("target", "")

        if target == "__all__":
            # Win+D
            _press_keys(0x5B, 0x44)
            self.speaker.say("Всё свёрнуто.")
            return CommandResult(follow_up=True)

        if target:
            hwnd = WindowManager.find_window_by_name(target)
            if hwnd:
                WindowManager.minimize_window(hwnd)
                self.speaker.say(f"Свернула {target}.")
            else:
                self.speaker.say(f"Не нашла окно {target}.")
        else:
            # Текущее окно
            hwnd = WindowManager.get_foreground_window()
            WindowManager.minimize_window(hwnd)
            self.speaker.say("Свернула.")

        return CommandResult(follow_up=True)


class MaximizeWindowCommand(BaseCommand):
    """
    Развернуть окно.

    "разверни" — текущее
    "разверни хром" — конкретное
    """
    name = "maximize_window"
    keywords = [
        "разверни", "на весь экран", "максимизируй",
        "полный экран", "развернуть",
    ]
    priority = 10

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        target = _extract_window_target(text, self.keywords)
        return {"target": target}

    def execute(self, text: str, params: dict) -> CommandResult:
        target = params.get("target", "")

        if target:
            hwnd = WindowManager.find_window_by_name(target)
            if hwnd:
                WindowManager.maximize_window(hwnd)
                WindowManager.focus_window(hwnd)
                self.speaker.say(f"Развернула {target}.")
            else:
                self.speaker.say(f"Не нашла окно {target}.")
        else:
            hwnd = WindowManager.get_foreground_window()
            WindowManager.maximize_window(hwnd)
            self.speaker.say("Развернула.")

        return CommandResult(follow_up=True)


class CloseWindowCommand(BaseCommand):
    """
    Закрыть окно.

    "закрой окно" — текущее
    "закрой окно блокнота" — конкретное
    """
    name = "close_window"
    keywords = ["закрой окно", "закрыть окно"]
    priority = 12

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        target = _extract_window_target(text, self.keywords)
        return {"target": target}

    def execute(self, text: str, params: dict) -> CommandResult:
        target = params.get("target", "")

        if target:
            hwnd = WindowManager.find_window_by_name(target)
            if hwnd:
                WindowManager.close_window(hwnd)
                self.speaker.say(f"Закрыла окно {target}.")
            else:
                self.speaker.say(f"Не нашла окно {target}.")
        else:
            hwnd = WindowManager.get_foreground_window()
            WindowManager.close_window(hwnd)
            self.speaker.say("Закрыла окно.")

        return CommandResult(follow_up=True)


class SwitchWindowCommand(BaseCommand):
    """
    Переключиться на окно.

    "переключись на хром" — фокус на Chrome
    "переключи окно" — Alt+Tab
    "следующее окно" — Alt+Tab
    """
    name = "switch_window"
    keywords = [
        "переключись на", "переключи на",
        "переключи окно", "следующее окно", "другое окно",
        "альт таб", "покажи окно",
    ]
    priority = 11

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        target = _extract_window_target(text, self.keywords)
        return {"target": target}

    def execute(self, text: str, params: dict) -> CommandResult:
        target = params.get("target", "")

        if target:
            hwnd = WindowManager.find_window_by_name(target)
            if hwnd:
                WindowManager.focus_window(hwnd)
                self.speaker.say(f"Переключила на {target}.")
            else:
                self.speaker.say(f"Не нашла окно {target}.")
        else:
            # Alt+Tab
            _press_keys(0x12, 0x09)
            self.speaker.say("Переключила.")

        return CommandResult(follow_up=True)


class SnapWindowCommand(BaseCommand):
    """
    Прикрепить окно к краю/углу экрана.

    "окно влево" — левая половина
    "окно вправо" — правая половина
    "окно в левый верхний угол" — четверть
    "блокнот влево" — конкретное окно влево
    "по центру" — 60% экрана по центру
    """
    name = "snap_window"
    keywords = [
        "окно влево", "окно вправо",
        "прикрепи влево", "прикрепи вправо",
        "половина экрана влево", "половина экрана вправо",
        "в левый верхний угол", "в правый верхний угол",
        "в левый нижний угол", "в правый нижний угол",
        "влево", "вправо",
        "по центру", "в центр окна",
    ]
    priority = 10

    # Маппинг фраз → позиция
    POSITION_MAP = {
        "левый верхний": "top-left",
        "правый верхний": "top-right",
        "левый нижний": "bottom-left",
        "правый нижний": "bottom-right",
        "влево": "left",
        "налево": "left",
        "вправо": "right",
        "направо": "right",
        "по центру": "center",
        "в центр": "center",
    }

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        # Определяем позицию
        position = None
        for phrase, pos in self.POSITION_MAP.items():
            if phrase in text:
                position = pos
                break

        # Определяем целевое окно
        target = _extract_window_target(text, self.keywords)

        return {"position": position, "target": target}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("position"))

    def execute(self, text: str, params: dict) -> CommandResult:
        position = params.get("position", "left")
        target = params.get("target", "")

        if target:
            hwnd = WindowManager.find_window_by_name(target)
            if not hwnd:
                self.speaker.say(f"Не нашла окно {target}.")
                return CommandResult(follow_up=True)
        else:
            hwnd = WindowManager.get_foreground_window()

        success = WindowManager.snap_window(hwnd, position)

        position_names = {
            "left": "слева",
            "right": "справа",
            "top-left": "в левом верхнем углу",
            "top-right": "в правом верхнем углу",
            "bottom-left": "в левом нижнем углу",
            "bottom-right": "в правом нижнем углу",
            "center": "по центру",
        }

        if success:
            pos_name = position_names.get(position, position)
            if target:
                self.speaker.say(f"{target} {pos_name}.")
            else:
                self.speaker.say(f"Окно {pos_name}.")
        else:
            self.speaker.say("Не удалось переместить окно.")

        return CommandResult(follow_up=True)


class ListWindowsCommand(BaseCommand):
    """
    Список открытых окон.

    "какие окна открыты"
    "список окон"
    """
    name = "list_windows"
    keywords = [
        "какие окна открыты", "список окон",
        "открытые окна", "что открыто",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        windows = WindowManager.get_all_windows()

        if not windows:
            self.speaker.say("Открытых окон не найдено.")
            return CommandResult(follow_up=True)

        # Берём первые 7 (больше — слишком длинно для голоса)
        titles = []
        for w in windows[:7]:
            # Сокращаем длинные заголовки
            title = w["title"]
            if len(title) > 30:
                title = title[:27] + "..."
            titles.append(title)

        count = len(windows)
        shown = len(titles)

        msg = f"Открыто {count} окон. "
        if count > shown:
            msg += f"Первые {shown}: "
        msg += "; ".join(titles) + "."

        self.speaker.say(msg)
        return CommandResult(follow_up=True)