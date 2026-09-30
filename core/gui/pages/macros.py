# core/gui/pages/macros.py

"""
Макросы — компактный список + визуальный редактор.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSplitter, QScrollArea, QFrame, QListWidget, QListWidgetItem,
    QLineEdit, QGraphicsView, QGraphicsScene, QMessageBox,
    QMenu, QSizePolicy, QApplication,
)
from PySide6.QtCore import Qt, Signal, QTimer, QMimeData, QPointF, QSize
from PySide6.QtGui import (
    QColor, QPen, QPainterPath, QPainter, QDrag, QFontMetrics,
)

from core.gui.pages.base_page import BasePage
from core.gui.theme import Theme, get_icon
from core.gui.components import (
    Card, SectionHeader, AccentButton, GhostButton, IconButton,
)
from core.gui.macro_nodes import MacroNode, ACTION_CONFIGS

T = Theme
_NS = "background: transparent; border: none;"


# ═══════════════════════════════════════════
#  Palette
# ═══════════════════════════════════════════

class _Palette(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setFixedWidth(150)
        self.setStyleSheet(f"""
            QListWidget {{
                background-color: {T.BG_DARKEST};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
            }}
            QListWidget::item {{
                padding: 7px 10px;
                border-bottom: 1px solid {T.BORDER};
            }}
            QListWidget::item:hover {{
                background-color: {T.BG_HOVER};
            }}
        """)
        for action_type, cfg in ACTION_CONFIGS.items():
            item = QListWidgetItem(cfg["title"])
            item.setData(Qt.UserRole, action_type)
            item.setForeground(QColor(T.TEXT_PRIMARY))
            self.addItem(item)

    def startDrag(self, actions):
        item = self.currentItem()
        if not item:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(item.data(Qt.UserRole))
        drag.setMimeData(mime)
        drag.exec(actions)


# ═══════════════════════════════════════════
#  Canvas
# ═══════════════════════════════════════════

class _Canvas(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(QColor(T.BG_DARKEST))
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setStyleSheet(f"""
            QGraphicsView {{
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
            }}
        """)
        self.nodes: list[MacroNode] = []

    def dragEnterEvent(self, e):
        if e.mimeData().hasText():
            e.accept()

    def dragMoveEvent(self, e):
        if e.mimeData().hasText():
            e.accept()

    def dropEvent(self, e):
        action = e.mimeData().text()
        if action in ACTION_CONFIGS:
            pos = self.mapToScene(e.pos())
            self.add_node(action, pos.x(), pos.y())
            e.accept()

    def add_node(self, action: str, x: float, y: float, step: dict = None):
        node = MacroNode(action)
        node.setPos(x, y)
        if step:
            node.set_data(step)
        node.moved.connect(self._redraw_lines)
        self.scene().addItem(node)
        self.nodes.append(node)
        self._redraw_lines()

    def clear_all(self):
        for item in list(self.scene().items()):
            self.scene().removeItem(item)
        self.nodes.clear()

    def sorted_nodes(self) -> list[MacroNode]:
        return sorted(self.nodes, key=lambda n: n.scenePos().y())

    def _redraw_lines(self):
        for item in list(self.scene().items()):
            if getattr(item, "_is_conn", False):
                self.scene().removeItem(item)
        pen = QPen(QColor(T.TEXT_MUTED), 2.5, Qt.SolidLine,
                   Qt.RoundCap, Qt.RoundJoin)
        ordered = self.sorted_nodes()
        for i in range(len(ordered) - 1):
            n1, n2 = ordered[i], ordered[i + 1]
            p1 = n1.scenePos() + QPointF(MacroNode.WIDTH / 2, MacroNode.HEIGHT)
            p2 = n2.scenePos() + QPointF(MacroNode.WIDTH / 2, 0)
            path = QPainterPath(p1)
            path.cubicTo(
                QPointF(p1.x(), p1.y() + 35),
                QPointF(p2.x(), p2.y() - 35), p2,
            )
            line = self.scene().addPath(path, pen)
            line._is_conn = True
            line.setZValue(-1)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            for item in self.scene().selectedItems():
                if isinstance(item, MacroNode) and item in self.nodes:
                    self.nodes.remove(item)
                    self.scene().removeItem(item)
            self._redraw_lines()
        super().keyPressEvent(e)


# ═══════════════════════════════════════════
#  MacroRow — однострочная, без inline-кнопок
# ═══════════════════════════════════════════

class _MacroRow(QFrame):
    """
    Одна строка в списке.

    Гарантия видимости: фиксированные элементы справа
    занимают ≤56px, остальное — имя с обрезкой.

    Клик → edit.  «⋮» → контекстное меню.
    """

    edit_requested = Signal(str)
    delete_requested = Signal(str)

    ROW_H = 40

    def __init__(self, macro_id: str, data: dict, parent=None):
        super().__init__(parent)
        self._id = macro_id
        self._name = data.get("name", macro_id)
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(self.ROW_H)
        self._apply_style()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(6)

        # ── Имя (растягивается, обрезается) ──
        self._name_label = QLabel()
        self._name_label.setFont(T.font(12, bold=True))
        self._name_label.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_NS}")
        self._name_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred,
        )
        self._name_label.setMinimumWidth(0)
        layout.addWidget(self._name_label)

        # ── Бейдж шагов (фиксированная ширина) ──
        steps_count = len(data.get("steps", []))
        badge = QLabel(str(steps_count))
        badge.setFont(T.font(9, bold=True))
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(22, 18)
        badge.setStyleSheet(f"""
            background: rgba(124, 111, 247, 0.15);
            color: {T.ACCENT_PRIMARY};
            border-radius: 9px;
            border: none;
        """)
        layout.addWidget(badge)

        # ── Кнопка «⋮» (фиксированная ширина) ──
        self._menu_btn = IconButton(
            "fa6s.ellipsis-vertical", "", size=26,
            color=T.TEXT_MUTED,
        )
        self._menu_btn.clicked.connect(self._show_menu)
        layout.addWidget(self._menu_btn)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._apply_style()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_name()

    def showEvent(self, event):
        super().showEvent(event)
        # Отложенная обрезка после layout
        QTimer.singleShot(0, self._elide_name)

    def _elide_name(self):
        """Обрезать имя с «…» по реальной ширине."""
        fm = QFontMetrics(self._name_label.font())
        avail = self._name_label.width()
        if avail <= 0:
            avail = 80  # fallback
        elided = fm.elidedText(self._name, Qt.ElideRight, avail)
        self._name_label.setText(elided)

    def _apply_style(self):
        if self._selected:
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: rgba(124, 111, 247, 0.08);
                    border: 1px solid {T.ACCENT_PRIMARY};
                    border-left: 3px solid {T.ACCENT_PRIMARY};
                    border-radius: {T.RADIUS_SM}px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: transparent;
                    border: 1px solid transparent;
                    border-left: 3px solid transparent;
                    border-radius: {T.RADIUS_SM}px;
                }}
                QFrame:hover {{
                    background-color: {T.BG_HOVER};
                    border: 1px solid {T.BORDER};
                    border-left: 3px solid {T.ACCENT_PRIMARY};
                }}
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.edit_requested.emit(self._id)
        super().mousePressEvent(event)

    def _show_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {T.BG_CARD};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 24px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {T.BG_HOVER};
            }}
        """)

        edit_act = menu.addAction(
            get_icon("fa6s.pen", T.ACCENT_PRIMARY), "Редактировать",
        )
        delete_act = menu.addAction(
            get_icon("fa6s.trash", T.ACCENT_DANGER), "Удалить",
        )

        action = menu.exec(self._menu_btn.mapToGlobal(
            self._menu_btn.rect().bottomRight()
        ))

        if action == edit_act:
            self.edit_requested.emit(self._id)
        elif action == delete_act:
            self.delete_requested.emit(self._id)

    def contextMenuEvent(self, event):
        """ПКМ по строке = то же меню."""
        self._show_menu()


