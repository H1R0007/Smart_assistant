# core/gui/macro_nodes.py

"""
Графические узлы для визуального редактора макросов.
Каждый узел = один шаг макроса.
"""

from PySide6.QtWidgets import (
    QGraphicsObject, QGraphicsItem, QGraphicsProxyWidget,
    QLineEdit, QDoubleSpinBox,
)
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QPainterPath
from PySide6.QtCore import Qt, QRectF, QPointF, Signal

from core.gui.theme import Theme

T = Theme

# ── Конфигурация действий ──

ACTION_CONFIGS = {
    "open_app": {
        "color": T.ACCENT_SECONDARY,
        "title": "Открыть приложение",
        "param": "app",
    },
    "open_url": {
        "color": T.ACCENT_WARNING,
        "title": "Открыть URL",
        "param": "url",
    },
    "volume": {
        "color": T.ACCENT_SUCCESS,
        "title": "Громкость (%)",
        "param": "value",
    },
    "brightness": {
        "color": "#e8a050",
        "title": "Яркость (%)",
        "param": "value",
    },
    "wait": {
        "color": "#f7a072",
        "title": "Пауза (сек)",
        "param": "seconds",
    },
    "say": {
        "color": T.ACCENT_PRIMARY,
        "title": "Сказать",
        "param": "text",
    },
    "run_macro": {
        "color": T.ACCENT_DANGER,
        "title": "Запустить макрос",
        "param": "macro_id",
    },
}


class MacroNode(QGraphicsObject):
    """Один перетаскиваемый блок на канвасе."""

    moved = Signal()

    WIDTH = 240
    HEIGHT = 66
    RADIUS = 8
    PIN_R = 5

    def __init__(self, action_type: str, parent=None):
        super().__init__(parent)
        self.action_type = action_type
        self.config = ACTION_CONFIGS.get(action_type, ACTION_CONFIGS["say"])

        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)

        self._input = self._make_input()
        self._proxy = QGraphicsProxyWidget(self)
        self._proxy.setWidget(self._input)
        self._proxy.setPos(10, 33)

    # ── Input widget ──

    def _make_input(self):
        if self.action_type in ("volume", "brightness"):
            w = QDoubleSpinBox()
            w.setRange(0, 100)
            w.setDecimals(0)
            w.setFixedSize(220, 24)
        elif self.action_type == "wait":
            w = QDoubleSpinBox()
            w.setRange(0.1, 3600)
            w.setSingleStep(0.5)
            w.setValue(1.0)
            w.setFixedSize(220, 24)
        else:
            w = QLineEdit()
            w.setPlaceholderText(f"{self.config['param']}...")
            w.setFixedSize(220, 24)

        w.setStyleSheet(f"""
            QWidget {{
                background-color: {T.BG_DARKEST};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: 4px;
                font-size: 12px;
            }}
        """)
        return w

    # ── Data I/O ──

    def get_data(self) -> dict:
        """Собрать данные шага (формат совместим с MacroStep)."""
        raw = ""
        if isinstance(self._input, QLineEdit):
            raw = self._input.text()
        elif isinstance(self._input, QDoubleSpinBox):
            raw = self._input.value()

        step = {"action": self.action_type}

        if self.action_type in ("volume", "brightness"):
            step["value"] = str(int(raw)) if raw else "0"
        elif self.action_type == "wait":
            step["value"] = str(raw) if raw else "1"
        else:
            step["target"] = str(raw) if raw else ""

        return step

    def set_data(self, step: dict):
        """Заполнить виджет из словаря шага."""
        if self.action_type in ("volume", "brightness"):
            v = step.get("value")
            if v is not None and isinstance(self._input, QDoubleSpinBox):
                try:
                    self._input.setValue(float(v))
                except (ValueError, TypeError):
                    pass
        elif self.action_type == "wait":
            v = step.get("value") or step.get("target") or step.get("delay")
            if v is not None and isinstance(self._input, QDoubleSpinBox):
                try:
                    self._input.setValue(float(v))
                except (ValueError, TypeError):
                    pass
        else:
            v = step.get("target")
            if v is not None and isinstance(self._input, QLineEdit):
                self._input.setText(str(v))

    # ── Qt overrides ──

    def boundingRect(self) -> QRectF:
        m = self.PIN_R + 2
        return QRectF(-m, -m, self.WIDTH + m * 2, self.HEIGHT + m * 2)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.moved.emit()
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing)

        bg = QColor(self.config["color"])
        if self.isSelected():
            painter.setPen(QPen(QColor(T.TEXT_PRIMARY), 2))
            bg = bg.lighter(115)
        else:
            painter.setPen(Qt.NoPen)

        path = QPainterPath()
        path.addRoundedRect(
            0, 0, self.WIDTH, self.HEIGHT, self.RADIUS, self.RADIUS,
        )
        painter.fillPath(path, QBrush(bg))
        painter.drawPath(path)

        # Заголовок
        painter.setPen(QPen(QColor("#11111b")))
        painter.setFont(QFont(T.FONT_FAMILY, 10, QFont.Bold))
        painter.drawText(
            QRectF(10, 4, self.WIDTH - 20, 24),
            Qt.AlignLeft | Qt.AlignVCenter,
            self.config["title"],
        )

        # Пины
        painter.setBrush(QBrush(QColor(T.BORDER)))
        painter.setPen(QPen(QColor("#11111b"), 1))
        cx = self.WIDTH / 2
        painter.drawEllipse(QPointF(cx, 0), self.PIN_R, self.PIN_R)
        painter.drawEllipse(
            QPointF(cx, self.HEIGHT), self.PIN_R, self.PIN_R,
        )