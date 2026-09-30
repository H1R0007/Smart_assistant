# commands/apps.py

"""
Открытие и закрытие приложений.

Изменения v3.1:
- Поиск по границам слов (не подстроки)
- Стемминг для нормализации окончаний
- is_valid_candidate вместо хардкода в роутере
"""

import os
import subprocess
import logging
import threading
from pathlib import Path

from commands.base import BaseCommand, CommandResult, stem_russian
from core.app_registry import get_app_registry

logger = logging.getLogger(__name__)


class AppSearcher:
    """
    Утилита для поиска приложений в тексте.
    Заменяет старый AppRegistry.find_app().
    """
    def __init__(self, registry):
        self.registry = registry
        # Предвычисляем стемы алиасов для быстрого поиска
        self._stemmed_aliases = {}
        for alias, app_cmd in self.registry.get_all_aliases().items():
            stemmed = " ".join(stem_russian(w) for w in alias.split())
            self._stemmed_aliases[stemmed] = (alias, app_cmd)

    def find_app(self, text: str) -> list[dict]:
        """Найти все приложения упомянутые в тексте."""
        results = []
        text_lower = text.lower()
        text_words = text_lower.split()
        text_stemmed = " ".join(stem_russian(w) for w in text_words)

        all_aliases = self.registry.get_all_aliases()
        sorted_aliases = sorted(all_aliases.items(), key=lambda x: len(x[0]), reverse=True)
        found_positions = set()

        for alias, app_cmd in sorted_aliases:
            # Точное совпадение
            match_pos = self._find_word_boundary(alias, text_words, found_positions)
            if match_pos is not None:
                results.append({"app": app_cmd, "raw_name": alias})
                found_positions.update(match_pos)
                continue

            # Стемминг
            alias_stemmed = " ".join(stem_russian(w) for w in alias.split())
            if alias_stemmed in self._stemmed_aliases:
                stemmed_words = text_stemmed.split()
                match_pos = self._find_word_boundary(alias_stemmed, stemmed_words, found_positions)
                if match_pos is not None:
                    _, original_app_cmd = self._stemmed_aliases[alias_stemmed]
                    results.append({"app": original_app_cmd, "raw_name": alias})
                    found_positions.update(match_pos)

        return results

    @staticmethod
    def _find_word_boundary(phrase: str, text_words: list[str], occupied: set[int]) -> set[int] | None:
        phrase_words = phrase.split()
        phrase_len = len(phrase_words)
        for i in range(len(text_words) - phrase_len + 1):
            positions = set(range(i, i + phrase_len))
            if positions & occupied:
                continue
            if text_words[i:i + phrase_len] == phrase_words:
                return positions
        return None


class StartMenuCache:
    """
    Кэш ярлыков Start Menu.

    Проблема: сканирование Start Menu = 50-200мс на каждый запрос.
    При 3+ неизвестных приложениях — заметная задержка.

    Решение: сканируем ОДИН РАЗ при запуске, храним в памяти.
    Инвалидируем каждые 10 минут (приложения ставятся не часто).
    """

    CACHE_TTL = 600  # 10 минут

    def __init__(self):
        self._cache: dict[str, str] = {}  # name_lower → lnk_path
        self._last_scan: float = 0
        self._lock = threading.Lock()

    def find(self, app_name: str) -> str | None:
        """Найти приложение в кэше Start Menu."""
        self._ensure_scanned()

        app_lower = app_name.lower()

        with self._lock:
            # Точное совпадение
            if app_lower in self._cache:
                return self._cache[app_lower]

            # Подстрока
            for name, path in self._cache.items():
                if app_lower in name:
                    return path

        return None

    def _ensure_scanned(self) -> None:
        """Сканировать если кэш устарел."""
        import time as _time
        now = _time.time()

        if now - self._last_scan < self.CACHE_TTL:
            return

        with self._lock:
            # Double-check после захвата lock
            if now - self._last_scan < self.CACHE_TTL:
                return
            self._scan()
            self._last_scan = now

    def _scan(self) -> None:
        """Сканирование Start Menu."""
        self._cache.clear()

        search_dirs = []

        common = Path(os.environ.get(
            "PROGRAMDATA", "C:\\ProgramData"
        )) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if common.exists():
            search_dirs.append(common)

        user = Path(os.environ.get(
            "APPDATA", ""
        )) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if user.exists():
            search_dirs.append(user)

        for search_dir in search_dirs:
            try:
                for lnk_file in search_dir.rglob("*.lnk"):
                    name = lnk_file.stem.lower()
                    self._cache[name] = str(lnk_file)
            except PermissionError:
                pass

        logger.debug(
            "Start Menu cache: %d ярлыков", len(self._cache)
        )

_start_menu_cache = StartMenuCache()

