# core/gui/onboarding.py

"""
Onboarding — пошаговый гайд при первом запуске.
Шаги переключаются кнопками Назад/Далее.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor

from core.gui.theme import Theme, get_icon
from core.gui.components import AccentButton, GhostButton

T = Theme
_NS = "background: transparent; border: none;"


_STEPS = [
    {
        "title": "Добро пожаловать!",
        "subtitle": "Пятница — ваш голосовой ассистент для Windows",
        "items": [
            ("fa6s.microphone", T.ACCENT_SECONDARY,
             "Голос",
             "Скажите ключевое слово и произнесите команду"),
            ("fa6s.keyboard", T.ACCENT_PRIMARY,
             "Текст",
             "Введите команду внизу экрана или нажмите Ctrl+K"),
            ("fa6s.robot", T.ACCENT_SUCCESS,
             "ИИ",
             "Задайте любой вопрос — ассистент ответит через LLM"),
        ],
    },
    {
        "title": "Управление",
        "subtitle": "Основные возможности",
        "items": [
            ("fa6s.rocket", T.ACCENT_PRIMARY,
             "Приложения",
             "«Открой блокнот», «Закрой браузер»"),
            ("fa6s.globe", T.ACCENT_SECONDARY,
             "Интернет",
             "«Найди котиков», «Открой ютуб»"),
            ("fa6s.volume-high", T.ACCENT_WARNING,
             "Медиа",
             "«Громче», «Тише», «Таймер на 5 минут»"),
            ("fa6s.window-restore", T.ACCENT_PRIMARY,
             "Окна",
             "«Сверни», «Разверни», «Окно влево»"),
        ],
    },
    {
        "title": "Персонализация",
        "subtitle": "Ассистент учится и запоминает",
        "items": [
            ("fa6s.brain", "#c07cf7",
             "База знаний",
             "«Запомни: пароль от Wi-Fi — 12345»"),
            ("fa6s.wand-magic-sparkles", T.ACCENT_WARNING,
             "Макросы",
             "Автоматизируйте последовательности действий"),
            ("fa6s.chart-line", T.ACCENT_SUCCESS,
             "Аналитика",
             "Анализ привычек и проактивные предложения"),
        ],
    },
    {
        "title": "Горячие клавиши",
        "subtitle": "Быстрый доступ к функциям",
        "items": [
            ("fa6s.magnifying-glass", T.ACCENT_PRIMARY,
             "Ctrl + K",
             "Палитра команд — поиск по всем возможностям"),
            ("fa6s.keyboard", T.TEXT_SECONDARY,
             "Ctrl + L",
             "Фокус на строку ввода команды"),
            ("fa6s.arrows-rotate", T.ACCENT_SUCCESS,
             "F5",
             "Обновить текущую страницу"),
            ("fa6s.bars", T.TEXT_SECONDARY,
             "Ctrl + B",
             "Свернуть / развернуть боковую панель"),
        ],
    },
]


class _ItemRow(QFrame):
    """Одна строка в шаге onboarding."""

    def __init__(self, icon_name, color, title, desc, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: rgba(255,255,255,0.03);
                border: 1px solid {T.BORDER};
                border-left: 3px solid {color};
                border-radius: {T.RADIUS_SM}px;
            }}
        """)
        self.setFixedHeight(52)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(10)

        ic = QLabel()
        ic.setPixmap(get_icon(icon_name, color).pixmap(18, 18))
        ic.setFixedWidth(22)
        ic.setStyleSheet(_NS)
        layout.addWidget(ic)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(0)

        t = QLabel(title)
        t.setFont(T.font(12, bold=True))
        t.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_NS}")
        text_col.addWidget(t)

        d = QLabel(desc)
        d.setFont(T.font(10))
        d.setStyleSheet(f"color: {T.TEXT_SECONDARY}; {_NS}")
        text_col.addWidget(d)

        layout.addLayout(text_col, 1)


