# core/router.py

"""
Маршрутизатор команд v3.2.

Изменения v3.2:
- AssistantContext импортируется из core.assistant_context
- DialogContext перенесён в core.assistant_context
- _update_context() удалён — логика в командах (BaseCommand.update_context)
- _get_command() использует dict вместо линейного поиска O(N)
- on_reload_command callback вместо ctx._router
"""

import time
import logging
from dataclasses import dataclass

from core.assistant_context import AssistantContext, DialogContext
from commands import load_all_commands
from commands.base import BaseCommand, CommandResult, normalize_text

logger = logging.getLogger(__name__)


@dataclass
class MatchCandidate:
    """Один кандидат на выполнение."""
    command_name: str
    priority: int
    params: dict
    match_type: str  # "exact", "fuzzy", "custom"
    score: float = 1.0


class CommandRouter:
    """
    Маршрутизатор команд v3.2.

    Изменения:
    - Нет _update_context(): каждая команда сама вызывает
      ctx_manager.update() через BaseCommand.update_context().
    - _get_command() — O(1) через dict.
    - ctx._router убран: передаём on_reload_command callback.
    """

    GARBAGE_PATTERNS = {
        "ммм", "мммм", "ааа", "эээ", "ну",
        "так", "ага", "угу", "ой", "ох",
    }

    MIN_TEXT_LENGTH = 2

    CHAIN_SEPARATORS = [
        " а потом ", " а затем ", " затем ",
        " после этого ", " а также ", " и ещё ",
        " и еще ", " плюс ",
    ]

    def __init__(
        self,
        ctx: AssistantContext,
        explain: bool = False,
    ) -> None:
        self._ctx = ctx
        self._explain = explain

        # Загружаем команды
        self._commands: list[BaseCommand] = load_all_commands(ctx)

        # O(1) поиск по имени — вместо линейного O(N)
        self._commands_by_name: dict[str, BaseCommand] = {
            cmd.name: cmd for cmd in self._commands
        }

        # Подключаем callback для hot-reload
        ctx.on_reload_command = self.reload_command

        # Диалог доступен через ctx
        # (DialogContext теперь в AssistantContext.dialog)

        logger.info(
            "Router: %d команд загружено", len(self._commands),
        )
        logger.debug(
            "Приоритеты: %s",
            sorted(
                [(c.name, c.priority) for c in self._commands],
                key=lambda x: -x[1],
            ),
        )

    # ══════════════════════════════════════════════
    #  Публичные свойства
    # ══════════════════════════════════════════════

    @property
    def command_count(self) -> int:
        return len(self._commands)

    @property
    def explain_mode(self) -> bool:
        return self._explain

    @explain_mode.setter
    def explain_mode(self, value: bool) -> None:
        self._explain = value

    # ══════════════════════════════════════════════
    #  Публичный API
    # ══════════════════════════════════════════════

    def process(self, text: str) -> CommandResult:
        """Главная точка входа. Текст нормализуется здесь ОДИН РАЗ."""
        if not text:
            return CommandResult()

        # Единственная нормализация в системе
        text = normalize_text(text)

        if not self._is_valid_input(text):
            logger.info("Мусор отфильтрован: '%s'", text)
            self._ctx.speaker.say("Повтори, пожалуйста.")
            return CommandResult()

        logger.info("Router: '%s'", text)
        total_start = time.time()

        parts = self._split_chain(text)
        if len(parts) > 1:
            logger.info("Цепочка: %s", parts)

        final_result = CommandResult()

        for i, part in enumerate(parts):
            part = part.strip()
            if not part or not self._is_valid_input(part):
                continue

            if len(parts) > 1:
                logger.info(
                    "Часть %d/%d: '%s'", i + 1, len(parts), part,
                )

            result = self._process_single(part)

            # Агрегируем результат цепочки:
            # should_continue=False от любой части — останавливаем всё.
            # follow_up=True от любой части — сохраняем.
            if not result.should_continue:
                return result

            # follow_up берём от последней части, если предыдущая
            # не выставила его в True.
            if result.follow_up:
                final_result = result
            else:
                final_result = result

        logger.debug(
            "Router: обработка за %.3f сек",
            time.time() - total_start,
        )
        return final_result

    def reset_dialog(self) -> None:
        """Сбросить состояние диалога (вызывается при новом wake word)."""
        self._ctx.dialog.reset()
        self._ctx.context.clear()
        if self._ctx.llm:
            self._ctx.llm.clear_history()

    def reload_command(self, name: str) -> bool:
        """
        Перезагрузить команду по имени.

        Создаёт новый экземпляр того же класса.
        ВАЖНО: перезагружает экземпляр, не Python-модуль.
        Для перезагрузки кода команды нужен рестарт ассистента.
        Используется для подхвата изменений в данных
        (например, новый макрос в config.yaml).

        Returns: True если команда найдена и перезагружена.
        """
        cmd = self._commands_by_name.get(name)
        if cmd is None:
            logger.warning(
                "Команда '%s' не найдена для перезагрузки", name,
            )
            return False

        try:
            new_cmd = type(cmd)(self._ctx)
            # Обновляем в обоих структурах
            idx = self._commands.index(cmd)
            self._commands[idx] = new_cmd
            self._commands_by_name[name] = new_cmd
            logger.info("Команда '%s' перезагружена", name)
            return True
        except Exception as e:
            logger.error(
                "Ошибка перезагрузки '%s': %s", name, e,
            )
            return False

    # ══════════════════════════════════════════════
    #  Внутренняя обработка
    # ══════════════════════════════════════════════

    def _process_single(self, text: str) -> CommandResult:
        match_start = time.time()
        candidates = self._collect_candidates(text)
        match_elapsed = time.time() - match_start

        if not candidates:
            if self._explain:
                logger.info("EXPLAIN: Нет кандидатов для '%s'", text)
            logger.info(
                "Нет кандидатов для '%s' (матчинг: %.3f сек)",
                text, match_elapsed,
            )
            self._ctx.speaker.say("Не поняла, повтори.")
            return CommandResult()

        self._log_candidates(candidates, match_elapsed)

        for candidate in candidates:
            cmd = self._commands_by_name.get(candidate.command_name)
            if cmd is None:
                continue

            try:
                exec_start = time.time()
                result = cmd.execute(text, candidate.params)
                exec_elapsed = time.time() - exec_start

                logger.info(
                    "Выполнено: %s (%.3f сек)",
                    candidate.command_name, exec_elapsed,
                )

                if result.success:
                    # Команда сама обновляет контекст
                    cmd.update_context(candidate.params)

                    # Обновляем диалог
                    self._ctx.dialog.update(
                        cmd.name, candidate.params, text,
                    )

                    self._ctx.trigger_command_executed(cmd.name, candidate.params)

                return result

            except Exception as e:
                logger.error(
                    "Ошибка выполнения %s: %s",
                    candidate.command_name, e,
                    exc_info=True,
                )
                # Intentional fallback: пробуем следующего кандидата
                continue

        self._ctx.speaker.say("Произошла ошибка.")
        return CommandResult()

    def _collect_candidates(
        self, text: str,
    ) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        explain_log: list[str] = []

        for cmd in self._commands:
            try:
                matched, params = cmd.match(text)

                if not matched:
                    if self._explain:
                        explain_log.append(
                            f"  ✗ {cmd.name}: no match",
                        )
                    continue

                if not cmd.is_valid_candidate(params):
                    if self._explain:
                        explain_log.append(
                            f"  ✗ {cmd.name}: matched but "
                            f"invalid candidate (params={params})",
                        )
                    continue

                match_type = self._determine_match_type(cmd, text)
                candidates.append(MatchCandidate(
                    command_name=cmd.name,
                    priority=cmd.priority,
                    params=params,
                    match_type=match_type,
                    score=1.0 if match_type == "exact" else 0.9,
                ))

                if self._explain:
                    explain_log.append(
                        f"  ✓ {cmd.name}: {match_type} "
                        f"p={cmd.priority} params={params}",
                    )

            except Exception as e:
                logger.error("Ошибка матчинга %s: %s", cmd.name, e)

        if self._explain and explain_log:
            logger.info("EXPLAIN для '%s':", text)
            for line in explain_log:
                logger.info(line)

        type_order = {"exact": 2, "custom": 1, "fuzzy": 0}
        candidates.sort(
            key=lambda c: (
                type_order.get(c.match_type, 0),
                c.priority,
                c.score,
            ),
            reverse=True,
        )

        return candidates

    def _is_valid_input(self, text: str) -> bool:
        if len(text) < self.MIN_TEXT_LENGTH:
            return False
        if text in self.GARBAGE_PATTERNS:
            return False
        if len(set(text.replace(" ", ""))) <= 2 and len(text) > 3:
            return False
        return True

    def _split_chain(self, text: str) -> list[str]:
        for sep in self.CHAIN_SEPARATORS:
            if sep in text:
                return [
                    p.strip()
                    for p in text.split(sep)
                    if p.strip()
                ]
        return [text]

    def _determine_match_type(self, cmd: BaseCommand, text: str) -> str:
        """Тип матча — берём из кэша (вычислено при загрузке)."""
        cached = getattr(cmd, '_cached_match_type', None)
        if cached == "custom":
            return "custom"
        # Для keyword-команд проверяем exact vs fuzzy
        text_lower = text.lower()
        for kw in cmd.keywords:
            if kw in text_lower:
                return "exact"
        return "fuzzy"

    def _log_candidates(
        self,
        candidates: list[MatchCandidate],
        match_time: float,
    ) -> None:
        if len(candidates) == 1:
            c = candidates[0]
            logger.info(
                "→ %s [%s, p=%d] (матчинг: %.3f сек)",
                c.command_name, c.match_type,
                c.priority, match_time,
            )
        else:
            logger.info(
                "Кандидаты (%d, матчинг: %.3f сек):",
                len(candidates), match_time,
            )
            for i, c in enumerate(candidates):
                marker = "→" if i == 0 else " "
                logger.info(
                    "  %s %s [%s, p=%d, s=%.2f]",
                    marker, c.command_name,
                    c.match_type, c.priority, c.score,
                )