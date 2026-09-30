# core/gui/components.py

"""
Библиотека переиспользуемых UI-компонентов.
v2: анимации hover на Card, pulse на StatusBadge, fade на ResultBar.
"""

import time as _time
from datetime import datetime

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QWidget, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect, QSizePolicy,
)
from PySide6.QtCore import (
    Qt, Signal, QTimer, QSize,
    QVariantAnimation, QEasingCurve,
    QPropertyAnimation,
)
from PySide6.QtGui import QColor, QPainter

from core.gui.theme import Theme, get_icon

T = Theme


# ═══════════════════════════════════════════
#  Card (с hover-анимацией тени)
# ═══════════════════════════════════════════

class Card(QFrame):
    """Карточка с тенью и анимированным hover-glow."""

    def __init__(self, title: str = "", accent_color: str = None,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("card")

        border_left = (
            f"border-left: 3px solid {accent_color};"
            if accent_color else ""
        )
        self.setStyleSheet(f"""
            QFrame#card {{
                background-color: {T.BG_CARD};
                border-radius: {T.RADIUS_MD}px;
                border: 1px solid {T.BORDER};
                {border_left}
            }}
        """)

        # Тень с анимацией
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(20)
        self._shadow.setColor(QColor(0, 0, 0, 35))
        self._shadow.setOffset(0, 2)
        self.setGraphicsEffect(self._shadow)

        self._hover_t = 0.0
        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setDuration(200)
        self._hover_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_tick)

        self._main = QVBoxLayout(self)
        self._main.setContentsMargins(16, 12, 16, 12)
        self._main.setSpacing(8)

        if title:
            lbl = QLabel(title)
            lbl.setFont(T.font(14, bold=True))
            lbl.setStyleSheet(
                f"color: {T.TEXT_PRIMARY}; border: none; background: transparent;"
            )
            self._main.addWidget(lbl)

        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(6)
        self._main.addLayout(self.content_layout)

    def add_widget(self, widget: QWidget):
        self.content_layout.addWidget(widget)

    def enterEvent(self, event):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(event)

    def _on_hover_tick(self, val):
        t = float(val)
        self._hover_t = t
        blur = 20 + t * 15          # 20 → 35
        alpha = 35 + t * 30         # 35 → 65
        offset_y = 2 + t * 2        # 2 → 4
        self._shadow.setBlurRadius(blur)
        self._shadow.setColor(QColor(0, 0, 0, int(alpha)))
        self._shadow.setOffset(0, offset_y)


# ═══════════════════════════════════════════
#  Buttons
# ═══════════════════════════════════════════

class AccentButton(QPushButton):
    def __init__(self, text: str, icon_name: str = None, parent=None):
        super().__init__(text, parent)
        if icon_name:
            self.setIcon(get_icon(icon_name, "#ffffff"))
            self.setIconSize(T.ICON_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(T.font(13, bold=True))
        self.setFixedHeight(36)
        self.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {T.ACCENT_PRIMARY}, stop:1 {T.ACCENT_SECONDARY}
                );
                color: #ffffff;
                border: none;
                border-radius: {T.RADIUS_SM}px;
                padding: 0 20px;
            }}
            QPushButton:hover {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {T.ACCENT_SECONDARY}, stop:1 {T.ACCENT_PRIMARY}
                );
            }}
            QPushButton:pressed {{ background-color: {T.ACCENT_PRIMARY}; }}
        """)


class GhostButton(QPushButton):
    def __init__(self, text: str, icon_name: str = None,
                 color: str = None, parent=None):
        super().__init__(text, parent)
        c = color or T.TEXT_SECONDARY
        if icon_name:
            self.setIcon(get_icon(icon_name, c))
            self.setIconSize(T.ICON_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(T.font(12))
        self.setFixedHeight(34)
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {c};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: {T.BG_HOVER};
                border-color: {c};
                color: {T.TEXT_PRIMARY};
            }}
        """)


