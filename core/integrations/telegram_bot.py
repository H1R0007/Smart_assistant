# core/integrations/telegram_bot.py

"""
Интеграция с Telegram через Bot API.

Настройка:
1. Создать бота через @BotFather → получить token
2. Написать боту /start → получить chat_id
3. Добавить в config.yaml:
   integrations:
     telegram:
       bot_token: "123456:ABC-DEF..."
       default_chat_id: 123456789

Ограничения Bot API:
- Бот может отправлять сообщения только тем,
  кто с ним взаимодействовал (/start)
- Бот не может читать сообщения из обычных чатов
- Для отправки в группу — добавить бота в группу

Почему Bot API, а не UI-автоматизация:
- Надёжно (не зависит от версии клиента)
- Быстро (HTTP запрос vs поиск кнопок)
- Работает даже если Telegram свёрнут/закрыт
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Обёртка над Telegram Bot API.

    Минимальный набор: отправка текста и получение chat_id.
    Расширяемо для отправки файлов, фото и т.д.
    """

    API_URL = "https://api.telegram.org/bot{token}/{method}"

    def __init__(
        self,
        bot_token: str,
        default_chat_id: int | str = 0,
    ):
        self._token = bot_token
        self._default_chat_id = int(default_chat_id)
        self._session = None

    def _get_url(self, method: str) -> str:
        return self.API_URL.format(
            token=self._token, method=method,
        )

    def _request(
        self, method: str, data: dict,
    ) -> dict | None:
        """
        HTTP-запрос к Telegram Bot API.

        Использует requests/httpx — что доступно.
        """
        url = self._get_url(method)

        try:
            # Пробуем httpx (может быть в проекте)
            try:
                import httpx
                resp = httpx.post(
                    url, json=data, timeout=10,
                )
                result = resp.json()
            except ImportError:
                import urllib.request
                import json
                req = urllib.request.Request(
                    url,
                    data=json.dumps(data).encode('utf-8'),
                    headers={
                        'Content-Type': 'application/json',
                    },
                )
                with urllib.request.urlopen(
                    req, timeout=10,
                ) as resp:
                    result = json.loads(resp.read())

            if not result.get("ok"):
                logger.error(
                    "Telegram API error: %s",
                    result.get("description", "unknown"),
                )
                return None

            return result.get("result")

        except Exception as e:
            logger.error("Telegram request error: %s", e)
            return None

    def send_message(
        self,
        text: str,
        chat_id: int | str | None = None,
        parse_mode: str | None = None,
    ) -> bool:
        """
        Отправить текстовое сообщение.

        Args:
            text: Текст сообщения
            chat_id: ID чата (или default)
            parse_mode: "HTML" | "Markdown" | None

        Returns: True если отправлено
        """
        target = chat_id or self._default_chat_id
        if not target:
            logger.error(
                "Telegram: не указан chat_id",
            )
            return False

        data = {
            "chat_id": target,
            "text": text,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode

        result = self._request("sendMessage", data)
        if result:
            logger.info(
                "Telegram: сообщение отправлено "
                "(chat_id=%s)", target,
            )
            return True
        return False

    def get_me(self) -> dict | None:
        """Информация о боте (для проверки токена)."""
        return self._request("getMe", {})

    def get_updates(self, offset: int = 0) -> list:
        """
        Получить обновления (для определения chat_id).

        Пользователь пишет боту /start,
        затем вызываем get_updates чтобы узнать chat_id.
        """
        result = self._request("getUpdates", {
            "offset": offset,
            "limit": 10,
        })
        return result or []

    def is_configured(self) -> bool:
        """Проверить, настроен ли бот."""
        return bool(
            self._token and self._default_chat_id
        )

    def verify(self) -> bool:
        """Проверить что токен валиден."""
        info = self.get_me()
        if info:
            logger.info(
                "Telegram bot: @%s",
                info.get("username", "?"),
            )
            return True
        return False


def create_telegram_bot(config) -> TelegramBot | None:
    """
    Создать TelegramBot из конфигурации.

    config.yaml:
      integrations:
        telegram:
          bot_token: "123456:ABC-DEF..."
          default_chat_id: 123456789
    """
    integrations = config.get("integrations") or {}
    tg_config = integrations.get("telegram") or {}

    token = tg_config.get("bot_token", "")
    chat_id = tg_config.get("default_chat_id", 0)

    if not token:
        logger.debug("Telegram: bot_token не настроен")
        return None

    bot = TelegramBot(token, chat_id)

    if not chat_id:
        logger.warning(
            "Telegram: default_chat_id не указан. "
            "Напишите боту /start и укажите chat_id "
            "в config.yaml",
        )

    return bot