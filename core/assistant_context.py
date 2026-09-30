# core/assistant_context.py

"""
Контракт зависимостей для команд-плагинов.

v3.3:
- context: ContextManager — единый контекст follow-up диалога
- Инжекция в глобальный get_context() для обратной совместимости
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Any

if TYPE_CHECKING:
    from core.speaker import Speaker
    from core.config import Config
    from core.llm import LLM
    from core.knowledge import KnowledgeBase
    from core.recognizer import Recognizer

from core.context import ContextManager, set_instance as _set_global_context


@dataclass
class DialogContext:
    """Состояние текущей диалоговой сессии (wake → silence)."""
    last_command_name: str = ""
    last_params: dict = field(default_factory=dict)
    last_raw_text: str = ""
    commands_in_session: int = 0

    def update(self, command_name: str, params: dict, raw_text: str) -> None:
        self.last_command_name = command_name
        self.last_params = params
        self.last_raw_text = raw_text
        self.commands_in_session += 1

    def reset(self) -> None:
        self.last_command_name = ""
        self.last_params = {}
        self.last_raw_text = ""
        self.commands_in_session = 0


@dataclass
class AssistantContext:
    """Контейнер зависимостей для команд-плагинов."""

    # Обязательные
    speaker: "Speaker"
    config: "Config"

    # Опциональные
    llm: Optional["LLM"] = None
    knowledge: Optional["KnowledgeBase"] = None
    recognizer: Optional["Recognizer"] = None

    # Сервисы
    timer_manager: Optional[Any] = None
    volume_control: Optional[Any] = None
    weather_cache: Optional[Any] = None

    # Контексты
    dialog: DialogContext = field(default_factory=DialogContext)
    context: ContextManager = field(default_factory=ContextManager)

    # Callbacks
    on_reload_command: Optional[Callable[[str], bool]] = None
    on_command_executed: Optional[Callable[[str, dict], None]] = None

    def __post_init__(self):
        # Инжектируем в глобальный get_context()
        # для обратной совместимости с командами,
        # которые ещё используют from core.context import get_context
        _set_global_context(self.context)

    def reload_command(self, name: str) -> bool:
        if self.on_reload_command is None:
            return False
        return self.on_reload_command(name)

    def trigger_command_executed(self, command_name: str, params: dict) -> None:
        if self.on_command_executed:
            try:
                self.on_command_executed(command_name, params)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    "Error in on_command_executed: %s", e,
                )