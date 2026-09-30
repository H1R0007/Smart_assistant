# core/response_classifier.py

"""
Единый классификатор ответов пользователя.
Заменяет дублирование в BaseCommand и ProactiveManager.
"""


_POSITIVE = {
    "да", "давай", "конечно", "ага", "угу",
    "ок", "окей", "хорошо", "сохрани",
    "сохраняй", "запомни", "запоминай",
    "подходит", "нормально", "отлично",
    "годится", "пойдёт", "пойдет",
    "выключай", "перезагружай", "делай",
    "точно", "верно", "можно", "валяй",
    "вперёд", "вперед", "запусти",
    "открой", "выполни",
}

_NEGATIVE = {
    "нет", "не", "неа", "нельзя",
    "отмена", "стоп", "хватит", "отмени",
    "не хочу", "потом", "позже", "не сейчас",
    "отстань",
}

_NEGATIVE_PHRASES = {
    "не надо", "не нужно", "нет спасибо",
    "спасибо не надо",
}

_WHY = {
    "почему", "зачем", "объясни", "подробнее",
    "с чего", "откуда", "на основании",
}


def classify(text: str) -> str:
    """
    Классифицировать ответ.
    
    Returns: "accept" | "reject" | "why" | "unknown"
    """
    if not text:
        return "unknown"

    t = text.lower().strip()
    words = set(t.split())

    # Фразы (проверяем первыми — длиннее)
    for phrase in _NEGATIVE_PHRASES:
        if phrase in t:
            return "reject"

    if words & _WHY:
        return "why"

    if words & _POSITIVE:
        return "accept"

    if words & _NEGATIVE:
        return "reject"

    return "unknown"


def is_positive(text: str) -> bool:
    return classify(text) == "accept"


def is_negative(text: str) -> bool:
    return classify(text) == "reject"