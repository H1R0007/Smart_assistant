# core/gui/pages/knowledge.py

"""
Knowledge Base — база знаний.
Поиск, фильтры, карточки, теги, добавление, редактирование, удаление.
"""

import time as _time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QDialog, QTextEdit,
    QComboBox, QLineEdit, QMessageBox, QPushButton,
    QMenu, QApplication,
)
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QColor

from core.gui.pages.base_page import BasePage
from core.gui.theme import Theme, get_icon
from core.gui.components import (
    SectionHeader, AccentButton, GhostButton, IconButton, SearchBar,
)

T = Theme
_NS = "background: transparent; border: none;"

TYPE_META = {
    "note":       ("Заметка",    "fa6s.note-sticky",            T.ACCENT_WARNING),
    "fact":       ("Факт",       "fa6s.lightbulb",              T.ACCENT_SUCCESS),
    "snippet":    ("Фрагмент",   "fa6s.code",                   T.ACCENT_PRIMARY),
    "bookmark":   ("Закладка",   "fa6s.bookmark",               T.ACCENT_SECONDARY),
    "thought":    ("Мысль",      "fa6s.brain",                  "#c07cf7"),
    "app_switch": ("Активность", "fa6s.arrow-right-arrow-left", T.TEXT_MUTED),
    "command":    ("Команда",    "fa6s.terminal",               T.ACCENT_PRIMARY),
    "search":     ("Поиск",      "fa6s.magnifying-glass",       T.ACCENT_SECONDARY),
    "clipboard":  ("Буфер",      "fa6s.clipboard",              T.ACCENT_SUCCESS),
}

SOURCE_META = {
    "voice":     ("Голос",      "fa6s.microphone", T.ACCENT_SECONDARY),
    "gui":       ("Интерфейс",  "fa6s.keyboard",   T.ACCENT_PRIMARY),
    "clipboard": ("Буфер",      "fa6s.clipboard",  T.ACCENT_SUCCESS),
    "tracker":   ("Трекер",     "fa6s.robot",       T.ACCENT_WARNING),
    "import":    ("Импорт",     "fa6s.file-import", T.ACCENT_PRIMARY),
}

TYPE_FILTERS = [
    (None,       "Все",        "fa6s.layer-group", T.TEXT_SECONDARY),
    ("note",     "Заметки",    "fa6s.note-sticky", T.ACCENT_WARNING),
    ("fact",     "Факты",      "fa6s.lightbulb",   T.ACCENT_SUCCESS),
    ("snippet",  "Фрагменты",  "fa6s.code",        T.ACCENT_PRIMARY),
    ("thought",  "Мысли",      "fa6s.brain",       "#c07cf7"),
]

SOURCE_FILTERS = [
    (None,        "Все",         "fa6s.layer-group", T.TEXT_SECONDARY),
    ("user",      "Мои записи",  "fa6s.user",        T.ACCENT_PRIMARY),
    ("clipboard", "Буфер",       "fa6s.clipboard",   T.ACCENT_SUCCESS),
    ("tracker",   "Трекер",      "fa6s.robot",        T.ACCENT_WARNING),
]

IMPORTANCE_COLORS = {0: None, 1: T.ACCENT_WARNING, 2: T.ACCENT_DANGER}


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def _decline_entries(n: int) -> str:
    last_two = abs(n) % 100
    last_one = abs(n) % 10
    if 11 <= last_two <= 19:
        w = "записей"
    elif last_one == 1:
        w = "запись"
    elif 2 <= last_one <= 4:
        w = "записи"
    else:
        w = "записей"
    return f"{n} {w}"


_FIELD_STYLE = f"""
    QLineEdit, QTextEdit {{
        background-color: {T.BG_INPUT};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM}px;
        padding: 6px 10px;
    }}
    QLineEdit:focus, QTextEdit:focus {{
        border-color: {T.BORDER_FOCUS};
    }}
"""

_COMBO_STYLE = f"""
    QComboBox {{
        background-color: {T.BG_INPUT};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM}px;
        padding: 4px 8px;
        min-width: 110px;
    }}
    QComboBox:focus {{ border-color: {T.BORDER_FOCUS}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background-color: {T.BG_CARD};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        selection-background-color: {T.BG_HOVER};
    }}
"""


# ═══════════════════════════════════════════
#  FilterChip / FilterRow (без изменений)
# ═══════════════════════════════════════════

