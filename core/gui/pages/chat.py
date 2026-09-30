# core/gui/pages/chat.py

"""
Chat — персистентный диалог с ассистентом.
Markdown, поиск, экспорт, история между сессиями.
"""

import re
import html
import time as _time
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QMenu, QApplication,
    QFileDialog, QLineEdit,
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut

from core.gui.pages.base_page import BasePage
from core.gui.theme import Theme, get_icon
from core.gui.components import (
    SectionHeader, GhostButton, IconButton,
)

T = Theme
_NS = "background: transparent; border: none;"

# ═══════════════════════════════════════════
#  Markdown → HTML
# ═══════════════════════════════════════════

_URL_RE = re.compile(r'(https?://[^\s<>\[\]()\"\']+)', re.IGNORECASE)
_CODE_BLOCK_RE = re.compile(r'```(\w*)\n?(.*?)```', re.DOTALL)
_INLINE_CODE_RE = re.compile(r'`([^`\n]+)`')
_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
_ITALIC_RE = re.compile(r'(?<!\*)\*([^*\n]+?)\*(?!\*)')
_HEADER_RE = re.compile(r'^(#{1,3})\s+(.+)$', re.MULTILINE)
_UL_RE = re.compile(r'^[\s]*[-•]\s+(.+)$', re.MULTILINE)
_OL_RE = re.compile(r'^[\s]*\d+\.\s+(.+)$', re.MULTILINE)


def _format_message_html(text: str) -> str:
    result = text

    code_blocks = []
    def _save_cb(m):
        idx = len(code_blocks)
        code_blocks.append((f"\x00CB{idx}\x00", html.escape(m.group(2).strip())))
        return code_blocks[-1][0]
    result = _CODE_BLOCK_RE.sub(_save_cb, result)

    inline_codes = []
    def _save_ic(m):
        idx = len(inline_codes)
        inline_codes.append((f"\x00IC{idx}\x00", html.escape(m.group(1))))
        return inline_codes[-1][0]
    result = _INLINE_CODE_RE.sub(_save_ic, result)

    result = html.escape(result)
    result = _BOLD_RE.sub(rf'<b style="color:{T.TEXT_PRIMARY};">\1</b>', result)
    result = _ITALIC_RE.sub(r'<i>\1</i>', result)

    def _heading(m):
        sz = {1: 16, 2: 14, 3: 13}.get(len(m.group(1)), 13)
        return f'<div style="font-size:{sz}px;font-weight:700;color:{T.TEXT_PRIMARY};margin:4px 0 2px 0;">{m.group(2)}</div>'
    result = _HEADER_RE.sub(_heading, result)

    result = _UL_RE.sub(
        lambda m: f'<div style="margin-left:12px;padding:1px 0;"><span style="color:{T.ACCENT_PRIMARY};">●</span> {m.group(1)}</div>',
        result,
    )

    _c = [0]
    def _ol(m):
        _c[0] += 1
        return f'<div style="margin-left:12px;padding:1px 0;"><span style="color:{T.ACCENT_SECONDARY};font-weight:600;">{_c[0]}.</span> {m.group(1)}</div>'
    _c[0] = 0
    result = _OL_RE.sub(_ol, result)

    for ph, code in code_blocks:
        result = result.replace(
            html.escape(ph),
            f'<div style="background-color:{T.BG_DARKEST};border:1px solid {T.BORDER};border-radius:4px;padding:8px 10px;margin:4px 0;font-family:\'{T.FONT_MONO}\';font-size:11px;color:{T.ACCENT_SUCCESS};"><pre style="margin:0;white-space:pre-wrap;">{code}</pre></div>',
        )
    for ph, code in inline_codes:
        result = result.replace(
            html.escape(ph),
            f'<code style="background-color:{T.BG_DARKEST};border:1px solid {T.BORDER};border-radius:3px;padding:1px 5px;font-family:\'{T.FONT_MONO}\';font-size:12px;color:{T.ACCENT_SUCCESS};">{code}</code>',
        )

    def _url_link(m):
        url = m.group(1)
        short = url if len(url) <= 55 else url[:53] + "…"
        return f'<a href="{url}" style="color:{T.ACCENT_SECONDARY};text-decoration:none;">{short}</a>'
    result = _URL_RE.sub(_url_link, result)
    result = result.replace('\n', '<br>')
    return result