class IconButton(QPushButton):
    def __init__(self, icon_name: str, tooltip: str = "",
                 size: int = 32, color: str = None, parent=None):
        super().__init__(parent)
        c = color or T.TEXT_SECONDARY
        self.setIcon(get_icon(icon_name, c))
        self.setIconSize(QSize(size - 12, size - 12))
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                border-radius: {size // 2}px;
            }}
            QPushButton:hover {{ background-color: {T.BG_HOVER}; }}
        """)


# ═══════════════════════════════════════════
#  SearchBar
# ═══════════════════════════════════════════

class SearchBar(QWidget):
    search_requested = Signal(str)

    def __init__(self, placeholder: str = "Поиск...", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        self.input.setClearButtonEnabled(True)
        self.input.setFixedHeight(38)
        self.input.setFont(T.font(13))

        try:
            import qtawesome as qta
            self.input.addAction(
                qta.icon("fa6s.magnifying-glass", color=T.TEXT_MUTED),
                QLineEdit.LeadingPosition,
            )
        except Exception:
            pass

        self.input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.BG_INPUT};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
                padding: 0 12px 0 8px;
            }}
            QLineEdit:focus {{ border-color: {T.BORDER_FOCUS}; }}
        """)
        layout.addWidget(self.input)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(
            lambda: self.search_requested.emit(self.input.text().strip())
        )
        self.input.textChanged.connect(lambda: self._debounce.start())
        self.input.returnPressed.connect(
            lambda: self.search_requested.emit(self.input.text().strip())
        )

    @property
    def text(self) -> str:
        return self.input.text().strip()


# ═══════════════════════════════════════════
#  StatusBadge (с pulse-анимацией)
# ═══════════════════════════════════════════

class StatusBadge(QWidget):
    """Цветная точка + текст. Точка пульсирует при смене статуса."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = T.TEXT_MUTED
        self._text = "Отключено"
        self._dot_scale = 1.0
        self.setFixedHeight(24)
        self.setMinimumWidth(100)

        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(300)
        self._pulse.setEasingCurve(QEasingCurve.OutCubic)
        self._pulse.valueChanged.connect(self._on_pulse)

    def set_status(self, color: str, text: str):
        self._color = color
        self._text = text
        # Pulse
        self._pulse.stop()
        self._pulse.setStartValue(1.5)
        self._pulse.setEndValue(1.0)
        self._pulse.start()
        self.update()

    def _on_pulse(self, val):
        self._dot_scale = float(val)
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        y = self.height() / 2

        # Точка с масштабом
        r = 4 * self._dot_scale
        cx = 8
        p.setBrush(QColor(self._color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(int(cx - r), int(y - r), int(r * 2), int(r * 2))

        # Текст
        p.setPen(QColor(T.TEXT_SECONDARY))
        p.setFont(T.font(12))
        p.drawText(18, int(y) + 4, self._text)
        p.end()


# ═══════════════════════════════════════════
#  CommandInput
# ═══════════════════════════════════════════

class CommandInput(QWidget):
    """Строка ввода с автодополнением из палитры команд."""
    command_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(T.COMMAND_INPUT_H)
        self.setStyleSheet(f"background-color: {T.BG_CARD};")

        self._suggestions: list[dict] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Введите команду...")
        self.input.setFont(T.font(13))
        self.input.setFixedHeight(36)
        self.input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.BG_INPUT};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
                padding: 0 12px;
            }}
            QLineEdit:focus {{ border-color: {T.BORDER_FOCUS}; }}
        """)
        self.input.returnPressed.connect(self._submit)
        self.input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.input)

        send_btn = IconButton(
            "fa6s.paper-plane", "Отправить", size=36,
            color=T.ACCENT_PRIMARY,
        )
        send_btn.clicked.connect(self._submit)
        layout.addWidget(send_btn)

        # Popup подсказок
        self._popup = _SuggestionPopup(self)
        self._popup.item_selected.connect(self._on_suggestion)
        self._popup.hide()

    def set_suggestions(self, commands: list[dict]):
        """Установить список команд для автодополнения."""
        self._suggestions = commands

    def _submit(self):
        self._popup.hide()
        text = self.input.text().strip()
        if text:
            self.command_submitted.emit(text)
            self.input.clear()

    def _on_text_changed(self, text: str):
        text = text.strip().lower()
        if len(text) < 2 or not self._suggestions:
            self._popup.hide()
            return

        matches = []
        for cmd in self._suggestions:
            searchable = (
                cmd.get("description", "") + " " +
                " ".join(cmd.get("keywords", []))
            ).lower()
            if text in searchable:
                matches.append(cmd)
            if len(matches) >= 5:
                break

        if matches:
            self._popup.show_suggestions(matches, self)
        else:
            self._popup.hide()

    def _on_suggestion(self, keyword: str):
        self.input.setText(keyword)
        self._popup.hide()
        self._submit()


