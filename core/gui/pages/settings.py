# core/gui/pages/settings.py

"""
Settings — полноценная страница настроек.
Секции с виджетами, toggle-свитчи, комбо-боксы,
пароли с глазом, сворачиваемый сырой YAML.
"""

import yaml

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QLineEdit, QComboBox,
    QSpinBox, QTextEdit, QMessageBox, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QColor, QPen

from core.gui.pages.base_page import BasePage
from core.gui.theme import Theme, get_icon
from core.gui.components import (
    Card, SectionHeader, AccentButton, GhostButton, IconButton,
)

T = Theme
_NS = "background: transparent; border: none;"

# ═══════════════════════════════════════════
#  Стили полей
# ═══════════════════════════════════════════

_INPUT = f"""
    QLineEdit, QTextEdit {{
        background-color: {T.BG_INPUT};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM}px;
        padding: 4px 10px;
    }}
    QLineEdit:focus, QTextEdit:focus {{
        border-color: {T.BORDER_FOCUS};
    }}
"""

_COMBO = f"""
    QComboBox {{
        background-color: {T.BG_INPUT};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM}px;
        padding: 4px 8px;
        min-width: 140px;
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

_SPIN = f"""
    QSpinBox {{
        background-color: {T.BG_INPUT};
        color: {T.TEXT_PRIMARY};
        border: 1px solid {T.BORDER};
        border-radius: {T.RADIUS_SM}px;
        padding: 4px 8px;
        min-width: 80px;
    }}
    QSpinBox:focus {{ border-color: {T.BORDER_FOCUS}; }}
    QSpinBox::up-button, QSpinBox::down-button {{
        width: 20px; background: transparent; border: none;
    }}
