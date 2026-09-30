# core/gui/state.py

"""
Сохранение и восстановление состояния GUI между сессиями.
Использует QSettings (реестр Windows / ini-файл).
"""

from PySide6.QtCore import QSettings, QByteArray


class StateManager:
    """
    Singleton для хранения состояния GUI.

    Сохраняет:
    - Геометрию окна (позиция + размер)
    - Активную страницу
    - Состояние сайдбара
    - Позиции сплиттеров
    """

    _instance = None

    @classmethod
    def instance(cls) -> "StateManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._s = QSettings("FridayAssistant", "GUI")

    # ── Геометрия окна ──

    def save_geometry(self, window) -> None:
        self._s.setValue("window/geometry", window.saveGeometry())

    def restore_geometry(self, window) -> bool:
        g = self._s.value("window/geometry")
        if isinstance(g, QByteArray) and not g.isEmpty():
            window.restoreGeometry(g)
            return True
        return False

    # ── Значения ──

    def set(self, key: str, value) -> None:
        self._s.setValue(key, value)

    def get(self, key: str, default=None):
        v = self._s.value(key)
        return v if v is not None else default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._s.value(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self._s.value(key, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1")
        return default

    def get_list(self, key: str, default: list = None) -> list:
        """QSettings может вернуть единичное значение вместо списка."""
        v = self._s.value(key)
        if v is None:
            return default or []
        if isinstance(v, list):
            return v
        return [v]