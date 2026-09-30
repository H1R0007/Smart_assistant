# core/macros/macro.py

"""
Модели данных макросов.

Чистые dataclass без side-effects.
Нет зависимостей от runtime (ctx, speaker, etc.).
"""

from dataclasses import dataclass, field


# ═══════════════════════════════════════════
#  Поддерживаемые действия
# ═══════════════════════════════════════════

# Набор фиксирован и проверяется при валидации.
# Добавление нового действия:
#   1. Добавить сюда
#   2. Добавить обработчик в MacroExecutor._STEP_HANDLERS
#   3. Документировать формат в config.yaml
SUPPORTED_ACTIONS = frozenset({
    "open_app",     # Открыть приложение (через AppRegistry)
    "open_url",     # Открыть URL в браузере
    "volume",       # Установить громкость (0–100)
    "brightness",   # Установить яркость (5–100)
    "wait",         # Пауза N секунд
    "say",          # Произнести фразу
    "run_macro",    # Запустить другой макрос (вложенность)
})

# Действия, которые ОБЯЗАТЕЛЬНО требуют target
_ACTIONS_REQUIRE_TARGET = {"open_app", "open_url", "say", "run_macro"}

# Действия, которые ОБЯЗАТЕЛЬНО требуют value
_ACTIONS_REQUIRE_VALUE = {"volume", "brightness"}


# ═══════════════════════════════════════════
#  MacroStep
# ═══════════════════════════════════════════

@dataclass
class MacroStep:
    """
    Один шаг макроса.

    Поля:
        action: имя действия из SUPPORTED_ACTIONS
        target: объект действия (имя приложения, URL, текст, macro_id)
        value:  числовое значение (громкость, яркость, секунды)
        delay:  пауза ПОСЛЕ шага (секунды, 0 = дефолт executor'а)
    """
    action: str
    target: str = ""
    value: str = ""
    delay: float = 0.0

    def validate(self) -> str | None:
        """
        Проверить корректность шага.

        Returns: строка ошибки или None (всё ок).
        """
        if self.action not in SUPPORTED_ACTIONS:
            return (
                f"Неизвестное действие '{self.action}'. "
                f"Допустимые: {', '.join(sorted(SUPPORTED_ACTIONS))}"
            )

        # target обязателен для open_app, open_url, say, run_macro
        if self.action in _ACTIONS_REQUIRE_TARGET and not self.target:
            return f"Действие '{self.action}' требует поле target"

        # value обязателен для volume, brightness
        if self.action in _ACTIONS_REQUIRE_VALUE and not self.value:
            return f"Действие '{self.action}' требует поле value"

        # Валидация числовых значений
        if self.action in ("volume", "brightness"):
            try:
                v = int(self.value)
            except (ValueError, TypeError):
                return (
                    f"Поле value для '{self.action}' "
                    f"должно быть числом, получено '{self.value}'"
                )
            if not 0 <= v <= 100:
                return (
                    f"Значение {self.action} должно быть 0–100, "
                    f"получено {v}"
                )

        # wait: target или value должны быть числом (если заданы)
        if self.action == "wait":
            raw = self.target or self.value
            if raw:
                try:
                    float(raw)
                except (ValueError, TypeError):
                    return (
                        f"Поле target/value для 'wait' "
                        f"должно быть числом (секунды), получено '{raw}'"
                    )

        if self.delay < 0:
            return f"delay не может быть отрицательным: {self.delay}"

        return None

    def __repr__(self) -> str:
        parts = [f"action={self.action!r}"]
        if self.target:
            parts.append(f"target={self.target!r}")
        if self.value:
            parts.append(f"value={self.value!r}")
        if self.delay:
            parts.append(f"delay={self.delay}")
        return f"MacroStep({', '.join(parts)})"


# ═══════════════════════════════════════════
#  Macro
# ═══════════════════════════════════════════

@dataclass
class Macro:
    """
    Описание макроса из config.yaml.

    Поля:
        id:       ключ в конфиге ("morning")
        name:     отображаемое имя ("Утренний ритуал")
        keywords: фразы для голосового матчинга (lower case)
        greeting: фраза перед началом (может быть пустой)
        farewell: фраза после завершения (может быть пустой)
        steps:    список шагов
    """
    id: str
    name: str
    keywords: list[str] = field(default_factory=list)
    greeting: str = ""
    farewell: str = ""
    steps: list[MacroStep] = field(default_factory=list)

    def validate(self) -> list[str]:
        """
        Валидировать макрос целиком.

        Returns: список ошибок (пустой = всё ок).
        """
        errors = []

        if not self.name:
            errors.append(f"Макрос '{self.id}': пустое имя (name)")

        if not self.keywords:
            errors.append(
                f"Макрос '{self.id}': нет ключевых слов (keywords). "
                f"Команда не сможет его вызвать."
            )

        if not self.steps:
            errors.append(f"Макрос '{self.id}': нет шагов (steps)")

        for i, step in enumerate(self.steps, 1):
            err = step.validate()
            if err:
                errors.append(f"Макрос '{self.id}', шаг {i}: {err}")

        return errors

    def __repr__(self) -> str:
        return (
            f"Macro(id={self.id!r}, name={self.name!r}, "
            f"keywords={self.keywords}, steps={len(self.steps)})"
        )