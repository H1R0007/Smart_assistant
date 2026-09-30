# core/gui/animations.py

"""
Анимации и переходы для GUI.

Правила:
- fade_in не перезаписывает существующий QGraphicsEffect
  (т.е. Card с тенью останется без fade — это by design)
- Все анимации GC-safe (парентятся к виджету)
- PageFader корректно обрабатывает быстрые переключения
"""

from PySide6.QtWidgets import QWidget, QGraphicsOpacityEffect, QStackedWidget
from PySide6.QtCore import (
    QPropertyAnimation, QEasingCurve, QTimer, QAbstractAnimation,
)


def fade_in(widget: QWidget, duration: int = 250, delay: int = 0):
    """
    Плавное появление виджета.

    Пропускает виджеты, у которых уже есть GraphicsEffect
    (например, Card с тенью), чтобы не затереть их эффект.
    """
    if widget.graphicsEffect():
        return

    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _cleanup():
        try:
            widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    anim.finished.connect(_cleanup)

    if delay > 0:
        QTimer.singleShot(delay, lambda: _safe_start(anim))
    else:
        anim.start()


def staggered_fade_in(widgets: list[QWidget], duration: int = 250,
                      delay_step: int = 50, initial_delay: int = 0):
    """Каскадное появление списка виджетов."""
    for i, widget in enumerate(widgets):
        fade_in(widget, duration, initial_delay + i * delay_step)


def _safe_start(anim: QAbstractAnimation):
    try:
        if anim and anim.state() != QAbstractAnimation.Running:
            anim.start()
    except RuntimeError:
        pass


class PageFader:
    """
    Плавный fade-in при переключении страниц QStackedWidget.

    Логика:
    1. Ставим новой странице opacity=0
    2. Переключаем индекс (страница видна, но прозрачна)
    3. Анимируем opacity 0→1
    4. Убираем эффект (чтобы не мешал рендерингу)
    """

    def __init__(self, stack: QStackedWidget, duration: int = 200):
        self._stack = stack
        self._duration = duration
        self._anim: QPropertyAnimation | None = None
        self._target_page: QWidget | None = None

    def switch_to(self, index: int):
        if index == self._stack.currentIndex():
            return
        if not (0 <= index < self._stack.count()):
            return

        # Прервать текущую анимацию
        if self._anim and self._anim.state() == QPropertyAnimation.Running:
            self._anim.stop()
            self._cleanup()

        page = self._stack.widget(index)
        if not page:
            self._stack.setCurrentIndex(index)
            return

        # Временный эффект
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)
        self._target_page = page

        # Переключаем
        self._stack.setCurrentIndex(index)

        # Анимируем
        self._anim = QPropertyAnimation(effect, b"opacity", page)
        self._anim.setDuration(self._duration)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._cleanup)
        self._anim.start()

    def _cleanup(self):
        self._anim = None
        try:
            if self._target_page:
                self._target_page.setGraphicsEffect(None)
        except RuntimeError:
            pass
        self._target_page = None