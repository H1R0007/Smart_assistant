# core/macros/executor.py

"""
Выполнение шагов макроса.

Архитектура:
- Последовательное выполнение в вызывающем потоке
- Прерывание через _stop_event (между шагами)
- Таймаут на весь макрос (120 сек)
- Защита от рекурсии через _call_stack
- Каждый шаг изолирован: сбой одного не ломает остальные

Почему шаги выполняются напрямую, а не через роутер команд:
- Команды (BaseCommand) озвучивают каждый результат,
  обновляют контекст — макросу это не нужно.
- 5 шагов подряд с озвучкой каждого → утомительно.
- Executor использует те же утилиты (AppRegistry, webbrowser, pycaw),
  но без озвучки. Greeting и farewell достаточны.
"""

import os
import time
import subprocess
import webbrowser
import logging
import threading

from core.macros.macro import Macro, MacroStep

logger = logging.getLogger(__name__)


class MacroExecutor:
    """
    Выполняет шаги макроса последовательно.

    Потокобезопасность:
        - execute() блокирует вызывающий поток
        - stop() можно вызвать из любого потока
    """

    MAX_NESTING_DEPTH = 3
    MACRO_TIMEOUT = 120.0
    DEFAULT_STEP_DELAY = 0.5

    def __init__(self, ctx, macros: dict[str, Macro]):
        self._ctx = ctx
        self._macros = macros
        self._speaker = ctx.speaker
        self._config = ctx.config

        self._stop_event = threading.Event()
        self._call_stack: list[str] = []
        self._app_registry = None

    # ══════════════════════════════════════════════
    #  Публичный API
    # ══════════════════════════════════════════════

    def execute(self, macro: Macro) -> bool:
        """
        Выполнить макрос. Блокирующий вызов.

        Returns: True если все шаги пройдены (best-effort),
                 False если прерван / рекурсия / таймаут.
        """
        # Защита от рекурсии
        if macro.id in self._call_stack:
            logger.error(
                "Рекурсия: макрос '%s' уже в стеке вызовов %s",
                macro.id, self._call_stack,
            )
            self._speak(
                f"Макрос {macro.name} вызывает сам себя. Прерываю."
            )
            return False

        if len(self._call_stack) >= self.MAX_NESTING_DEPTH:
            logger.error(
                "Глубина вложенности %d (макс. %d): %s → %s",
                len(self._call_stack), self.MAX_NESTING_DEPTH,
                " → ".join(self._call_stack), macro.id,
            )
            self._speak("Слишком глубокая вложенность макросов.")
            return False

        # Начало выполнения
        self._call_stack.append(macro.id)

        # Сбрасываем stop только на верхнем уровне
        is_top_level = len(self._call_stack) == 1
        if is_top_level:
            self._stop_event.clear()

        start_time = time.monotonic()
        success_count = 0
        fail_count = 0

        try:
            if macro.greeting:
                self._speak(macro.greeting)

            for i, step in enumerate(macro.steps, 1):
                # Прерывание
                if self._stop_event.is_set():
                    logger.info(
                        "Макрос '%s' прерван на шаге %d/%d",
                        macro.name, i, len(macro.steps),
                    )
                    self._speak("Макрос прерван.")
                    return False

                # Таймаут
                elapsed = time.monotonic() - start_time
                if elapsed > self.MACRO_TIMEOUT:
                    logger.warning(
                        "Макрос '%s': таймаут %.0f сек на шаге %d/%d",
                        macro.name, self.MACRO_TIMEOUT, i, len(macro.steps),
                    )
                    self._speak("Макрос превысил время выполнения.")
                    return False

                # Выполнение шага
                logger.info(
                    "  [%d/%d] %s: %s",
                    i, len(macro.steps),
                    step.action, step.target or step.value or "—",
                )

                ok = self._execute_step(step)
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
                    logger.warning(
                        "  [%d/%d] ✗ %s не выполнен",
                        i, len(macro.steps), step.action,
                    )

                # Пауза между шагами
                delay = step.delay if step.delay > 0 else self.DEFAULT_STEP_DELAY
                if not self._interruptible_wait(delay):
                    return False

            if macro.farewell:
                self._speak(macro.farewell)

            elapsed = time.monotonic() - start_time
            logger.info(
                "✔ Макрос '%s' завершён за %.1fs "
                "(успешно: %d, ошибок: %d)",
                macro.name, elapsed, success_count, fail_count,
            )
            return True

        except Exception as e:
            logger.error(
                "Критическая ошибка в макросе '%s': %s",
                macro.name, e, exc_info=True,
            )
            self._speak("Произошла ошибка при выполнении макроса.")
            return False

        finally:
            self._call_stack.pop()

    def stop(self) -> None:
        """Прервать выполнение (из любого потока)."""
        self._stop_event.set()
        logger.info("Запрошена остановка макроса")

    @property
    def is_running(self) -> bool:
        return len(self._call_stack) > 0

    # ══════════════════════════════════════════════
    #  Диспетчеризация шагов
    # ══════════════════════════════════════════════

    def _execute_step(self, step: MacroStep) -> bool:
        """Выполнить один шаг с подстановкой переменных."""
        target = self._substitute_vars(step.target)
        value = self._substitute_vars(step.value)

        try:
            handler = self._STEP_HANDLERS.get(step.action)
            if handler is None:
                logger.warning("Нет обработчика для '%s'", step.action)
                return False
            return handler(self, target, value)

        except Exception as e:
            logger.error(
                "Ошибка в шаге %s (target=%s, value=%s): %s",
                step.action, target, value, e,
            )
            return False

    # ══════════════════════════════════════════════
    #  Обработчики шагов
    # ══════════════════════════════════════════════

    def _step_open_app(self, target: str, value: str) -> bool:
        """Открыть приложение через AppRegistry → subprocess → Start Menu."""
        app_cmd = target

        registry = self._get_app_registry()
        if registry:
            apps = registry.find_app(target)
            if apps:
                app_cmd = apps[0]["app"]

        return self._launch_app(app_cmd)

    def _step_open_url(self, target: str, value: str) -> bool:
        """Открыть URL в браузере по умолчанию."""
        url = target
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            webbrowser.open(url)
            logger.debug("URL открыт: %s", url)
            return True
        except Exception as e:
            logger.error("Не удалось открыть URL '%s': %s", url, e)
            return False

    def _step_volume(self, target: str, value: str) -> bool:
        """Установить системную громкость."""
        raw = value or target
        try:
            percent = max(0, min(100, int(raw)))
        except (ValueError, TypeError):
            logger.error("Некорректное значение громкости: '%s'", raw)
            return False

        return self._set_system_volume(percent)

    def _step_brightness(self, target: str, value: str) -> bool:
        """Установить яркость экрана."""
        raw = value or target
        try:
            percent = max(5, min(100, int(raw)))
        except (ValueError, TypeError):
            logger.error("Некорректное значение яркости: '%s'", raw)
            return False

        try:
            import screen_brightness_control as sbc
            sbc.set_brightness(percent)
            logger.debug("Яркость: %d%%", percent)
            return True
        except ImportError:
            logger.warning("screen_brightness_control не установлен")
            return False
        except Exception as e:
            logger.error("Ошибка установки яркости: %s", e)
            return False

    def _step_wait(self, target: str, value: str) -> bool:
        """Пауза N секунд (с прерыванием)."""
        raw = target or value or "1"
        try:
            seconds = max(0.0, float(raw))
        except (ValueError, TypeError):
            seconds = 1.0

        return self._interruptible_wait(seconds)

    def _step_say(self, target: str, value: str) -> bool:
        """Произнести фразу."""
        text = target or value
        if text:
            self._speak(text)
        return True

    def _step_run_macro(self, target: str, value: str) -> bool:
        """Запустить вложенный макрос (рекурсия проверяется в execute)."""
        macro_id = target
        macro = self._macros.get(macro_id)
        if not macro:
            logger.warning(
                "Вложенный макрос '%s' не найден. Доступные: %s",
                macro_id, list(self._macros.keys()),
            )
            return False

        logger.info("  → Запуск вложенного макроса '%s'", macro.name)
        return self.execute(macro)

    # Таблица диспетчеризации.
    # Добавление нового действия = одна строка здесь + метод выше.
    _STEP_HANDLERS = {
        "open_app":   _step_open_app,
        "open_url":   _step_open_url,
        "volume":     _step_volume,
        "brightness": _step_brightness,
        "wait":       _step_wait,
        "say":        _step_say,
        "run_macro":  _step_run_macro,
    }

    # ══════════════════════════════════════════════
    #  Утилиты
    # ══════════════════════════════════════════════

    def _launch_app(self, app_cmd: str) -> bool:
        """Запустить приложение: protocol → direct → shell → Start Menu."""
        if not app_cmd:
            return False

        # 1. Протокол (ms-settings:, steam://)
        if ":" in app_cmd and not app_cmd.endswith(".exe"):
            try:
                os.startfile(app_cmd)
                logger.debug("Запущено (protocol): %s", app_cmd)
                return True
            except OSError as e:
                logger.debug("Protocol failed: %s — %s", app_cmd, e)

        # 2. Прямой запуск
        try:
            subprocess.Popen(
                [app_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.debug("Запущено (direct): %s", app_cmd)
            return True
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("Direct failed: %s — %s", app_cmd, e)

        # 3. Shell (PATH)
        try:
            subprocess.Popen(
                app_cmd, shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.debug("Запущено (shell): %s", app_cmd)
            return True
        except Exception as e:
            logger.debug("Shell failed: %s — %s", app_cmd, e)

        # 4. Start Menu
        try:
            from commands.apps import _start_menu_cache
            found = _start_menu_cache.find(app_cmd)
            if found:
                os.startfile(found)
                logger.debug("Запущено (Start Menu): %s", found)
                return True
        except (ImportError, Exception) as e:
            logger.debug("Start Menu failed: %s", e)

        logger.warning("Приложение не найдено: %s", app_cmd)
        return False

    def _set_system_volume(self, percent: int) -> bool:
        """Громкость через pycaw с COM-инициализацией."""
        try:
            import comtypes
            try:
                comtypes.CoInitialize()
            except OSError:
                pass

            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None,
            )
            vol = interface.QueryInterface(IAudioEndpointVolume)
            vol.SetMasterVolumeLevelScalar(percent / 100.0, None)
            logger.debug("Громкость: %d%%", percent)
            return True

        except ImportError:
            logger.warning("pycaw/comtypes не установлены")
            return False
        except Exception as e:
            logger.error("Ошибка установки громкости: %s", e)
            return False

    def _interruptible_wait(self, seconds: float) -> bool:
        """Пауза с прерыванием. Returns True если дождались, False если прервано."""
        if seconds <= 0:
            return not self._stop_event.is_set()
        return not self._stop_event.wait(timeout=seconds)

    def _substitute_vars(self, text: str) -> str:
        """Подставить {time}, {date}, {last_target}, {last_app}."""
        if not text or "{" not in text:
            return text

        last_target = ""
        last_app = ""
        try:
            from core.context import get_context
            ctx_state = get_context().get()
            last_target = ctx_state.target or ""
            if ctx_state.action == "open_app":
                last_app = ctx_state.target or ""
        except Exception:
            pass

        replacements = {
            "{time}": time.strftime("%H:%M"),
            "{date}": time.strftime("%d.%m.%Y"),
            "{last_target}": last_target,
            "{last_app}": last_app,
        }

        result = text
        for var, val in replacements.items():
            if var in result:
                result = result.replace(var, val)

        return result

    def _speak(self, text: str) -> None:
        """Произнести текст, если speaker доступен."""
        if self._speaker and text:
            self._speaker.say(text)

    def _get_app_registry(self):
        """Ленивая загрузка AppRegistry."""
        if self._app_registry is None:
            try:
                from commands.apps import get_registry
                self._app_registry = get_registry(self._config)
            except ImportError:
                logger.debug("commands.apps не доступен для AppRegistry")
        return self._app_registry