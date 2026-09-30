"""Утилиты для разбора текста: числа, длительность, очистка."""

import re

WORD_NUMBERS = {
    "ноль": 0, "нуль": 0,
    "один": 1, "одна": 1, "одну": 1, "одно": 1,
    "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30,
    "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300,
    "четыреста": 400, "пятьсот": 500,
}


def extract_number(text: str) -> int | None:
    """Извлечь первое число из текста (цифры или слова)."""
    match = re.search(r"\d+", text)
    if match:
        return int(match.group())

    for word, num in sorted(WORD_NUMBERS.items(), key=lambda x: -len(x[0])):
        if word in text.lower():
            return num

    return None


def extract_duration_seconds(text: str) -> int | None:
    """
    Извлечь длительность в секундах.

    «5 минут» → 300, «полчаса» → 1800, «час» → 3600
    """
    text = text.lower()

    if "полчаса" in text:
        return 1800
    if "полминуты" in text:
        return 30
    if "полтора часа" in text:
        return 5400

    number = extract_number(text)

    if number is not None:
        if any(w in text for w in ["час", "часа", "часов"]):
            return number * 3600
        if any(w in text for w in ["минут", "мин"]):
            return number * 60
        if any(w in text for w in ["секунд", "сек"]):
            return number
        # по умолчанию — минуты
        return number * 60

    # просто «час» / «минуту»
    if any(w in text for w in ["час", "часа", "часов"]):
        return 3600
    if any(w in text for w in ["минут", "мин"]):
        return 60

    return None


def clean_text(text: str, remove_words: list[str]) -> str:
    """Удалить указанные слова/фразы из текста."""
    result = text.lower()
    for word in sorted(remove_words, key=len, reverse=True):
        result = result.replace(word, "")
    return " ".join(result.split())  # убираем лишние пробелы