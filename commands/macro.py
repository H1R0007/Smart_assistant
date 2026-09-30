# commands/macro.py

"""
Команда для выполнения декларативных макросов из config.yaml.

Отличие от commands/macros.py (WorkStartCommand):
  - macros.py — захардкоженный сценарий с диалогами и параллельным запуском.
  - macro.py  — универсальный исполнитель ЛЮБЫХ макросов из конфига.
Оба загружаются плагин-системой и сосуществуют.
"""

import logging

from commands.base import BaseCommand, CommandResult, normalize_text
from core.macros import load_macros, MacroExecutor
logger = logging.getLogger(__name__)


class MacroCommand(BaseCommand):
    """
    Динамическая команда для пользовательских макросов.

    keywords собираются из всех макросов в конфиге.
    Если макросов нет — keywords=[], match() всегда False.
    """

    name = "macro"
    description = "Пользовательские макросы из config.yaml"

    # Приоритет 12: выше стандартных (5–9), ниже WorkStartCommand (15).
    priority = 12

    fuzzy_enabled = True

    # Заполняется динамически в __init__
    keywords = []

    def __init__(self, ctx):
        super().__init__(ctx)

        # Загружаем макросы из конфига
        self._macros = load_macros(ctx.config)

        # Ключевые слова из всех макросов
        self.keywords = []
        for macro in self._macros.values():
            self.keywords.extend(macro.keywords)

        # Обратный маппинг: keyword → macro_id
        self._kw_to_macro: dict[str, str] = {}
        for macro_id, macro in self._macros.items():
            for kw in macro.keywords:
                if kw in self._kw_to_macro:
                    logger.warning(
                        "Дублирование keyword '%s': '%s' перезапишет '%s'",
                        kw, macro_id, self._kw_to_macro[kw],
                    )
                self._kw_to_macro[kw] = macro_id

        # Executor переиспользуется
        self._executor = (
            MacroExecutor(ctx, self._macros) if self._macros else None
        )

        if self._macros:
            self.logger.info(
                "Макросы: %s",
                ", ".join(
                    f"'{m.name}' ({len(m.steps)} шагов)"
                    for m in self._macros.values()
                ),
            )

    # ══════════════════════════════════════════════
    #  Матчинг
    # ══════════════════════════════════════════════

    def match(self, text: str) -> tuple[bool, dict]:
        """Стандартный BaseCommand.match с динамическими keywords."""
        if not self._macros:
            return False, {}
        return super().match(text)

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        """Определить, какой макрос вызвать по matched keyword."""
        # Быстрый путь
        if matched_keyword and matched_keyword in self._kw_to_macro:
            return {
                "macro_id": self._kw_to_macro[matched_keyword],
                "keyword": matched_keyword,
            }

        # Fallback: поиск вручную
        # БЫЛО: text_norm = normalize_text(text)
        # СТАЛО: text уже нормализован
        text_norm = text
        for kw in sorted(self._kw_to_macro, key=len, reverse=True):
            if self._exact_match(kw, text_norm):
                return {
                    "macro_id": self._kw_to_macro[kw],
                    "keyword": kw,
                }

        return {}

    # ══════════════════════════════════════════════
    #  Валидация
    # ══════════════════════════════════════════════

    def is_valid_candidate(self, params: dict) -> bool:
        if self._executor is None:
            return False
        macro_id = params.get("macro_id")
        return macro_id is not None and macro_id in self._macros

    # ══════════════════════════════════════════════
    #  Выполнение
    # ══════════════════════════════════════════════

    def execute(self, text: str, params: dict) -> CommandResult:
        """Запустить макрос и обновить контекст."""
        macro_id = params.get("macro_id")
        macro = self._macros.get(macro_id)

        if not macro or not self._executor:
            self.speaker.say("Макрос не найден.")
            return CommandResult(success=False)

        self.logger.info(
            "▶ Запуск макроса '%s' (%d шагов)",
            macro.name, len(macro.steps),
        )

        try:
            success = self._executor.execute(macro)
        except Exception as e:
            self.logger.error(
                "Критическая ошибка макроса '%s': %s",
                macro.name, e, exc_info=True,
            )
            self.speaker.say("Произошла ошибка при выполнении макроса.")
            success = False

        # Контекст для follow-up ("запусти ещё раз")
        self.ctx.context.update(
            action="macro",
            target=macro_id,
            params={"macro_name": macro.name},
        )

        self.logger.info(
            "%s Макрос '%s' %s",
            "✔" if success else "✗",
            macro.name,
            "завершён" if success else "завершён с ошибками",
        )

        return CommandResult(success=success, follow_up=True)