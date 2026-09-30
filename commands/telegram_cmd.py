# commands/telegram_cmd.py

"""
Telegram: голосовые команды.

"отправь в телеграм [сообщение]"
"напиши в телеграм [сообщение]"
"телеграм сообщение [текст]"
"настрой телеграм" — помощь с настройкой

Требуется настройка в config.yaml:
  integrations:
    telegram:
      bot_token: "..."
      default_chat_id: 123456
"""

import logging
from commands.base import BaseCommand, CommandResult
logger = logging.getLogger(__name__)


class TelegramCommand(BaseCommand):
    """Отправка сообщений через Telegram Bot API."""

    name = "telegram"
    description = "Отправить сообщение в Telegram"
    priority = 8
    fuzzy_enabled = True

    keywords = [
        "отправь в телеграм",
        "напиши в телеграм",
        "телеграм сообщение",
        "отправь сообщение в телеграм",
        "напиши сообщение в телеграм",
        "настрой телеграм",
        "открой телеграм",
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._bot = None
        self._init_bot()

    def _init_bot(self):
        """Инициализация бота из конфига."""
        try:
            from core.integrations.telegram_bot import (
                create_telegram_bot,
            )
            self._bot = create_telegram_bot(self.config)
        except Exception as e:
            logger.debug("Telegram init: %s", e)

    def extract_params(
        self, text: str, matched_keyword: str = "",
    ) -> dict:
        text_lower = text.lower()

        if "настрой" in text_lower:
            return {"action": "setup"}

        if "открой" in text_lower:
            return {"action": "open"}

        # Извлекаем текст сообщения
        message = self._clean_text(text, [
            matched_keyword,
            "в телеграм", "в telegram",
            "сообщение",
        ])

        return {"action": "send", "message": message}

    def is_valid_candidate(self, params: dict) -> bool:
        action = params.get("action", "")
        if action == "setup" or action == "open":
            return True
        message = params.get("message", "")
        return bool(message)

    def execute(self, text: str, params: dict) -> CommandResult:
        action = params.get("action", "send")

        if action == "setup":
            return self._handle_setup()

        if action == "open":
            return self._handle_open()

        if action == "send":
            return self._handle_send(params)

        return CommandResult()

    def _handle_send(self, params: dict) -> CommandResult:
        """Отправить сообщение."""
        message = params.get("message", "")

        if not self._bot:
            self.speaker.say(
                "Телеграм не настроен. "
                "Скажи «настрой телеграм» "
                "для инструкций."
            )
            return CommandResult(success=False)

        if not self._bot.is_configured():
            self.speaker.say(
                "Не указан chat ID. "
                "Скажи «настрой телеграм» "
                "для инструкций."
            )
            return CommandResult(success=False)

        self.speaker.say("Отправляю сообщение.")

        if self._bot.send_message(message):
            self.speaker.say("Сообщение отправлено.")
            self.ctx.context.update(
                "telegram", "send",
                {"message": message},
            )
            return CommandResult(follow_up=True)
        else:
            self.speaker.say(
                "Не удалось отправить сообщение. "
                "Проверь настройки."
            )
            return CommandResult(success=False)

    def _handle_setup(self) -> CommandResult:
        """Инструкции по настройке."""
        self.speaker.say(
            "Для настройки Телеграма нужно: "
            "Первое — создать бота через BotFather. "
            "Второе — получить токен. "
            "Третье — написать боту старт. "
            "Четвёртое — добавить токен и chat ID "
            "в конфиг. Подробности в документации."
        )
        return CommandResult(follow_up=True)

    def _handle_open(self) -> CommandResult:
        """Открыть Telegram."""
        import subprocess
        try:
            # Пробуем протокол
            import os
            os.startfile("tg://")
            self.speaker.say("Открываю Телеграм.")
        except Exception:
            try:
                subprocess.Popen(
                    ["telegram"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self.speaker.say("Открываю Телеграм.")
            except FileNotFoundError:
                self.speaker.say(
                    "Не удалось найти Телеграм.",
                )
                return CommandResult(success=False)

        return CommandResult(follow_up=True)