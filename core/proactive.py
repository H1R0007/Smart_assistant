# core/proactive.py

"""
Проактивные предложения: связь PatternEngine с голосом.

Два режима:
1. After-command: проверка pending suggestion после команды
2. Idle: фоновая проверка каждые N минут

v2: полноценная поддержка create_macro:
- _handle_create_macro сохраняет макрос через auto_generator
- _reload_macro_command обновляет MacroCommand через router API
- Голосовой фидбек с ключевым словом запуска

Потокобезопасность:
- voice_lock: speaker + recognizer используются
  одним потоком в момент времени
- Idle-поток пропускает итерацию если voice_lock занят
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from core.patterns.suggestions import Suggestion

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Статистика сессии
# ═══════════════════════════════════════════

@dataclass
class ProactiveStats:
    """
    Счётчики текущей сессии.

    Для исторических данных → storage.get_feedback_stats().
    """
    shown: int = 0
    accepted: int = 0
    rejected: int = 0
    ignored: int = 0

    @property
    def total_resolved(self) -> int:
        return self.accepted + self.rejected

    @property
    def acceptance_rate(self) -> float:
        total = self.total_resolved
        return self.accepted / total if total else 0.0

    def summary(self) -> str:
        rate = (
            f"{self.acceptance_rate:.0%}"
            if self.total_resolved else "—"
        )
        return (
            f"Предложения: {self.shown} показано, "
            f"{self.accepted} принято, "
            f"{self.rejected} отклонено, "
            f"{self.ignored} проигнорировано "
            f"(acceptance: {rate})"
        )


# ═══════════════════════════════════════════
#  Менеджер проактивных предложений
# ═══════════════════════════════════════════

class ProactiveManager:
    """
    Связывает KnowledgeBase (анализ + pending suggestions)
    с голосовым интерфейсом (speaker/recognizer).

    Использование в main.py:
        proactive = ProactiveManager(
            knowledge=self._knowledge,
            speaker=self._speaker,
            recognizer=self._recognizer,
            listener=self._listener,
            router=self._router,
            tray=self._tray,
        )
        proactive.start()

        # В _handle_wake:
        proactive.begin_interaction()
        try:
            conversation_loop()
            proactive.try_after_command()
        finally:
            proactive.end_interaction()

        proactive.stop()
    """

    # ── Настройки ──
    IDLE_CHECK_INTERVAL = 600   # 10 мин между idle-проверками
    RESPONSE_TIMEOUT = 8        # Ожидание ответа (сек)
    IDLE_QUIET_AFTER_WAKE = 60  # Тишина N сек после диалога

    def __init__(
        self,
        knowledge,      # KnowledgeBase
        speaker,        # Speaker
        recognizer,     # Recognizer
        listener,       # Listener
        router,         # CommandRouter
        tray,           # TrayIcon
    ):
        self._knowledge = knowledge
        self._speaker = speaker
        self._recognizer = recognizer
        self._listener = listener
        self._router = router
        self._tray = tray

        # Голосовой мьютекс
        self._voice_lock = threading.Lock()

        # Фоновый поток
        self._idle_stop = threading.Event()
        self._idle_thread: Optional[threading.Thread] = None

        # Время последнего взаимодействия
        self._last_interaction: float = 0

        self._enabled = True
        self.stats = ProactiveStats()

    # ══════════════════════════════════════════════
    #  Управление
    # ══════════════════════════════════════════════

    def start(self) -> None:
        """Запустить фоновый idle-checker."""
        if self._idle_thread and self._idle_thread.is_alive():
            return

        self._idle_stop.clear()
        self._idle_thread = threading.Thread(
            target=self._idle_loop,
            daemon=True,
            name="proactive-idle",
        )
        self._idle_thread.start()
        logger.info("ProactiveManager: idle checker запущен")

    def stop(self) -> None:
        """Остановить idle-checker."""
        self._idle_stop.set()
        if self._idle_thread:
            self._idle_thread.join(timeout=5)
        logger.info(
            "ProactiveManager остановлен. %s",
            self.stats.summary(),
        )

    def set_enabled(self, enabled: bool) -> None:
        """Включить/выключить проактивные предложения."""
        self._enabled = enabled
        logger.info(
            "Проактивные предложения: %s",
            "ВКЛ" if enabled else "ВЫКЛ",
        )

    # ══════════════════════════════════════════════
    #  Voice session (вызывается из main.py)
    # ══════════════════════════════════════════════

    def begin_interaction(self) -> None:
        """Захватить voice_lock перед диалогом."""
        self._voice_lock.acquire()

    def end_interaction(self) -> None:
        """Освободить voice_lock после диалога."""
        self._last_interaction = time.time()
        try:
            self._voice_lock.release()
        except RuntimeError:
            pass

    # ══════════════════════════════════════════════
    #  After-command: проверка после команды
    # ══════════════════════════════════════════════

    def try_after_command(self) -> bool:
        """
        Проверить pending suggestion после голосовой команды.

        ВАЖНО: вызывать только с захваченным voice_lock.

        Returns: True если предложение было показано
        """
        if not self._enabled:
            return False

        suggestion = self._knowledge.get_pending_suggestion()
        if not suggestion:
            return False

        logger.info(
            "After-command suggestion: %s", suggestion.text,
        )
        self._present_suggestion(suggestion)
        return True

    # ══════════════════════════════════════════════
    #  Idle: фоновые проверки
    # ══════════════════════════════════════════════

    def _idle_loop(self) -> None:
        """Фоновый цикл idle-проверок."""
        self._idle_stop.wait(timeout=self.IDLE_CHECK_INTERVAL)

        while not self._idle_stop.is_set():
            try:
                self._idle_check()
            except Exception as e:
                logger.error(
                    "Idle check error: %s", e, exc_info=True,
                )
            self._idle_stop.wait(timeout=self.IDLE_CHECK_INTERVAL)

    def _idle_check(self) -> None:
        """Одна итерация idle-проверки."""
        if not self._enabled:
            return

        if time.time() - self._last_interaction < self.IDLE_QUIET_AFTER_WAKE:
            return

        suggestion = self._knowledge.get_pending_suggestion()

        if not suggestion:
            suggestion = self._knowledge.patterns.check_idle()

        if not suggestion:
            return

        if not self._voice_lock.acquire(blocking=False):
            logger.debug(
                "Idle suggestion skipped: voice_lock busy",
            )
            self._knowledge.patterns.record_feedback(
                suggestion.id, ignored=True,
            )
            self.stats.ignored += 1
            return

        try:
            self._listener.pause()
            self._tray.set_active()

            logger.info("Idle suggestion: %s", suggestion.text)

            self._speaker.beep()
            time.sleep(0.3)

            self._present_suggestion(suggestion)

        finally:
            self._last_interaction = time.time()
            self._tray.set_listening()
            self._listener.resume()
            self._voice_lock.release()

    # ══════════════════════════════════════════════
    #  Диалог предложения
    # ══════════════════════════════════════════════

    def _present_suggestion(self, suggestion: Suggestion) -> None:
        """
        Озвучить предложение и обработать ответ.

        Диалоговое дерево:
        ┌─ text ("Обычно ты открываешь X. Открыть?")
        ├─ "да"      → выполнить → accepted
        ├─ "нет"     → rejected
        ├─ "почему?" → brief → "Выполнить?"
        ├─ таймаут   → ignored
        └─ непонятно → переспрос (1 раз)
        """
        self._tray.set_processing()
        self.stats.shown += 1

        self._speaker.say(suggestion.text)

        response = self._listen_response()

        if response is None:
            self._record_feedback(suggestion, "ignored")
            return

        intent = self._classify_response(response)

        if intent == "why":
            intent = self._why_dialog(suggestion)

        if intent == "unknown":
            intent = self._retry_dialog(suggestion)

        if intent == "accept":
            self._record_feedback(suggestion, "accepted")
            self._execute_action(suggestion)

        elif intent == "reject":
            self._record_feedback(suggestion, "rejected")

        else:
            self._record_feedback(suggestion, "ignored")

    def _why_dialog(self, suggestion: Suggestion) -> str:
        """Обработка "почему?" — до двух уровней."""
        self._speaker.say(suggestion.brief_explanation)
        self._speaker.say("Выполнить?")

        response = self._listen_response()
        if response is None:
            return "ignored"

        intent = self._classify_response(response)

        if intent == "why":
            self._speaker.say(suggestion.explanation)
            self._speaker.say("Выполнить?")

            response = self._listen_response()
            if response is None:
                return "ignored"
            intent = self._classify_response(response)

        return intent

    def _retry_dialog(self, suggestion: Suggestion) -> str:
        """Переспрос при непонятном ответе (один раз)."""
        target = suggestion.action_params.get("target", "")
        if target:
            self._speaker.say(
                f"Извини, не расслышала. "
                f"Открыть {target} — да или нет?"
            )
        else:
            self._speaker.say(
                "Извини, не расслышала. Да или нет?"
            )

        response = self._listen_response()
        if response is None:
            return "ignored"

        intent = self._classify_response(response)
        return intent if intent != "unknown" else "ignored"

    # ══════════════════════════════════════════════
    #  Классификация ответов
    # ══════════════════════════════════════════════

    def _listen_response(self) -> str | None:
        """Слушать ответ пользователя с таймаутом."""
        self._tray.set_active()
        text = self._recognizer.listen(
            timeout=self.RESPONSE_TIMEOUT,
            phrase_limit=5,
        )
        self._tray.set_processing()
        return text

    @staticmethod
    def _classify_response(text: str) -> str:
        from core.response_classifier import classify
        return classify(text)

    # ══════════════════════════════════════════════
    #  Обратная связь
    # ══════════════════════════════════════════════

    def _record_feedback(
        self, suggestion: Suggestion, status: str,
    ) -> None:
        """Записать feedback + обновить локальные счётчики."""
        if status == "accepted":
            self._knowledge.patterns.record_feedback(
                suggestion.id, accepted=True,
            )
            self.stats.accepted += 1

        elif status == "rejected":
            self._knowledge.patterns.record_feedback(
                suggestion.id, accepted=False,
            )
            self.stats.rejected += 1

        else:
            self._knowledge.patterns.record_feedback(
                suggestion.id, ignored=True,
            )
            self.stats.ignored += 1

    # ══════════════════════════════════════════════
    #  Выполнение действий
    # ══════════════════════════════════════════════

    def _execute_action(self, suggestion: Suggestion) -> None:
        """
        Выполнить действие из предложения.

        Поддерживаемые actions:
        - open_app: через router.process()
        - run_macro: через router.process()
        - create_macro: через _handle_create_macro()
        """
        action = suggestion.action
        params = suggestion.action_params

        try:
            if action == "open_app":
                target = params.get("target", "")
                if target:
                    result = self._router.process(
                        f"открой {target}",
                    )
                    if result.success:
                        logger.info(
                            "Proactive: open_app '%s'",
                            target,
                        )
                    else:
                        logger.warning(
                            "Proactive: open_app '%s' — "
                            "не выполнено", target,
                        )

            elif action == "run_macro":
                name = params.get("name", "")
                if name:
                    result = self._router.process(
                        f"макрос {name}",
                    )
                    if result.success:
                        logger.info(
                            "Proactive: macro '%s'", name,
                        )

            elif action == "create_macro":
                self._handle_create_macro(
                    params.get("macro"),
                )

            else:
                logger.warning(
                    "Unknown suggestion action: %s",
                    action,
                )

        except Exception as e:
            logger.error(
                "Proactive action error: %s",
                e, exc_info=True,
            )
            self._speaker.say(
                "Не удалось выполнить действие.",
            )

    def _handle_create_macro(
        self, macro_data: dict | None,
    ) -> None:
        """
        Сохранить макрос, перезагрузить команду.

        Вызывается когда пользователь согласился
        с предложением "Создать макрос?".
        """
        if not macro_data:
            self._speaker.say("Не удалось создать макрос.")
            logger.warning(
                "create_macro: пустые данные макроса",
            )
            return

        auto_gen = getattr(
            self._knowledge, "macro_generator", None,
        )
        if not auto_gen:
            self._speaker.say(
                "Система макросов не подключена.",
            )
            return

        # Сохраняем keyword ДО save_macro (pop удалит id)
        keywords = macro_data.get("keywords", [])
        keyword = keywords[0] if keywords else (
            macro_data.get("name", "макрос")
        )

        success = auto_gen.save_macro(macro_data)

        if success:
            self._speaker.say(
                f"Макрос сохранён! "
                f"Скажи «{keyword}» чтобы запустить."
            )
            self._reload_macro_command()
            logger.info(
                "Proactive: макрос создан (%s)",
                keyword,
            )
        else:
            self._speaker.say(
                "Не удалось сохранить макрос. "
                "Возможно, похожий уже существует."
            )

    def _reload_macro_command(self) -> None:
        """Перезагрузить MacroCommand через router API."""
        try:
            if hasattr(self._router, 'reload_command'):
                self._router.reload_command("macro")
            else:
                for i, cmd in enumerate(
                    self._router._commands,
                ):
                    if cmd.name == "macro":
                        new_cmd = type(cmd)(
                            self._router._ctx,
                        )
                        self._router._commands[i] = new_cmd
                        logger.info(
                            "MacroCommand перезагружен",
                        )
                        break
        except Exception as e:
            logger.warning(
                "Перезагрузка MacroCommand: %s "
                "(макрос сохранён, доступен "
                "после перезапуска)", e,
            )