class OnboardingOverlay(QWidget):
    """Пошаговый onboarding с навигацией."""

    finished = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.hide()
        self._current_step = 0

        # ── Панель ──
        self._panel = QFrame(self)
        self._panel.setStyleSheet(f"""
            QFrame {{
                background-color: {T.BG_CARD};
                border: 1px solid {T.BORDER};
                border-radius: {T.RADIUS_LG}px;
            }}
        """)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(28, 24, 28, 20)
        panel_layout.setSpacing(12)

        # ── Заголовок (динамический) ──
        self._title = QLabel()
        self._title.setFont(T.font(20, bold=True))
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet(f"color: {T.TEXT_PRIMARY}; {_NS}")
        panel_layout.addWidget(self._title)

        self._subtitle = QLabel()
        self._subtitle.setFont(T.font(12))
        self._subtitle.setAlignment(Qt.AlignCenter)
        self._subtitle.setStyleSheet(f"color: {T.TEXT_SECONDARY}; {_NS}")
        panel_layout.addWidget(self._subtitle)

        panel_layout.addSpacing(4)

        # ── Контент (динамический) ──
        self._content = QVBoxLayout()
        self._content.setSpacing(6)
        panel_layout.addLayout(self._content, 1)

        panel_layout.addSpacing(8)

        # ── Индикатор шагов ──
        self._dots_layout = QHBoxLayout()
        self._dots_layout.setAlignment(Qt.AlignCenter)
        self._dots_layout.setSpacing(8)
        self._dots: list[QLabel] = []
        for i in range(len(_STEPS)):
            dot = QLabel("●")
            dot.setFont(T.font(10))
            dot.setAlignment(Qt.AlignCenter)
            dot.setFixedWidth(16)
            dot.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")
            self._dots.append(dot)
            self._dots_layout.addWidget(dot)
        panel_layout.addLayout(self._dots_layout)

        panel_layout.addSpacing(4)

        # ── Кнопки навигации ──
        btns = QHBoxLayout()
        btns.setSpacing(10)

        self._skip_btn = GhostButton("Пропустить")
        self._skip_btn.clicked.connect(self._finish)
        btns.addWidget(self._skip_btn)

        btns.addStretch()

        self._back_btn = GhostButton("Назад", "fa6s.arrow-left")
        self._back_btn.clicked.connect(self._prev)
        btns.addWidget(self._back_btn)

        self._next_btn = AccentButton("Далее", "fa6s.arrow-right")
        self._next_btn.clicked.connect(self._next)
        btns.addWidget(self._next_btn)

        panel_layout.addLayout(btns)

    # ══════════════════════════════════════
    #  Public
    # ══════════════════════════════════════

    def show_onboarding(self):
        self._current_step = 0
        self._render_step()
        self._reposition()
        self.show()
        self.raise_()

    # ══════════════════════════════════════
    #  Navigation
    # ══════════════════════════════════════

    def _next(self):
        if self._current_step < len(_STEPS) - 1:
            self._current_step += 1
            self._render_step()
        else:
            self._finish()

    def _prev(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._render_step()

    def _finish(self):
        self.hide()
        self.finished.emit()

    # ══════════════════════════════════════
    #  Rendering
    # ══════════════════════════════════════

    def _render_step(self):
        step = _STEPS[self._current_step]
        total = len(_STEPS)
        idx = self._current_step

        # Заголовок
        self._title.setText(step["title"])
        self._subtitle.setText(step["subtitle"])

        # Контент — очистить и заполнить
        while self._content.count():
            item = self._content.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for icon, color, title, desc in step["items"]:
            self._content.addWidget(_ItemRow(icon, color, title, desc))

        # Dots
        for i, dot in enumerate(self._dots):
            if i == idx:
                dot.setStyleSheet(f"color: {T.ACCENT_PRIMARY}; {_NS}")
            else:
                dot.setStyleSheet(f"color: {T.TEXT_MUTED}; {_NS}")

        # Кнопки
        self._back_btn.setVisible(idx > 0)

        if idx == total - 1:
            self._next_btn.setText("Начать работу")
            try:
                self._next_btn.setIcon(get_icon("fa6s.rocket", "#ffffff"))
            except Exception:
                pass
        else:
            self._next_btn.setText("Далее")
            try:
                self._next_btn.setIcon(get_icon("fa6s.arrow-right", "#ffffff"))
            except Exception:
                pass

    # ══════════════════════════════════════
    #  Geometry
    # ══════════════════════════════════════

    def _reposition(self):
        pw, ph = self.parent().width(), self.parent().height()
        self.setGeometry(0, 0, pw, ph)

        panel_w = min(460, pw - 40)
        panel_h = min(440, ph - 40)
        panel_x = (pw - panel_w) // 2
        panel_y = (ph - panel_h) // 2

        self._panel.setGeometry(panel_x, panel_y, panel_w, panel_h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 160))
        p.end()

    def mousePressEvent(self, event):
        if not self._panel.geometry().contains(event.pos()):
            self._finish()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self._reposition()