class OpenAppCommand(BaseCommand):
    """Открыть одно или несколько приложений."""

    name = "open_app"
    description = "Открывать программы"
    category = "Приложения" 
    keywords = [
        "открой", "запусти", "включи",
        "открыть", "запустить", "включить",
        "запуск", "покажи"
    ]
    priority = 5

    def __init__(self, ctx):
        super().__init__(ctx)
        self.registry = get_app_registry(ctx.config)
        self.searcher = AppSearcher(self.registry)

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords + [
            "пожалуйста", "мне", "приложение", "программу"
        ])
        apps = self.searcher.find_app(cleaned)
        return {"apps": apps}

    def is_valid_candidate(self, params: dict) -> bool:
        """Должны быть найдены приложения."""
        return bool(params.get("apps"))

    def execute(self, text: str, params: dict) -> CommandResult:
        apps = params.get("apps", [])

        if not apps:
            self.speaker.say("Не поняла, какое приложение открыть.")
            return CommandResult(follow_up=True)

        opened = []
        failed = []

        for app_info in apps:
            app_cmd = app_info["app"]
            raw_name = app_info["raw_name"]

            success = self._launch(app_cmd, raw_name)
            if success:
                opened.append(raw_name)
            else:
                failed.append(raw_name)

        if opened and not failed:
            if len(opened) == 1:
                self.speaker.say(f"Открываю {opened[0]}.")
            else:
                self.speaker.say(f"Открываю {', '.join(opened)}.")
        elif failed and not opened:
            self.speaker.say(f"Не удалось открыть {', '.join(failed)}.")
        elif opened and failed:
            self.speaker.say(
                f"Открыла {', '.join(opened)}. "
                f"Не удалось: {', '.join(failed)}."
            )

        return CommandResult(follow_up=True)

    def _launch(self, app_cmd: str, name: str) -> bool:
        try:
            if ":" in app_cmd and not app_cmd.endswith(".exe"):
                os.startfile(app_cmd)
                self.logger.info("Запущено (startfile): %s", app_cmd)
                return True

            try:
                subprocess.Popen(
                    [app_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.logger.info("Запущено: %s", app_cmd)
                return True
            except FileNotFoundError:
                pass

            try:
                subprocess.Popen(
                    app_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self.logger.info("Запущено (shell): %s", app_cmd)
                return True
            except Exception:
                pass

            found = self._find_in_start_menu(app_cmd)
            if found:
                os.startfile(found)
                self.logger.info("Запущено (Start Menu): %s", found)
                return True

            self.logger.warning("Не найдено: %s", app_cmd)
            return False

        except Exception as e:
            self.logger.error("Ошибка запуска %s: %s", app_cmd, e)
            return False
    
    def update_context(self, params: dict) -> None:
        if params.get("apps"):
            app = params["apps"][0]
            self.ctx.context.update("open_app", app["app"], params)

    @staticmethod
    def _find_in_start_menu(app_name: str) -> str | None:
        """Поиск через кэш Start Menu."""
        return _start_menu_cache.find(app_name)


class CloseAppCommand(BaseCommand):
    """Закрыть приложение."""

    name = "close_app"
    description = "Закрывать программы" 
    category = "Приложения"    
    keywords = [
        "закрой", "закрыть", "выключи приложение",
        "останови", "убери", "выруби"
    ]
    priority = 5
    fuzzy_enabled = False

    def __init__(self, ctx):
        super().__init__(ctx)
        self.registry = get_app_registry(ctx.config)
        self.searcher = AppSearcher(self.registry)

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords + [
            "пожалуйста", "приложение", "программу"
        ])
        apps = self.searcher.find_app(cleaned)
        return {"apps": apps}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("apps"))

    def execute(self, text: str, params: dict) -> CommandResult:
        apps = params.get("apps", [])

        if not apps:
            self.speaker.say("Не поняла, какое приложение закрыть.")
            return CommandResult(follow_up=True)

        closed = []
        not_running = []
        failed = []

        for app_info in apps:
            app_cmd = app_info["app"]
            raw_name = app_info["raw_name"]

            result = self._close(app_cmd, raw_name)
            if result == "closed":
                closed.append(raw_name)
            elif result == "not_running":
                not_running.append(raw_name)
            else:
                failed.append(raw_name)

        parts = []
        if closed:
            names = ", ".join(closed)
            parts.append(f"Закрываю {names}")
        if not_running:
            names = ", ".join(not_running)
            parts.append(f"{names} не запущен")
        if failed:
            names = ", ".join(failed)
            parts.append(f"не удалось закрыть {names}")

        self.speaker.say(". ".join(parts) + ".")
        return CommandResult(follow_up=True)

    def _close(self, app_cmd: str, name: str) -> str:
        # Находим AppInfo по алиасу
        app_info = self.registry.get_by_alias(name)
        if app_info:
            process_name = app_info.process_name
        else:
            # Fallback
            process_name = app_cmd if app_cmd.endswith(".exe") else f"{app_cmd}.exe"

        if not self._is_running(process_name):
            return "not_running"

        try:
            result = subprocess.run(
                ["taskkill", "/im", process_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.logger.info("Закрыто (мягко): %s", process_name)
                return "closed"

            result = subprocess.run(
                ["taskkill", "/f", "/im", process_name],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.logger.info("Закрыто (force): %s", process_name)
                return "closed"

            self.logger.warning("taskkill failed: %s", process_name)
            return "failed"

        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    ["taskkill", "/f", "/im", process_name],
                    capture_output=True, timeout=3
                )
                return "closed"
            except Exception:
                return "failed"
        except Exception as e:
            self.logger.error("Ошибка закрытия %s: %s", process_name, e)
            return "failed"

    @staticmethod
    def _is_running(process_name: str) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/fi", f"imagename eq {process_name}",
                 "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=3
            )
            return process_name.lower() in result.stdout.lower()
        except Exception:
            return True

    def match(self, text: str) -> tuple[bool, dict]:
        t = text.lower().strip()

        # Контекстный матч
        if t in ["закрой его", "закрой это", "закрой", "закрой приложение"]:
            ctx = self.ctx.context.get()
            if ctx.is_fresh and ctx.action == "open_app":
                self.logger.info(f"Контекстное закрытие: {ctx.target}")
                return True, {
                    "apps": [{"app": ctx.target, "raw_name": "последнее приложение"}],
                    "is_context": True
                }

        return super().match(text)