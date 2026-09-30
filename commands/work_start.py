# commands/work_start.py

"""
Специальная команда «Начинаем работу» (ранее macros.py).

Сценарий:
    1. Спрашивает язык (Python / C++)
    2. Параллельно открывает IDE + браузер
    3. Открывает PowerShell (сворачивает)
    4. Расставляет окна по мониторам
    5. Настраивает громкость
    6. Предлагает музыку

Рефакторинг v3.2:
- Убрано дублирование Win32 (теперь использует core.window_utils)
- Убрано дублирование VolumeControl (использует media._get_volume)
- Имя файла изменено для устранения путаницы с macro.py
"""

from __future__ import annotations

import os
import time
import subprocess
import webbrowser
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable
from urllib.parse import quote

from commands.base import BaseCommand, CommandResult
from core.window_utils import MonitorManager
from commands.window_control import WindowManager, SW_RESTORE, SW_MINIMIZE

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Конфигурация
# ═══════════════════════════════════════════════════════════════

class Language(Enum):
    PYTHON = "python"
    CPP = "cpp"

class WindowPosition(Enum):
    FULL = "full"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"

@dataclass(frozen=True)
class MacroConfig:
    """Единое место для всех настроек макроса."""
    vscode_path: str = r"C:\Users\Zeta\Desktop\Visual Studio Code.lnk"
    visual_studio_path: str = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Visual Studio.lnk"
    powershell_path: str = r"C:\Users\Zeta\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Windows PowerShell\Windows PowerShell.lnk"

    window_poll_interval: float = 0.3
    window_poll_timeout: float = 10.0
    listen_timeout: int = 6
    listen_phrase_limit: int = 6
    listen_retries: int = 2
    default_volume: int = 25

    homepage_url: str = "https://ya.ru"
    yandex_music_url: str = "https://music.yandex.ru/home"
    youtube_search_url: str = "https://www.youtube.com/results?search_query={query}"
    youtube_music_url: str = "https://www.youtube.com/feed/music"

    python_keywords: tuple[str, ...] = ("python", "питон", "пайтон", "пайфон")
    cpp_keywords: tuple[str, ...] = ("си плюс", "плюс плюс", "c++", "cpp", "си пп", "сиплюсплюс", "плюсы")
    yes_keywords: tuple[str, ...] = ("да", "давай", "конечно", "ага", "угу", "ok", "ок", "окей", "хорошо", "включи")
    no_keywords: tuple[str, ...] = ("нет", "не надо", "не нужно", "нее", "отмена", "пропусти", "без музыки")
    yandex_music_keywords: tuple[str, ...] = ("яндекс", "музык", "yandex")
    youtube_keywords: tuple[str, ...] = ("ютуб", "youtube", "ютюб", "тюб")

    vscode_window_titles: tuple[str, ...] = ("Visual Studio Code", "VS Code")
    vs_window_titles: tuple[str, ...] = ("Visual Studio",)
    browser_window_titles: tuple[str, ...] = ("Яндекс", "Yandex", "Chrome", "Edge", "Firefox", "Браузер")
    powershell_window_titles: tuple[str, ...] = ("PowerShell", "pwsh")

# ═══════════════════════════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════════════════════════

def safe_startfile(path: str) -> bool:
    if not os.path.exists(path):
        logger.warning("Файл не найден: %s", path)
        return False
    try:
        os.startfile(path)
        logger.debug("Запущен: %s", path)
        return True
    except OSError as e:
        logger.error("Не удалось запустить %s: %s", path, e)
        return False

