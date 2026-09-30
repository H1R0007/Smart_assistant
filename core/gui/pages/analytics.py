# core/gui/pages/analytics.py

"""
Analytics — статистика активности и привычки.
Кастомные бар-чарты, карточки паттернов, fallback при отсутствии данных.
"""

import math

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QGridLayout,
)
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QFont, QPen, QLinearGradient

from core.gui.pages.base_page import BasePage
from core.gui.theme import Theme, get_icon
from core.gui.components import Card, SectionHeader, GhostButton

T = Theme
_NS = "background: transparent; border: none;"

def _safe_float(val, default=0.0):
    """Безопасная конвертация: '85%' → 85.0, '+5%' → 5.0."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        cleaned = val.replace('%', '').replace('+', '').strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return default
    return default

# ═══════════════════════════════════════════
#  HorizontalBar — один столбец в чарте
# ═══════════════════════════════════════════

class _BarChart(QWidget):
    """Горизонтальный бар-чарт: имя | бар | значение."""

    BAR_HEIGHT = 22
    SPACING = 6
    LABEL_W = 120
    VALUE_W = 60
    MIN_BAR_W = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[tuple[str, float]] = []
        self._max_val = 1.0
        self._color_start = QColor(T.ACCENT_PRIMARY)
        self._color_end = QColor(T.ACCENT_SECONDARY)
        self.setMinimumHeight(40)

    def set_data(self, data: list[tuple[str, float]],
                 color_start: str = None, color_end: str = None):
        """data: [(label, value), ...]"""
        self._data = data
        self._max_val = max((v for _, v in data), default=1) or 1
        if color_start:
            self._color_start = QColor(color_start)
        if color_end:
            self._color_end = QColor(color_end)
        h = max(40, len(data) * (self.BAR_HEIGHT + self.SPACING) + 10)
        self.setFixedHeight(h)
        self.update()

    def paintEvent(self, _ev):
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        bar_area = w - self.LABEL_W - self.VALUE_W - 20

        for i, (label, value) in enumerate(self._data):
            y = i * (self.BAR_HEIGHT + self.SPACING) + 4

            # Label
            p.setPen(QColor(T.TEXT_SECONDARY))
            p.setFont(T.font(11))
            label_display = label
            if len(label_display) > 16:
                label_display = label_display[:14] + "…"
            p.drawText(
                QRectF(0, y, self.LABEL_W, self.BAR_HEIGHT),
                Qt.AlignRight | Qt.AlignVCenter,
                label_display,
            )

            # Bar
            ratio = value / self._max_val if self._max_val else 0
            bar_w = max(4, int(bar_area * ratio))
            bar_x = self.LABEL_W + 10

            grad = QLinearGradient(bar_x, 0, bar_x + bar_w, 0)
            grad.setColorAt(0, self._color_start)
            grad.setColorAt(1, self._color_end)

            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(
                bar_x, y + 2, bar_w, self.BAR_HEIGHT - 4, 4, 4,
            )

            # Value
            p.setPen(QColor(T.TEXT_PRIMARY))
            p.setFont(T.font(11, bold=True))
            val_text = self._format_value(value)
            p.drawText(
                QRectF(bar_x + bar_area + 10, y,
                       self.VALUE_W, self.BAR_HEIGHT),
                Qt.AlignLeft | Qt.AlignVCenter,
                val_text,
            )

        p.end()

    @staticmethod
    def _format_value(v: float) -> str:
        if v >= 60:
            h = v / 60
            return f"{h:.1f} ч"
        return f"{v:.0f} мин"


# ═══════════════════════════════════════════
#  MetricCard — маленькая карточка с числом
# ═══════════════════════════════════════════

class _MetricCard(QFrame):
    def __init__(self, icon_name: str, label: str, value: str,
                 color: str = T.ACCENT_PRIMARY, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_CARD};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_MD}px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        ic = QLabel()
        ic.setPixmap(get_icon(icon_name, color).pixmap(20, 20))
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(_NS)
        layout.addWidget(ic)

        val = QLabel(value)
        val.setFont(T.font(18, bold=True))
        val.setAlignment(Qt.AlignCenter)
        val.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_NS}")
        layout.addWidget(val)
        self._val = val

        lbl = QLabel(label)
        lbl.setFont(T.font(10))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
        layout.addWidget(lbl)

    def set_value(self, v: str):
        self._val.setText(v)


# ═══════════════════════════════════════════
#  PatternCard — временной или последовательный
# ═══════════════════════════════════════════

class _PatternCard(QFrame):
    def __init__(self, icon_name: str, title: str, description: str,
                 color: str, parent=None):
        super().__init__(parent)
        c = QColor(color)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_CARD};
                border: 1px solid {T.BORDER};
                border-left: 3px solid {color};
                border-radius: {T.RADIUS_SM}px;
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        ic = QLabel()
        ic.setPixmap(get_icon(icon_name, color).pixmap(16, 16))
        ic.setFixedWidth(20)
        ic.setStyleSheet(_NS)
        layout.addWidget(ic)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        t = QLabel(title)
        t.setFont(T.font(12, bold=True))
        t.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_NS}")
        text_col.addWidget(t)

        d = QLabel(description)
        d.setFont(T.font(11))
        d.setWordWrap(True)
        d.setStyleSheet(f"color: {T.TEXT_SECONDARY}; {_NS}")
        text_col.addWidget(d)

        layout.addLayout(text_col, 1)


# ═══════════════════════════════════════════
#  AnalyticsPage
# ═══════════════════════════════════════════

class AnalyticsPage(BasePage):
    title = "Аналитика"
    icon = "fa6s.chart-line"

    def setup_ui(self):
        # ── Header ──
        refresh_btn = GhostButton("Обновить", "fa6s.arrows-rotate")
        refresh_btn.clicked.connect(self._load)
        self.page_layout.addWidget(SectionHeader("Аналитика", refresh_btn))

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

        self._state_label = QLabel("Подключение к ядру...")
        self._state_label.setFont(T.font(13))
        self._state_label.setAlignment(Qt.AlignCenter)
        self._state_label.setStyleSheet(
            f"color: {T.TEXT_MUTED}; padding: 60px; {_NS}"
        )
        self._body.addWidget(self._state_label)

        scroll.setWidget(self._container)
        self.page_layout.addWidget(scroll, 1)

        # Auto-load
        if self.ipc:
            self.ipc.connected.connect(self._load)
            QTimer.singleShot(400, self._try_load)

    # ── Loading ──

    def _try_load(self):
        if self.ipc and self.ipc.is_connected:
            self._load()

    def _load(self):
        if not self.ipc or not self.ipc.is_connected:
            return
        self._show_state("Загрузка данных...")
        self.ipc.request("get_patterns", {}, callback=self._on_data)

    def _on_data(self, payload: dict):
        if not payload.get("success"):
            self._show_state(
                f"Ошибка: {payload.get('error', '?')}"
            )
            return

        # Защита от нечисловых значений
        raw_stats = payload.get("stats_7d", [])
        stats_7d = []
        for item in raw_stats:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                stats_7d.append((str(item[0]), _safe_float(item[1])))

        patterns = payload.get("patterns", {})
        temporal = patterns.get("temporal", [])

        raw_sequences = patterns.get("sequences", [])
        sequences = []
        for s in raw_sequences:
            if isinstance(s, dict):
                sequences.append({
                    "pattern": str(s.get("pattern", "?")),
                    "confidence": _safe_float(s.get("confidence", 0)),
                })

        has_data = bool(stats_7d) or bool(temporal) or bool(sequences)

        if not has_data:
            self._show_empty()
            return

        self._clear_body()
        self._build_dashboard(stats_7d, temporal, sequences)

    # ── Build ──

    def _build_dashboard(self, stats_7d, temporal, sequences):
        # ══ Метрики ══
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(12)

        total_min = sum(v for _, v in stats_7d) if stats_7d else 0
        total_h = total_min / 60

        apps_count = len(stats_7d) if stats_7d else 0
        pattern_count = len(temporal) + len(sequences)

        m1 = _MetricCard(
            "fa6s.clock", "Активность (7д)",
            f"{total_h:.1f} ч" if total_h >= 1 else f"{total_min:.0f} мин",
            T.ACCENT_PRIMARY,
        )
        m2 = _MetricCard(
            "fa6s.window-restore", "Приложений",
            str(apps_count), T.ACCENT_SECONDARY,
        )
        m3 = _MetricCard(
            "fa6s.brain", "Паттернов",
            str(pattern_count), T.ACCENT_SUCCESS,
        )
        metrics_row.addWidget(m1, 1)
        metrics_row.addWidget(m2, 1)
        metrics_row.addWidget(m3, 1)

        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")
        wrap.setLayout(metrics_row)
        self._body.addWidget(wrap)

        # ══ Топ приложений ══
        if stats_7d:
            apps_card = Card("Топ приложений (7 дней)",
                             accent_color=T.ACCENT_SECONDARY)
            chart = _BarChart()
            chart.set_data(
                stats_7d[:10],
                color_start=T.ACCENT_PRIMARY,
                color_end=T.ACCENT_SECONDARY,
            )
            apps_card.add_widget(chart)
            self._body.addWidget(apps_card)

        # ══ Временные паттерны ══
        if temporal:
            self._body.addWidget(self._section_label("Временные привычки"))
            for p in temporal[:8]:
                target = p.get("target", "?")
                when = p.get("when", "")
                day = p.get("day_type", "")
                day_text = " (будни)" if day == "weekday" else \
                           " (выходные)" if day == "weekend" else ""
                card = _PatternCard(
                    "fa6s.clock", target,
                    f"{when}{day_text}",
                    T.ACCENT_WARNING,
                )
                self._body.addWidget(card)

        # ══ Последовательности ══
        if sequences:
            self._body.addWidget(self._section_label("Последовательности"))
            for p in sequences[:8]:
                pattern = p.get("pattern", "?")
                conf = p.get("confidence", 0)
                card = _PatternCard(
                    "fa6s.arrow-right", pattern,
                    f"Уверенность: {conf:.0%}",
                    T.ACCENT_SUCCESS,
                )
                self._body.addWidget(card)
        
        # Собираем все виджеты для анимации (кроме Card — у них тень)
        animated = []
        for i in range(self._body.count()):
            w = self._body.itemAt(i).widget()
            if w and not isinstance(w, Card):
                animated.append(w)

        from core.gui.animations import staggered_fade_in
        staggered_fade_in(animated, delay_step=60)

        self._body.addStretch()

    # ── Helpers ──

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setFont(T.font(14, bold=True))
        lbl.setStyleSheet(f"color: {T.TEXT_PRIMARY}; padding-top: 8px; {_NS}")
        return lbl

    def _show_state(self, text: str):
        self._clear_body()
        lbl = QLabel(text)
        lbl.setFont(T.font(13))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color: {T.TEXT_MUTED}; padding: 60px; {_NS}"
        )
        self._body.addWidget(lbl)

    def _show_empty(self):
        self._clear_body()

        card = Card(accent_color=T.ACCENT_WARNING)

        ic = QLabel()
        ic.setPixmap(
            get_icon("fa6s.chart-line", T.ACCENT_WARNING).pixmap(40, 40)
        )
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(_NS)
        card.add_widget(ic)

        title = QLabel("Недостаточно данных")
        title.setFont(T.font(16, bold=True))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_NS}")
        card.add_widget(title)

        desc = QLabel(
            "Ассистент начнёт собирать статистику\n"
            "после нескольких дней активного использования.\n\n"
            "Трекер активности работает в фоне —\n"
            "просто продолжайте работать как обычно."
        )
        desc.setFont(T.font(13))
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {T.TEXT_SECONDARY}; {_NS}")
        card.add_widget(desc)

        self._body.addStretch()
        self._body.addWidget(card)
        self._body.addStretch()

    def _clear_body(self):
        while self._body.count():
            item = self._body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()