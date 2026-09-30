# core/gui/pages/dashboard.py

"""
Dashboard — живой главный экран.
Статус, метрики, активное приложение, последний ответ,
таймеры, быстрые действия, лента команд.
"""

import time as _time
from datetime import datetime

from PySide6.QtWidgets import (
    QLabel, QHBoxLayout, QVBoxLayout, QWidget,
    QScrollArea, QFrame, QInputDialog, QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontMetrics

from core.gui.pages.base_page import BasePage
from core.gui.theme import Theme, get_icon
from core.gui.components import (
    Card, SectionHeader, GhostButton, CommandHistoryCard,
)
from core.gui.voice_visualizer import VoiceVisualizer

T = Theme
_T = "background: transparent; border: none;"

PAGE_CHAT = 1
PAGE_KNOWLEDGE = 3


def _fmt_remaining(sec: int) -> str:
    if sec <= 0:
        return "0:00"
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _time_group_label(ts: float) -> str:
    delta = _time.time() - ts
    if delta < 120:
        return "Только что"
    if delta < 900:
        return f"{int(delta / 60)} мин. назад"
    if delta < 3600:
        return "Менее часа назад"
    try:
        dt = datetime.fromtimestamp(ts)
        now = datetime.now()
        if dt.date() == now.date():
            return f"Сегодня, {dt.strftime('%H:%M')}"
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return "Ранее"


class _InfoCard(QFrame):
    def __init__(self, icon_name: str, title: str,
                 color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("icard")
        self.setStyleSheet(f"""
            QFrame#icard {{
                background-color: {T.BG_CARD};
                border: 1px solid {T.BORDER};
                border-top: 2px solid {color};
                border-radius: {T.RADIUS_MD}px;
            }}
        """)
        self.setMinimumHeight(80)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(get_icon(icon_name, color).pixmap(14, 14))
        ic.setFixedWidth(18)
        ic.setStyleSheet(_T)
        hdr.addWidget(ic)
        ttl = QLabel(title)
        ttl.setFont(T.font(11))
        ttl.setStyleSheet(f"color: {T.TEXT_MUTED}; {_T}")
        hdr.addWidget(ttl)
        hdr.addStretch()
        outer.addLayout(hdr)

        self.body = QVBoxLayout()
        self.body.setSpacing(2)
        self.body.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.body)
        outer.addStretch()


class _TimeSeparator(QWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setStyleSheet(_T)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        line_l = QFrame()
        line_l.setFixedHeight(1)
        line_l.setStyleSheet(f"background-color: {T.BORDER};")

        lbl = QLabel(text)
        lbl.setFont(T.font(10))
        lbl.setStyleSheet(
            f"color: {T.TEXT_MUTED}; font-style: italic; {_T}"
        )

        line_r = QFrame()
        line_r.setFixedHeight(1)
        line_r.setStyleSheet(f"background-color: {T.BORDER};")

        layout.addWidget(line_l, 1)
        layout.addWidget(lbl)
        layout.addWidget(line_r, 1)


class DashboardPage(BasePage):
    title = "Dashboard"
    icon = "fa6s.house"

    MAX_HISTORY = 50

    def setup_ui(self):
        self._cmd_count = 0
        self._connected_time = None
        self._last_cmd_ts = 0.0

        # ══ Top row: Статус + Метрики ══
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        status_card = Card("Статус")
        self.vis = VoiceVisualizer()
        self.vis.setFixedHeight(48)
        status_card.add_widget(self.vis)

        self.state_label = QLabel("Ожидание подключения...")
        self.state_label.setFont(T.font(14, bold=True))
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; {_T}"
        )
        status_card.add_widget(self.state_label)
        top_row.addWidget(status_card, 2)

        metrics_card = Card("Сессия")
        self._lbl_commands = self._metric(
            metrics_card, "fa6s.bolt", "Команд:", "0",
        )
        self._lbl_uptime = self._metric(
            metrics_card, "fa6s.clock", "Аптайм:", "—",
        )
        self._lbl_status = self._metric(
            metrics_card, "fa6s.circle", "Статус:", "Отключено",
        )
        top_row.addWidget(metrics_card, 1)
        self.page_layout.addLayout(top_row)

        # ══ Info row: App + Response + Timers ══
        info_row = QHBoxLayout()
        info_row.setSpacing(12)

        self._app_card = _InfoCard(
            "fa6s.window-restore", "Активное приложение",
            T.ACCENT_SECONDARY,
        )
        self._app_name = QLabel("—")
        self._app_name.setFont(T.font(13, bold=True))
        self._app_name.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_T}")
        self._app_card.body.addWidget(self._app_name)
        self._app_title = QLabel("")
        self._app_title.setFont(T.font(10))
        self._app_title.setStyleSheet(f"color: {T.TEXT_MUTED}; {_T}")
        self._app_title.setWordWrap(True)
        self._app_title.setMaximumHeight(30)
        self._app_card.body.addWidget(self._app_title)
        info_row.addWidget(self._app_card, 1)

        self._resp_card = _InfoCard(
            "fa6s.robot", "Последний ответ",
            T.ACCENT_SUCCESS,
        )
        self._resp_text = QLabel("—")
        self._resp_text.setFont(T.font(12))
        self._resp_text.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_T}")
        self._resp_text.setWordWrap(True)
        self._resp_text.setMaximumHeight(40)
        self._resp_card.body.addWidget(self._resp_text)
        self._resp_time = QLabel("")
        self._resp_time.setFont(T.font(10))
        self._resp_time.setStyleSheet(f"color: {T.TEXT_MUTED}; {_T}")
        self._resp_card.body.addWidget(self._resp_time)
        info_row.addWidget(self._resp_card, 1)

        self._timer_card = _InfoCard(
            "fa6s.stopwatch", "Таймеры",
            T.ACCENT_WARNING,
        )
        self._timer_empty = QLabel("Нет активных")
        self._timer_empty.setFont(T.font(11))
        self._timer_empty.setStyleSheet(f"color: {T.TEXT_MUTED}; {_T}")
        self._timer_card.body.addWidget(self._timer_empty)
        self._timer_list_widget = QWidget()
        self._timer_list_widget.setStyleSheet(_T)
        self._timer_list = QVBoxLayout(self._timer_list_widget)
        self._timer_list.setContentsMargins(0, 0, 0, 0)
        self._timer_list.setSpacing(2)
        self._timer_list_widget.hide()
        self._timer_card.body.addWidget(self._timer_list_widget)
        info_row.addWidget(self._timer_card, 1)

        wrap_info = QWidget()
        wrap_info.setStyleSheet(_T)
        wrap_info.setLayout(info_row)
        self.page_layout.addWidget(wrap_info)

        # ══ Проактивные предложения ══
        self._suggestion_container = QWidget()
        self._suggestion_container.setStyleSheet(_T)
        self._suggestion_layout = QVBoxLayout(self._suggestion_container)
        self._suggestion_layout.setContentsMargins(0, 0, 0, 0)
        self._suggestion_layout.setSpacing(6)
        self.page_layout.addWidget(self._suggestion_container)

        self._suggestion_poll = QTimer(self)
        self._suggestion_poll.setInterval(30000)
        self._suggestion_poll.timeout.connect(self._poll_suggestions)

        # ══ Quick Actions ══
        self.page_layout.addWidget(SectionHeader("Быстрые действия"))

        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)
        for icon, label, color, cb in [
            ("fa6s.clock",            "Таймер",  T.ACCENT_WARNING,   self._quick_timer),
            ("fa6s.note-sticky",      "Заметка", T.ACCENT_SUCCESS,   self._quick_note),
            ("fa6s.magnifying-glass", "Поиск",   T.ACCENT_PRIMARY,   self._quick_search),
        ]:
            btn = GhostButton(label, icon, color)
            btn.setFixedHeight(42)
            btn.clicked.connect(cb)
            actions_row.addWidget(btn)

        wrap_actions = QWidget()
        wrap_actions.setStyleSheet(_T)
        wrap_actions.setLayout(actions_row)
        self.page_layout.addWidget(wrap_actions)

        # ══ Command Feed ══
        self.page_layout.addWidget(SectionHeader("Последние команды"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        self._history_widget = QWidget()
        self._history_widget.setStyleSheet("background: transparent;")
        self._history_layout = QVBoxLayout(self._history_widget)
        self._history_layout.setAlignment(Qt.AlignTop)
        self._history_layout.setSpacing(4)
        self._history_layout.setContentsMargins(0, 0, 0, 0)

        self._placeholder = QLabel("Здесь будут ваши команды")
        self._placeholder.setFont(T.font(12))
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color: {T.TEXT_MUTED}; padding: 40px; {_T}"
        )
        self._history_layout.addWidget(self._placeholder)

        scroll.setWidget(self._history_widget)
        self.page_layout.addWidget(scroll, 1)

        # ══ Uptime timer ══
        self._uptime_timer = QTimer(self)
        self._uptime_timer.setInterval(1000)
        self._uptime_timer.timeout.connect(self._tick_uptime)

    # ══════════════════════════════════════
    #  Public API
    # ══════════════════════════════════════

    def update_state(self, state: str, text: str = ""):
        state_map = {
            "connected":  "Готов к работе",
            "waiting":    "Ожидаю ключевое слово...",
            "listening":  "Слушаю вас...",
            "processing": f"Выполняю: {text}" if text else "Выполняю...",
            "speaking":   "Отвечаю...",
        }
        self.state_label.setText(state_map.get(state, state))
        self.vis.set_state(state)

    def on_connected(self):
        self._connected_time = _time.time()
        self._uptime_timer.start()
        self._suggestion_poll.start()
        self._lbl_status.setText("Подключено")
        self._lbl_status.setStyleSheet(
            f"color: {T.ACCENT_SUCCESS}; font-weight: 600; {_T}"
        )
        self.update_state("waiting")

        if self.ipc:
            self.ipc.request(
                "get_recent_commands", {"limit": 15},
                callback=self._on_recent_commands,
            )
            self._poll_suggestions()

    def on_disconnected(self):
        self._uptime_timer.stop()
        self._suggestion_poll.stop()
        self._connected_time = None
        self._lbl_status.setText("Отключено")
        self._lbl_status.setStyleSheet(
            f"color: {T.ACCENT_DANGER}; font-weight: 600; {_T}"
        )
        self._lbl_uptime.setText("—")
        self.update_state("connected")

    def set_last_response(self, text: str, timestamp: float = None):
        if not text or not text.strip():
            return
        display = text[:120] + "…" if len(text) > 120 else text
        self._resp_text.setText(f"«{display}»")
        if timestamp:
            try:
                ts = datetime.fromtimestamp(timestamp).strftime("%H:%M")
                self._resp_time.setText(ts)
            except Exception:
                pass

    def add_command_entry(self, text: str, success: bool,
                          source: str, timestamp: float):
        if self._placeholder.isVisible():
            self._placeholder.hide()

        if self._last_cmd_ts > 0:
            gap = timestamp - self._last_cmd_ts
            if gap > 300:
                label = _time_group_label(self._last_cmd_ts)
                self._history_layout.insertWidget(0, _TimeSeparator(label))

        self._last_cmd_ts = timestamp

        card = CommandHistoryCard(text, success, source, timestamp)
        self._history_layout.insertWidget(0, card)

        from core.gui.animations import fade_in
        fade_in(card, duration=200)

        self._cmd_count += 1
        self._lbl_commands.setText(str(self._cmd_count))

        while self._history_layout.count() > self.MAX_HISTORY + 10:
            item = self._history_layout.takeAt(
                self._history_layout.count() - 1,
            )
            w = item.widget()
            if w and w is not self._placeholder:
                w.deleteLater()

    # ══════════════════════════════════════
    #  Push handlers
    # ══════════════════════════════════════

    def on_timers_push(self, timers: list[dict]):
        if not timers:
            self._timer_empty.show()
            self._timer_list_widget.hide()
            return

        self._timer_empty.hide()
        self._timer_list_widget.show()

        while self._timer_list.count():
            item = self._timer_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for t in timers:
            row = QWidget()
            row.setStyleSheet(_T)
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)

            lbl = QLabel(t.get("label", "?"))
            lbl.setFont(T.font(11))
            lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_T}")
            hl.addWidget(lbl)
            hl.addStretch()

            remaining = t.get("remaining_sec", 0)
            time_lbl = QLabel(_fmt_remaining(remaining))
            time_lbl.setFont(T.font(12, bold=True))
            if remaining > 60:
                c = T.ACCENT_SUCCESS
            elif remaining > 10:
                c = T.ACCENT_WARNING
            else:
                c = T.ACCENT_DANGER
            time_lbl.setStyleSheet(f"color: {c}; {_T}")
            hl.addWidget(time_lbl)

            self._timer_list.addWidget(row)

    def on_active_app_push(self, app: str, title: str):
        if app:
            self._app_name.setText(app)
            if title and title != app:
                display = title[:58] + "…" if len(title) > 58 else title
                self._app_title.setText(display)
            else:
                self._app_title.setText("")
        else:
            self._app_name.setText("—")
            self._app_title.setText("")

    # ══════════════════════════════════════
    #  Suggestions
    # ══════════════════════════════════════

    def _poll_suggestions(self):
        if not self.ipc or not self.ipc.is_connected:
            return
        self.ipc.request("get_suggestion", {}, callback=self._on_suggestion)

    def _on_suggestion(self, payload: dict):
        text = payload.get("text", "")
        if not text:
            return

        while self._suggestion_layout.count():
            item = self._suggestion_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_CARD};
                border: 1px solid {T.ACCENT_WARNING};
                border-left: 3px solid {T.ACCENT_WARNING};
                border-radius: {T.RADIUS_MD}px;
            }}
        """)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(10, 6, 10, 6)
        cl.setSpacing(8)

        ic = QLabel()
        ic.setPixmap(get_icon("fa6s.lightbulb", T.ACCENT_WARNING).pixmap(14, 14))
        ic.setFixedWidth(18)
        ic.setStyleSheet(_T)
        cl.addWidget(ic)

        lbl = QLabel(text)
        lbl.setFont(T.font(11))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_T}")
        cl.addWidget(lbl, 1)

        yes = GhostButton("Да", "fa6s.check", T.ACCENT_SUCCESS)
        yes.setFixedHeight(26)
        yes.clicked.connect(self._accept_suggestion)
        cl.addWidget(yes)

        no = GhostButton("Нет", "fa6s.xmark", T.ACCENT_DANGER)
        no.setFixedHeight(26)
        no.clicked.connect(self._reject_suggestion)
        cl.addWidget(no)

        self._suggestion_layout.addWidget(card)

    def _accept_suggestion(self):
        self._clear_suggestions()
        self._exec("да")

    def _reject_suggestion(self):
        self._clear_suggestions()

    def _clear_suggestions(self):
        while self._suggestion_layout.count():
            item = self._suggestion_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_recent_commands(self, payload: dict):
        commands = payload.get("commands", [])
        if not commands:
            return
        for cmd in reversed(commands):
            text = cmd.get("text", "")
            if not text:
                continue
            self.add_command_entry(
                text, cmd.get("success", True),
                cmd.get("source", "voice"),
                cmd.get("timestamp", 0),
            )

    # ══════════════════════════════════════
    #  Internal
    # ══════════════════════════════════════

    @staticmethod
    def _metric(card: Card, icon_name: str,
                label_text: str, value_text: str) -> QLabel:
        row = QWidget()
        row.setStyleSheet(_T)
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 2, 0, 2)
        hl.setSpacing(8)

        ic = QLabel()
        ic.setPixmap(get_icon(icon_name, T.TEXT_MUTED).pixmap(14, 14))
        ic.setFixedWidth(18)
        ic.setStyleSheet(_T)

        name = QLabel(label_text)
        name.setFont(T.font(12))
        name.setStyleSheet(f"color: {T.TEXT_SECONDARY}; {_T}")

        val = QLabel(value_text)
        val.setFont(T.font(12, bold=True))
        val.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_T}")

        hl.addWidget(ic)
        hl.addWidget(name)
        hl.addStretch()
        hl.addWidget(val)

        card.add_widget(row)
        return val

    def _tick_uptime(self):
        if not self._connected_time:
            return
        elapsed = int(_time.time() - self._connected_time)
        m, s = divmod(elapsed, 60)
        h, m = divmod(m, 60)
        self._lbl_uptime.setText(
            f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        )

    def _exec(self, cmd: str):
        if not self.ipc or not self.ipc.is_connected:
            return
        self.ipc.request(
            "execute_command", {"text": cmd},
            callback=lambda p: self.add_command_entry(
                cmd, p.get("success", False), "gui", _time.time(),
            ),
        )

    def _quick_timer(self):
        minutes, ok = QInputDialog.getInt(
            self, "Таймер", "На сколько минут?", 5, 1, 120,
        )
        if ok:
            self._exec(f"таймер на {minutes} минут")

    def _quick_note(self):
        text, ok = QInputDialog.getText(self, "Заметка", "Что запомнить?")
        if ok and text.strip():
            self._exec(f"запомни {text.strip()}")

    def _quick_search(self):
        self.navigate_requested.emit(PAGE_KNOWLEDGE)