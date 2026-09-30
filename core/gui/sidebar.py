# core/gui/sidebar.py

"""
Боковая навигационная панель.
Сворачивается до иконок, разворачивается с анимацией.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QFrame
)
from PySide6.QtCore import Qt, Signal, QVariantAnimation, QEasingCurve, QSize
from PySide6.QtGui import QFont

from core.gui.theme import Theme, get_icon

T = Theme


class SidebarButton(QPushButton):
    """Кнопка навигации: иконка + текст."""

    def __init__(self, icon_name: str, text: str, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.full_text = text
        self._active = False

        self.setText(text)
        self.setIcon(get_icon(icon_name, T.TEXT_SECONDARY))
        self.setIconSize(T.ICON_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(T.SIDEBAR_BTN_H)
        self.setFont(T.font(13))
        self._apply_style()

    def set_active(self, active: bool):
        self._active = active
        color = T.ACCENT_PRIMARY if active else T.TEXT_SECONDARY
        self.setIcon(get_icon(self.icon_name, color))
        self._apply_style()

    def show_text(self, show: bool):
        self.setText(self.full_text if show else "")
        self.setToolTip("" if show else self.full_text)

    def update_icon(self, icon_name: str):
        self.icon_name = icon_name
        color = T.ACCENT_PRIMARY if self._active else T.TEXT_SECONDARY
        self.setIcon(get_icon(icon_name, color))

    def _apply_style(self):
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    text-align: left;
                    padding: 0 16px;
                    border: none;
                    border-left: 3px solid {T.ACCENT_PRIMARY};
                    background-color: rgba(124, 111, 247, 0.08);
                    color: {T.TEXT_PRIMARY};
                    font-weight: 600;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    text-align: left;
                    padding: 0 16px;
                    border: none;
                    border-left: 3px solid transparent;
                    background-color: transparent;
                    color: {T.TEXT_SECONDARY};
                }}
                QPushButton:hover {{
                    background-color: {T.BG_HOVER};
                    color: {T.TEXT_PRIMARY};
                }}
            """)


class Sidebar(QWidget):
    """
    Сворачиваемая боковая панель.

    pages_config: list[dict] — каждый элемент:
        {"title": "Dashboard", "icon": "fa6s.house"}
    """
    page_changed = Signal(int)

    def __init__(self, pages_config: list[dict], parent=None):
        super().__init__(parent)
        self._expanded = True
        self._buttons: list[SidebarButton] = []
        self._active = 0

        self.setFixedWidth(T.SIDEBAR_EXPANDED)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {T.BG_DARKEST};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── Логотип ──
        self._logo = QLabel("ПЯТНИЦА")
        self._logo.setAlignment(Qt.AlignCenter)
        self._logo.setFont(T.font(15, bold=True))
        self._logo.setStyleSheet(f"""
            color: {T.ACCENT_PRIMARY};
            padding: 18px 8px 14px 8px;
            background: transparent;
        """)
        layout.addWidget(self._logo)

        # ── Разделитель ──
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {T.BORDER};")
        layout.addWidget(sep)

        layout.addSpacing(8)

        # ── Навигация ──
        for i, page in enumerate(pages_config):
            btn = SidebarButton(page["icon"], page["title"])
            btn.clicked.connect(lambda _=False, idx=i: self._on_click(idx))
            self._buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch()

        # ── Разделитель ──
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet(f"background-color: {T.BORDER};")
        layout.addWidget(sep2)

        # ── Кнопка сворачивания ──
        self._toggle = SidebarButton("fa6s.angles-left", "Свернуть")
        self._toggle.clicked.connect(self.toggle)
        layout.addWidget(self._toggle)

        layout.addSpacing(4)

        # Выделяем первую
        if self._buttons:
            self._buttons[0].set_active(True)

        # ── Анимация ──
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.valueChanged.connect(self._on_anim_tick)

    # ── Публичное API ──

    def toggle(self):
        self._expanded = not self._expanded
        self._anim.setStartValue(self.width())
        target = T.SIDEBAR_EXPANDED if self._expanded else T.SIDEBAR_COLLAPSED
        self._anim.setEndValue(target)
        self._anim.start()

        icon = "fa6s.angles-left" if self._expanded else "fa6s.angles-right"
        text = "Свернуть" if self._expanded else ""
        self._toggle.update_icon(icon)
        self._toggle.full_text = "Свернуть"
        self._toggle.show_text(self._expanded)

    def set_active(self, index: int):
        self._active = index
        for i, btn in enumerate(self._buttons):
            btn.set_active(i == index)

    # ── Внутреннее ──

    def _on_click(self, index: int):
        if index == self._active:
            return
        self.set_active(index)
        self.page_changed.emit(index)

    def _on_anim_tick(self, value):
        w = int(value)
        self.setFixedWidth(w)
        show = w > 100
        self._logo.setText("ПЯТНИЦА" if show else "П")
        for btn in self._buttons:
            btn.show_text(show)
        self._toggle.show_text(show)