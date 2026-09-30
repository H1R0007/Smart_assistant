"""
Модуль системного трея.

Архитектура:
1. Кэшированные иконки для каждого состояния
2. Информативное меню с действиями
3. Динамический tooltip с текущим статусом
4. Интеграция с Windows notifications
5. Индикация ошибок (красная иконка)
6. Потокобезопасные обновления

Каждое решение обосновано конкретной проблемой.
"""

import os
import threading
import logging
import webbrowser
from enum import Enum
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import pystray

logger = logging.getLogger(__name__)


class TrayStatus(Enum):
    """Возможные состояния ассистента."""
    LISTENING = "listening"      # Ждёт wake word (зелёный)
    ACTIVE = "active"            # Слушает команду (жёлтый)
    PROCESSING = "processing"    # Выполняет (синий)
    SPEAKING = "speaking"        # Говорит (голубой)
    ERROR = "error"              # Ошибка (красный)
    PAUSED = "paused"            # На паузе (серый)


# Цвета для каждого состояния
STATUS_COLORS = {
    TrayStatus.LISTENING:   "#4CAF50",  # зелёный
    TrayStatus.ACTIVE:      "#FFC107",  # жёлтый
    TrayStatus.PROCESSING:  "#2196F3",  # синий
    TrayStatus.SPEAKING:    "#03A9F4",  # голубой
    TrayStatus.ERROR:       "#F44336",  # красный
    TrayStatus.PAUSED:      "#9E9E9E",  # серый
}

STATUS_LABELS = {
    TrayStatus.LISTENING:   "Слушаю...",
    TrayStatus.ACTIVE:      "Говори команду",
    TrayStatus.PROCESSING:  "Выполняю...",
    TrayStatus.SPEAKING:    "Говорю...",
    TrayStatus.ERROR:       "Ошибка",
    TrayStatus.PAUSED:      "На паузе",
}


