# commands/base.py

"""
Базовый класс для всех команд-плагинов.

v3.2:
- update_context() — каждая команда сама обновляет контекст диалога
- description, category — метаданные для auto-help
- Нормализация текста только ОДИН РАЗ (в router),
  match() получает уже нормализованный текст
"""

import re
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.assistant_context import AssistantContext


@dataclass
class CommandResult:
    """
    Результат выполнения команды.
    Инвариант: follow_up=True имеет смысл только при success=True.
    """
    success: bool = True
    should_continue: bool = True
    follow_up: bool = False

    def __post_init__(self) -> None:
        if self.follow_up and not self.success:
            self.follow_up = False


# ═══════════════════════════════════════════
#  Простой стемминг русского языка
# ═══════════════════════════════════════════

_RU_SUFFIXES = [
    "ывать", "ивать", "овать", "евать",
    "ться", "ются", "ешь", "ете",
    "ного", "ному", "ными",
    "ами", "ями", "ому", "ого",
    "ой", "ый", "ий", "ая", "яя", "ое", "ее",
    "ам", "ям", "ов", "ев", "ей",
    "ку", "ке", "ка", "ки",
    "ом", "ем", "им",
    "ую", "юю",
    "ах", "ях",
    "у", "е", "и", "ы", "а", "я", "о",
]

_MIN_STEM_LENGTH = 3

def stem_russian(word: str) -> str:
    """Простой стеммер: обрезает типичные русские окончания."""
    if len(word) <= _MIN_STEM_LENGTH:
        return word
    for suffix in _RU_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM_LENGTH:
            return word[:-len(suffix)]
    return word


# ═══════════════════════════════════════════
#  Нормализация текста
# ═══════════════════════════════════════════

def normalize_text(text: str) -> str:
    """
    Нормализация текста перед обработкой.
    Вызывается ТОЛЬКО ОДИН РАЗ в CommandRouter.process().
    """
    text = text.lower().strip()
    text = text.strip(".,!?;:…–—-\"'«»()")
    text = text.replace(",", " ")
    text = " ".join(text.split())
    return text


# ═══════════════════════════════════════════
#  Базовый класс команды
# ═══════════════════════════════════════════

