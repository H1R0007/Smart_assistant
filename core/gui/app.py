# core/gui/app.py

"""
Главное окно GUI v3.1.
Трей, персистентность, toast, горячие клавиши.
"""

import time as _time
import sys

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QStackedWidget, QFrame, QSystemTrayIcon,
    QMenu, QApplication,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QIcon, QAction, QPixmap, QPainter, QColor

from core.gui.theme import Theme, get_icon
from core.gui.components import StatusBadge, CommandInput, ResultBar, IconButton
from core.gui.voice_visualizer import VoiceVisualizer
from core.gui.sidebar import Sidebar
from core.gui.ipc_client import IPCClient
from core.gui.state import StateManager
from core.gui.toast import ToastManager
from core.gui.animations import PageFader
from core.gui.command_palette import CommandPalette, _AUTO_CATEGORIES, _auto_description
from core.gui.onboarding import OnboardingOverlay

from core.gui.pages import (
    DashboardPage, ChatPage, MacrosPage,
    KnowledgePage, SettingsPage, AnalyticsPage,
)

T = Theme

PAGES = [
    {"title": "Dashboard",   "icon": "fa6s.house",               "cls": DashboardPage},
    {"title": "Диалог",      "icon": "fa6s.comments",            "cls": ChatPage},
    {"title": "Макросы",     "icon": "fa6s.wand-magic-sparkles", "cls": MacrosPage},
    {"title": "База знаний", "icon": "fa6s.brain",               "cls": KnowledgePage},
    {"title": "Настройки",   "icon": "fa6s.gear",                "cls": SettingsPage},
    {"title": "Аналитика",   "icon": "fa6s.chart-line",          "cls": AnalyticsPage},
]

_IDX_DASHBOARD = 0
_IDX_CHAT = 1


# ═══════════════════════════════════════════
#  Генерация иконки трея
# ═══════════════════════════════════════════

def _make_tray_icon(color: str = T.ACCENT_PRIMARY) -> QIcon:
    """Генерирует QIcon для трея — скруглённый квадрат с буквой П."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)

    # Фон
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, size - 4, size - 4, 12, 12)

    # Буква
    p.setPen(QColor("#ffffff"))
    f = T.font(28, bold=True)
    p.setFont(f)
    p.drawText(pixmap.rect(), Qt.AlignCenter, "П")
    p.end()

    return QIcon(pixmap)


# ═══════════════════════════════════════════
#  HeaderBar
# ═══════════════════════════════════════════

class HeaderBar(QWidget):
    palette_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(T.HEADER_H)
        self.setStyleSheet(f"""
            background-color: {T.BG_CARD};
            border-bottom: 1px solid {T.BORDER};
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)

        self._page_title = QLabel("Dashboard")
        self._page_title.setFont(T.font(15, bold=True))
        self._page_title.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; background: transparent; border: none;"
        )
        layout.addWidget(self._page_title)
        layout.addStretch()

        self.visualizer = VoiceVisualizer()
        self.visualizer.setFixedWidth(200)
        layout.addWidget(self.visualizer)
        layout.addStretch()

        self.status = StatusBadge()
        layout.addWidget(self.status)

        # Кнопка палитры команд
        palette_btn = IconButton(
            "fa6s.magnifying-glass", "Палитра команд (Ctrl+K)",
            size=32, color=T.TEXT_SECONDARY,
        )
        palette_btn.clicked.connect(self.palette_requested.emit)
        layout.addWidget(palette_btn)

        hint = QLabel("Ctrl+1…6")
        hint.setFont(T.font(10))
        hint.setStyleSheet(
            f"color: {T.TEXT_MUTED}; background: transparent; "
            f"border: none; padding-left: 12px;"
        )
        hint.setToolTip(
            "Ctrl+1…6 — страницы\n"
            "Ctrl+K — палитра команд\n"
            "Ctrl+L — фокус на ввод\n"
            "F5 — обновить"
        )
        layout.addWidget(hint)

    def set_page_title(self, title: str):
        self._page_title.setText(title)

