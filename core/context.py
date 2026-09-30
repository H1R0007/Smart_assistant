# core/context.py

"""
Менеджер контекста диалога.
Позволяет командам понимать "а теперь там же", "закрой его".

v3.3:
- _context остаётся для обратной совместимости
- set_instance() позволяет AssistantContext подставить свой экземпляр
"""

import time
import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ContextState:
    """Слепок состояния диалога."""
    action: str = ""
    target: str = ""
    params: dict = field(default_factory=dict)
    timestamp: float = 0.0

    @property
    def is_fresh(self) -> bool:
        return (time.time() - self.timestamp) < 60


class ContextManager:
    """Менеджер контекста — один экземпляр на ассистента."""

    def __init__(self):
        self._state = ContextState()
        self._lock = threading.Lock()

    def update(self, action: str, target: str, params: dict = None):
        with self._lock:
            self._state = ContextState(
                action=action,
                target=target,
                params=params or {},
                timestamp=time.time(),
            )
        logger.info("Context updated: %s -> %s", action, target)

    def get(self) -> ContextState:
        with self._lock:
            return self._state

    def clear(self):
        with self._lock:
            self._state = ContextState()


# ── Глобальный инстанс (обратная совместимость) ──
_context = ContextManager()


def set_instance(ctx: ContextManager) -> None:
    """
    Подменить глобальный экземпляр.
    Вызывается из AssistantContext.__post_init__().
    """
    global _context
    _context = ctx
    logger.debug("ContextManager: глобальный экземпляр заменён")


def get_context() -> ContextManager:
    return _context