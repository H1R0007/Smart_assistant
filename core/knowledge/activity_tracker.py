# core/knowledge/activity_tracker.py

"""
Автоматический трекер активности.

Отслеживает:
- Переключения между приложениями (с длительностью)
- Выполненные команды ассистента
- Копирование в буфер обмена

Архитектура:
- Фоновый поток с polling (500мс)
- Дедупликация (не логировать если окно не менялось)
- Минимальная длительность фокуса (2 сек — игнорировать мелькания)
- Батчевая запись в БД (раз в 10 сек)
- Двойная запись: activity_log (аналитика) + entries (семантика)

Почему polling, а не хуки:
- SetWinEventHook требует message pump в том же потоке
- Polling 500мс = <0.1% CPU
- Проще отлаживать
- Нет проблем с потоками

Почему двойная запись (activity_log + entries):
- activity_log хранит сырые данные с точными таймстемпами и длительностями,
  идеален для агрегации (топ приложений, сумма часов).
- entries с source='tracker' хранят человекочитаемый текст, который
  индексируется FTS5 и EmbeddingEngine для семантического поиска и RAG.
- Разные задачи — разные структуры хранения.
"""

import ctypes
import ctypes.wintypes
import time
import logging
import threading
import json
from collections import deque
from typing import Optional

from core.knowledge.storage import KnowledgeStorage

try:
    import pyperclip
    PYPERCLIP_AVAILABLE = True
except ImportError:
    PYPERCLIP_AVAILABLE = False

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32