def _format_time(timestamp=None):
    if not timestamp:
        timestamp = _time.time()
    try:
        dt = datetime.fromtimestamp(timestamp)
        now = datetime.now()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return ""


def _decline_messages(n):
    r = abs(n) % 100
    o = abs(n) % 10
    if 11 <= r <= 19: w = "сообщений"
    elif o == 1: w = "сообщение"
    elif 2 <= o <= 4: w = "сообщения"
    else: w = "сообщений"
    return f"{n} {w}"


class ThinkingIndicator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumWidth(100)
        self.setStyleSheet(f"QFrame{{background-color:{T.BG_CARD};border:1px solid {T.BORDER};border-left:3px solid {T.ACCENT_SECONDARY};border-radius:{T.RADIUS_MD}px;}}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        self._label = QLabel("●  ○  ○")
        self._label.setFont(T.font(12))
        self._label.setStyleSheet(f"color:{T.TEXT_MUTED};{_NS}")
        layout.addWidget(self._label)
        self._step = 0
        self._anim = QTimer(self)
        self._anim.setInterval(400)
        self._anim.timeout.connect(self._tick)
        self._anim.start()
    _FRAMES = ["●  ○  ○", "○  ●  ○", "○  ○  ●"]
    def _tick(self):
        self._step = (self._step + 1) % 3
        self._label.setText(self._FRAMES[self._step])


class ChatBubble(QFrame):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_SYSTEM = "system"

    def __init__(self, text, role, source="", timestamp=None, parent=None):
        super().__init__(parent)
        self._text = text
        self._role = role
        self._source = source
        self._timestamp = timestamp or _time.time()
        ts = _format_time(self._timestamp)
        if role == self.ROLE_SYSTEM:
            self._build_system(text)
        elif role == self.ROLE_USER:
            self._build_user(text, source, ts)
        else:
            self._build_assistant(text, ts)

    @property
    def plain_text(self): return self._text
    @property
    def role(self): return self._role

    def set_search_visible(self, v):
        self.setVisible(v)
        p = self.parent()
        if p: p.setVisible(v)

    def export_line(self):
        ts = datetime.fromtimestamp(self._timestamp).strftime("%H:%M:%S")
        if self._role == self.ROLE_USER:
            pfx = f"[{ts}] Вы" + (" (голос)" if self._source == "voice" else "")
            return f"{pfx}: {self._text}"
        elif self._role == self.ROLE_ASSISTANT:
            return f"[{ts}] Пятница: {self._text}"
        return f"[{ts}] --- {self._text} ---"

    def _build_user(self, text, source, ts):
        self.setMaximumWidth(600)
        self.setStyleSheet(f"QFrame{{background-color:rgba(124,111,247,0.10);border:1px solid rgba(124,111,247,0.18);border-right:3px solid {T.ACCENT_PRIMARY};border-radius:{T.RADIUS_MD}px;}}")
        layout = QVBoxLayout(self); layout.setContentsMargins(12,8,12,8); layout.setSpacing(4)
        hdr = QHBoxLayout(); hdr.setSpacing(6)
        ic_name = "fa6s.microphone" if source == "voice" else "fa6s.keyboard"
        ic_color = T.ACCENT_SECONDARY if source == "voice" else T.ACCENT_PRIMARY
        ic = QLabel(); ic.setPixmap(get_icon(ic_name, ic_color).pixmap(12,12)); ic.setFixedWidth(16); ic.setStyleSheet(_NS)
        nm = QLabel("Вы (голос)" if source == "voice" else "Вы"); nm.setFont(T.font(11,True)); nm.setStyleSheet(f"color:{T.ACCENT_PRIMARY};{_NS}")
        tl = QLabel(ts); tl.setFont(T.font(10)); tl.setStyleSheet(f"color:{T.TEXT_MUTED};{_NS}")
        hdr.addWidget(ic); hdr.addWidget(nm); hdr.addStretch(); hdr.addWidget(tl); layout.addLayout(hdr)
        b = QLabel(text); b.setFont(T.font(13)); b.setWordWrap(True); b.setTextInteractionFlags(Qt.TextSelectableByMouse); b.setStyleSheet(f"color:{T.TEXT_PRIMARY};{_NS}"); layout.addWidget(b)

    def _build_assistant(self, text, ts):
        self.setMaximumWidth(600)
        self.setStyleSheet(f"QFrame{{background-color:{T.BG_CARD};border:1px solid {T.BORDER};border-left:3px solid {T.ACCENT_SECONDARY};border-radius:{T.RADIUS_MD}px;}}")
        layout = QVBoxLayout(self); layout.setContentsMargins(12,8,12,8); layout.setSpacing(4)
        hdr = QHBoxLayout(); hdr.setSpacing(6)
        ic = QLabel(); ic.setPixmap(get_icon("fa6s.robot", T.ACCENT_SECONDARY).pixmap(12,12)); ic.setFixedWidth(16); ic.setStyleSheet(_NS)
        nm = QLabel("Пятница"); nm.setFont(T.font(11,True)); nm.setStyleSheet(f"color:{T.ACCENT_SECONDARY};{_NS}")
        tl = QLabel(ts); tl.setFont(T.font(10)); tl.setStyleSheet(f"color:{T.TEXT_MUTED};{_NS}")
        hdr.addWidget(ic); hdr.addWidget(nm); hdr.addStretch(); hdr.addWidget(tl); layout.addLayout(hdr)
        b = QLabel(); b.setFont(T.font(13)); b.setWordWrap(True)
        b.setTextFormat(Qt.RichText); b.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        b.setOpenExternalLinks(True); b.setText(_format_message_html(text)); b.setStyleSheet(f"color:{T.TEXT_PRIMARY};{_NS}")
        layout.addWidget(b)

    def _build_system(self, text):
        self.setStyleSheet("background:transparent;border:none;")
        layout = QHBoxLayout(self); layout.setContentsMargins(0,6,0,6)
        ll = QFrame(); ll.setFixedHeight(1); ll.setStyleSheet(f"background-color:{T.BORDER};")
        lb = QLabel(f"  {text}  "); lb.setFont(T.font(11)); lb.setStyleSheet(f"color:{T.TEXT_MUTED};font-style:italic;{_NS}")
        lr = QFrame(); lr.setFixedHeight(1); lr.setStyleSheet(f"background-color:{T.BORDER};")
        layout.addWidget(ll,1); layout.addWidget(lb); layout.addWidget(lr,1)

    def contextMenuEvent(self, event):
        if self._role == self.ROLE_SYSTEM: return
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu{{background-color:{T.BG_CARD};color:{T.TEXT_PRIMARY};border:1px solid {T.BORDER};border-radius:{T.RADIUS_SM}px;padding:4px;}}QMenu::item{{padding:6px 20px;border-radius:4px;}}QMenu::item:selected{{background-color:{T.BG_HOVER};}}")
        ca = menu.addAction(get_icon("fa6s.copy", T.TEXT_SECONDARY), "Копировать")
        a = menu.exec(event.globalPos())
        if a == ca: QApplication.clipboard().setText(self._text)


class _SearchOverlay(QFrame):
    search_changed = Signal(str)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setStyleSheet(f"QFrame{{background-color:{T.BG_CARD};border:1px solid {T.BORDER};border-radius:{T.RADIUS_SM}px;}}")
        layout = QHBoxLayout(self); layout.setContentsMargins(10,4,10,4); layout.setSpacing(8)
        ic = QLabel(); ic.setPixmap(get_icon("fa6s.magnifying-glass", T.TEXT_MUTED).pixmap(14,14)); ic.setFixedWidth(18); ic.setStyleSheet(_NS); layout.addWidget(ic)
        self._input = QLineEdit(); self._input.setPlaceholderText("Поиск по чату..."); self._input.setFont(T.font(12)); self._input.setFixedHeight(28)
        self._input.setStyleSheet(f"QLineEdit{{background-color:{T.BG_INPUT};color:{T.TEXT_PRIMARY};border:1px solid {T.BORDER};border-radius:{T.RADIUS_SM}px;padding:0 8px;}}QLineEdit:focus{{border-color:{T.BORDER_FOCUS};}}")
        self._input.textChanged.connect(self.search_changed.emit); layout.addWidget(self._input, 1)
        self._count = QLabel(""); self._count.setFont(T.font(11)); self._count.setStyleSheet(f"color:{T.TEXT_MUTED};{_NS}"); self._count.setFixedWidth(80); layout.addWidget(self._count)
        cl = IconButton("fa6s.xmark", "Закрыть", size=26, color=T.TEXT_MUTED); cl.clicked.connect(self.closed.emit); layout.addWidget(cl)

    def focus_input(self): self._input.setFocus(); self._input.selectAll()
    def set_count(self, f, t):
        self._count.setText(f"{f} из {t}" if self._input.text().strip() else "")
    def clear_and_hide(self): self._input.clear(); self.hide()

    @property
    def query(self): return self._input.text().strip().lower()


class ChatPage(BasePage):
    title = "Диалог"
    icon = "fa6s.comments"
    MAX_MESSAGES = 200

    def setup_ui(self):
        self._msg_count = 0
        self._thinking_widget = None
        self._bubbles: list[ChatBubble] = []
        self._loading_history = False

        self._count_label = QLabel(_decline_messages(0))
        self._count_label.setFont(T.font(12))
        self._count_label.setStyleSheet(f"color:{T.TEXT_MUTED};")

        search_btn = IconButton("fa6s.magnifying-glass", "Поиск (Ctrl+F)", size=30, color=T.TEXT_SECONDARY)
        search_btn.clicked.connect(self._toggle_search)
        export_btn = GhostButton("Экспорт", "fa6s.file-export", T.TEXT_SECONDARY)
        export_btn.clicked.connect(self._export_chat)
        clear_btn = GhostButton("Очистить", "fa6s.trash", T.ACCENT_DANGER)
        clear_btn.clicked.connect(self._clear_chat)

        hr_w = QWidget(); hr_w.setStyleSheet(_NS)
        hr = QHBoxLayout(hr_w); hr.setContentsMargins(0,0,0,0); hr.setSpacing(8)
        hr.addWidget(self._count_label); hr.addWidget(search_btn); hr.addWidget(export_btn); hr.addWidget(clear_btn)
        self.page_layout.addWidget(SectionHeader("Диалог", hr_w))

        self._search = _SearchOverlay()
        self._search.search_changed.connect(self._on_search)
        self._search.closed.connect(self._close_search)
        self._search.hide()
        self.page_layout.addWidget(self._search)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        container = QWidget(); container.setStyleSheet("background:transparent;")
        self._messages = QVBoxLayout(container)
        self._messages.setAlignment(Qt.AlignTop); self._messages.setSpacing(8); self._messages.setContentsMargins(8,8,8,8)
        self._scroll.setWidget(container)
        self.page_layout.addWidget(self._scroll, 1)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self._toggle_search)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self._close_search)

        self._append_bubble(ChatBubble("Чат готов.", ChatBubble.ROLE_SYSTEM))

        # ── Персистентность: загрузка при подключении ──
        if self.ipc:
            self.ipc.connected.connect(self._load_history)

    # ══════════════════════════════════════
    #  Персистентность
    # ══════════════════════════════════════

    def _load_history(self):
        if not self.ipc or not self.ipc.is_connected:
            return
        self.ipc.request("get_chat_history", {"limit": 50}, callback=self._on_history_loaded)

    def _on_history_loaded(self, payload):
        messages = payload.get("messages", [])
        if not messages:
            return

        self._loading_history = True
        self.add_system_message("── Предыдущая сессия ──")

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            source = msg.get("source", "")
            ts = msg.get("timestamp", 0)

            if role == "user":
                self.add_user_message(content, source or "voice", ts)
            elif role == "assistant":
                self.add_assistant_message(content, ts)

        self.add_system_message("── Текущая сессия ──")
        self._loading_history = False

    def _persist_message(self, role, content, source="", timestamp=0):
        """Сохранить сообщение в БД (если не загружаем историю)."""
        if self._loading_history:
            return
        if not self.ipc or not self.ipc.is_connected:
            return
        self.ipc.send_command("save_chat_message", {
            "role": role, "content": content,
            "source": source, "timestamp": timestamp or _time.time(),
        })

    # ══════════════════════════════════════
    #  Public API
    # ══════════════════════════════════════

    def add_user_message(self, text, source="voice", timestamp=None):
        if not text or not text.strip(): return
        ts = timestamp or _time.time()
        b = ChatBubble(text, ChatBubble.ROLE_USER, source, ts)
        self._append_bubble(b, align_right=True)
        self._persist_message("user", text, source, ts)

    def add_assistant_message(self, text, timestamp=None):
        if not text or not text.strip(): return
        self.hide_thinking()
        ts = timestamp or _time.time()
        b = ChatBubble(text, ChatBubble.ROLE_ASSISTANT, timestamp=ts)
        self._append_bubble(b, align_right=False)
        # Ассистентские сообщения сохраняет ядро через _on_speaker_text

    def add_system_message(self, text):
        self._append_bubble(ChatBubble(text, ChatBubble.ROLE_SYSTEM))

    def show_thinking(self):
        if self._thinking_widget: return
        ind = ThinkingIndicator()
        row = self._align(ind, right=False)
        self._thinking_widget = row
        self._messages.addWidget(row)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def hide_thinking(self):
        if self._thinking_widget:
            self._messages.removeWidget(self._thinking_widget)
            self._thinking_widget.deleteLater()
            self._thinking_widget = None

    # ══════════════════════════════════════
    #  Search
    # ══════════════════════════════════════

    def _toggle_search(self):
        if self._search.isVisible(): self._close_search()
        else: self._search.show(); self._search.focus_input()

    def _close_search(self):
        self._search.clear_and_hide()
        for b in self._bubbles: b.set_search_visible(True)

    def _on_search(self, query):
        query = query.strip().lower()
        if not query:
            for b in self._bubbles: b.set_search_visible(True)
            self._search.set_count(0, 0)
            return
        found = 0
        searchable = [b for b in self._bubbles if b.role != ChatBubble.ROLE_SYSTEM]
        for b in self._bubbles:
            if b.role == ChatBubble.ROLE_SYSTEM: b.set_search_visible(True); continue
            match = query in b.plain_text.lower()
            b.set_search_visible(match)
            if match: found += 1
        self._search.set_count(found, len(searchable))

    # ══════════════════════════════════════
    #  Export
    # ══════════════════════════════════════

    def _export_chat(self):
        if not self._bubbles: return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт чата", f"chat_{ts}.txt", "Text (*.txt)")
        if not path: return
        try:
            lines = [f"=== Экспорт чата с Пятницей ===", f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}", f"Сообщений: {len(self._bubbles)}", "=" * 40, ""]
            for b in self._bubbles: lines.append(b.export_line())
            lines += ["", "=" * 40]
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        except Exception: pass

    # ══════════════════════════════════════
    #  Internal
    # ══════════════════════════════════════

    @staticmethod
    def _align(widget, right):
        row = QWidget(); row.setStyleSheet("background:transparent;")
        l = QHBoxLayout(row); l.setContentsMargins(0,0,0,0); l.setSpacing(0)
        if right: l.addStretch(1); l.addWidget(widget)
        else: l.addWidget(widget); l.addStretch(1)
        return row

    def _append_bubble(self, bubble, align_right=None):
        auto = self._should_auto_scroll()
        if align_right is not None:
            w = self._align(bubble, right=align_right)
            self._messages.addWidget(w)
        else:
            self._messages.addWidget(bubble)
        self._bubbles.append(bubble)
        self._msg_count += 1
        self._count_label.setText(_decline_messages(self._msg_count))
        while len(self._bubbles) > self.MAX_MESSAGES:
            old = self._bubbles.pop(0)
            p = old.parent()
            if p and p is not old: self._messages.removeWidget(p); p.deleteLater()
            else: self._messages.removeWidget(old); old.deleteLater()
            self._msg_count = max(0, self._msg_count - 1)
        if auto: QTimer.singleShot(50, self._scroll_to_bottom)

    def _should_auto_scroll(self):
        sb = self._scroll.verticalScrollBar()
        return sb.value() >= sb.maximum() - 60

    def _scroll_to_bottom(self):
        self._scroll.verticalScrollBar().setValue(self._scroll.verticalScrollBar().maximum())

    def _clear_chat(self):
        self.hide_thinking(); self._close_search()
        for b in self._bubbles:
            p = b.parent()
            if p and p is not b: p.deleteLater()
            else: b.deleteLater()
        self._bubbles.clear()
        while self._messages.count():
            item = self._messages.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._msg_count = 0
        self._count_label.setText(_decline_messages(0))
        if self.ipc and self.ipc.is_connected:
            self.ipc.send_command("clear_chat_history", {})
        self._append_bubble(ChatBubble("Чат очищен", ChatBubble.ROLE_SYSTEM))