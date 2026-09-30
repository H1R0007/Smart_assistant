# core/gui/pages/base_page.py

"""Базовый класс для всех страниц."""

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal

from core.gui.theme import Theme, get_icon
from core.gui.components import Card

T = Theme


class BasePage(QWidget):
    """Все страницы наследуют BasePage."""

    title: str = "Page"
    icon: str = "fa6s.circle"

    # Страница может попросить переключиться на другую
    navigate_requested = Signal(int)

    def __init__(self, ipc=None, parent=None):
        super().__init__(parent)
        self.ipc = ipc
        self.setStyleSheet(f"background-color: {T.BG_DARK};")

        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(24, 20, 24, 20)
        self.page_layout.setSpacing(16)

        self.setup_ui()

    def setup_ui(self):
        pass

    def make_placeholder(self, description: str):
        card = Card(accent_color=T.ACCENT_PRIMARY)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(
            get_icon(self.icon, T.ACCENT_PRIMARY).pixmap(48, 48)
        )
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        card.add_widget(icon_lbl)

        title_lbl = QLabel(self.title)
        title_lbl.setFont(T.font(18, bold=True))
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; background: transparent; border: none;"
        )
        card.add_widget(title_lbl)

        desc = QLabel(description)
        desc.setFont(T.font(13))
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; background: transparent; border: none;"
        )
        card.add_widget(desc)

        self.page_layout.addStretch()
        self.page_layout.addWidget(card)
        self.page_layout.addStretch()