# core/integrations/browser.py

"""
Интеграция с браузером.

Возможности:
- Открытие URL (через webbrowser)
- YouTube: поиск, генерация URL
- Извлечение домена/сайта из заголовка окна
- Определение активного браузера

Поддерживаемые браузеры: Chrome, Firefox, Edge, Яндекс.
"""

import webbrowser
import logging
from urllib.parse import quote_plus

from core.integrations import (
    find_window_by_title,
    get_window_title,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Константы
# ═══════════════════════════════════════════

BROWSER_INDICATORS = {
    "google chrome": "Chrome",
    "mozilla firefox": "Firefox",
    "microsoft edge": "Edge",
    "яндекс браузер": "Яндекс",
    "yandex browser": "Яндекс",
    "opera": "Opera",
}

YOUTUBE_DOMAINS = [
    "youtube.com", "youtu.be", "m.youtube.com",
]


# ═══════════════════════════════════════════
#  URL операции
# ═══════════════════════════════════════════

def open_url(url: str) -> bool:
    """
    Открыть URL в браузере по умолчанию.

    Автоматически добавляет https:// если отсутствует.
    """
    if not url:
        return False

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        webbrowser.open(url)
        logger.info("Открыт URL: %s", url)
        return True
    except Exception as e:
        logger.error("Ошибка открытия URL: %s", e)
        return False


def search_google(query: str) -> bool:
    """Поиск в Google."""
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    return open_url(url)


# ═══════════════════════════════════════════
#  YouTube
# ═══════════════════════════════════════════

def youtube_search(query: str) -> bool:
    """
    Поиск на YouTube.

    Открывает страницу результатов поиска.
    """
    url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}"
    )
    return open_url(url)


def youtube_open_video(query: str) -> bool:
    """
    Открыть видео на YouTube по запросу.

    Стратегия: открываем поиск.
    Прямой запуск видео невозможен без API/парсинга.
    Но пользователю удобнее сказать "включи на ютубе
    лофи музыку" чем вручную открывать браузер.
    """
    return youtube_search(query)


def youtube_open_main() -> bool:
    """Открыть главную YouTube."""
    return open_url("https://www.youtube.com")


def youtube_open_subscriptions() -> bool:
    """Открыть подписки YouTube."""
    return open_url("https://www.youtube.com/feed/subscriptions")


def youtube_open_trending() -> bool:
    """Открыть тренды YouTube."""
    return open_url("https://www.youtube.com/feed/trending")


def is_youtube_active() -> bool:
    """Проверить, открыт ли YouTube в браузере."""
    hwnd = _find_browser_with_site("youtube")
    return hwnd is not None


# ═══════════════════════════════════════════
#  Интеллект браузера
# ═══════════════════════════════════════════

def get_active_browser_site() -> dict | None:
    """
    Определить какой сайт открыт в активном браузере.

    Парсит заголовок окна браузера:
    "GitHub - Google Chrome" → {"browser": "Chrome", "site": "GitHub"}
    "Inbox (3) - Gmail — Mozilla Firefox" → {"browser": "Firefox", "site": "Inbox (3) - Gmail"}

    Returns: dict с browser и site, или None
    """
    for indicator, browser_name in BROWSER_INDICATORS.items():
        hwnd = find_window_by_title(indicator)
        if not hwnd:
            continue

        title = get_window_title(hwnd)
        if not title:
            continue

        site = _extract_site_from_title(title, indicator)
        return {
            "browser": browser_name,
            "site": site,
            "hwnd": hwnd,
            "full_title": title,
        }

    return None


def extract_domain_hint(window_title: str) -> str:
    """
    Извлечь подсказку о домене/сайте из заголовка.

    "GitHub — Google Chrome" → "github"
    "Inbox (5) - user@gmail.com - Gmail" → "gmail"
    "YouTube" → "youtube"
    "Stack Overflow - How to..." → "stackoverflow"

    Используется трекером для обогащения activity_log.
    """
    if not window_title:
        return ""

    title_lower = window_title.lower()

    # Известные сайты
    known_sites = {
        "github": "github",
        "gmail": "gmail",
        "google": "google",
        "youtube": "youtube",
        "stack overflow": "stackoverflow",
        "stackoverflow": "stackoverflow",
        "habr": "habr",
        "хабр": "habr",
        "reddit": "reddit",
        "twitter": "twitter",
        "vk.com": "vk",
        "вконтакте": "vk",
        "telegram web": "telegram",
        "whatsapp": "whatsapp",
        "notion": "notion",
        "figma": "figma",
        "jira": "jira",
        "confluence": "confluence",
        "gitlab": "gitlab",
        "bitbucket": "bitbucket",
        "chatgpt": "chatgpt",
        "claude": "claude",
        "docs.google": "google_docs",
        "google sheets": "google_sheets",
        "google docs": "google_docs",
        "yandex": "yandex",
        "яндекс": "yandex",
    }

    for pattern, domain in known_sites.items():
        if pattern in title_lower:
            return domain

    return ""


def _extract_site_from_title(
    title: str, browser_indicator: str,
) -> str:
    """Извлечь имя сайта из заголовка браузера."""
    # Убираем имя браузера
    for sep in [" — ", " - ", " – "]:
        indicator_with_sep = f"{sep}{browser_indicator}"
        parts = title.lower().split(indicator_with_sep.lower())
        if len(parts) > 1:
            return title[:len(parts[0])].strip()

    # Fallback: всё до последнего разделителя
    for sep in [" — ", " - ", " – "]:
        if sep in title:
            return title.rsplit(sep, 1)[0].strip()

    return title


def _find_browser_with_site(site_hint: str) -> int | None:
    """Найти окно браузера с определённым сайтом."""
    hwnd = find_window_by_title(site_hint)
    if hwnd:
        title = get_window_title(hwnd).lower()
        for indicator in BROWSER_INDICATORS:
            if indicator in title:
                return hwnd
    return None