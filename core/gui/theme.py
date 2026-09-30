# core/gui/theme.py

"""
Единый источник визуальных констант и стилей.
Все цвета, шрифты, размеры — только здесь.
"""

from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtCore import QSize


class Theme:
    """Палитра, шрифты, радиусы."""

    # ── Фоны (3 уровня глубины) ──
    BG_DARKEST = "#0f0f1a"
    BG_DARK = "#161626"
    BG_CARD = "#1e1e32"
    BG_INPUT = "#252540"
    BG_HOVER = "#2a2a45"

    # ── Акценты ──
    ACCENT_PRIMARY = "#7c6ff7"
    ACCENT_SECONDARY = "#5b8cf7"
    ACCENT_SUCCESS = "#4ecdc4"
    ACCENT_WARNING = "#f7b955"
    ACCENT_DANGER = "#f76c6c"

    # ── Текст ──
    TEXT_PRIMARY = "#e4e4f0"
    TEXT_SECONDARY = "#8888a8"
    TEXT_MUTED = "#555570"

    # ── Границы ──
    BORDER = "#2a2a45"
    BORDER_FOCUS = "#7c6ff7"

    # ── Шрифты ──
    FONT_FAMILY = "Segoe UI"
    FONT_MONO = "Cascadia Code"

    # ── Скругления ──
    RADIUS_SM = 6
    RADIUS_MD = 10
    RADIUS_LG = 14

    # ── Размеры ──
    HEADER_H = 52
    COMMAND_INPUT_H = 52
    SIDEBAR_EXPANDED = 200
    SIDEBAR_COLLAPSED = 56
    SIDEBAR_BTN_H = 44
    ICON_SIZE = QSize(18, 18)

    @classmethod
    def font(cls, size: int = 13, bold: bool = False) -> QFont:
        f = QFont(cls.FONT_FAMILY, size)
        if bold:
            f.setWeight(QFont.DemiBold)
        return f

    @classmethod
    def mono_font(cls, size: int = 11) -> QFont:
        f = QFont(cls.FONT_MONO, size)
        f.setStyleHint(QFont.Monospace)
        return f


def get_icon(name: str, color: str = None) -> QIcon:
    """Безопасное создание иконки через qtawesome."""
    try:
        import qtawesome as qta
        return qta.icon(name, color=color or Theme.TEXT_PRIMARY)
    except Exception:
        return QIcon()


def apply_theme(app) -> None:
    """Применяет базовую тему ко всему QApplication."""
    T = Theme
    app.setFont(T.font())
    app.setStyleSheet(f"""
        * {{
            font-family: '{T.FONT_FAMILY}';
        }}
        QMainWindow {{
            background-color: {T.BG_DARK};
        }}
        QWidget {{
            color: {T.TEXT_PRIMARY};
        }}

        /* ── Скроллбары ── */
        QScrollBar:vertical {{
            background: {T.BG_DARKEST};
            width: 8px;
            border-radius: 4px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {T.BORDER};
            border-radius: 4px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {T.TEXT_MUTED};
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical,
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            height: 0; background: none; border: none;
        }}
        QScrollBar:horizontal {{
            background: {T.BG_DARKEST};
            height: 8px;
            border-radius: 4px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: {T.BORDER};
            border-radius: 4px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {T.TEXT_MUTED};
        }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal,
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {{
            width: 0; background: none; border: none;
        }}

        /* ── Тултипы ── */
        QToolTip {{
            background-color: {T.BG_CARD};
            color: {T.TEXT_PRIMARY};
            border: 1px solid {T.BORDER};
            padding: 6px 10px;
            border-radius: {T.RADIUS_SM}px;
            font-size: 12px;
        }}
    """)