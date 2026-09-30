# commands/translate.py

"""
Перевод текста через LLM.

v3.1:
- Автоопределение языка по тексту
- Больше языков (немецкий, французский, японский, китайский)
- Перевод фраз из буфера обмена
- Более умное определение направления
"""

import re
import logging

from commands.base import BaseCommand, CommandResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Определение языка
# ═══════════════════════════════════════════

def _detect_language(text: str) -> str:
    """
    Определить язык текста по символам.

    Возвращает: "ru", "en", "zh", "ja", "mixed"
    """
    if not text:
        return "mixed"

    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
    latin = sum(1 for c in text if 'A' <= c <= 'z')
    cjk_chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    cjk_japanese = sum(
        1 for c in text
        if '\u3040' <= c <= '\u309f'   # Хирагана
        or '\u30a0' <= c <= '\u30ff'   # Катакана
    )

    total = cyrillic + latin + cjk_chinese + cjk_japanese
    if total == 0:
        return "mixed"

    if cyrillic / total > 0.5:
        return "ru"
    elif cjk_japanese / total > 0.2:
        return "ja"
    elif cjk_chinese / total > 0.3:
        return "zh"
    elif latin / total > 0.5:
        return "en"
    return "mixed"


# Маппинг языков
LANGUAGE_MAP = {
    # Русские названия → код языка
    "английский": "en",
    "английском": "en",
    "английски": "en",
    "по-английски": "en",
    "по английски": "en",

    "русский": "ru",
    "русском": "ru",
    "русски": "ru",
    "по-русски": "ru",
    "по русски": "ru",

    "немецкий": "de",
    "немецком": "de",
    "по-немецки": "de",
    "по немецки": "de",

    "французский": "fr",
    "французском": "fr",
    "по-французски": "fr",
    "по французски": "fr",

    "испанский": "es",
    "испанском": "es",
    "по-испански": "es",
    "по испански": "es",

    "японский": "ja",
    "японском": "ja",
    "по-японски": "ja",
    "по японски": "ja",

    "китайский": "zh",
    "китайском": "zh",
    "по-китайски": "zh",
    "по китайски": "zh",

    "итальянский": "it",
    "итальянском": "it",
    "по-итальянски": "it",

    "корейский": "ko",
    "корейском": "ko",
    "по-корейски": "ko",
}

# Человекочитаемые названия
LANGUAGE_NAMES = {
    "en": "английский",
    "ru": "русский",
    "de": "немецкий",
    "fr": "французский",
    "es": "испанский",
    "ja": "японский",
    "zh": "китайский",
    "it": "итальянский",
    "ko": "корейский",
}


def _detect_target_language(text: str) -> tuple[str | None, str]:
    """
    Определить целевой язык из текста.

    "переведи на английский hello" → ("en", "hello")
    "как по-немецки привет" → ("de", "привет")

    Returns: (language_code, cleaned_text)
    """
    text_lower = text.lower()

    # Ищем явное указание языка
    for phrase, lang_code in sorted(
        LANGUAGE_MAP.items(), key=lambda x: len(x[0]), reverse=True
    ):
        if phrase in text_lower:
            # Убираем фразу с языком
            cleaned = text_lower.replace(phrase, "")
            # Убираем предлоги
            cleaned = re.sub(
                r'\b(на|в|с|по|как|будет|переведи|перевод)\b',
                '', cleaned
            )
            cleaned = " ".join(cleaned.split()).strip()
            return lang_code, cleaned

    return None, text_lower


class TranslateCommand(BaseCommand):
    """
    Перевод текста.

    Поддерживает:
    - "переведи hello" → автоопределение (en→ru)
    - "переведи на английский привет" → ru→en
    - "как по-немецки спасибо" → ru→de
    - "переведи на японский кошка" → ru→ja
    - "переведи что в буфере" → перевод из буфера
    """
    name = "translate"
    keywords = [
        "переведи", "перевод", "как будет по-английски",
        "как по-английски", "переведи на",
        "как будет", "как сказать",
        "как по-немецки", "как по-французски",
        "как по-испански", "как по-японски",
        "как по-китайски", "как по-итальянски",
        "как по-корейски",
    ]
    priority = 10

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        # Убираем ключевые слова
        cleaned = self._clean_text(text, [
            "переведи", "перевод", "как будет", "как сказать",
            "пожалуйста", "мне", "слово", "фразу",
        ])

        # Проверяем буфер обмена
        if any(
            w in text.lower()
            for w in ["буфер", "скопированное", "из буфера"]
        ):
            try:
                import pyperclip
                clipboard_text = pyperclip.paste()
                if clipboard_text and clipboard_text.strip():
                    cleaned = clipboard_text.strip()
            except Exception:
                pass

        # Определяем целевой язык
        target_lang, query = _detect_target_language(cleaned)

        # Если язык не указан — автоопределение
        if not target_lang:
            source_lang = _detect_language(query)
            if source_lang == "ru":
                target_lang = "en"
            elif source_lang == "en":
                target_lang = "ru"
            else:
                target_lang = "ru"  # По умолчанию → русский

        return {
            "query": query,
            "target_lang": target_lang,
        }

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("query", "").strip())

    def execute(self, text: str, params: dict) -> CommandResult:
        query = params.get("query", "").strip()
        target_lang = params.get("target_lang", "en")

        if not query:
            self.speaker.say("Что перевести?")
            return CommandResult()

        llm = getattr(self.ctx, 'llm', None)
        if llm is None:
            self.speaker.say("Нужна языковая модель для перевода.")
            return CommandResult()

        lang_name = LANGUAGE_NAMES.get(target_lang, target_lang)

        prompt = (
            f"Переведи на {lang_name}. "
            f"Ответь ТОЛЬКО переводом, без пояснений и кавычек.\n\n"
            f"{query}"
        )

        self.speaker.beep()
        response = llm.ask(prompt)

        if response:
            # Убираем кавычки если LLM их добавила
            response = response.strip('"\'«»„"')
            self.speaker.say(f"{query} — {response}")
        else:
            self.speaker.say("Не удалось перевести.")

        return CommandResult(follow_up=True)