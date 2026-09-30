# core/macros/loader.py

"""
Загрузка и парсинг макросов из config.yaml.

Отвечает за:
- Чтение секции macros из конфига
- Парсинг каждого макроса в объект Macro
- Валидация структуры и семантики
- Обрезку лишних шагов (защита от слишком длинных макросов)
"""

import logging
import os

from core.macros.macro import Macro, MacroStep

logger = logging.getLogger(__name__)

# Жёсткий лимит на шаги в одном макросе.
# 20 шагов ≈ 60 сек выполнения (при ~3 сек на шаг).
MAX_STEPS_PER_MACRO = 20

GENERATED_MACROS_PATH = "data/generated_macros.yaml"

def load_macros(config) -> dict[str, Macro]:
    """
    Загрузить макросы из конфигурации И автогенерированные.

    Порядок:
    1. Макросы из config.yaml (пользовательские, приоритет)
    2. Макросы из data/generated_macros.yaml (автоматические)

    При конфликте ID — побеждает config.yaml (пользователь главнее).
    """
    # 1. Из основного конфига (существующий код)
    raw_macros = config.get("macros") or {}

    if not isinstance(raw_macros, dict):
        logger.warning(
            "Секция macros: ожидается словарь, получено %s. Пропуск.",
            type(raw_macros).__name__,
        )
        raw_macros = {}

    # 2. Из автогенерированного файла
    generated = _load_generated_macros()

    # Объединяем: config.yaml перезаписывает generated
    # (пользовательские макросы приоритетнее)
    merged = dict(generated)
    merged.update(raw_macros)

    macros: dict[str, Macro] = {}

    for macro_id, macro_data in merged.items():
        # ... (существующий код парсинга, без изменений) ...
        if not isinstance(macro_data, dict):
            logger.warning(
                "Макрос '%s': ожидается словарь, получено %s. Пропуск.",
                macro_id, type(macro_data).__name__,
            )
            continue

        macro = _parse_macro(macro_id, macro_data)
        if macro is None:
            continue

        errors = macro.validate()
        if errors:
            for err in errors:
                logger.warning(err)
            if not macro.steps or not macro.keywords:
                continue

        if len(macro.steps) > MAX_STEPS_PER_MACRO:
            logger.warning(
                "Макрос '%s': %d шагов (макс. %d), лишние обрезаны",
                macro_id, len(macro.steps), MAX_STEPS_PER_MACRO,
            )
            macro.steps = macro.steps[:MAX_STEPS_PER_MACRO]

        macros[macro_id] = macro

    if macros:
        names = ", ".join(f"'{m.name}'" for m in macros.values())
        logger.info("Загружено макросов: %d (%s)", len(macros), names)
    else:
        logger.debug("Макросы не найдены в конфигурации")

    return macros


def _load_generated_macros() -> dict:
    """Загрузить автогенерированные макросы из отдельного файла."""
    if not os.path.exists(GENERATED_MACROS_PATH):
        return {}

    try:
        import yaml
        with open(GENERATED_MACROS_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            logger.debug(
                "Загружено %d автогенерированных макросов",
                len(data),
            )
            return data
    except Exception as e:
        logger.warning(
            "Ошибка загрузки %s: %s", GENERATED_MACROS_PATH, e,
        )

    return {}

def _parse_macro(macro_id: str, data: dict) -> Macro | None:
    """Распарсить один макрос из конфиг-словаря."""
    name = str(data.get("name", macro_id))

    # keywords: строка или список
    raw_keywords = data.get("keywords", [])
    if isinstance(raw_keywords, str):
        raw_keywords = [raw_keywords]
    if not isinstance(raw_keywords, list):
        logger.warning(
            "Макрос '%s': keywords должно быть списком, получено %s",
            macro_id, type(raw_keywords).__name__,
        )
        raw_keywords = []

    keywords = [str(kw).lower().strip() for kw in raw_keywords if kw]

    greeting = str(data.get("greeting", ""))
    farewell = str(data.get("farewell", ""))

    # Шаги
    raw_steps = data.get("steps", [])
    if not isinstance(raw_steps, list):
        logger.warning(
            "Макрос '%s': steps должно быть списком, получено %s. Пропуск.",
            macro_id, type(raw_steps).__name__,
        )
        return None

    steps = []
    for i, step_data in enumerate(raw_steps):
        step = _parse_step(macro_id, i, step_data)
        if step is not None:
            steps.append(step)

    return Macro(
        id=macro_id,
        name=name,
        keywords=keywords,
        greeting=greeting,
        farewell=farewell,
        steps=steps,
    )


def _parse_step(macro_id: str, index: int, data) -> MacroStep | None:
    """
    Распарсить один шаг макроса.

    Два формата:
      Полный (dict):  {action: open_app, target: chrome, delay: 1.0}
      Краткий (str):  wait
    """
    step_label = f"Макрос '{macro_id}', шаг {index + 1}"

    # Краткий формат
    if isinstance(data, str):
        return MacroStep(action=data)

    # Полный формат
    if not isinstance(data, dict):
        logger.warning(
            "%s: ожидается словарь или строка, получено %s. Пропуск.",
            step_label, type(data).__name__,
        )
        return None

    action = data.get("action", "")
    if not action:
        logger.warning("%s: отсутствует поле action. Пропуск.", step_label)
        return None

    # str() — на случай если YAML распарсил числа (target: 25 → int)
    target = str(data.get("target", ""))
    value = str(data.get("value", ""))

    try:
        delay = float(data.get("delay", 0))
    except (ValueError, TypeError):
        logger.warning(
            "%s: delay должен быть числом, получено '%s'. Используется 0.",
            step_label, data.get("delay"),
        )
        delay = 0.0

    return MacroStep(
        action=str(action),
        target=target,
        value=value,
        delay=delay,
    )