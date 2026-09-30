# core/macros/__init__.py

"""
Система декларативных макросов.

Позволяет описывать сценарии в config.yaml и выполнять голосом.

Три слоя:
  - macro.py:    модели данных (Macro, MacroStep) — чистые dataclass
  - loader.py:   парсинг config.yaml → dict[str, Macro]
  - executor.py: выполнение шагов с защитами
"""

from core.macros.macro import Macro, MacroStep, SUPPORTED_ACTIONS
from core.macros.loader import load_macros
from core.macros.executor import MacroExecutor

__all__ = [
    "Macro", "MacroStep", "SUPPORTED_ACTIONS",
    "load_macros", "MacroExecutor",
]