class FilterChip(QPushButton):
    def __init__(self, label, value, icon_name, color, parent=None):
        super().__init__(f" {label}", parent)
        self._value = value
        self._color = color
        self._icon_name = icon_name
        self._active = False
        self.setIcon(get_icon(icon_name, T.TEXT_MUTED))
        self.setIconSize(QSize(12, 12))
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(T.font(11))
        self.setFixedHeight(28)
        self._update_style()

    @property
    def value(self):
        return self._value

    def set_active(self, active):
        self._active = active
        self.setIcon(get_icon(self._icon_name, self._color if active else T.TEXT_MUTED))
        self._update_style()

    def _update_style(self):
        if self._active:
            c = QColor(self._color)
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba({c.red()},{c.green()},{c.blue()},0.12);
                    color: {self._color}; border: 1px solid rgba({c.red()},{c.green()},{c.blue()},0.30);
                    border-radius: 14px; padding: 0 10px; font-weight: 600;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {T.TEXT_MUTED};
                    border: 1px solid {T.BORDER}; border-radius: 14px; padding: 0 10px;
                }}
                QPushButton:hover {{
                    background-color: {T.BG_HOVER}; color: {T.TEXT_SECONDARY};
                }}
            """)


class FilterRow(QWidget):
    changed = Signal(object)

    def __init__(self, label, options, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_NS)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        lbl = QLabel(label)
        lbl.setFont(T.font(11))
        lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
        lbl.setFixedWidth(70)
        layout.addWidget(lbl)
        self._chips = []
        self._selected = None
        for value, display, icon_name, color in options:
            chip = FilterChip(display, value, icon_name, color)
            chip.clicked.connect(lambda _, v=value: self._select(v))
            self._chips.append(chip)
            layout.addWidget(chip)
        layout.addStretch()
        if self._chips:
            self._chips[0].set_active(True)

    @property
    def value(self):
        return self._selected

    def _select(self, value):
        if value == self._selected:
            return
        self._selected = value
        for chip in self._chips:
            chip.set_active(chip.value == value)
        self.changed.emit(value)


# ═══════════════════════════════════════════
#  EntryDialog (add + edit)
# ═══════════════════════════════════════════

class EntryDialog(QDialog):
    """Добавление или редактирование записи."""

    def __init__(self, parent=None, data: dict = None):
        super().__init__(parent)
        self._editing = data is not None
        self.setWindowTitle("Редактировать запись" if self._editing else "Добавить запись")
        self.setMinimumWidth(480)
        self.setStyleSheet(f"QDialog {{ background-color: {T.BG_DARK}; color: {T.TEXT_PRIMARY}; }}")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("Редактирование" if self._editing else "Новая запись")
        title.setFont(T.font(16, bold=True))
        layout.addWidget(title)

        lbl_c = QLabel("Содержание:")
        lbl_c.setFont(T.font(12))
        lbl_c.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        layout.addWidget(lbl_c)

        self.content_edit = QTextEdit()
        self.content_edit.setPlaceholderText("Что запомнить?")
        self.content_edit.setMinimumHeight(100)
        self.content_edit.setMaximumHeight(200)
        self.content_edit.setFont(T.font(13))
        self.content_edit.setStyleSheet(_FIELD_STYLE)
        layout.addWidget(self.content_edit)

        row1 = QHBoxLayout()
        row1.setSpacing(16)
        lbl_t = QLabel("Тип:")
        lbl_t.setFont(T.font(12))
        lbl_t.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        row1.addWidget(lbl_t)
        self.type_combo = QComboBox()
        self.type_combo.setFont(T.font(12))
        self.type_combo.setStyleSheet(_COMBO_STYLE)
        for key in ("note", "fact", "snippet", "bookmark", "thought"):
            label, _, _ = TYPE_META[key]
            self.type_combo.addItem(label, key)
        row1.addWidget(self.type_combo)
        row1.addStretch()
        lbl_i = QLabel("Важность:")
        lbl_i.setFont(T.font(12))
        lbl_i.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        row1.addWidget(lbl_i)
        self.imp_combo = QComboBox()
        self.imp_combo.setFont(T.font(12))
        self.imp_combo.setStyleSheet(_COMBO_STYLE)
        self.imp_combo.addItem("Обычная", 0)
        self.imp_combo.addItem("⚠ Важная", 1)
        self.imp_combo.addItem("🔴 Критичная", 2)
        row1.addWidget(self.imp_combo)
        layout.addLayout(row1)

        lbl_tg = QLabel("Теги (через запятую):")
        lbl_tg.setFont(T.font(12))
        lbl_tg.setStyleSheet(f"color: {T.TEXT_SECONDARY};")
        layout.addWidget(lbl_tg)
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("работа, python, идея")
        self.tags_edit.setFont(T.font(12))
        self.tags_edit.setFixedHeight(34)
        self.tags_edit.setStyleSheet(_FIELD_STYLE)
        layout.addWidget(self.tags_edit)

        layout.addSpacing(8)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = GhostButton("Отмена", "fa6s.xmark")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = AccentButton("Сохранить", "fa6s.floppy-disk")
        save.clicked.connect(self._try_accept)
        btns.addWidget(save)
        layout.addLayout(btns)

        # Pre-fill for editing
        if data:
            self.content_edit.setPlainText(data.get("content", ""))
            idx = self.type_combo.findData(data.get("entry_type", "note"))
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)
            imp_idx = self.imp_combo.findData(data.get("importance", 0))
            if imp_idx >= 0:
                self.imp_combo.setCurrentIndex(imp_idx)
            self.tags_edit.setText(data.get("tags", ""))

        QTimer.singleShot(100, self.content_edit.setFocus)

    def _try_accept(self):
        if not self.content_edit.toPlainText().strip():
            self.content_edit.setStyleSheet(_FIELD_STYLE.replace(T.BORDER, T.ACCENT_DANGER))
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "content": self.content_edit.toPlainText().strip(),
            "entry_type": self.type_combo.currentData(),
            "tags": self.tags_edit.text().strip(),
            "importance": self.imp_combo.currentData(),
        }


# ═══════════════════════════════════════════
#  KnowledgeCard
# ═══════════════════════════════════════════

class KnowledgeCard(QFrame):
    delete_requested = Signal(int)
    edit_requested = Signal(dict)       # полные данные записи
    tag_clicked = Signal(str)           # тег для фильтрации

    MAX_CONTENT = 300

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self._data = data
        self._id = data.get("id", 0)
        self._content = data.get("content", "")

        entry_type = data.get("entry_type", "note")
        source = data.get("source", "voice")
        tags = data.get("tags", "")
        importance = data.get("importance", 0)
        age = data.get("age", "")
        score = data.get("score")

        type_label, type_icon, type_color = TYPE_META.get(
            entry_type, ("Другое", "fa6s.circle", T.TEXT_MUTED))
        accent = IMPORTANCE_COLORS.get(importance) or type_color

        self.setObjectName("kcard")
        self.setStyleSheet(f"""
            QFrame#kcard {{
                background-color: {T.BG_CARD};
                border: 1px solid {T.BORDER};
                border-left: 3px solid {accent};
                border-radius: {T.RADIUS_MD}px;
            }}
            QFrame#kcard:hover {{ border-color: {T.ACCENT_PRIMARY}; border-left: 3px solid {accent}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(6)

        badge = QLabel(f"  {type_label}  ")
        badge.setFont(T.font(10))
        badge.setFixedHeight(20)
        badge.setStyleSheet(f"background:{_hex_to_rgba(type_color,0.12)};color:{type_color};border-radius:10px;padding:0 6px;border:none;")
        hdr.addWidget(badge)

        src_label, src_icon, src_color = SOURCE_META.get(source, ("?", "fa6s.circle", T.TEXT_MUTED))
        src_lbl = QLabel()
        src_lbl.setPixmap(get_icon(src_icon, src_color).pixmap(12, 12))
        src_lbl.setToolTip(src_label)
        src_lbl.setFixedWidth(16)
        src_lbl.setStyleSheet(_NS)
        hdr.addWidget(src_lbl)

        if importance == 1:
            imp = QLabel("⚠")
            imp.setToolTip("Важная")
            imp.setStyleSheet(f"color:{T.ACCENT_WARNING};{_NS}")
            hdr.addWidget(imp)
        elif importance == 2:
            imp = QLabel("🔴")
            imp.setToolTip("Критичная")
            imp.setStyleSheet(f"color:{T.ACCENT_DANGER};{_NS}")
            hdr.addWidget(imp)

        hdr.addStretch()

        if score is not None:
            sc = QLabel(f"{score:.0%}")
            sc.setFont(T.font(10))
            sc.setStyleSheet(f"color:{T.TEXT_MUTED};{_NS}")
            hdr.addWidget(sc)

        age_lbl = QLabel(age)
        age_lbl.setFont(T.font(10))
        age_lbl.setStyleSheet(f"color:{T.TEXT_MUTED};{_NS}")
        hdr.addWidget(age_lbl)

        # Edit button
        edit_btn = IconButton("fa6s.pen", "Редактировать", size=26, color=T.ACCENT_PRIMARY)
        edit_btn.clicked.connect(lambda: self.edit_requested.emit(self._data))
        hdr.addWidget(edit_btn)

        del_btn = IconButton("fa6s.trash", "Удалить", size=26, color=T.ACCENT_DANGER)
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._id))
        hdr.addWidget(del_btn)

        layout.addLayout(hdr)

        # Content
        preview = self._content
        if len(preview) > self.MAX_CONTENT:
            preview = preview[:self.MAX_CONTENT] + "…"
        body = QLabel(preview)
        body.setFont(T.font(12))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet(f"color:{T.TEXT_PRIMARY};{_NS}")
        layout.addWidget(body)

        # Tags (clickable)
        if tags and tags.strip():
            tags_row = QHBoxLayout()
            tags_row.setSpacing(4)
            for tag in tags.split(","):
                tag = tag.strip()
                if not tag:
                    continue
                chip = QPushButton(f" {tag} ")
                chip.setFont(T.font(10))
                chip.setCursor(Qt.PointingHandCursor)
                chip.setFixedHeight(22)
                chip.setStyleSheet(f"""
                    QPushButton {{
                        background:{_hex_to_rgba(T.ACCENT_PRIMARY,0.10)};
                        color:{T.ACCENT_PRIMARY};border-radius:8px;
                        padding:0 6px;border:none;
                    }}
                    QPushButton:hover {{
                        background:{_hex_to_rgba(T.ACCENT_PRIMARY,0.20)};
                    }}
                """)
                chip.clicked.connect(lambda _, t=tag: self.tag_clicked.emit(t))
                tags_row.addWidget(chip)
            tags_row.addStretch()
            layout.addLayout(tags_row)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background-color:{T.BG_CARD};color:{T.TEXT_PRIMARY};border:1px solid {T.BORDER};border-radius:{T.RADIUS_SM}px;padding:4px; }}
            QMenu::item {{ padding:6px 20px;border-radius:4px; }}
            QMenu::item:selected {{ background-color:{T.BG_HOVER}; }}
        """)
        copy_act = menu.addAction(get_icon("fa6s.copy", T.TEXT_SECONDARY), "Копировать")
        edit_act = menu.addAction(get_icon("fa6s.pen", T.ACCENT_PRIMARY), "Редактировать")
        action = menu.exec(event.globalPos())
        if action == copy_act:
            QApplication.clipboard().setText(self._content)
        elif action == edit_act:
            self.edit_requested.emit(self._data)


# ═══════════════════════════════════════════
#  KnowledgePage
# ═══════════════════════════════════════════

class KnowledgePage(BasePage):
    title = "База знаний"
    icon = "fa6s.brain"
    MAX_RESULTS = 50

    def setup_ui(self):
        self._total = 0
        self._stats_label = QLabel("…")
        self._stats_label.setFont(T.font(12))
        self._stats_label.setStyleSheet(f"color:{T.TEXT_MUTED};")

        add_btn = AccentButton("Добавить", "fa6s.plus")
        add_btn.clicked.connect(self._on_add)

        hdr_right = QWidget()
        hdr_right.setStyleSheet(_NS)
        hr = QHBoxLayout(hdr_right)
        hr.setContentsMargins(0, 0, 0, 0)
        hr.setSpacing(12)
        hr.addWidget(self._stats_label)
        hr.addWidget(add_btn)

        self.page_layout.addWidget(SectionHeader("База знаний", hdr_right))

        self._search_bar = SearchBar("Поиск по базе знаний...")
        self._search_bar.search_requested.connect(lambda _: self._refresh())
        self.page_layout.addWidget(self._search_bar)

        self._type_filter = FilterRow("Тип:", TYPE_FILTERS)
        self._type_filter.changed.connect(lambda _: self._refresh())
        self.page_layout.addWidget(self._type_filter)

        self._source_filter = FilterRow("Источник:", SOURCE_FILTERS)
        self._source_filter.changed.connect(lambda _: self._refresh())
        self.page_layout.addWidget(self._source_filter)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._list_container = QWidget()
        self._list_container.setStyleSheet("background:transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setAlignment(Qt.AlignTop)
        self._list_layout.setSpacing(8)
        self._list_layout.setContentsMargins(0, 0, 4, 0)

        self._state_label = QLabel("Подключение к ядру...")
        self._state_label.setFont(T.font(13))
        self._state_label.setAlignment(Qt.AlignCenter)
        self._state_label.setStyleSheet(f"color:{T.TEXT_MUTED};padding:60px;{_NS}")
        self._list_layout.addWidget(self._state_label)
        scroll.setWidget(self._list_container)
        self.page_layout.addWidget(scroll, 1)

        if self.ipc:
            self.ipc.connected.connect(self._refresh)
            QTimer.singleShot(300, self._try_initial_load)

    def _try_initial_load(self):
        if self.ipc and self.ipc.is_connected:
            self._refresh()

    def _refresh(self):
        if not self.ipc or not self.ipc.is_connected:
            return
        payload = {"query": self._search_bar.text, "limit": self.MAX_RESULTS}
        tv = self._type_filter.value
        if tv:
            payload["entry_type"] = tv
        sv = self._source_filter.value
        if sv:
            payload["source"] = sv
        self._show_state("Загрузка...")
        self.ipc.request("search_knowledge", payload, callback=self._on_results)

    def _on_results(self, payload):
        if not payload.get("success"):
            self._show_state(f"Ошибка: {payload.get('error', '?')}")
            return
        results = payload.get("results", [])
        total = payload.get("total", 0)
        self._total = total
        self._stats_label.setText(_decline_entries(total))
        self._render_cards(results)

    def _render_cards(self, results):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not results:
            q = self._search_bar.text
            msg = f'Ничего не найдено по «{q}»' if q else (
                "База пуста.\nДобавьте запись кнопкой «Добавить»"
            )
            self._show_state(msg)
            return

        cards = []
        for data in results:
            card = KnowledgeCard(data)
            card.delete_requested.connect(self._on_delete)
            card.edit_requested.connect(self._on_edit)
            card.tag_clicked.connect(self._on_tag_click)
            self._list_layout.addWidget(card)
            cards.append(card)

        from core.gui.animations import staggered_fade_in
        staggered_fade_in(cards)

    def _show_state(self, text):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._state_label = QLabel(text)
        self._state_label.setFont(T.font(13))
        self._state_label.setAlignment(Qt.AlignCenter)
        self._state_label.setWordWrap(True)
        self._state_label.setStyleSheet(f"color:{T.TEXT_MUTED};padding:60px;{_NS}")
        self._list_layout.addWidget(self._state_label)

    # ── Add ──
    def _on_add(self):
        dlg = EntryDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        if not data["content"]:
            return
        if not self.ipc or not self.ipc.is_connected:
            return
        self.ipc.request("add_knowledge_entry", data, callback=self._on_added)

    def _on_added(self, payload):
        if payload.get("success"):
            self._refresh()

    # ── Edit ──
    def _on_edit(self, data: dict):
        dlg = EntryDialog(self, data=data)
        if dlg.exec() != QDialog.Accepted:
            return
        new_data = dlg.get_data()
        if not self.ipc or not self.ipc.is_connected:
            return
        self.ipc.request("edit_knowledge_entry", {
            "entry_id": data.get("id"),
            "content": new_data["content"],
            "tags": new_data["tags"],
            "importance": new_data["importance"],
        }, callback=self._on_edited)

    def _on_edited(self, payload):
        if payload.get("success"):
            self._refresh()

    # ── Delete ──
    def _on_delete(self, entry_id):
        reply = QMessageBox.question(
            self, "Удаление", "Удалить эту запись?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if not self.ipc or not self.ipc.is_connected:
            return
        self.ipc.request("delete_knowledge_entry", {"entry_id": entry_id},
                         callback=lambda p: self._refresh() if p.get("success") else None)

    # ── Tag click → search ──
    def _on_tag_click(self, tag: str):
        self._search_bar.input.setText(tag)
        self._refresh()