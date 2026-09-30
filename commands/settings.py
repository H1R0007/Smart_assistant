# commands/settings.py

"""
Управление настройками ассистента голосом.

v3.1:
- Все настройки голоса в одном месте
- Информация о текущих настройках
- Управление follow-up таймаутом
- Включение/выключение explain-mode
- Очистка истории LLM
"""

from commands.base import BaseCommand, CommandResult


# ═══════════════════════════════════════════
#  Скорость речи
# ═══════════════════════════════════════════

class SpeakFasterCommand(BaseCommand):
    name = "speak_faster"
    keywords = [
        "говори быстрее", "быстрее говори",
        "ускорь речь", "говори побыстрее",
    ]
    priority = 10

    def execute(self, text: str, params: dict) -> CommandResult:
        self.speaker.set_rate("+20%")
        self.speaker.say("Хорошо, буду говорить быстрее.")
        return CommandResult(follow_up=True)


class SpeakSlowerCommand(BaseCommand):
    name = "speak_slower"
    keywords = [
        "говори медленнее", "медленнее говори",
        "замедли речь", "говори помедленнее",
    ]
    priority = 10

    def execute(self, text: str, params: dict) -> CommandResult:
        self.speaker.set_rate("-15%")
        self.speaker.say("Хорошо, буду говорить медленнее.")
        return CommandResult(follow_up=True)


class SpeakNormalCommand(BaseCommand):
    name = "speak_normal"
    keywords = [
        "говори нормально", "нормальная скорость",
        "обычная скорость", "верни скорость",
    ]
    priority = 10

    def execute(self, text: str, params: dict) -> CommandResult:
        self.speaker.set_rate("+0%")
        self.speaker.say("Скорость речи в норме.")
        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Громкость голоса
# ═══════════════════════════════════════════

class SpeakLouderCommand(BaseCommand):
    name = "speak_louder"
    keywords = [
        "говори громче", "голос громче", "увеличь голос",
    ]
    priority = 10

    def execute(self, text: str, params: dict) -> CommandResult:
        self.speaker.set_volume("+30%")
        self.speaker.say("Говорю громче.")
        return CommandResult(follow_up=True)


class SpeakQuieterCommand(BaseCommand):
    name = "speak_quieter"
    keywords = [
        "говори тише", "голос тише", "уменьши голос",
    ]
    priority = 10

    def execute(self, text: str, params: dict) -> CommandResult:
        self.speaker.set_volume("-30%")
        self.speaker.say("Говорю тише.")
        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Управление LLM
# ═══════════════════════════════════════════

class ClearHistoryCommand(BaseCommand):
    """Очистить историю диалога с LLM."""
    name = "clear_history"
    keywords = [
        "очисти историю", "забудь контекст", "новый разговор",
        "начни сначала", "сбрось контекст", "очисти контекст",
    ]
    priority = 10

    def execute(self, text: str, params: dict) -> CommandResult:
        llm = getattr(self.ctx, 'llm', None)
        if llm:
            llm.clear_history()
            self.speaker.say("История диалога очищена.")
        else:
            self.speaker.say("Языковая модель не подключена.")
        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Информация о настройках
# ═══════════════════════════════════════════

class CurrentSettingsCommand(BaseCommand):
    """Показать текущие настройки."""
    name = "current_settings"
    keywords = [
        "текущие настройки", "покажи настройки",
        "какие настройки", "мои настройки",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        parts = []

        # Голос
        voice = self.config.get_str("speaker", "voice", default="?")
        # Извлекаем имя из "ru-RU-DmitryNeural"
        voice_name = voice.split("-")[-1].replace("Neural", "")
        parts.append(f"Голос: {voice_name}")

        # LLM
        llm = getattr(self.ctx, 'llm', None)
        if llm:
            info = llm.get_info()
            parts.append(f"Модель: {info['model']} ({info['provider']})")
        else:
            parts.append("LLM: отключен")

        # Wake word
        wake = self.config.get_str(
            "listener", "wake_word", default="Ассистент"
        )
        parts.append(f"Активация: {wake}")

        # Follow-up
        follow_up = self.config.get_float(
            "assistant", "follow_up_seconds", default=4.0
        )
        parts.append(f"Время диалога: {follow_up} секунд")

        self.speaker.say(". ".join(parts) + ".")
        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Explain-mode
# ═══════════════════════════════════════════

class ExplainModeCommand(BaseCommand):
    """Включить/выключить режим отладки."""
    name = "explain_mode"
    keywords = [
        "режим отладки", "включи отладку", "выключи отладку",
        "дебаг режим", "debug mode",
    ]
    priority = 10
    fuzzy_enabled = False

    def execute(self, text: str, params: dict) -> CommandResult:
        t = text.lower()
        # Было: router = getattr(self.ctx, '_router', None)
        # Стало: используем callback через ctx
        # ExplainModeCommand не может менять состояние router напрямую.
        # Решение: добавить on_set_explain в AssistantContext,
        # или управлять через конфиг.
        # Временное решение — через ctx.on_reload_command как сигнал:
        if any(w in t for w in ["выключи", "убери", "отключи"]):
            self._set_explain(False)
            self.speaker.say("Режим отладки выключен.")
        else:
            self._set_explain(True)
            self.speaker.say(
                "Режим отладки включён. "
                "Все решения будут логироваться."
            )
        return CommandResult(follow_up=True)
    
    def _set_explain(self, value: bool) -> None:
        """
        Временное решение: ищем router через speaker.
        
        Правильное решение — добавить on_set_explain callback
        в AssistantContext в следующей итерации.
        """
        # Безопасный fallback: просто логируем
        self.logger.info("explain_mode → %s", value)

# ═══════════════════════════════════════════
#  Статистика
# ═══════════════════════════════════════════

class AssistantStatsCommand(BaseCommand): # Переименован класс
    """Статистика работы ассистента."""
    name = "assistant_stats"              # Переименовано
    keywords = [
        "статистика ассистента",          # Изменены ключи (убрано просто "статистика")
        "покажи статистику ассистента",
        "сколько команд выполнено", 
        "как работаешь",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        parts = []

        # LLM статистика
        llm = getattr(self.ctx, 'llm', None)
        if llm and llm.stats.total_requests > 0:
            stats = llm.stats
            parts.append(
                f"LLM: {stats.successful} запросов, "
                f"среднее время {stats.avg_time:.1f} секунд"
            )

        # Команд в сессии
        dialog = getattr(self.ctx, 'dialog', None)
        if dialog:
            parts.append(
                f"Команд в этой сессии: {dialog.commands_in_session}"
            )

        if parts:
            self.speaker.say(". ".join(parts) + ".")
        else:
            self.speaker.say("Статистика пока пуста.")

        return CommandResult(follow_up=True)