class ActivityTracker:
    """
    Фоновый трекер активности пользователя.
    
    Потокобезопасный. Работает в отдельном daemon-потоке.
    
    Данные:
    - Какие приложения открыты и сколько времени в каждом
    - Какие команды выполнялись
    - Что копировалось в буфер
    
    Каждое событие сохраняется в два места:
    1. activity_log — сырые данные для аналитики
    2. entries (source='tracker') — текст для поиска и RAG
    """

    POLL_INTERVAL = 0.5        # Интервал проверки (сек)
    MIN_FOCUS_SEC = 2.0        # Минимальное время фокуса для записи в activity_log
    FLUSH_INTERVAL = 10.0      # Интервал записи в БД (сек)
    MAX_TITLE_LENGTH = 200     # Обрезка длинных заголовков
    MAX_BUFFER_SIZE = 100      # Максимум записей в буфере
    CLIPBOARD_POLL_INTERVAL = 2.0  # Проверка буфера (сек)
    CLIPBOARD_MIN_LENGTH = 10      # Минимум символов для записи
    CLIPBOARD_MAX_LENGTH = 500     # Максимум символов для записи

    # Порог для записи в базу знаний (entries).
    # app_switch короче 30 сек — это мелькание окон,
    # бесполезное для семантического поиска.
    # В activity_log пишем всё от MIN_FOCUS_SEC (2 сек),
    # а в знания — только осмысленные сессии.
    KNOWLEDGE_MIN_APP_DURATION = 30.0

    def __init__(self, storage: KnowledgeStorage):
        self._storage = storage
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Состояние
        self._current_window: str = ""
        self._current_title: str = ""
        self._focus_start: float = 0
        self._last_flush: float = 0

        # Буфер для батчевой записи
        self._buffer: deque = deque(maxlen=self.MAX_BUFFER_SIZE)
        self._buffer_lock = threading.Lock()

        # Статистика сессии
        self._session_start = time.time()
        self._app_time: dict[str, float] = {}

        # Мониторинг буфера обмена
        self._last_clipboard: str = ""
        self._clipboard_counter: float = 0
        self._auto_save_clipboard = False  # Включается отдельно
        self._activity_callback = None

    # ══════════════════════════════════════════════
    #  Управление
    # ══════════════════════════════════════════════

    def start(self) -> None:
        """Запустить трекер в фоне."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._session_start = time.time()
        self._thread = threading.Thread(
            target=self._tracking_loop,
            daemon=True,
            name="activity-tracker",
        )
        self._thread.start()
        logger.info("ActivityTracker запущен")

    def stop(self) -> None:
        """Остановить трекер."""
        self._stop_event.set()
        # Финальная запись текущего окна
        self._record_window_switch("")
        self._flush_buffer()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("ActivityTracker остановлен")

    # ══════════════════════════════════════════════
    #  Публичный API для записи событий
    # ══════════════════════════════════════════════

    def log_command(self, command_name: str, params: dict = None) -> None:
        """Записать выполнение команды ассистента."""
        self._add_to_buffer(
            action="command",
            target=command_name,
            details=json.dumps(params or {}, ensure_ascii=False),
        )

    def log_search(self, query: str, source: str = "voice") -> None:
        """Записать поисковый запрос."""
        self._add_to_buffer(
            action="search",
            target=query,
            details=json.dumps({"source": source}, ensure_ascii=False),
        )

    def log_clipboard(self, content: str) -> None:
        """Записать копирование в буфер."""
        # Обрезаем до разумной длины
        preview = content[:200] if content else ""
        self._add_to_buffer(
            action="clipboard",
            target=preview,
        )

    # ══════════════════════════════════════════════
    #  Основной цикл
    # ══════════════════════════════════════════════

    def _tracking_loop(self) -> None:
        """Основной цикл отслеживания."""
        while not self._stop_event.is_set():
            try:
                self._poll_active_window()
            except Exception as e:
                logger.debug("Tracker poll error: %s", e)

            # Мониторинг буфера обмена
            self._clipboard_counter += self.POLL_INTERVAL
            if (self._auto_save_clipboard
                    and PYPERCLIP_AVAILABLE
                    and self._clipboard_counter >= self.CLIPBOARD_POLL_INTERVAL):
                self._clipboard_counter = 0
                try:
                    self._poll_clipboard()
                except Exception as e:
                    logger.debug("Clipboard poll error: %s", e)

            # Периодический flush
            now = time.time()
            if now - self._last_flush >= self.FLUSH_INTERVAL:
                self._flush_buffer()
                self._last_flush = now

            self._stop_event.wait(timeout=self.POLL_INTERVAL)

    def _poll_active_window(self) -> None:
        """Проверить активное окно."""
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return

        # Получаем заголовок
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value[:self.MAX_TITLE_LENGTH]

        # Извлекаем имя приложения из заголовка
        app_name = self._extract_app_name(title)

        if app_name != self._current_window:
            self._record_window_switch(app_name, title)

    def _record_window_switch(
        self,
        new_app: str,
        new_title: str = "",
    ) -> None:
        """Записать переключение окна."""
        now = time.time()

        # Записываем предыдущее окно (если было достаточно долго)
        if self._current_window and self._focus_start:
            duration = now - self._focus_start
            if duration >= self.MIN_FOCUS_SEC:
                self._add_to_buffer(
                    action="app_switch",
                    target=self._current_window,
                    window_title=self._current_title,
                    duration_sec=round(duration, 1),
                )

                # Обновляем статистику сессии
                self._app_time[self._current_window] = (
                    self._app_time.get(self._current_window, 0) + duration
                )

        # Обновляем текущее
        self._current_window = new_app
        self._current_title = new_title
        self._focus_start = now

    def enable_clipboard_tracking(self, enabled: bool = True) -> None:
        """Включить/выключить автосохранение буфера обмена."""
        self._auto_save_clipboard = enabled
        state = "включён" if enabled else "выключен"
        logger.info("Clipboard tracking: %s", state)

    def _poll_clipboard(self) -> None:
        """
        Проверить буфер обмена на новое содержимое.
        
        Сохраняет только «интересный» контент:
        - URL
        - Код (содержит спецсимволы)
        - Текст достаточной длины
        
        НЕ сохраняет:
        - Слишком короткое (< 10 символов)
        - Слишком длинное (> 500 символов) — обрезает
        - Пароли (содержат только спецсимволы)
        - Одинаковое с прошлым разом
        """
        try:
            content = pyperclip.paste()
        except Exception:
            return

        if not content or not content.strip():
            return

        content = content.strip()

        # Дедупликация
        if content == self._last_clipboard:
            return
        self._last_clipboard = content

        # Фильтрация
        if len(content) < self.CLIPBOARD_MIN_LENGTH:
            return

        # Определяем тип
        clip_type = self._classify_clipboard(content)
        if clip_type is None:
            return

        # Обрезаем если слишком длинное
        if len(content) > self.CLIPBOARD_MAX_LENGTH:
            content = content[:self.CLIPBOARD_MAX_LENGTH] + "..."

        # Логируем
        self._add_to_buffer(
            action="clipboard",
            target=content[:100],
            details=clip_type,
        )

        logger.debug("Clipboard captured (%s): %s", clip_type, content[:50])

    @staticmethod
    def _classify_clipboard(content: str) -> Optional[str]:
        """
        Классифицировать содержимое буфера.
        
        Returns: тип или None (не сохранять)
        """
        # URL
        if content.startswith(("http://", "https://", "www.")):
            return "url"

        # Путь к файлу
        if any(
            content.startswith(p)
            for p in ["C:\\", "D:\\", "/home/", "/usr/"]
        ):
            return "path"

        # Код (эвристика: содержит спецсимволы кода)
        code_chars = sum(
            1 for c in content
            if c in "{}[]();=<>/"
        )
        if code_chars > len(content) * 0.05 and len(content) > 20:
            return "code"

        # Обычный текст достаточной длины
        if len(content) >= 20:
            # Проверяем что это не бинарный мусор
            printable = sum(
                1 for c in content
                if c.isprintable() or c in "\n\t"
            )
            if printable / len(content) > 0.9:
                return "text"

        return None

    @staticmethod
    def _extract_app_name(title: str) -> str:
        """
        Извлечь имя приложения из заголовка окна.
        
        "main.py — assistant — Visual Studio Code" → "VS Code"
        "Google — Google Chrome" → "Chrome"
        "Telegram (3)" → "Telegram"
        "Создать новую виртуальную машину" → пропуск (диалог)
        """
        if not title:
            return ""

        title_lower = title.lower()

        app_patterns = {
            "visual studio code": "VS Code",
            "vs code": "VS Code",
            "google chrome": "Chrome",
            "mozilla firefox": "Firefox",
            "microsoft edge": "Edge",
            "яндекс браузер": "Яндекс Браузер",
            "yandex browser": "Яндекс Браузер",
            "telegram": "Telegram",
            "discord": "Discord",
            "explorer": "Проводник",
            "проводник": "Проводник",
            "notepad": "Блокнот",
            "блокнот": "Блокнот",
            "cmd.exe": "CMD",
            "command prompt": "CMD",
            "командная строка": "CMD",
            "powershell": "PowerShell",
            "windows terminal": "Terminal",
            "spotify": "Spotify",
            "steam": "Steam",
            "word": "Word",
            "excel": "Excel",
        }

        for pattern, name in app_patterns.items():
            if pattern in title_lower:
                return name

        # Fallback: последняя часть после разделителя
        for sep in [" — ", " - ", " – "]:
            if sep in title:
                last_part = title.split(sep)[-1].strip()
                if len(last_part) <= 25:
                    return last_part

        # Если заголовок короткий — вернуть как есть
        if len(title) <= 20:
            return title

        # Длинный неизвестный заголовок — скорее диалоговое окно
        return ""

    # ══════════════════════════════════════════════
    #  Буфер и запись
    # ══════════════════════════════════════════════

    def _add_to_buffer(
        self,
        action: str,
        target: str = "",
        details: str = "",
        window_title: str = "",
        duration_sec: float = 0,
    ) -> None:
        """Добавить запись в буфер."""
        record = {
            "action": action,
            "target": target,
            "details": details,
            "window_title": window_title,
            "duration_sec": duration_sec,
            "timestamp": time.time(),
        }
        with self._buffer_lock:
            self._buffer.append(record)

    def set_activity_callback(self, callback) -> None:
        """
        Установить callback для уведомления о новых действиях.

        Вызывается KnowledgeBase для связи с PatternEngine.
        callback(record: dict) вызывается после записи в БД.
        """
        self._activity_callback = callback

    def _flush_buffer(self) -> None:
        """
        Записать буфер в БД.
        
        Каждая запись сохраняется в ДВА места:
        1. activity_log — сырые данные (таймстемпы, длительности)
           для агрегации и точной аналитики.
        2. entries (source='tracker') — человекочитаемый текст
           для FTS, семантического поиска и RAG.
        
        Запись в знания может провалиться (например, контент
        отфильтрован по длительности) — это нормально,
        сырые данные в activity_log всё равно сохранятся.
        """
        with self._buffer_lock:
            if not self._buffer:
                return
            records = list(self._buffer)
            self._buffer.clear()

        for record in records:
            # 1. Сырые данные → activity_log (всегда)
            try:
                self._storage.add_activity(**record)
            except Exception as e:
                logger.debug("Activity write error: %s", e)

            # 2. Форматированный текст → entries (если проходит фильтр)
            try:
                self._save_to_knowledge(record)
            except Exception as e:
                logger.debug("Knowledge save error: %s", e)

            # 3. Уведомляем PatternEngine (ДОБАВИТЬ)
            if self._activity_callback:
                try:
                    self._activity_callback(record)
                except Exception as e:
                    logger.debug("Activity callback error: %s", e)

    # ══════════════════════════════════════════════
    #  Интеграция с базой знаний
    # ══════════════════════════════════════════════

    def _save_to_knowledge(self, record: dict) -> None:
        """
        Сохранить событие трекера как запись в базе знаний (entries).
        
        Форматирует сырое событие в человекочитаемый текст,
        который будет проиндексирован FTS5 и EmbeddingEngine.
        
        Фильтрация:
        - Пустой target → пропуск (нечего искать)
        - app_switch < 30 сек → пропуск (шум переключений)
        - Команды, поиск, буфер → всегда сохраняем
        
        Потокобезопасность: вызывается из tracker-потока,
        KnowledgeStorage._write_lock защищает запись.
        """
        target = record.get("target", "")
        if not target:
            return

        content = self._format_activity_content(record)
        if not content:
            return

        tags = self._make_activity_tags(record)

        self._storage.add_entry(
            content=content,
            entry_type=record["action"],
            source="tracker",
            tags=tags,
            importance=0,
        )

        logger.debug(
            "Tracker → knowledge: [%s] %s",
            record["action"], content[:60],
        )

    def _format_activity_content(self, record: dict) -> Optional[str]:
        """
        Сформировать человекочитаемый текст из сырого события.
        
        Требования к тексту:
        1. FTS5 должен находить по ключевым словам
           ("VS Code", "Chrome", имя команды)
        2. Embedding-модель должна улавливать семантику
           ("работал" ≈ "использовал" ≈ "был в приложении")
        3. LLM должен уметь использовать как контекст для ответа
        
        Returns: текст или None (не сохранять в знания)
        """
        action = record["action"]
        target = record["target"]
        duration = record.get("duration_sec", 0)
        window_title = record.get("window_title", "")

        if action == "app_switch":
            # Короткие переключения — шум для семантики.
            # В activity_log они уже есть для точной аналитики.
            if duration < self.KNOWLEDGE_MIN_APP_DURATION:
                return None
            dur_str = self._format_duration(duration)
            text = f"Работал в приложении {target} ({dur_str})"
            # Заголовок окна добавляет контекст:
            # "Работал в VS Code (15 мин.). Заголовок: main.py — assistant"
            # Это помогает при вопросах типа "над каким файлом я работал?"
            if window_title and window_title != target:
                # Обрезаем, чтобы не засорять embedding
                title_preview = window_title[:100]
                text += f". Заголовок: {title_preview}"
            return text

        elif action == "command":
            details = record.get("details", "")
            if details and details != "{}":
                return (
                    f"Выполнил команду ассистента: {target}. "
                    f"Параметры: {details}"
                )
            return f"Выполнил команду ассистента: {target}"

        elif action == "search":
            return f"Выполнил поиск: {target}"

        elif action == "clipboard":
            return f"Скопировал в буфер обмена: {target}"

        # Неизвестный тип — на будущее, не ломаем
        return f"Действие {action}: {target}"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """
        Форматировать длительность в читаемый вид.
        
        Используем сокращения ("мин.", "ч.") вместо
        полных слов, чтобы избежать сложного склонения
        в русском языке (1 минута / 2 минуты / 5 минут).
        """
        minutes = seconds / 60
        if minutes < 1:
            return f"{int(seconds)} сек."
        if minutes < 60:
            return f"{int(minutes)} мин."
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        if mins == 0:
            return f"{hours} ч."
        return f"{hours} ч. {mins} мин."

    @staticmethod
    def _make_activity_tags(record: dict) -> str:
        """
        Сформировать теги для записи активности.

        Теги служат трём целям:
        1. Ускоряют FTS-поиск по типу события
        2. Позволяют фильтровать записи через get_by_tag()
        3. Ключевые слова из заголовка окна для поиска
           по файлам/сайтам ("main.py", "gmail")

        Примеры:
          "активность,app_switch,vs code,main.py,assistant"
          "активность,command"
        """
        action = record["action"]
        target = record.get("target", "")
        window_title = record.get("window_title", "")

        tags = ["активность", action]

        if action == "app_switch" and target:
            tags.append(target.lower())

            # Ключевые слова из заголовка окна.
            # "main.py — assistant — VS Code"
            #   → parts: ["main.py", "assistant"]
            # Помогает FTS находить записи по именам
            # файлов, сайтов, документов.
            if window_title and window_title != target:
                for sep in [" — ", " - ", " – "]:
                    if sep in window_title:
                        parts = window_title.split(sep)
                        # Без последней части (имя приложения,
                        # уже есть в target)
                        for p in parts[:-1]:
                            clean = p.strip().lower()
                            if 3 <= len(clean) <= 50:
                                tags.append(clean)
                        break

        return ",".join(tags[:8])  # Ограничиваем количество тегов

    # ══════════════════════════════════════════════
    #  Статистика сессии
    # ══════════════════════════════════════════════

    def get_session_summary(self) -> dict:
        """Сводка текущей сессии."""
        # Финализируем текущее окно
        if self._current_window and self._focus_start:
            duration = time.time() - self._focus_start
            current_time = dict(self._app_time)
            current_time[self._current_window] = (
                current_time.get(self._current_window, 0) + duration
            )
        else:
            current_time = dict(self._app_time)

        # Сортируем по времени
        sorted_apps = sorted(
            current_time.items(),
            key=lambda x: x[1],
            reverse=True
        )

        session_duration = time.time() - self._session_start

        return {
            "session_minutes": round(session_duration / 60, 1),
            "top_apps": [
                (name, round(secs / 60, 1))
                for name, secs in sorted_apps[:5]
            ],
        }
    
