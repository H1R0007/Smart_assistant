# core/gui/toast.py

"""
Неблокирующие toast-уведомления.
Появляются поверх контента, анимируются, стэкаются.
"""

from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, Signal,
)
from PySide6.QtGui import QColor

from core.gui.theme import Theme, get_icon

T = Theme


# ═══════════════════════════════════════════
#  Конфигурация типов
# ═══════════════════════════════════════════

TOAST_TYPES = {
    "success": {
        "icon": "fa6s.circle-check",
        "color": T.ACCENT_SUCCESS,
        "bg": "rgba(78, 205, 196, 0.12)",
        "border": "rgba(78, 205, 196, 0.30)",
    },
    "error": {
        "icon": "fa6s.circle-xmark",
        "color": T.ACCENT_DANGER,
        "bg": "rgba(247, 108, 108, 0.12)",
        "border": "rgba(247, 108, 108, 0.30)",
    },
    "info": {
        "icon": "fa6s.circle-info",
        "color": T.ACCENT_SECONDARY,
        "bg": "rgba(91, 140, 247, 0.12)",
        "border": "rgba(91, 140, 247, 0.30)",
    },
    "warning": {
        "icon": "fa6s.triangle-exclamation",
        "color": T.ACCENT_WARNING,
        "bg": "rgba(247, 185, 85, 0.12)",
        "border": "rgba(247, 185, 85, 0.30)",
    },
}


# ═══════════════════════════════════════════
#  Toast — одно уведомление
# ═══════════════════════════════════════════

class Toast(QWidget):
    """Одиночный toast с fade-in/out анимацией."""

    closed = Signal()

    DURATION = 3500
    FADE_IN_MS = 200
    FADE_OUT_MS = 350
    HEIGHT = 44
    MIN_WIDTH = 220
    MAX_WIDTH = 420
    MARGIN_RIGHT = 24
    MARGIN_TOP = 12

    def __init__(self, text: str, toast_type: str = "info",
                 parent: QWidget = None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        cfg = TOAST_TYPES.get(toast_type, TOAST_TYPES["info"])

        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setMaximumWidth(self.MAX_WIDTH)

        self.setStyleSheet(f"""
            QWidget#toast {{
                background-color: {T.BG_CARD};
                border: 1px solid {cfg['border']};
                border-left: 3px solid {cfg['color']};
                border-radius: {T.RADIUS_SM}px;
            }}
        """)
        self.setObjectName("toast")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 14, 0)
        layout.setSpacing(8)

        ic = QLabel()
        ic.setPixmap(get_icon(cfg["icon"], cfg["color"]).pixmap(16, 16))
        ic.setFixedWidth(20)
        ic.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(ic)

        lbl = QLabel(text)
        lbl.setFont(T.font(12))
        lbl.setStyleSheet(
            f"color: {T.TEXT_PRIMARY}; background: transparent; border: none;"
        )
        lbl.setWordWrap(False)
        layout.addWidget(lbl, 1)

        # Opacity
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        # Dismiss timer
        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.setInterval(self.DURATION)
        self._dismiss_timer.timeout.connect(self.fade_out)

        self.adjustSize()

    def show_animated(self):
        """Fade in и запустить таймер автоскрытия."""
        self.show()
        self.raise_()

        anim = QPropertyAnimation(self._opacity, b"opacity", self)
        anim.setDuration(self.FADE_IN_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

        self._dismiss_timer.start()

    def fade_out(self):
        """Плавное скрытие."""
        self._dismiss_timer.stop()

        anim = QPropertyAnimation(self._opacity, b"opacity", self)
        anim.setDuration(self.FADE_OUT_MS)
        anim.setStartValue(self._opacity.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self._on_faded)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def _on_faded(self):
        self.hide()
        self.closed.emit()

    def mousePressEvent(self, _event):
        """Клик по toast → немедленное скрытие."""
        self.fade_out()


# ═══════════════════════════════════════════
#  ToastManager — стэк уведомлений
# ═══════════════════════════════════════════

class ToastManager:
    """
    Управляет стопкой toast-уведомлений.

    Привязывается к parent-виджету (обычно MainWindow).
    Размещает toast'ы в правом верхнем углу, стэкает вниз.

    Использование:
        self.toasts = ToastManager(self)
        self.toasts.show("Сохранено!", "success")
        self.toasts.show("Ошибка подключения", "error")
    """

    MAX_VISIBLE = 4
    GAP = 6

    def __init__(self, parent: QWidget):
        self._parent = parent
        self._active: list[Toast] = []

    def show(self, text: str, toast_type: str = "info") -> Toast:
        """Показать новый toast."""
        # Лимит
        while len(self._active) >= self.MAX_VISIBLE:
            oldest = self._active[0]
            oldest.fade_out()

        toast = Toast(text, toast_type, self._parent)
        toast.closed.connect(lambda t=toast: self._on_closed(t))

        self._active.append(toast)
        self._reposition()
        toast.show_animated()

        return toast

    def success(self, text: str) -> Toast:
        return self.show(text, "success")

    def error(self, text: str) -> Toast:
        return self.show(text, "error")

    def info(self, text: str) -> Toast:
        return self.show(text, "info")

    def warning(self, text: str) -> Toast:
        return self.show(text, "warning")

    # ── Внутренние ──

    def _on_closed(self, toast: Toast):
        if toast in self._active:
            self._active.remove(toast)
            toast.deleteLater()
            self._reposition()

    def _reposition(self):
        """Разместить все активные toast'ы стопкой."""
        pw = self._parent.width()
        y = Toast.MARGIN_TOP + T.HEADER_H + 4

        for toast in self._active:
            tw = toast.sizeHint().width()
            tw = max(Toast.MIN_WIDTH, min(tw, Toast.MAX_WIDTH))
            toast.setFixedWidth(tw)

            x = pw - tw - Toast.MARGIN_RIGHT
            toast.move(x, y)
            y += Toast.HEIGHT + self.GAP

    def reposition_all(self):
        """Вызвать при resize родителя."""
        self._reposition()