class _SuggestionPopup(QFrame):
    """Всплывающий список подсказок над строкой ввода."""
    item_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_CARD};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
            }}
        """)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(2)

    def show_suggestions(self, commands: list[dict], anchor: QWidget):
        # Очистить
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for cmd in commands:
            kw = cmd.get("keywords", [""])[0] if cmd.get("keywords") else ""
            desc = cmd.get("description", kw)
            cat = cmd.get("category", "")

            btn = QPushButton(f"{desc}  ·  {cat}")
            btn.setFont(T.font(11))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {T.TEXT_PRIMARY};
                    text-align: left;
                    padding: 0 10px;
                    border: none;
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    background-color: {T.BG_HOVER};
                }}
            """)
            btn.clicked.connect(lambda _, k=kw: self.item_selected.emit(k))
            self._layout.addWidget(btn)

        # Позиция: над строкой ввода
        pos = anchor.mapToGlobal(anchor.rect().topLeft())
        self.setFixedWidth(anchor.width())
        self.adjustSize()
        self.move(pos.x(), pos.y() - self.height() - 4)
        self.show()
        self.raise_()


# ═══════════════════════════════════════════
#  SectionHeader
# ═══════════════════════════════════════════

class SectionHeader(QWidget):
    def __init__(self, title: str, right_widget: QWidget = None,
                 parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel(title)
        lbl.setFont(T.font(16, bold=True))
        lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY};")
        layout.addWidget(lbl)
        layout.addStretch()
        if right_widget:
            layout.addWidget(right_widget)


# ═══════════════════════════════════════════
#  CommandHistoryCard
# ═══════════════════════════════════════════

_TRANSPARENT = "background: transparent; border: none;"


class CommandHistoryCard(QFrame):
    def __init__(self, text: str, success: bool, source: str,
                 timestamp: float, parent=None):
        super().__init__(parent)
        self.setFixedHeight(44)

        accent = T.ACCENT_SUCCESS if success else T.ACCENT_DANGER
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_CARD};
                border-left: 3px solid {accent};
                border-radius: {T.RADIUS_SM}px;
                border-right: none; border-top: none; border-bottom: none;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(10)

        icon_name = "fa6s.microphone" if source == "voice" else "fa6s.keyboard"
        icon_color = T.ACCENT_SECONDARY if source == "voice" else T.ACCENT_PRIMARY
        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_icon(icon_name, icon_color).pixmap(14, 14))
        icon_lbl.setFixedWidth(18)
        icon_lbl.setStyleSheet(_TRANSPARENT)
        layout.addWidget(icon_lbl)

        text_lbl = QLabel(text)
        text_lbl.setFont(T.font(12))
        text_lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_TRANSPARENT}")
        layout.addWidget(text_lbl, 1)

        try:
            time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M")
        except Exception:
            time_str = "--:--"
        time_lbl = QLabel(time_str)
        time_lbl.setFont(T.font(11))
        time_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; {_TRANSPARENT}")
        layout.addWidget(time_lbl)

        res_name = "fa6s.circle-check" if success else "fa6s.circle-xmark"
        res_lbl = QLabel()
        res_lbl.setPixmap(get_icon(res_name, accent).pixmap(14, 14))
        res_lbl.setFixedWidth(18)
        res_lbl.setStyleSheet(_TRANSPARENT)
        layout.addWidget(res_lbl)


# ═══════════════════════════════════════════
#  ResultBar (с fade-анимацией)
# ═══════════════════════════════════════════

class ResultBar(QFrame):
    """
    Полоса обратной связи с анимированным появлением/скрытием.
    """

    SHOW_DURATION = 3500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(8)

        self._icon = QLabel()
        self._icon.setFixedWidth(18)
        self._icon.setStyleSheet(_TRANSPARENT)
        layout.addWidget(self._icon)

        self._label = QLabel()
        self._label.setFont(T.font(12))
        self._label.setStyleSheet(_TRANSPARENT)
        layout.addWidget(self._label)
        layout.addStretch()

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.setInterval(self.SHOW_DURATION)
        self._dismiss_timer.timeout.connect(self._fade_out)

    def show_result(self, text: str, success: bool = True):
        self._dismiss_timer.stop()

        color = T.ACCENT_SUCCESS if success else T.ACCENT_DANGER
        icon_name = "fa6s.circle-check" if success else "fa6s.circle-xmark"

        self._icon.setPixmap(get_icon(icon_name, color).pixmap(14, 14))
        self._label.setText(text)
        self._label.setStyleSheet(f"color: {color}; {_TRANSPARENT}")

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_CARD};
                border-top: 1px solid {T.BORDER};
            }}
        """)

        # Fade in
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)
        self.show()

        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: self.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeleteWhenStopped)

        self._dismiss_timer.start()

    def _fade_out(self):
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(1.0)
        self.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(300)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(lambda: (self.hide(), self.setGraphicsEffect(None)))
        anim.start(QPropertyAnimation.DeleteWhenStopped)