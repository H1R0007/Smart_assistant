# commands/youtube.py

"""
YouTube: голосовые команды.

"найди на ютубе [запрос]"
"открой ютуб"
"включи на ютубе [запрос]"
"открой подписки на ютубе"
"открой тренды на ютубе"
"""

import logging
from commands.base import BaseCommand, CommandResult
from core.context import get_context

logger = logging.getLogger(__name__)


class YouTubeCommand(BaseCommand):
    """Управление YouTube через браузер."""

    name = "youtube"
    description = "YouTube: поиск и открытие видео"
    priority = 10
    fuzzy_enabled = True

    keywords = [
        "найди на ютубе",
        "найди на youtube",
        "поищи на ютубе",
        "включи на ютубе",
        "открой на ютубе",
        "покажи на ютубе",
        "открой ютуб",
        "открой youtube",
        "ютуб подписки",
        "ютуб тренды",
    ]

    def extract_params(
        self, text: str, matched_keyword: str = "",
    ) -> dict:
        # Извлекаем запрос (всё после ключевого слова)
        query = self._clean_text(text, [
            matched_keyword,
            "на ютубе", "на youtube",
            "в ютубе", "в youtube",
            "ютуб", "youtube",
        ])

        # Определяем действие
        action = "search"
        if matched_keyword in (
            "открой ютуб", "открой youtube",
        ):
            action = "open"
        elif "подписки" in text.lower():
            action = "subscriptions"
        elif "тренды" in text.lower():
            action = "trending"

        return {"action": action, "query": query}

    def is_valid_candidate(self, params: dict) -> bool:
        action = params.get("action", "")
        query = params.get("query", "")
        # Для search нужен запрос, для open — не нужен
        if action == "search" and not query:
            return False
        return True

    def execute(self, text: str, params: dict) -> CommandResult:
        from core.integrations.browser import (
            youtube_search,
            youtube_open_main,
            youtube_open_subscriptions,
            youtube_open_trending,
        )

        action = params.get("action", "search")
        query = params.get("query", "")

        if action == "search" and query:
            self.speaker.say(f"Ищу на YouTube: {query}")
            youtube_search(query)

        elif action == "open":
            self.speaker.say("Открываю YouTube.")
            youtube_open_main()

        elif action == "subscriptions":
            self.speaker.say("Открываю подписки.")
            youtube_open_subscriptions()

        elif action == "trending":
            self.speaker.say("Открываю тренды.")
            youtube_open_trending()

        else:
            self.speaker.say("Не поняла, что найти на YouTube.")
            return CommandResult(follow_up=True)

        get_context().update(
            "youtube", query or action,
            {"action": action, "query": query},
        )

        return CommandResult(follow_up=True)