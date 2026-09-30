# tests/test_macros/helpers.py

"""
Вспомогательные классы для тестов макросов.

Вынесены из conftest.py чтобы можно было импортировать
напрямую (conftest не предназначен для прямого импорта).
"""

from dataclasses import dataclass


@dataclass
class MockActivityRecord:
    """
    Мок ActivityRecord.

    Повторяет структуру
    core.knowledge.storage.ActivityRecord
    без зависимости от реального модуля.
    """
    id: int = 0
    action: str = "app_switch"
    target: str = ""
    details: str = ""
    window_title: str = ""
    timestamp: float = 0.0
    duration_sec: float = 0.0