def contains_any(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    text_lower = text.lower().strip()
    return any(kw in text_lower for kw in keywords)

# ═══════════════════════════════════════════════════════════════
#  Шаги макроса
# ═══════════════════════════════════════════════════════════════

class MacroSteps:
    def __init__(self, config: MacroConfig):
        self.config = config
        self.wm = WindowManager()

    def open_ide(self, language: Language) -> Optional[int]:
        if language == Language.PYTHON:
            path, titles = self.config.vscode_path, self.config.vscode_window_titles
        else:
            path, titles = self.config.visual_studio_path, self.config.vs_window_titles

        if not safe_startfile(path):
            try:
                cmd = "code" if language == Language.PYTHON else "devenv"
                subprocess.Popen([cmd], shell=True)
                logger.info("IDE запущена через PATH fallback")
            except FileNotFoundError:
                logger.error("IDE не найдена ни по ярлыку, ни в PATH")
                return None

        # Ожидание окна (используем метод из commands.window_control)
        deadline = time.monotonic() + self.config.window_poll_timeout
        while time.monotonic() < deadline:
            hwnd = self.wm.find_window_by_name(titles[0]) # Используем новый метод реестра
            if hwnd:
                return hwnd
            time.sleep(self.config.window_poll_interval)
        return None

    def open_browser(self) -> Optional[int]:
        webbrowser.open(self.config.homepage_url)
        deadline = time.monotonic() + self.config.window_poll_timeout
        while time.monotonic() < deadline:
            hwnd = self.wm.find_window_by_name(self.config.browser_window_titles[0])
            if hwnd:
                return hwnd
            time.sleep(self.config.window_poll_interval)
        return None

    def open_powershell(self) -> Optional[int]:
        if not safe_startfile(self.config.powershell_path):
            try:
                subprocess.Popen(["powershell.exe"], creationflags=subprocess.CREATE_NEW_CONSOLE)
            except FileNotFoundError:
                return None
        
        deadline = time.monotonic() + self.config.window_poll_timeout
        while time.monotonic() < deadline:
            hwnd = self.wm.find_window_by_name("PowerShell")
            if hwnd:
                self.wm.minimize_window(hwnd)
                return hwnd
            time.sleep(self.config.window_poll_interval)
        return None

    def arrange_windows(self, ide_hwnd: Optional[int], browser_hwnd: Optional[int]) -> None:
        monitors = MonitorManager.get_monitors()
        if not monitors:
            return

        if len(monitors) == 1:
            if ide_hwnd: self.wm.snap_window(ide_hwnd, "left")
            if browser_hwnd: self.wm.snap_window(browser_hwnd, "right")
        else:
            secondary = monitors[1]
            if secondary.is_vertical:
                if browser_hwnd: self.wm.move_window(browser_hwnd, secondary.x, secondary.y, secondary.width, secondary.height)
                if ide_hwnd: self.wm.snap_window(ide_hwnd, "left") # Упрощённо: full на первом мониторе
            else:
                if ide_hwnd: self.wm.snap_window(ide_hwnd, "left")
                if browser_hwnd: self.wm.move_window(browser_hwnd, secondary.x, secondary.y, secondary.width, secondary.height)

        if ide_hwnd:
            time.sleep(0.2)
            self.wm.focus_window(ide_hwnd)

    def open_yandex_music(self): webbrowser.open(self.config.yandex_music_url)
    def open_youtube_search(self, query): webbrowser.open(self.config.youtube_search_url.format(query=quote(query)))
    def open_youtube_music(self): webbrowser.open(self.config.youtube_music_url)


# ═══════════════════════════════════════════════════════════════
#  Команда
# ═══════════════════════════════════════════════════════════════

class WorkStartCommand(BaseCommand):
    name = "macro_work"
    keywords = ["начинаем работу", "рабочий режим", "начни работу", "за работу", "поехали работать"]
    priority = 15

    def __init__(self, ctx):
        super().__init__(ctx)
        self._recognizer = ctx.recognizer
        self._config = MacroConfig()
        self._steps = MacroSteps(self._config)

    def execute(self, text: str, params: dict) -> CommandResult:
        logger.info("▶ Макрос «Начинаем работу» запущен")
        self.speaker.say("Настраиваю рабочее пространство.")

        language = self._ask_language()
        ide_hwnd, browser_hwnd = self._open_apps_parallel(language)
        
        self._safe_step("Расстановка окон", lambda: self._steps.arrange_windows(ide_hwnd, browser_hwnd))
        
        vol = self.ctx.volume_control
        if vol:
            self._safe_step("Настройка громкости", lambda: vol.set_volume(self._config.default_volume))
        
        self._safe_step("Музыка", self._ask_music)

        self.speaker.say("Готово. Приятной работы!")
        return CommandResult(follow_up=True)

    def _ask_language(self) -> Language:
        self.speaker.say("Python или C плюс плюс?")
        for _ in range(self._config.listen_retries):
            resp = self._listen()
            if not resp: continue
            if contains_any(resp, self._config.python_keywords):
                self.speaker.say("Принято, Python.")
                return Language.PYTHON
            if contains_any(resp, self._config.cpp_keywords):
                self.speaker.say("Принято, C плюс плюс.")
                return Language.CPP
        self.speaker.say("По умолчанию открою Python.")
        return Language.PYTHON

    def _ask_music(self) -> None:
        self.speaker.say("Включить музыку?")
        resp = self._listen()
        if not resp or contains_any(resp, self._config.no_keywords) or not contains_any(resp, self._config.yes_keywords):
            return

        self.speaker.say("Яндекс Музыка или YouTube?")
        resp = self._listen()
        if not resp: return

        if contains_any(resp, self._config.yandex_music_keywords):
            self._steps.open_yandex_music()
            self.speaker.say("Открываю Яндекс Музыку.")
        elif contains_any(resp, self._config.youtube_keywords):
            self.speaker.say("Что найти на YouTube?")
            query = self._listen(timeout=8)
            if query and query.strip():
                self._steps.open_youtube_search(query)
                self.speaker.say(f"Ищу: {query}.")
            else:
                self._steps.open_youtube_music()
                self.speaker.say("Открываю YouTube Music.")

    def _open_apps_parallel(self, language: Language) -> tuple[Optional[int], Optional[int]]:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_ide = pool.submit(self._safe_step_return, "IDE", lambda: self._steps.open_ide(language))
            f_browser = pool.submit(self._safe_step_return, "Браузер", self._steps.open_browser)
            f_ps = pool.submit(self._safe_step_return, "PowerShell", self._steps.open_powershell)
            for _ in as_completed([f_ide, f_browser, f_ps]): pass
            return f_ide.result(), f_browser.result()

    def _listen(self, timeout=None) -> Optional[str]:
        if not self._recognizer: return None
        try:
            return self._recognizer.listen(timeout=timeout or self._config.listen_timeout, phrase_limit=self._config.listen_phrase_limit)
        except Exception:
            return None

    def _safe_step(self, name: str, func: Callable):
        try: func()
        except Exception: pass

    def _safe_step_return(self, name: str, func: Callable):
        try: return func()
        except Exception: return None