# ═══════════════════════════════════════════
#  MainWindow
# ═══════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Пятница — Голосовой ассистент")
        self.setMinimumSize(900, 550)
        self.setStyleSheet(f"background-color: {T.BG_DARK};")

        self._state = StateManager.instance()
        self._is_quitting = False

        # ── IPC ──
        self.ipc = IPCClient()
        self.ipc.connected.connect(self._on_connected)
        self.ipc.disconnected.connect(self._on_disconnected)
        self.ipc.status_updated.connect(self._on_status)
        self.ipc.broadcast_received.connect(self._on_broadcast)
        self.ipc.start()

        # ── Layout ──
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self.sidebar = Sidebar([
            {"title": p["title"], "icon": p["icon"]} for p in PAGES
        ])
        self.sidebar.page_changed.connect(self._switch_page)
        root.addWidget(self.sidebar)

        if not self._state.get_bool("sidebar/expanded", True):
            self.sidebar.toggle()

        # Right column
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        self.header = HeaderBar()
        self.header.palette_requested.connect(self._open_palette)
        right.addWidget(self.header)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background-color: {T.BG_DARK};")

        self._pages: list = []
        for cfg in PAGES:
            page = cfg["cls"](ipc=self.ipc)
            page.navigate_requested.connect(self._switch_page)
            self._pages.append(page)
            self.stack.addWidget(page)
        
        self._fader = PageFader(self.stack)

        right.addWidget(self.stack, 1)

        self.result_bar = ResultBar()
        right.addWidget(self.result_bar)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {T.BORDER};")
        right.addWidget(sep)

        self.cmd_input = CommandInput()
        self.cmd_input.command_submitted.connect(self._on_command)
        right.addWidget(self.cmd_input)

        root.addLayout(right, 1)

        # ── Toasts ──
        self.toasts = ToastManager(self)

        # ── Tray ──
        self._setup_tray()

        # ── Shortcuts ──
        self._setup_shortcuts()

        # ── Restore state ──
        if not self._state.restore_geometry(self):
            self.resize(1100, 700)

        start_page = self._state.get_int("nav/active_page", 0)
        self._switch_page(start_page)

        # ── Command Palette ──
        self._palette = CommandPalette(central)
        self._palette.command_selected.connect(self._on_palette_command)
        # ── Onboarding ──
        self._onboarding = OnboardingOverlay(central)
        self._onboarding.finished.connect(self._on_onboarding_done)

        if not self._state.get_bool("app/onboarding_done", False):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, self._show_onboarding)
    
    def _on_suggestions_loaded(self, payload: dict):
        commands = payload.get("commands", [])
        self.cmd_input.set_suggestions(commands)

    # ══════════════════════════════════════
    #  System Tray
    # ══════════════════════════════════════

    def _setup_tray(self):
        self._tray_icon_connected = _make_tray_icon(T.ACCENT_PRIMARY)
        self._tray_icon_disconnected = _make_tray_icon(T.ACCENT_DANGER)

        self._tray = QSystemTrayIcon(self._tray_icon_disconnected, self)
        self._tray.setToolTip("Пятница — GUI")
        self._tray.activated.connect(self._on_tray_activated)

        # Меню трея
        tray_menu = QMenu()
        tray_menu.setStyleSheet(f"""
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

        show_action = QAction("Показать", self)
        show_action.triggered.connect(self._show_from_tray)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        quit_action = QAction("Выход", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Одинарный клик
            self._show_from_tray()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self):
        """Восстановить окно из трея."""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _notify_tray(self, title: str, message: str):
        """Показать уведомление из трея (если окно скрыто)."""
        if not self.isVisible() and self._tray.supportsMessages():
            self._tray.showMessage(title, message,
                                   QSystemTrayIcon.MessageIcon.Information,
                                   3000)

    def _quit_app(self):
        """Полный выход (не сворачивание)."""
        self._is_quitting = True
        self._state.save_geometry(self)
        self._state.set("sidebar/expanded", self.sidebar._expanded)
        self.ipc.stop()
        self._tray.hide()
        QApplication.instance().quit()

    # ══════════════════════════════════════
    #  Shortcuts
    # ══════════════════════════════════════

    def _setup_shortcuts(self):
        for i in range(min(9, len(PAGES))):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._switch_page(idx))

        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(
            lambda: self.cmd_input.input.setFocus()
        )

        QShortcut(QKeySequence("F5"), self).activated.connect(
            self._refresh_current_page
        )

        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(
            self._toggle_sidebar
        )
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(
            self._open_palette
        )

    def _refresh_current_page(self):
        page = self._pages[self.stack.currentIndex()]
        for method_name in ("_refresh", "_load"):
            fn = getattr(page, method_name, None)
            if callable(fn):
                fn()
                self.toasts.info("Обновлено")
                return

    def _toggle_sidebar(self):
        self.sidebar.toggle()
        self._state.set("sidebar/expanded", self.sidebar._expanded)
    
    def _open_palette(self):
        """Открыть Command Palette — загрузить команды и показать."""
        if not self.ipc or not self.ipc.is_connected:
            self.toasts.warning("Ядро не подключено")
            return
        self.ipc.request("get_commands", {}, callback=self._on_commands_loaded)

    def _on_commands_loaded(self, payload: dict):
        commands = payload.get("commands", [])
        self._palette.set_commands(commands)
        self._palette.open_palette()

    def _on_palette_command(self, text: str):
        """Выполнить команду из палитры."""
        self._on_command(text)
    # ══════════════════════════════════════
    #  Helpers
    # ══════════════════════════════════════

    @property
    def _dashboard(self):
        return self._pages[_IDX_DASHBOARD] if self._pages else None

    @property
    def _chat(self):
        return self._pages[_IDX_CHAT] if len(self._pages) > _IDX_CHAT else None

    # ══════════════════════════════════════
    #  Navigation
    # ══════════════════════════════════════

    def _switch_page(self, index: int):
        if not (0 <= index < len(self._pages)):
            return
        self._fader.switch_to(index)
        self.sidebar.set_active(index)
        self.header.set_page_title(PAGES[index]["title"])
        self._state.set("nav/active_page", index)

    # ══════════════════════════════════════
    #  IPC
    # ══════════════════════════════════════

    def _on_connected(self):
        self.header.status.set_status(T.ACCENT_SUCCESS, "Подключено")
        self.header.visualizer.set_state("waiting")
        self._tray.setIcon(self._tray_icon_connected)
        self._tray.setToolTip("Пятница — Подключено")
        self.toasts.success("Подключено к ядру")

        if self._dashboard:
            self._dashboard.on_connected()
        if self._chat:
            self._chat.add_system_message("Подключено к ядру ассистента")
        
        # Запрашиваем snapshot сессии
        self.ipc.request("get_session_snapshot", {}, callback=self._on_snapshot)
        # Загрузить подсказки для автодополнения
        self.ipc.request("get_commands", {}, callback=self._on_suggestions_loaded)

    def _on_snapshot(self, payload: dict):
        """Восстановить состояние Dashboard из snapshot ядра."""
        if not payload.get("success"):
            return

        if self._dashboard:
            # Восстановить счётчик команд
            cmd_count = payload.get("commands_count", 0)
            if cmd_count > 0:
                self._dashboard._cmd_count = cmd_count
                self._dashboard._lbl_commands.setText(str(cmd_count))

    def _on_disconnected(self):
        self.header.status.set_status(T.ACCENT_DANGER, "Переподключение...")
        self.header.visualizer.set_state("connected")
        self._tray.setIcon(self._tray_icon_disconnected)
        self._tray.setToolTip("Пятница — Отключено")

        if self._dashboard:
            self._dashboard.on_disconnected()
        if self._chat:
            self._chat.add_system_message("Соединение потеряно — переподключение...")

    def _on_status(self, payload: dict):
        state = payload.get("state", "waiting")
        text = payload.get("text", "")

        self.header.visualizer.set_state(state)
        state_labels = {
            "waiting":    ("Ожидание",  T.TEXT_MUTED),
            "listening":  ("Слушаю",    T.ACCENT_SECONDARY),
            "processing": ("Думаю",     T.ACCENT_WARNING),
            "speaking":   ("Говорю",    T.ACCENT_SUCCESS),
        }
        label, color = state_labels.get(state, ("", T.TEXT_MUTED))
        self.header.status.set_status(color, label)

        if self._dashboard:
            self._dashboard.update_state(state, text)

        if state == "processing" and text:
            if self._chat:
                self._chat.add_user_message(text, source="voice")

    def _on_broadcast(self, msg_type: str, payload: dict):
        if msg_type == "command_executed":
            success = payload.get("success", False)
            if self._dashboard:
                self._dashboard.add_command_entry(
                    payload.get("text", "?"), success,
                    payload.get("source", "voice"),
                    payload.get("timestamp", _time.time()),
                )
            self.result_bar.show_result(
                "Голосовая команда выполнена" if success else "Ошибка",
                success,
            )

        elif msg_type == "assistant_speak":
            text = payload.get("text", "")
            ts = payload.get("timestamp")
            if self._chat:
                self._chat.add_assistant_message(text, ts)
            if self._dashboard:
                self._dashboard.set_last_response(text, ts)

        elif msg_type == "timers_update":
            if self._dashboard:
                self._dashboard.on_timers_push(payload.get("timers", []))

        elif msg_type == "active_app_update":
            if self._dashboard:
                self._dashboard.on_active_app_push(
                    payload.get("app", ""),
                    payload.get("title", ""),
                )

    # ══════════════════════════════════════
    #  Text input
    # ══════════════════════════════════════

    def _on_command(self, text: str):
        if self._chat:
            self._chat.add_user_message(text, source="gui")

        if not self.ipc.is_connected:
            self.toasts.error("Ядро не подключено")
            return

        if self._chat:
            self._chat.show_thinking()

        self.ipc.request(
            "execute_command",
            {"text": text},
            callback=lambda p: self._on_gui_result(text, p),
        )

    def _on_gui_result(self, text: str, payload: dict):
        success = payload.get("success", False)

        if self._chat:
            self._chat.hide_thinking()

        if self._dashboard:
            self._dashboard.add_command_entry(
                text, success, "gui", _time.time(),
            )

        self.result_bar.show_result(
            "Команда выполнена" if success else "Ошибка выполнения",
            success,
        )

    # ══════════════════════════════════════
    #  Window events
    # ══════════════════════════════════════

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toasts.reposition_all()
        if self._palette.isVisible():
            self._palette._reposition()
        if self._onboarding.isVisible():
            self._onboarding._reposition()

    def closeEvent(self, event):
        """
        Закрытие окна → сворачивание в трей (не выход).
        Полный выход — через меню трея «Выход».
        """
        if self._is_quitting:
            # Реальный выход
            self._state.save_geometry(self)
            self._state.set("sidebar/expanded", self.sidebar._expanded)
            self.ipc.stop()
            self._tray.hide()
            event.accept()
        else:
            # Сворачиваем в трей
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "Пятница",
                "Приложение свёрнуто в трей",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )
    def _show_onboarding(self):
        self._onboarding.show_onboarding()

    def _on_onboarding_done(self):
        self._state.set("app/onboarding_done", False)