class TrayIcon:
    """
    Иконка в системном трее.
    
    Потокобезопасная. Методы set_* можно вызывать из любого потока.
    """

    APP_NAME = "Голосовой ассистент"
    VERSION = "3.0"

    def __init__(
        self,
        on_quit: callable,
        on_toggle_pause: callable = None,
        wake_word: str = "Ассистент"
    ):
        """
        Args:
            on_quit: callback при выборе "Выход".
            on_toggle_pause: callback при выборе "Пауза/Продолжить".
            wake_word: для отображения в меню.
        """
        self._on_quit = on_quit
        self._on_toggle_pause = on_toggle_pause
        self._wake_word = wake_word
        self._icon = None
        self._current_status = TrayStatus.LISTENING
        self._is_paused = False
        self._commands_count = 0
        self._error_message = ""
        self._lock = threading.RLock()

        # Кэш иконок — создаём один раз
        self._icon_cache = {}
        for status in TrayStatus:
            color = STATUS_COLORS[status]
            self._icon_cache[status] = self._create_icon(color)

        logger.debug("TrayIcon инициализирован")

    # ══════════════════════════════════════════════
    #  Создание иконок
    # ══════════════════════════════════════════════

    def _create_icon(self, color: str) -> Image.Image:
        """
        Создать иконку для заданного цвета.
        
        Дизайн: скруглённый квадрат с микрофоном.
        Без текста — читается лучше в маленьком размере (16x16 в трее).
        """
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        # Фон: скруглённый квадрат
        margin = 2
        radius = 12
        draw.rounded_rectangle(
            [margin, margin, size - margin, size - margin],
            radius=radius,
            fill=color
        )

        # Символ микрофона (упрощённый)
        cx, cy = size // 2, size // 2
        mic_color = "white"

        # Корпус микрофона (овал)
        mic_w, mic_h = 10, 16
        draw.ellipse(
            [cx - mic_w, cy - mic_h, cx + mic_w, cy + 2],
            fill=mic_color
        )

        # Ножка
        leg_w = 3
        draw.rectangle(
            [cx - leg_w, cy + 2, cx + leg_w, cy + 12],
            fill=mic_color
        )

        # Подставка
        draw.rectangle(
            [cx - 10, cy + 10, cx + 10, cy + 14],
            fill=mic_color
        )

        # Дуга (скобка вокруг микрофона)
        draw.arc(
            [cx - 16, cy - 12, cx + 16, cy + 8],
            start=0, end=180,
            fill=mic_color,
            width=3
        )

        return image

    # ══════════════════════════════════════════════
    #  Меню
    # ══════════════════════════════════════════════

    def _build_menu(self) -> pystray.Menu:
        """
        Построить контекстное меню.
        
        Структура:
        - Статус (информационная строка)
        - Wake word
        - ─────────────
        - Пауза / Продолжить
        - ─────────────
        - Открыть лог
        - Статистика
        - ─────────────
        - Выход
        """
        status_label = STATUS_LABELS.get(
            self._current_status, "Неизвестно"
        )

        items = [
            pystray.MenuItem(
                f"● {status_label}",
                None,
                enabled=False
            ),
            pystray.MenuItem(
                f"Wake word: \"{self._wake_word}\"",
                None,
                enabled=False
            ),
            pystray.Menu.SEPARATOR,
        ]

        # Пауза/Продолжить
        if self._on_toggle_pause:
            pause_label = "▶ Продолжить" if self._is_paused else "⏸ Пауза"
            items.append(
                pystray.MenuItem(pause_label, self._on_pause_clicked)
            )
            items.append(pystray.Menu.SEPARATOR)

        # Утилиты
        items.extend([
            pystray.MenuItem("📄 Открыть лог", self._on_open_log),
            pystray.MenuItem(
                f"📊 Команд: {self._commands_count}",
                None,
                enabled=False
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Выход", self._on_quit_clicked),
        ])

        return pystray.Menu(*items)

    # ══════════════════════════════════════════════
    #  Обработчики меню
    # ══════════════════════════════════════════════

    def _on_quit_clicked(self, icon, item):
        """Выход."""
        logger.info("Выход из трея")
        icon.stop()
        if self._on_quit:
            self._on_quit()

    def _on_pause_clicked(self, icon, item):
        """Пауза/Продолжить."""
        self._is_paused = not self._is_paused
        if self._is_paused:
            self.set_status(TrayStatus.PAUSED)
        else:
            self.set_status(TrayStatus.LISTENING)
        if self._on_toggle_pause:
            self._on_toggle_pause(self._is_paused)
        # Обновляем меню
        self._update_menu()

    def _on_open_log(self, icon, item):
        """Открыть файл лога."""
        log_file = "assistant.log"
        if os.path.exists(log_file):
            os.startfile(log_file)
        else:
            logger.warning("Лог-файл не найден: %s", log_file)

    # ══════════════════════════════════════════════
    #  Публичный API
    # ══════════════════════════════════════════════

    def run(self) -> None:
        """
        Запуск иконки в трее (блокирующий).
        Должен вызываться из основного потока (требование pystray/win32).
        """
        menu = self._build_menu()

        self._icon = pystray.Icon(
            name="assistant",
            icon=self._icon_cache[TrayStatus.LISTENING],
            title=self._build_tooltip(),
            menu=menu
        )

        logger.info("Трей запущен")
        self._icon.run()

    def stop(self) -> None:
        """Остановка иконки."""
        with self._lock:
            if self._icon:
                try:
                    self._icon.stop()
                except Exception:
                    pass
                self._icon = None

    def set_status(self, status: TrayStatus, message: str = "") -> None:
        """
        Установить статус ассистента.
        Потокобезопасный метод.
        
        Args:
            status: новый статус
            message: дополнительное сообщение (для ERROR)
        """
        with self._lock:
            self._current_status = status
            if status == TrayStatus.ERROR:
                self._error_message = message

            if self._icon:
                try:
                    self._icon.icon = self._icon_cache[status]
                    self._icon.title = self._build_tooltip()
                except Exception as e:
                    logger.debug("Tray update error: %s", e)

    def increment_commands(self) -> None:
        """Увеличить счётчик команд."""
        with self._lock:
            self._commands_count += 1
            self._update_menu()

    # ── Удобные методы (обратная совместимость) ──

    def set_listening(self) -> None:
        """Ждёт wake word."""
        self.set_status(TrayStatus.LISTENING)

    def set_active(self) -> None:
        """Слушает команду."""
        self.set_status(TrayStatus.ACTIVE)

    def set_processing(self) -> None:
        """Выполняет команду."""
        self.set_status(TrayStatus.PROCESSING)

    def set_speaking(self) -> None:
        """Говорит ответ."""
        self.set_status(TrayStatus.SPEAKING)

    def set_error(self, message: str = "") -> None:
        """Ошибка."""
        self.set_status(TrayStatus.ERROR, message)

    def notify(self, title: str, message: str) -> None:
        """
        Показать всплывающее уведомление через трей.
        
        Нативный Windows balloon notification.
        Не требует дополнительных библиотек.
        """
        with self._lock:
            if self._icon:
                try:
                    self._icon.notify(message, title)
                except Exception as e:
                    logger.debug("Tray notify error: %s", e)

    # ══════════════════════════════════════════════
    #  Внутренние методы
    # ══════════════════════════════════════════════

    def _build_tooltip(self) -> str:
        """Построить текст tooltip."""
        status_text = STATUS_LABELS.get(
            self._current_status, ""
        )

        parts = [self.APP_NAME]

        if status_text:
            parts.append(status_text)

        if self._current_status == TrayStatus.ERROR and self._error_message:
            parts.append(self._error_message)

        if self._current_status == TrayStatus.LISTENING:
            parts.append(f"Скажи \"{self._wake_word}\"")

        return " | ".join(parts)

    def _update_menu(self) -> None:
        """Обновить меню (для динамических элементов)."""
        with self._lock:
            if self._icon:
                try:
                    self._icon.menu = self._build_menu()
                except Exception:
                    pass