# ═══════════════════════════════════════════
#  EditorPanel
# ═══════════════════════════════════════════

class _EditorPanel(QWidget):
    saved = Signal()
    cancelled = Signal()

    def __init__(self, ipc, parent=None):
        super().__init__(parent)
        self.ipc = ipc
        self._editing_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ── Fields ──
        fields = QVBoxLayout()
        fields.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.f_id = self._field("ID (латиница)")
        self.f_name = self._field("Название")
        row1.addWidget(QLabel("ID:"))
        row1.addWidget(self.f_id, 1)
        row1.addWidget(QLabel("Имя:"))
        row1.addWidget(self.f_name, 2)
        fields.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.f_kw = self._field("голосовые фразы через запятую")
        row2.addWidget(QLabel("Фразы:"))
        row2.addWidget(self.f_kw, 1)
        fields.addLayout(row2)

        layout.addLayout(fields)

        # ── Palette + Canvas ──
        mid = QSplitter(Qt.Horizontal)
        self.palette = _Palette()
        mid.addWidget(self.palette)

        self._scene = QGraphicsScene()
        self._scene.setSceneRect(0, 0, 2000, 2000)
        self.canvas = _Canvas(self._scene)
        mid.addWidget(self.canvas)
        mid.setSizes([150, 600])
        layout.addWidget(mid, 1)

        hint = QLabel(
            "Перетащите действия из палитры → на холст.  "
            "Порядок — сверху вниз.  Delete — удалить блок."
        )
        hint.setFont(T.font(10))
        hint.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
        layout.addWidget(hint)

        # ── Buttons ──
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = GhostButton("Отмена", "fa6s.xmark")
        cancel.clicked.connect(self.cancelled.emit)
        btns.addWidget(cancel)
        save = AccentButton("Сохранить", "fa6s.floppy-disk")
        save.clicked.connect(self._save)
        btns.addWidget(save)
        layout.addLayout(btns)

    @staticmethod
    def _field(placeholder: str) -> QLineEdit:
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        w.setFont(T.font(12))
        w.setFixedHeight(30)
        w.setStyleSheet(f"""
            QLineEdit {{
                background-color: {T.BG_INPUT};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
                padding: 0 8px;
            }}
            QLineEdit:focus {{ border-color: {T.BORDER_FOCUS}; }}
        """)
        return w

    def load_macro(self, macro_id: str, data: dict):
        self._editing_id = macro_id
        self.f_id.setText(macro_id)
        self.f_id.setReadOnly(True)
        self.f_name.setText(data.get("name", ""))
        kw = data.get("keywords", [])
        self.f_kw.setText(", ".join(kw) if isinstance(kw, list) else str(kw))
        self.canvas.clear_all()
        steps = data.get("steps", [])
        positions = data.get("_gui_positions")
        for i, step in enumerate(steps):
            action = step.get("action")
            if action not in ACTION_CONFIGS:
                continue
            if positions and i < len(positions):
                x, y = positions[i]
            else:
                x, y = 80, 40 + i * 90
            self.canvas.add_node(action, x, y, step)

    def new_macro(self):
        self._editing_id = None
        self.f_id.clear()
        self.f_id.setReadOnly(False)
        self.f_name.clear()
        self.f_kw.clear()
        self.canvas.clear_all()

    def _save(self):
        mid = self.f_id.text().strip()
        name = self.f_name.text().strip()
        kw_raw = self.f_kw.text().strip()
        if not mid:
            QMessageBox.warning(self, "Ошибка", "Укажите ID макроса.")
            return
        if not kw_raw:
            QMessageBox.warning(
                self, "Ошибка", "Укажите хотя бы одну фразу.",
            )
            return
        ordered = self.canvas.sorted_nodes()
        if not ordered:
            QMessageBox.warning(
                self, "Ошибка", "Добавьте хотя бы одно действие.",
            )
            return

        steps, positions = [], []
        for node in ordered:
            steps.append(node.get_data())
            pos = node.scenePos()
            positions.append([round(pos.x(), 1), round(pos.y(), 1)])

        keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]
        macro_data = {
            mid: {
                "name": name or mid,
                "keywords": keywords,
                "steps": steps,
                "_gui_positions": positions,
            }
        }
        self.ipc.request(
            "save_macro", {"macro_data": macro_data},
            callback=self._on_saved,
        )

    def _on_saved(self, payload: dict):
        if payload.get("success"):
            self.saved.emit()
        else:
            QMessageBox.warning(
                self, "Ошибка",
                payload.get("error", "Не удалось сохранить"),
            )