"""


# ═══════════════════════════════════════════
#  Схема секций
# ═══════════════════════════════════════════

# Кортеж: (path, label, type, *extra)
#   text / password  — QLineEdit
#   combo            — QComboBox,  extra[0] = [options]
#   toggle           — ToggleSwitch
#   number           — QSpinBox,   extra[0] = min, extra[1] = max
#   textarea         — QTextEdit
#   list             — QLineEdit,  сохраняется как YAML-список
#   None             — визуальный разделитель

SECTIONS = [
    {
        "title": "Распознавание",
        "icon": "fa6s.ear-listen",
        "color": T.ACCENT_SECONDARY,
        "fields": [
            ("listener.wake_word", "Ключевое слово", "text"),
            ("listener.alternative_words", "Доп. слова (через запятую)", "list"),
            ("listener.model_path", "Путь к модели Vosk", "text"),
            None,
            ("recognizer.use_whisper", "Использовать Whisper", "toggle"),
            ("recognizer.model_size", "Модель Whisper", "combo",
             ["tiny", "base", "small", "medium", "large-v3"]),
            ("recognizer.language", "Язык", "combo", ["ru", "en"]),
            ("recognizer.timeout", "Таймаут (сек)", "number", 1, 30),
            ("recognizer.phrase_limit", "Макс. длина фразы (сек)", "number", 1, 30),
        ],
    },
    {
        "title": "Голос",
        "icon": "fa6s.volume-high",
        "color": T.ACCENT_SUCCESS,
        "fields": [
            ("speaker.voice", "Голос", "combo",
             ["ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"]),
            ("speaker.rate", "Скорость речи", "text"),
            ("speaker.volume", "Громкость голоса", "text"),
            ("speaker.cache", "Кэшировать аудио", "toggle"),
        ],
    },
    {
        "title": "Языковая модель (LLM)",
        "icon": "fa6s.brain",
        "color": "#c07cf7",
        "fields": [
            ("llm.provider", "Провайдер", "combo",
             ["none", "ollama", "openai"]),
            None,
            ("llm.ollama.model", "Модель Ollama", "text"),
            ("llm.ollama.base_url", "URL Ollama", "text"),
            None,
            ("llm.openai.model", "Модель OpenAI", "text"),
            ("llm.openai.api_key", "API ключ OpenAI", "password"),
            None,
            ("llm.max_tokens", "Макс. токенов", "number", 10, 4000),
            ("llm.max_history", "Глубина истории", "number", 0, 50),
            ("llm.system_prompt", "Системный промпт", "textarea"),
        ],
    },
    {
        "title": "Общие",
        "icon": "fa6s.gear",
        "color": T.TEXT_SECONDARY,
        "fields": [
            ("assistant.follow_up_seconds", "Ожидание команды (сек)",
             "number", 1, 30),
            ("app.hide_console", "Скрыть консоль", "toggle"),
            ("app.show_notifications", "Уведомления Windows", "toggle"),
            ("weather.city", "Город для погоды", "text"),
            ("logging.level", "Уровень логирования", "combo",
             ["DEBUG", "INFO", "WARNING", "ERROR"]),
        ],
    },
    {
        "title": "Интеграции",
        "icon": "fa6s.plug",
        "color": T.ACCENT_WARNING,
        "fields": [
            ("integrations.telegram.bot_token",
             "Telegram: токен бота", "password"),
            ("integrations.telegram.default_chat_id",
             "Telegram: Chat ID", "text"),
            None,
            ("integrations.vscode.projects_dir",
             "VS Code: папка проектов", "text"),
            ("integrations.vscode.code_cmd",
             "VS Code: команда запуска", "text"),
            None,
            ("folders.projects", "Папка проектов", "text"),
        ],
    },
]


# ═══════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════

def _get_nested(d: dict, path: str, default=None):
    if not d:
        return default
    for key in path.split("."):
        if isinstance(d, dict):
            d = d.get(key)
        else:
            return default
        if d is None:
            return default
    return d


def _set_nested(d: dict, path: str, value):
    keys = path.split(".")
    for k in keys[:-1]:
        if k not in d or not isinstance(d.get(k), dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value


# ═══════════════════════════════════════════
#  ToggleSwitch — анимированный переключатель
# ═══════════════════════════════════════════

class ToggleSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 24)
        self._checked = False
        self._knob_x = 2.0
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.valueChanged.connect(self._on_tick)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, val: bool):
        self._checked = val
        self._knob_x = 22.0 if val else 2.0
        self.update()

    def mousePressEvent(self, _ev):
        self._checked = not self._checked
        self._anim.setStartValue(self._knob_x)
        self._anim.setEndValue(22.0 if self._checked else 2.0)
        self._anim.start()
        self.toggled.emit(self._checked)

    def _on_tick(self, value):
        self._knob_x = float(value)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        bg = QColor(T.ACCENT_PRIMARY) if self._checked else QColor(T.BG_INPUT)
        p.setBrush(bg)
        p.setPen(QPen(QColor(T.BORDER), 1))
        p.drawRoundedRect(0, 0, 44, 24, 12, 12)

        p.setBrush(QColor("#ffffff"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(int(self._knob_x), 2, 20, 20)
        p.end()


# ═══════════════════════════════════════════
#  SettingsPage
# ═══════════════════════════════════════════

class SettingsPage(BasePage):
    title = "Настройки"
    icon = "fa6s.gear"

    # ──────────────────────────────
    #  UI
    # ──────────────────────────────

    def setup_ui(self):
        self._config: dict = {}
        self._raw_yaml: str = ""
        self._widgets: dict[str, QWidget] = {}   # path → widget
        self._dirty = False

        # ── Header ──
        self._discard_btn = GhostButton("Отменить", "fa6s.rotate-left")
        self._discard_btn.clicked.connect(self._discard)
        self._discard_btn.setEnabled(False)

        self._save_btn = AccentButton("Сохранить", "fa6s.floppy-disk")
        self._save_btn.clicked.connect(self._save)
        self._save_btn.setEnabled(False)

        hdr_right = QWidget()
        hdr_right.setStyleSheet(_NS)
        hr = QHBoxLayout(hdr_right)
        hr.setContentsMargins(0, 0, 0, 0)
        hr.setSpacing(8)
        hr.addWidget(self._discard_btn)
        hr.addWidget(self._save_btn)

        self.page_layout.addWidget(SectionHeader("Настройки", hdr_right))

        # ── Scroll ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._body = QVBoxLayout(self._container)
        self._body.setAlignment(Qt.AlignTop)
        self._body.setSpacing(16)
        self._body.setContentsMargins(0, 0, 4, 0)

        self._placeholder = QLabel("Загрузка конфигурации...")
        self._placeholder.setFont(T.font(13))
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color: {T.TEXT_MUTED}; padding: 60px; {_NS}"
        )
        self._body.addWidget(self._placeholder)

        scroll.setWidget(self._container)
        self.page_layout.addWidget(scroll, 1)

        # ── Restart notice ──
        note = QLabel(
            "⚠  Для применения большинства настроек "
            "требуется перезапуск ассистента"
        )
        note.setFont(T.font(11))
        note.setAlignment(Qt.AlignCenter)
        note.setStyleSheet(f"color: {T.TEXT_MUTED}; padding: 8px; {_NS}")
        self.page_layout.addWidget(note)

        # ── Auto-load ──
        if self.ipc:
            self.ipc.connected.connect(self._load)
            QTimer.singleShot(300, self._try_load)

    # ──────────────────────────────
    #  Загрузка
    # ──────────────────────────────

    def _try_load(self):
        if self.ipc and self.ipc.is_connected:
            self._load()

    def _load(self):
        self.ipc.request("get_config", {}, callback=self._on_loaded)

    def _on_loaded(self, payload: dict):
        if not payload.get("success"):
            self._set_placeholder(
                f"Ошибка: {payload.get('error', '?')}"
            )
            return

        content = payload.get("content", "")
        try:
            self._config = yaml.safe_load(content) or {}
        except yaml.YAMLError as e:
            self._set_placeholder(f"Ошибка YAML: {e}")
            return

        self._raw_yaml = content
        self._build_all()

    def _set_placeholder(self, text: str):
        self._clear_body()
        lbl = QLabel(text)
        lbl.setFont(T.font(13))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; padding: 60px; {_NS}")
        self._body.addWidget(lbl)

    def _clear_body(self):
        while self._body.count():
            item = self._body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ──────────────────────────────
    #  Построение секций
    # ──────────────────────────────
    def _preview_voice(self):
        """Предпросмотр голоса с текущими настройками."""
        if not self.ipc or not self.ipc.is_connected:
            return

        self._collect()

        voice = _get_nested(self._config, "speaker.voice") or "ru-RU-SvetlanaNeural"
        rate = _get_nested(self._config, "speaker.rate") or "+0%"

        self.ipc.request("preview_voice", {
            "voice": voice,
            "rate": rate,
        })

    def _build_all(self):
        self._clear_body()
        self._widgets.clear()

        for sec in SECTIONS:
            card = Card(sec["title"], accent_color=sec.get("color"))
            self._fill_card(card, sec["fields"])
            self._body.addWidget(card)

        self._build_raw_yaml()
        # Кнопка предпросмотра голоса
        preview_row = QWidget()
        preview_row.setStyleSheet(_NS)
        pr_layout = QHBoxLayout(preview_row)
        pr_layout.setContentsMargins(0, 0, 0, 0)
        pr_layout.setSpacing(8)
        pr_layout.addStretch()

        preview_btn = GhostButton("🔊 Послушать голос", color=T.ACCENT_SUCCESS)
        preview_btn.clicked.connect(self._preview_voice)
        pr_layout.addWidget(preview_btn)

        self._body.addWidget(preview_row)
        self._body.addStretch()

        self._dirty = False
        self._update_buttons()

    def _fill_card(self, card: Card, fields: list):
        for spec in fields:
            if spec is None:
                sep = QFrame()
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background-color: {T.BORDER};")
                card.add_widget(sep)
                continue

            path, label, ftype = spec[0], spec[1], spec[2]
            extra = spec[3:] if len(spec) > 3 else ()

            row = QWidget()
            row.setStyleSheet(_NS)
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 2, 0, 2)
            hl.setSpacing(12)

            lbl = QLabel(label)
            lbl.setFont(T.font(12))
            lbl.setStyleSheet(f"color: {T.TEXT_SECONDARY}; {_NS}")
            lbl.setFixedWidth(210)
            hl.addWidget(lbl)

            widget = self._make_field(path, ftype, extra)
            hl.addWidget(widget, 1)

            card.add_widget(row)

    # ──────────────────────────────
    #  Фабрика виджетов
    # ──────────────────────────────

    def _make_field(self, path: str, ftype: str, extra: tuple) -> QWidget:
        val = _get_nested(self._config, path)

        # ── text ──
        if ftype == "text":
            w = self._line_edit(str(val) if val is not None else "")
            self._widgets[path] = w
            return w

        # ── password ──
        if ftype == "password":
            le = self._line_edit(str(val) if val is not None else "")
            le.setEchoMode(QLineEdit.Password)

            eye = IconButton(
                "fa6s.eye", "Показать / скрыть", size=28,
                color=T.TEXT_MUTED,
            )
            eye.clicked.connect(lambda: le.setEchoMode(
                QLineEdit.Normal
                if le.echoMode() == QLineEdit.Password
                else QLineEdit.Password
            ))

            box = QWidget()
            box.setStyleSheet(_NS)
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(4)
            bl.addWidget(le, 1)
            bl.addWidget(eye)

            self._widgets[path] = le
            return box

        # ── combo ──
        if ftype == "combo":
            options = extra[0] if extra else []
            w = QComboBox()
            w.setFont(T.font(12))
            w.setFixedHeight(32)
            w.setEditable(True)
            w.setStyleSheet(_COMBO)
            w.addItems(options)
            if val is not None:
                idx = w.findText(str(val))
                if idx >= 0:
                    w.setCurrentIndex(idx)
                else:
                    w.setCurrentText(str(val))
            w.currentTextChanged.connect(self._mark_dirty)
            self._widgets[path] = w
            return w

        # ── toggle ──
        if ftype == "toggle":
            w = ToggleSwitch()
            w.setChecked(bool(val) if val is not None else False)
            w.toggled.connect(lambda _: self._mark_dirty())
            self._widgets[path] = w
            return w

        # ── number ──
        if ftype == "number":
            lo = extra[0] if len(extra) > 0 else 0
            hi = extra[1] if len(extra) > 1 else 9999
            w = QSpinBox()
            w.setFont(T.font(12))
            w.setFixedHeight(32)
            w.setRange(lo, hi)
            w.setStyleSheet(_SPIN)
            try:
                w.setValue(int(val) if val is not None else lo)
            except (TypeError, ValueError):
                w.setValue(lo)
            w.valueChanged.connect(lambda _: self._mark_dirty())
            self._widgets[path] = w
            return w

        # ── textarea ──
        if ftype == "textarea":
            w = QTextEdit()
            w.setFont(T.font(12))
            w.setMaximumHeight(100)
            w.setStyleSheet(_INPUT)
            w.setPlainText(str(val) if val is not None else "")
            w.textChanged.connect(self._mark_dirty)
            self._widgets[path] = w
            return w

        # ── list (comma-separated) ──
        if ftype == "list":
            text = ""
            if isinstance(val, list):
                text = ", ".join(str(v) for v in val)
            elif val is not None:
                text = str(val)
            w = self._line_edit(text)
            self._widgets[path] = w
            return w

        # fallback
        lbl = QLabel(f"[{ftype}?]")
        lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
        return lbl

    def _line_edit(self, text: str) -> QLineEdit:
        w = QLineEdit()
        w.setFont(T.font(12))
        w.setFixedHeight(32)
        w.setStyleSheet(_INPUT)
        w.setText(text)
        w.textChanged.connect(self._mark_dirty)
        return w

    # ──────────────────────────────
    #  Сырой YAML (collapsible)
    # ──────────────────────────────

    def _build_raw_yaml(self):
        self._yaml_expanded = False

        self._yaml_toggle = GhostButton(
            "▶  Расширенный режим: сырой YAML", color=T.TEXT_MUTED,
        )
        self._yaml_toggle.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {T.TEXT_MUTED};
                border: none;
                text-align: left;
                padding: 8px 0;
            }}
            QPushButton:hover {{ color: {T.TEXT_SECONDARY}; }}
        """)
        self._yaml_toggle.clicked.connect(self._toggle_yaml)
        self._body.addWidget(self._yaml_toggle)

        self._yaml_warn = QLabel(
            "⚠ Сохранение отсюда перезапишет файл целиком. "
            "Изменения из секций выше будут потеряны."
        )
        self._yaml_warn.setFont(T.font(11))
        self._yaml_warn.setWordWrap(True)
        self._yaml_warn.setStyleSheet(
            f"color: {T.ACCENT_WARNING}; padding: 4px 0; {_NS}"
        )
        self._yaml_warn.hide()
        self._body.addWidget(self._yaml_warn)

        self._yaml_editor = QTextEdit()
        self._yaml_editor.setFont(T.mono_font(11))
        self._yaml_editor.setMinimumHeight(300)
        self._yaml_editor.setStyleSheet(f"""
            QTextEdit {{
                background-color: {T.BG_DARKEST};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_SM}px;
                padding: 12px;
            }}
        """)
        self._yaml_editor.setPlainText(self._raw_yaml)
        self._yaml_editor.textChanged.connect(self._mark_dirty)
        self._yaml_editor.hide()
        self._body.addWidget(self._yaml_editor)

        self._yaml_save = AccentButton(
            "Сохранить YAML", "fa6s.floppy-disk",
        )
        self._yaml_save.clicked.connect(self._save_raw)
        self._yaml_save.hide()
        self._body.addWidget(self._yaml_save)

    def _toggle_yaml(self):
        self._yaml_expanded = not self._yaml_expanded
        vis = self._yaml_expanded
        self._yaml_warn.setVisible(vis)
        self._yaml_editor.setVisible(vis)
        self._yaml_save.setVisible(vis)
        arrow = "▼" if vis else "▶"
        self._yaml_toggle.setText(
            f"{arrow}  Расширенный режим: сырой YAML"
        )

    # ──────────────────────────────
    #  Dirty tracking
    # ──────────────────────────────

    def _mark_dirty(self, *_):
        if not self._dirty:
            self._dirty = True
            self._update_buttons()

    def _update_buttons(self):
        self._discard_btn.setEnabled(self._dirty)
        self._save_btn.setEnabled(self._dirty)

    # ──────────────────────────────
    #  Сбор значений
    # ──────────────────────────────

    def _collect(self):
        """Прочитать все виджеты обратно в self._config."""
        for path, widget in self._widgets.items():
            ftype = self._field_type(path)

            if isinstance(widget, QLineEdit):
                if ftype == "list":
                    lst = [
                        s.strip()
                        for s in widget.text().split(",")
                        if s.strip()
                    ]
                    _set_nested(self._config, path, lst)
                else:
                    text = widget.text()
                    orig = _get_nested(self._config, path)
                    if isinstance(orig, (int, float)):
                        try:
                            _set_nested(
                                self._config, path,
                                type(orig)(text),
                            )
                        except (TypeError, ValueError):
                            _set_nested(self._config, path, text)
                    else:
                        _set_nested(self._config, path, text)

            elif isinstance(widget, QComboBox):
                _set_nested(self._config, path, widget.currentText())

            elif isinstance(widget, ToggleSwitch):
                _set_nested(self._config, path, widget.isChecked())

            elif isinstance(widget, QSpinBox):
                _set_nested(self._config, path, widget.value())

            elif isinstance(widget, QTextEdit):
                _set_nested(
                    self._config, path, widget.toPlainText(),
                )

    def _field_type(self, path: str) -> str:
        for sec in SECTIONS:
            for spec in sec["fields"]:
                if spec is not None and spec[0] == path:
                    return spec[2]
        return "text"

    # ──────────────────────────────
    #  Сохранение
    # ──────────────────────────────

    def _save(self):
        """Сохранить из структурированного редактора."""
        self._collect()

        # Горячее применение поддерживаемых настроек
        self._try_hot_apply()

        try:
            text = yaml.dump(
                self._config,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Сериализация: {e}")
            return
        self._do_save(text)
    
    _HOT_PATHS = [
        "speaker.rate", "speaker.volume",
        "llm.system_prompt", "llm.max_tokens",
        "assistant.follow_up_seconds",
    ]

    def _try_hot_apply(self):
        """Попытаться применить настройки без перезапуска."""
        if not self.ipc or not self.ipc.is_connected:
            return

        for path in self._HOT_PATHS:
            value = _get_nested(self._config, path)
            if value is not None:
                self.ipc.send_command("apply_setting", {
                    "path": path, "value": value,
                })

    def _save_raw(self):
        """Сохранить из YAML-редактора."""
        text = self._yaml_editor.toPlainText()
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as e:
            QMessageBox.warning(
                self, "Ошибка YAML",
                f"Невалидный YAML:\n{e}",
            )
            return
        self._do_save(text)

    def _do_save(self, yaml_text: str):
        self.ipc.request(
            "save_config", {"content": yaml_text},
            callback=self._on_saved,
        )

    def _on_saved(self, payload: dict):
        if payload.get("success"):
            self._dirty = False
            self._update_buttons()
            self._load()          # перезагрузить для синхронизации
            QMessageBox.information(
                self, "Сохранено",
                "Настройки сохранены.\n"
                "Перезапустите ассистента для применения.",
            )
        else:
            QMessageBox.warning(
                self, "Ошибка",
                payload.get("error", "Не удалось сохранить"),
            )

    def _discard(self):
        """Откатить к сохранённым значениям."""
        self._load()
        self._dirty = False
        self._update_buttons()