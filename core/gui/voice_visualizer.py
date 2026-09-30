# core/gui/voice_visualizer.py

"""
Анимированный индикатор состояния ассистента.
Улучшенная версия: glow-эффект, адаптивный размер, плавные переходы.
"""

import math
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QLinearGradient
from PySide6.QtCore import Qt, QTimer

from core.gui.theme import Theme

T = Theme

# Цвета для каждого состояния
STATE_COLORS = {
    "connected": T.TEXT_MUTED,
    "waiting":   T.TEXT_MUTED,
    "listening": T.ACCENT_SECONDARY,
    "processing": T.ACCENT_WARNING,
    "speaking":  T.ACCENT_SUCCESS,
}

STATE_AMPLITUDES = {
    "connected": 0.12,
    "waiting":   0.12,
    "listening": 0.75,
    "processing": 0.35,
    "speaking":  0.85,
}

STATE_SPEEDS = {
    "connected": 0.06,
    "waiting":   0.06,
    "listening": 0.25,
    "processing": 0.15,
    "speaking":  0.30,
}


class VoiceVisualizer(QWidget):
    """
    Три перекрывающиеся синусоиды с glow-эффектом.
    Цвет, амплитуда и скорость плавно переходят при смене состояния.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(36)
        self.setMinimumWidth(80)

        self._state = "connected"
        self._phase = 0.0

        # Текущие (интерполируемые) значения
        self._amplitude = 0.12
        self._target_amp = 0.12
        self._color = QColor(T.TEXT_MUTED)
        self._target_color = QColor(T.TEXT_MUTED)
        self._speed = 0.06

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)  # ~60 FPS

    def set_state(self, state: str):
        if state == self._state:
            return
        self._state = state
        self._target_amp = STATE_AMPLITUDES.get(state, 0.12)
        self._target_color = QColor(STATE_COLORS.get(state, T.TEXT_MUTED))
        self._speed = STATE_SPEEDS.get(state, 0.06)

    def _tick(self):
        self._phase += self._speed
        if self._phase > math.pi * 2:
            self._phase -= math.pi * 2

        # Плавная интерполяция амплитуды
        self._amplitude += (self._target_amp - self._amplitude) * 0.08

        # Плавная интерполяция цвета
        r = self._color.red() + (self._target_color.red() - self._color.red()) * 0.06
        g = self._color.green() + (self._target_color.green() - self._color.green()) * 0.06
        b = self._color.blue() + (self._target_color.blue() - self._color.blue()) * 0.06
        self._color = QColor(int(r), int(g), int(b))

        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        mid = h / 2

        # 3 волны: основная + 2 glow-слоя
        layers = [
            (0, 255, 1.0, 2.5),   # основная
            (1.5, 120, 0.7, 4.0), # glow 1
            (3.0, 50, 0.4, 6.0),  # glow 2
        ]

        for phase_offset, alpha, amp_mod, line_w in layers:
            path = QPainterPath()
            path.moveTo(0, mid)

            for x in range(0, w, 2):
                nx = x / w
                # Оконная функция (затухание к краям)
                edge = math.sin(nx * math.pi)
                y = mid + (
                    math.sin(nx * 8 + self._phase + phase_offset) *
                    (h / 2) * self._amplitude * amp_mod * edge
                )
                path.lineTo(x, y)

            c = QColor(self._color)
            c.setAlpha(alpha)
            painter.setPen(QPen(c, line_w, Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(path)

        painter.end()