# ═══════════════════════════════════════════
#  MacrosPage
# ═══════════════════════════════════════════

class MacrosPage(BasePage):
    title = "Макросы"
    icon = "fa6s.wand-magic-sparkles"

    def setup_ui(self):
        self._data: dict = {}
        self._rows: dict[str, _MacroRow] = {}
        self._active_id: str | None = None

        # ── Header ──
        self._count_lbl = QLabel("")
        self._count_lbl.setFont(T.font(12))
        self._count_lbl.setStyleSheet(f"color: {T.TEXT_MUTED};")

        new_btn = AccentButton("Новый", "fa6s.plus")
        new_btn.clicked.connect(self._on_new)

        hdr_right = QWidget()
        hdr_right.setStyleSheet(_NS)
        hr = QHBoxLayout(hdr_right)
        hr.setContentsMargins(0, 0, 0, 0)
        hr.setSpacing(10)
        hr.addWidget(self._count_lbl)
        hr.addWidget(new_btn)

        self.page_layout.addWidget(SectionHeader("Макросы", hdr_right))

        # ── Splitter ──
        self._splitter = QSplitter(Qt.Horizontal)

        # ── Left: list ──
        left = QWidget()
        left.setStyleSheet(f"background: {T.BG_DARK};")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setAlignment(Qt.AlignTop)
        self._list_layout.setSpacing(2)
        self._list_layout.setContentsMargins(0, 0, 0, 0)

        self._list_empty = QLabel("Загрузка...")
        self._list_empty.setFont(T.font(12))
        self._list_empty.setAlignment(Qt.AlignCenter)
        self._list_empty.setStyleSheet(
            f"color: {T.TEXT_MUTED}; padding: 30px; {_NS}"
        )
        self._list_layout.addWidget(self._list_empty)

        scroll.setWidget(self._list_widget)
        left_lay.addWidget(scroll)
        self._splitter.addWidget(left)

        # ── Right: editor / placeholder ──
        self._right_stack = QWidget()
        self._right_layout = QVBoxLayout(self._right_stack)
        self._right_layout.setContentsMargins(0, 0, 0, 0)

        self._placeholder = Card(accent_color=T.ACCENT_PRIMARY)
        ic = QLabel()
        ic.setPixmap(
            get_icon("fa6s.wand-magic-sparkles", T.ACCENT_PRIMARY)
            .pixmap(40, 40)
        )
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(_NS)
        self._placeholder.add_widget(ic)
        ph = QLabel(
            "Выберите макрос или создайте новый"
        )
        ph.setFont(T.font(13))
        ph.setAlignment(Qt.AlignCenter)
        ph.setWordWrap(True)
        ph.setStyleSheet(f"color: {T.TEXT_SECONDARY}; {_NS}")
        self._placeholder.add_widget(ph)

        self._editor = _EditorPanel(self.ipc)
        self._editor.saved.connect(self._on_editor_saved)
        self._editor.cancelled.connect(self._show_placeholder)
        self._editor.hide()

        self._right_layout.addStretch()
        self._right_layout.addWidget(self._placeholder)
        self._right_layout.addStretch()
        self._right_layout.addWidget(self._editor)

        self._splitter.addWidget(self._right_stack)
        self._splitter.setSizes([180, 700])
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, False)

        self.page_layout.addWidget(self._splitter, 1)

        # ── Auto-load ──
        if self.ipc:
            self.ipc.connected.connect(self._load)
            QTimer.singleShot(200, self._try_load)

    # ── Data ──

    def _try_load(self):
        if self.ipc and self.ipc.is_connected:
            self._load()

    def _load(self):
        self.ipc.request("get_macros", {}, callback=self._on_loaded)

    def _on_loaded(self, payload: dict):
        macros = payload.get("macros", {})
        if not isinstance(macros, dict):
            macros = {}
        self._data = macros
        self._render_list()

    # ── Render ──

    def _render_list(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

        if not self._data:
            lbl = QLabel("Нет макросов")
            lbl.setFont(T.font(12))
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; padding: 30px; {_NS}")
            self._list_layout.addWidget(lbl)
            self._count_lbl.setText("")
            return

        self._count_lbl.setText(f"{len(self._data)}")

        for mid, mdata in self._data.items():
            row = _MacroRow(mid, mdata)
            row.edit_requested.connect(self._on_edit)
            row.delete_requested.connect(self._on_delete)
            self._rows[mid] = row
            self._list_layout.addWidget(row)
        
        from core.gui.animations import staggered_fade_in
        staggered_fade_in(list(self._rows.values()), delay_step=30)

        if self._active_id and self._active_id in self._rows:
            self._rows[self._active_id].set_selected(True)

    def _select(self, macro_id: str | None):
        old = self._active_id
        self._active_id = macro_id
        if old and old in self._rows:
            self._rows[old].set_selected(False)
        if macro_id and macro_id in self._rows:
            self._rows[macro_id].set_selected(True)

    # ── Actions ──

    def _on_new(self):
        self._select(None)
        self._editor.new_macro()
        self._show_editor()

    def _on_edit(self, macro_id: str):
        data = self._data.get(macro_id)
        if not data:
            return
        self._select(macro_id)
        self._editor.load_macro(macro_id, data)
        self._show_editor()

    def _on_delete(self, macro_id: str):
        name = self._data.get(macro_id, {}).get("name", macro_id)
        reply = QMessageBox.question(
            self, "Удаление",
            f"Удалить макрос «{name}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.ipc.request(
            "delete_macro", {"macro_id": macro_id},
            callback=self._on_deleted,
        )

    def _on_deleted(self, payload: dict):
        if payload.get("success"):
            self._active_id = None
            self._show_placeholder()
            self._load()
        else:
            QMessageBox.warning(
                self, "Ошибка",
                payload.get("error", "Не удалось удалить"),
            )

    def _on_editor_saved(self):
        self._show_placeholder()
        self._load()

    # ── View switching ──

    def _show_editor(self):
        self._placeholder.hide()
        for i in range(self._right_layout.count()):
            item = self._right_layout.itemAt(i)
            if item and item.spacerItem():
                item.spacerItem().changeSize(0, 0)
        self._editor.show()

    def _show_placeholder(self):
        self._select(None)
        self._editor.hide()
        self._placeholder.show()
        for i in range(self._right_layout.count()):
            item = self._right_layout.itemAt(i)
            if item and item.spacerItem():
                item.spacerItem().changeSize(
                    0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding,
                )
        self._right_layout.invalidate()