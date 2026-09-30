# core/gui/command_palette.py

"""
Command Palette — поиск и запуск команд.
Оверлей поверх основного контента.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea, QFrame,
    QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, Signal, QTimer, QPropertyAnimation, QEasingCurve, QEvent,
)
from PySide6.QtGui import QColor, QPainter, QKeyEvent

from core.gui.theme import Theme, get_icon

T = Theme
_NS = "background: transparent; border: none;"


# ═══════════════════════════════════════════
#  Авто-описания и категории
# ═══════════════════════════════════════════

_AUTO_CATEGORIES = {
    "open_app": "Приложения", "close_app": "Приложения",
    "web_search": "Интернет", "open_site": "Интернет",
    "time": "Информация", "date": "Информация",
    "weather": "Информация", "system_info": "Информация",
    "battery": "Информация", "ip_address": "Информация",
    "volume_up": "Медиа", "volume_down": "Медиа",
    "set_volume": "Медиа", "current_volume": "Медиа",
    "mute": "Медиа", "unmute": "Медиа",
    "music_control": "Медиа",
    "brightness_up": "Экран", "brightness_down": "Экран",
    "set_brightness": "Экран", "current_brightness": "Экран",
    "timer": "Утилиты", "cancel_timer": "Утилиты",
    "list_timers": "Утилиты", "screenshot": "Утилиты",
    "clipboard": "Утилиты", "clear_clipboard": "Утилиты",
    "copy_text": "Утилиты", "type_text": "Утилиты",
    "transform_clipboard": "Утилиты",
    "math": "Утилиты", "translate": "Утилиты",
    "reminder": "Утилиты", "cancel_reminder": "Утилиты",
    "list_reminders": "Утилиты",
    "kb_remember": "База знаний", "kb_search": "База знаний",
    "kb_ask": "База знаний", "kb_stats": "База знаний",
    "kb_forget": "База знаний", "kb_backup": "База знаний",
    "kb_context": "База знаний",
    "kb_clipboard_tracking": "База знаний",
    "minimize_window": "Окна", "maximize_window": "Окна",
    "close_window": "Окна", "switch_window": "Окна",
    "snap_window": "Окна", "list_windows": "Окна",
    "lock": "Система", "shutdown": "Система",
    "restart": "Система", "sleep": "Система",
    "cancel_shutdown": "Система", "empty_trash": "Система",
    "system_tool": "Система",
    "greeting": "Общие", "stop": "Общие", "help": "Общие",
    "thanks": "Общие", "how_are_you": "Общие",
    "who_are_you": "Общие", "repeat": "Общие",
    "speak_faster": "Настройки", "speak_slower": "Настройки",
    "speak_normal": "Настройки", "speak_louder": "Настройки",
    "speak_quieter": "Настройки",
    "clear_history": "Настройки", "current_settings": "Настройки",
    "explain_mode": "Настройки", "assistant_stats": "Настройки",
    "macro": "Макросы", "macro_work": "Макросы",
    "create_macro": "Макросы",
    "telegram": "Интеграции", "vscode": "Интеграции",
    "habits": "Аналитика", "activity_stats": "Аналитика",
    "open_folder": "Файлы", "folder_contents": "Файлы",
    "recent_files": "Файлы", "open_path": "Файлы",
    "clear_notes": "Файлы",
}

_CATEGORY_ICONS = {
    "Приложения": "fa6s.rocket",
    "Интернет": "fa6s.globe",
    "Информация": "fa6s.circle-info",
    "Медиа": "fa6s.volume-high",
    "Экран": "fa6s.sun",
    "Утилиты": "fa6s.screwdriver-wrench",
    "База знаний": "fa6s.brain",
    "Окна": "fa6s.window-restore",
    "Система": "fa6s.desktop",
    "Общие": "fa6s.comments",
    "Настройки": "fa6s.gear",
    "Макросы": "fa6s.wand-magic-sparkles",
    "Интеграции": "fa6s.plug",
    "Аналитика": "fa6s.chart-line",
    "Файлы": "fa6s.folder-open",
    "Другое": "fa6s.circle",
}

_CATEGORY_COLORS = {
    "Приложения": T.ACCENT_PRIMARY,
    "Интернет": T.ACCENT_SECONDARY,
    "Информация": T.ACCENT_SUCCESS,
    "Медиа": T.ACCENT_WARNING,
    "Экран": "#e8a050",
    "Утилиты": T.TEXT_SECONDARY,
    "База знаний": "#c07cf7",
    "Окна": T.ACCENT_PRIMARY,
    "Система": T.ACCENT_DANGER,
    "Общие": T.TEXT_SECONDARY,
    "Настройки": T.TEXT_SECONDARY,
    "Макросы": T.ACCENT_PRIMARY,
    "Интеграции": T.ACCENT_WARNING,
    "Аналитика": T.ACCENT_SUCCESS,
    "Файлы": T.ACCENT_SECONDARY,
    "Другое": T.TEXT_MUTED,
}


def _auto_description(name: str, keywords: list[str]) -> str:
    if not keywords:
        return name.replace("_", " ").title()
    kw = keywords[0]
    return kw[0].upper() + kw[1:]


# ═══════════════════════════════════════════
#  CommandItem — строка результата
# ═══════════════════════════════════════════

class _CommandItem(QFrame):
    clicked = Signal(str)

    def __init__(self, name: str, description: str, category: str,
                 keywords: list[str], parent=None):
        super().__init__(parent)
        self._keyword = keywords[0] if keywords else name
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(44)
        self._apply_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(10)

        # Иконка
        cat_icon = _CATEGORY_ICONS.get(category, "fa6s.circle")
        cat_color = _CATEGORY_COLORS.get(category, T.TEXT_MUTED)
        ic = QLabel()
        ic.setPixmap(get_icon(cat_icon, cat_color).pixmap(16, 16))
        ic.setFixedWidth(20)
        ic.setStyleSheet(_NS)
        layout.addWidget(ic)

        # Описание
        desc_lbl = QLabel(description)
        desc_lbl.setFont(T.font(13))
        desc_lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_NS}")
        layout.addWidget(desc_lbl, 1)

        # Ключевые слова (мелко, справа)
        if keywords:
            kw_text = keywords[0]
            if len(kw_text) > 25:
                kw_text = kw_text[:23] + "…"
            kw_lbl = QLabel(kw_text)
            kw_lbl.setFont(T.font(10))
            kw_lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
            layout.addWidget(kw_lbl)

        # Бейдж категории
        c = QColor(cat_color)
        badge = QLabel(category)
        badge.setFont(T.font(9))
        badge.setFixedHeight(18)
        badge.setStyleSheet(f"""
            background: rgba({c.red()},{c.green()},{c.blue()},0.12);
            color: {cat_color}; border-radius: 9px;
            padding: 0 8px; border: none;
        """)
        layout.addWidget(badge)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._apply_style()

    def _apply_style(self):
        if self._selected:
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: rgba(124,111,247,0.10);
                    border-left: 3px solid {T.ACCENT_PRIMARY};
                    border-radius: {T.RADIUS_SM}px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame {{
                    background: transparent;
                    border-left: 3px solid transparent;
                    border-radius: {T.RADIUS_SM}px;
                }}
                QFrame:hover {{
                    background-color: {T.BG_HOVER};
                }}
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._keyword)
        super().mousePressEvent(event)


# ═══════════════════════════════════════════
#  CommandPalette — оверлей
# ═══════════════════════════════════════════

class CommandPalette(QWidget):
    """
    Полноэкранный оверлей с поиском команд.
    Затемняет фон, клик вне панели = закрытие.
    """

    command_selected = Signal(str)

    MAX_VISIBLE = 10

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.hide()
        self.setFocusPolicy(Qt.StrongFocus)

        self._commands: list[dict] = []
        self._items: list[_CommandItem] = []
        self._selected_idx = 0

        # Панель поиска (центрированная, сверху)
        self._panel = QFrame(self)
        self._panel.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_CARD};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_LG}px;
            }}
        """)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(16, 16, 16, 12)
        panel_layout.setSpacing(8)

        # Заголовок
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        title = QLabel("Палитра команд")
        title.setFont(T.font(14, bold=True))
        title.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_NS}")
        hdr.addWidget(title)
        hdr.addStretch()

        esc_hint = QLabel("Esc — закрыть")
        esc_hint.setFont(T.font(10))
        esc_hint.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
        hdr.addWidget(esc_hint)

        panel_layout.addLayout(hdr)

        # Поле ввода
        self._input = QLineEdit()
        self._input.setPlaceholderText("Начните вводить команду...")
        self._input.setFont(T.font(14))
        self._input.setFixedHeight(44)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.BG_INPUT};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_MD}px;
                padding: 0 16px;
                font-size: 14px;
            }}
            QLineEdit:focus {{ border-color: {T.BORDER_FOCUS}; }}
        """)
        self._input.textChanged.connect(self._on_filter)
        self._input.returnPressed.connect(self._on_enter)
        self._input.installEventFilter(self)
        panel_layout.addWidget(self._input)

        # Подсказка навигации
        nav = QLabel("↑↓ навигация  ·  Enter выбор  ·  Esc закрыть")
        nav.setFont(T.font(10))
        nav.setAlignment(Qt.AlignCenter)
        nav.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
        panel_layout.addWidget(nav)

        # Счётчик результатов
        self._count = QLabel("")
        self._count.setFont(T.font(10))
        self._count.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
        panel_layout.addWidget(self._count)

        # Список результатов
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        self._scroll.setMaximumHeight(44 * self.MAX_VISIBLE + 20)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setAlignment(Qt.AlignTop)
        self._list_layout.setSpacing(2)
        self._list_layout.setContentsMargins(0, 0, 0, 0)

        self._scroll.setWidget(self._list_widget)
        panel_layout.addWidget(self._scroll)

    # ══════════════════════════════════════
    #  Geometry & paint
    # ══════════════════════════════════════

    def _reposition(self):
        """Разместить панель по центру-сверху родителя."""
        pw, ph = self.parent().width(), self.parent().height()
        self.setGeometry(0, 0, pw, ph)

        panel_w = min(640, pw - 80)
        panel_h = min(520, ph - 100)
        panel_x = (pw - panel_w) // 2
        panel_y = max(60, int(ph * 0.12))

        self._panel.setGeometry(panel_x, panel_y, panel_w, panel_h)

    def paintEvent(self, event):
        """Затемнение фона."""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 140))
        p.end()

    def mousePressEvent(self, event):
        """Клик вне панели = закрытие."""
        if not self._panel.geometry().contains(event.pos()):
            self.close_palette()
        super().mousePressEvent(event)

    # ══════════════════════════════════════
    #  Public API
    # ══════════════════════════════════════

    def set_commands(self, commands: list[dict]):
        self._commands = commands

    def open_palette(self):
        self._input.clear()
        self._reposition()
        self._render()
        self.show()
        self.raise_()
        self._input.setFocus()

    def close_palette(self):
        self.hide()

    # ══════════════════════════════════════
    #  Rendering
    # ══════════════════════════════════════

    def _render(self):
        self._clear_list()
        self._items.clear()
        self._selected_idx = 0

        query = self._input.text().strip().lower()
        filtered = [c for c in self._commands if self._matches(c, query)]

        self._count.setText(
            f"{len(filtered)} из {len(self._commands)}"
            if query else f"{len(self._commands)} команд"
        )

        for cmd in filtered[:self.MAX_VISIBLE]:
            item = _CommandItem(
                cmd["name"], cmd["description"],
                cmd["category"], cmd["keywords"],
            )
            item.clicked.connect(self._on_item_clicked)
            self._items.append(item)
            self._list_layout.addWidget(item)

        if not self._items and query:
            lbl = QLabel(f'Ничего не найдено по «{query}»')
            lbl.setFont(T.font(12))
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; padding: 20px; {_NS}")
            self._list_layout.addWidget(lbl)

        self._update_selection()

    @staticmethod
    def _matches(cmd: dict, query: str) -> bool:
        if not query:
            return True
        searchable = (
            cmd.get("name", "") + " " +
            cmd.get("description", "") + " " +
            cmd.get("category", "") + " " +
            " ".join(cmd.get("keywords", []))
        ).lower()
        # Все слова запроса должны присутствовать
        for word in query.split():
            if word not in searchable:
                return False
        return True

    def _clear_list(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _update_selection(self):
        for i, item in enumerate(self._items):
            item.set_selected(i == self._selected_idx)
        if self._items and 0 <= self._selected_idx < len(self._items):
            self._scroll.ensureWidgetVisible(
                self._items[self._selected_idx],
            )

    # ══════════════════════════════════════
    #  Events
    # ══════════════════════════════════════

    def _on_filter(self, _text):
        self._render()

    def _on_enter(self):
        text = self._input.text().strip()

        # Если есть выбранный элемент — его keyword
        if self._items and 0 <= self._selected_idx < len(self._items):
            kw = self._items[self._selected_idx]._keyword
            self.command_selected.emit(kw)
            self.close_palette()
            return

        # Иначе — прямой ввод
        if text:
            self.command_selected.emit(text)
            self.close_palette()

    def _on_item_clicked(self, keyword: str):
        self.command_selected.emit(keyword)
        self.close_palette()

    def eventFilter(self, obj, event):
        """Перехватываем ↑↓ и Esc в поле ввода."""
        if obj is self._input and isinstance(event, QKeyEvent):
            key = event.key()

            if key == Qt.Key_Escape:
                self.close_palette()
                return True

            if key == Qt.Key_Down:
                if self._items:
                    self._selected_idx = min(
                        self._selected_idx + 1,
                        len(self._items) - 1,
                    )
                    self._update_selection()
                return True

            if key == Qt.Key_Up:
                if self._items:
                    self._selected_idx = max(
                        self._selected_idx - 1, 0,
                    )
                    self._update_selection()
                return True

        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close_palette()
            return
        super().keyPressEvent(event)