class BaseCommand:
    """
    Наследуй этот класс для создания новой команды.
    """

    name: str = ""
    description: str = ""
    category: str = ""
    keywords: list[str] = []
    priority: int = 0
    fuzzy_enabled: bool = True
    enabled: bool = True

    def __init__(self, ctx: "AssistantContext") -> None:
        self.ctx = ctx
        self.speaker = ctx.speaker
        self.config = ctx.config
        self.logger = logging.getLogger(f"cmd.{self.name}")

    def match(self, text: str) -> tuple[bool, dict]:
        """
        Проверяет совпадение текста с командой.
        text приходит УЖЕ нормализованным из CommandRouter.
        """
        sorted_kw = sorted(self.keywords, key=len, reverse=True)

        for kw in sorted_kw:
            if self._exact_match(kw, text):
                params = self.extract_params(text, kw)
                return True, params

        if self.fuzzy_enabled:
            for kw in sorted_kw:
                if len(kw) < 8:
                    continue
                score = self._fuzzy_phrase_match(kw, text)
                if score >= 0.82:
                    self.logger.debug(
                        "Нечёткое совпадение: '%s' ~ '%s' (%.0f%%)",
                        kw, text, score * 100,
                    )
                    params = self.extract_params(text, kw)
                    return True, params

        return False, {}

    def update_context(self, params: dict) -> None:
        """Обновить глобальный контекст диалога после выполнения."""
        pass

    def is_valid_candidate(self, params: dict) -> bool:
        """Проверить пригодность кандидата."""
        return True

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        """Извлечь параметры из текста."""
        return {}

    def execute(self, text: str, params: dict) -> CommandResult:
        """Выполнить команду. Обязательно переопредели."""
        raise NotImplementedError

    @staticmethod
    def _exact_match(keyword: str, text: str) -> bool:
        if " " in keyword:
            kw_words = keyword.split()
            text_words = text.split()
            kw_len = len(kw_words)
            for i in range(len(text_words) - kw_len + 1):
                if text_words[i:i + kw_len] == kw_words:
                    return True
            return False
        words = text.split()
        return keyword in words

    @staticmethod
    def _fuzzy_phrase_match(keyword: str, text: str) -> float:
        kw_words = keyword.split()
        text_words = text.split()
        if len(text_words) < len(kw_words):
            return 0.0
        best_score = 0.0
        for start in range(len(text_words) - len(kw_words) + 1):
            window = text_words[start:start + len(kw_words)]
            pair_scores = [
                BaseCommand._safe_word_similarity(kw_w, t_w)
                for kw_w, t_w in zip(kw_words, window)
            ]
            window_score = min(pair_scores) if pair_scores else 0.0
            best_score = max(best_score, window_score)
        return best_score

    @staticmethod
    def _safe_word_similarity(a: str, b: str) -> float:
        if a == b:
            return 1.0
        if not a or not b:
            return 0.0
        len_a, len_b = len(a), len(b)
        length_ratio = min(len_a, len_b) / max(len_a, len_b)
        if length_ratio < 0.6:
            return 0.0
        shorter, longer = (a, b) if len_a <= len_b else (b, a)
        if longer.endswith(shorter) and len(shorter) >= 4:
            return 0.85 + (length_ratio * 0.15)
        if longer.startswith(shorter) and len(shorter) >= 4:
            return 0.85 + (length_ratio * 0.15)
        distance = BaseCommand._levenshtein(a, b)
        return 1.0 - (distance / max(len_a, len_b))

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        len_a, len_b = len(a), len(b)
        prev = list(range(len_b + 1))
        curr = [0] * (len_b + 1)
        for i in range(1, len_a + 1):
            curr[0] = i
            for j in range(1, len_b + 1):
                cost = 0 if a[i - 1] == b[j - 1] else 1
                curr[j] = min(
                    prev[j] + 1,
                    curr[j - 1] + 1,
                    prev[j - 1] + cost,
                )
            prev, curr = curr, prev
        return prev[len_b]

    @staticmethod
    def _extract_number(text: str) -> int | float | None:
        m = re.search(r'(\d+)', text)
        if m:
            return int(m.group(1))
        word_nums = {
            "один": 1, "одну": 1, "одна": 1, "одно": 1,
            "два": 2, "две": 2, "три": 3, "четыре": 4,
            "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
            "девять": 9, "десять": 10, "одиннадцать": 11,
            "двенадцать": 12, "тринадцать": 13,
            "четырнадцать": 14, "пятнадцать": 15,
            "шестнадцать": 16, "семнадцать": 17,
            "восемнадцать": 18, "девятнадцать": 19,
            "двадцать": 20, "тридцать": 30, "сорок": 40,
            "пятьдесят": 50, "шестьдесят": 60,
            "семьдесят": 70, "восемьдесят": 80,
            "девяносто": 90, "сто": 100, "двести": 200,
            "триста": 300, "четыреста": 400, "пятьсот": 500,
            "полчаса": 30, "полтора": 1,
        }
        words = text.split()
        total, found_any = 0, False
        for word in words:
            if word in word_nums:
                num = word_nums[word]
                if num >= 100:
                    total = total * num if total else num
                else:
                    total += num
                found_any = True
        return total if found_any else None

    @staticmethod
    def _clean_text(text: str, remove: list[str]) -> str:
        for word in sorted(remove, key=len, reverse=True):
            text = text.replace(word, "")
        return " ".join(text.split()).strip()
    
    # commands/base.py - В КОНЦЕ КЛАССА BaseCommand

    @staticmethod
    def _is_positive_response(text: str) -> bool:
        """Единый метод проверки согласия."""
        from core.response_classifier import is_positive
        return is_positive(text)

    @staticmethod
    def _is_negative_response(text: str) -> bool:
        """Единый метод проверки отказа."""
        from core.response_classifier import is_negative
        return is_negative(text)