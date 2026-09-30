# commands/web.py

"""
Веб-команды v3.1.

Изменения:
- Прямое открытие URL ("открой github.com")
- Более надёжный парсинг запросов (не только startswith)
- Расширенный реестр сервисов
- is_valid_candidate
- Поиск "в" сервисе: "найди в ютубе котиков"
"""

import re
import webbrowser
from urllib.parse import quote
from dataclasses import dataclass
from commands.base import BaseCommand, CommandResult, normalize_text
@dataclass
class WebService:
    name: str
    aliases: list[str]
    base_url: str
    search_url: str | None = None
    sections: dict[str, str] | None = None


class WebRegistry:
    def __init__(self):
        self._services = [
            WebService(
                name="YouTube",
                aliases=["ютуб", "youtube", "ютюб"],
                base_url="https://youtube.com",
                search_url="https://www.youtube.com/results?search_query={query}",
                sections={
                    "тренды": "https://youtube.com/feed/trending",
                    "подписки": "https://youtube.com/feed/subscriptions",
                    "история": "https://youtube.com/feed/history",
                }
            ),
            WebService(
                name="Google",
                aliases=["гугл", "google"],
                base_url="https://google.com",
                search_url="https://www.google.com/search?q={query}"
            ),
            WebService(
                name="Яндекс",
                aliases=["яндекс", "yandex"],
                base_url="https://ya.ru",
                search_url="https://yandex.ru/search/?text={query}",
                sections={
                    "карты": "https://yandex.ru/maps",
                    "маркет": "https://market.yandex.ru",
                    "музыка": "https://music.yandex.ru",
                    "почта": "https://mail.yandex.ru",
                }
            ),
            WebService(
                name="GitHub",
                aliases=["гитхаб", "github", "гит"],
                base_url="https://github.com",
                search_url="https://github.com/search?q={query}"
            ),
            WebService(
                name="Wikipedia",
                aliases=["википедия", "вики", "wikipedia"],
                base_url="https://ru.wikipedia.org",
                search_url="https://ru.wikipedia.org/w/index.php?search={query}"
            ),
            WebService(
                name="Reddit",
                aliases=["реддит", "reddit"],
                base_url="https://reddit.com",
                search_url="https://www.reddit.com/search/?q={query}"
            ),
            WebService(
                name="StackOverflow",
                aliases=["стековерфлоу", "stackoverflow", "стек"],
                base_url="https://stackoverflow.com",
                search_url="https://stackoverflow.com/search?q={query}"
            ),
            WebService(
                name="Habr",
                aliases=["хабр", "habr", "хабрахабр"],
                base_url="https://habr.com",
                search_url="https://habr.com/ru/search/?q={query}"
            ),
            WebService(
                name="Twitch",
                aliases=["твич", "twitch"],
                base_url="https://twitch.tv",
                search_url="https://www.twitch.tv/search?term={query}"
            ),
            WebService(
                name="VK",
                aliases=["вк", "вконтакте", "vk"],
                base_url="https://vk.com",
            ),
            WebService(
                name="Telegram Web",
                aliases=["телеграм веб", "telegram web"],
                base_url="https://web.telegram.org",
            ),
        ]

    def find_service(self, text: str) -> WebService | None:
        """
        Найти сайт по тексту.

        Ищет длинные алиасы первыми:
        "телеграм веб" найдётся раньше чем просто "телеграм"
        """
        for service in sorted(
            self._services,
            key=lambda s: max(len(a) for a in s.aliases),
            reverse=True
        ):
            for alias in sorted(service.aliases, key=len, reverse=True):
                if alias in text:
                    return service
        return None

    def remove_service_name(self, text: str, service: WebService) -> str:
        """Убрать название сервиса и предлоги из текста."""
        result = text
        for alias in sorted(service.aliases, key=len, reverse=True):
            result = result.replace(alias, "")
        # Убираем предлоги
        result = re.sub(r'\b(на|в|из|по|для)\b', '', result)
        return " ".join(result.split()).strip()


_registry = WebRegistry()


# ═══════════════════════════════════════════
#  Определение URL
# ═══════════════════════════════════════════

def _looks_like_url(text: str) -> str | None:
    """
    Проверить, является ли текст URL.

    "github.com" → "https://github.com"
    "ya.ru" → "https://ya.ru"
    "192.168.1.1" → "http://192.168.1.1"
    "просто слова" → None
    """
    text = text.strip()

    # Уже полный URL
    if text.startswith(("http://", "https://")):
        return text

    # Домен с точкой (github.com, ya.ru)
    if re.match(r'^[\w-]+\.\w{2,}', text) and " " not in text:
        return f"https://{text}"

    # IP адрес
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', text):
        return f"http://{text}"

    return None


# ═══════════════════════════════════════════
#  Поиск
# ═══════════════════════════════════════════

class WebSearchCommand(BaseCommand):
    """
    Универсальный поиск + контекст.

    Поддерживает:
    - "найди котиков" → Google
    - "найди на ютубе котиков" → YouTube
    - "найди котиков в ютубе" → YouTube (порядок не важен)
    - "ютуб котики" → YouTube
    - "загугли python tutorial" → Google
    - (контекст) "а на ютубе?" → повтор прошлого запроса на YouTube
    """
    name = "web_search"
    description = "Искать информацию в интернете"
    category = "Интернет"
    keywords = ["найди", "загугли", "поищи", "искать", "поиск", "нагугли"]
    priority = 3

    def match(self, text: str) -> tuple[bool, dict]:
        t = text

        # 1. КОНТЕКСТНЫЙ МАТЧ
        ctx = self.ctx.context.get()
        if ctx.is_fresh and ctx.action == "search":
            service = _registry.find_service(t)

            is_context_phrase = (
                t.startswith("а на ") or t.startswith("а в ")
                or t.startswith("на ") or t.startswith("в ")
                or (service and len(t.split()) <= 2)
            )

            if service and is_context_phrase:
                self.logger.info(
                    "Контекстный поиск: '%s' на %s",
                    ctx.target, service.name
                )
                return True, {
                    "service": service,
                    "query": ctx.target,
                    "is_context": True
                }

        # 2. "Сервис запрос" ("ютуб котики")
        service = _registry.find_service(t)
        if service and service.search_url:
            query = _registry.remove_service_name(t, service)
            if query and len(query) > 1:
                return True, {"service": service, "query": query}

        # 3. Поиск по ключевым словам
        for kw in sorted(self.keywords, key=len, reverse=True):
            # Ищем ключевое слово В ЛЮБОМ МЕСТЕ текста
            if kw in t:
                # Убираем ключевое слово
                query = t.replace(kw, "", 1).strip()

                # Проверяем сервис в оставшемся тексте
                service = _registry.find_service(query)
                if service and service.search_url:
                    clean_query = _registry.remove_service_name(
                        query, service
                    )
                    if clean_query:
                        return True, {
                            "service": service,
                            "query": clean_query
                        }

                if query:
                    return True, {"service": None, "query": query}

        return False, {}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("query"))

    def execute(self, text: str, params: dict) -> CommandResult:
        service = params.get("service")
        query = params.get("query")
        is_context = params.get("is_context", False)

        if not query:
            self.speaker.say("Что искать?")
            return CommandResult(follow_up=True)

        if service and service.search_url:
            url = service.search_url.format(query=quote(query))
            webbrowser.open(url)

            if is_context:
                self.speaker.say(f"Повторяю поиск на {service.name}: {query}.")
            else:
                self.speaker.say(f"Ищу на {service.name}: {query}.")
        else:
            url = "https://www.google.com/search?q=" + quote(query)
            webbrowser.open(url)
            self.speaker.say(f"Ищу: {query}.")

        return CommandResult(follow_up=True)
    
    def update_context(self, params: dict) -> None:
        query = params.get("query", "")
        self.ctx.context.update("search", query, params)
        # Логируем поисковый запрос в трекер (если KB доступна)
        kb = self.ctx.knowledge
        if kb and query:
            try:
                kb.tracker.log_search(query)
            except Exception:
                pass


# ═══════════════════════════════════════════
#  Открытие сайта
# ═══════════════════════════════════════════

class OpenSiteCommand(BaseCommand):
    """
    Открыть сайт или раздел.

    "открой ютуб" → youtube.com
    "открой ютуб тренды" → youtube.com/feed/trending
    "открой github.com" → https://github.com
    "зайди на хабр" → habr.com
    """
    name = "open_site"
    description = "Открывать сайты"
    category = "Интернет"
    keywords = ["открой", "зайди на", "перейди на", "зайди в"]
    priority = 6

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords + ["сайт"])

        # 1. Прямой URL
        url = _looks_like_url(cleaned)
        if url:
            # Извлекаем домен для озвучки
            domain = cleaned.split("/")[0].split("?")[0]
            return {"url": url, "name": domain}

        # 2. Известный сервис
        service = _registry.find_service(cleaned)
        if not service:
            return {}

        # 3. Проверяем разделы
        if service.sections:
            for section_name, section_url in service.sections.items():
                if section_name in cleaned:
                    return {
                        "url": section_url,
                        "name": f"{service.name} {section_name}"
                    }

        return {
            "url": service.base_url,
            "name": service.name
        }

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("url"))

    def execute(self, text: str, params: dict) -> CommandResult:
        url = params.get("url")
        name = params.get("name")

        if not url:
            return CommandResult(success=False)

        webbrowser.open(url)
        self.speaker.say(f"Открываю {name}.")
        return CommandResult(follow_up=True)