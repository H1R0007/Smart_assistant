# commands/utility.py

"""
Утилиты: таймер, скриншот, заметки, буфер обмена.

v3.1:
- Таймеры: отмена, список активных
- Заметки: бэкап, поиск, более умный парсинг
- Глобальный менеджер таймеров
"""

import os
import re
import json
import time
import shutil
import threading
import datetime
from pathlib import Path
from commands.base import BaseCommand, CommandResult
from core import notifier

# ═══════════════════════════════════════════
#  Менеджер таймеров (singleton)
# ═══════════════════════════════════════════

class TimerManager:
    """
    Управление всеми активными таймерами.

    Потокобезопасный. Позволяет:
    - Создавать именованные таймеры
    - Отменять по имени или все
    - Получать список активных
    """

    def __init__(self):
        self._timers: dict[str, dict] = {}  # id → {thread, end_time, label}
        self._lock = threading.Lock()
        self._counter = 0

    def add(
        self, seconds: int, label: str, callback: callable
    ) -> str:
        """
        Добавить таймер.

        Returns: ID таймера
        """
        with self._lock:
            self._counter += 1
            timer_id = f"timer_{self._counter}"

        cancel_event = threading.Event()

        def _run():
            # Ждём с возможностью отмены
            cancelled = cancel_event.wait(timeout=seconds)
            if not cancelled:
                callback()
            with self._lock:
                self._timers.pop(timer_id, None)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        with self._lock:
            self._timers[timer_id] = {
                "thread": thread,
                "cancel": cancel_event,
                "end_time": time.time() + seconds,
                "label": label,
            }

        return timer_id

    def cancel_last(self) -> str | None:
        """Отменить последний таймер. Возвращает label или None."""
        with self._lock:
            if not self._timers:
                return None

            # Последний по ID (наибольший _counter)
            timer_id = max(self._timers.keys())
            timer = self._timers.pop(timer_id)

        timer["cancel"].set()
        return timer["label"]

    def cancel_all(self) -> int:
        """Отменить все таймеры. Возвращает количество отменённых."""
        with self._lock:
            timers = dict(self._timers)
            self._timers.clear()

        for timer in timers.values():
            timer["cancel"].set()

        return len(timers)

    def get_active(self) -> list[dict]:
        """Список активных таймеров."""
        with self._lock:
            now = time.time()
            result = []
            for timer_id, timer in self._timers.items():
                remaining = max(0, timer["end_time"] - now)
                result.append({
                    "id": timer_id,
                    "label": timer["label"],
                    "remaining_sec": int(remaining),
                })
            return result

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._timers)
        
# ═══════════════════════════════════════════
#  Менеджер заметок
# ═══════════════════════════════════════════

class NotesManager:
    """
    Работа с заметками.

    - JSON хранилище
    - Автобэкап перед записью
    - Поиск по тексту
    """

    def __init__(self, notes_file: str = "data/notes.json"):
        self._file = notes_file
        self._backup_file = notes_file + ".bak"
        os.makedirs(os.path.dirname(notes_file), exist_ok=True)

    def load(self) -> list[dict]:
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save(self, notes: list[dict]) -> None:
        # Бэкап перед записью
        if os.path.exists(self._file):
            try:
                shutil.copy2(self._file, self._backup_file)
            except Exception:
                pass

        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)

    def add(self, text: str) -> None:
        notes = self.load()
        notes.append({
            "text": text,
            "time": datetime.datetime.now().isoformat(),
        })
        self.save(notes)

    def search(self, query: str) -> list[dict]:
        """Поиск по заметкам."""
        notes = self.load()
        query_lower = query.lower()
        return [n for n in notes if query_lower in n["text"].lower()]

    def get_recent(self, count: int = 5) -> list[dict]:
        notes = self.load()
        return notes[-count:]

    def clear(self) -> None:
        self.save([])

    @property
    def count(self) -> int:
        return len(self.load())


_notes_mgr = NotesManager()


# ═══════════════════════════════════════════
#  Команды: Таймер
# ═══════════════════════════════════════════

class TimerCommand(BaseCommand):
    name = "timer"
    keywords = ["таймер", "поставь таймер"]
    priority = 12

    def match(self, text: str) -> tuple[bool, dict]:
        text_lower = text.lower()
        patterns = [
            r'таймер\s+(?:на\s+)?(.+)',
            r'поставь\s+таймер\s*(.+)?',
        ]
        for pat in patterns:
            m = re.search(pat, text_lower)
            if m:
                return True, self._parse_duration(text_lower)
        return False, {}

    def _parse_duration(self, text: str) -> dict:
        # Специальные случаи ПЕРЕД извлечением числа
        if "полчаса" in text:
            return {"seconds": 1800, "num": 30}
        if "полтора часа" in text:
            return {"seconds": 5400, "num": 90}

        num = self._extract_number(text) or 1
        if any(w in text for w in ["час", "часа", "часов"]):
            total = num * 3600
        elif any(w in text for w in ["минут", "минуты", "минуту"]):
            total = num * 60
        elif any(w in text for w in ["секунд", "секунды", "секунду"]):
            total = num
        else:
            total = num * 60
        return {"seconds": total, "num": num}

    def execute(self, text: str, params: dict) -> CommandResult:
        seconds = params.get("seconds", 60)
        label = _format_duration(seconds)

        def _done():
            self.speaker.say(f"Таймер на {label} завершён!")
            notifier.notify("⏰ Таймер", f"Таймер на {label} завершён!")

        mgr = self.ctx.timer_manager
        if not mgr:
            self.speaker.say("Менеджер таймеров не настроен.")
            return CommandResult()
            
        mgr.add(seconds, label, _done)

        active = mgr.count
        msg = f"Таймер на {label}."
        if active > 1:
            msg += f" Активных таймеров: {active}."

        self.speaker.say(msg)
        return CommandResult(follow_up=True)


class CancelTimerCommand(BaseCommand):
    """Отменить таймер."""
    name = "cancel_timer"
    keywords = [
        "отмени таймер", "отменить таймер",
        "убери таймер", "останови таймер",
        "отмени все таймеры",
    ]
    priority = 13

    def execute(self, text: str, params: dict) -> CommandResult:
        mgr = self.ctx.timer_manager
        if not mgr:
            return CommandResult()

        if "все" in text or "всё" in text:
            count = mgr.cancel_all()
            if count:
                self.speaker.say(f"Отменила {count} таймеров.")
            else:
                self.speaker.say("Нет активных таймеров.")
        else:
            label = mgr.cancel_last()
            if label:
                self.speaker.say(f"Таймер на {label} отменён.")
            else:
                self.speaker.say("Нет активных таймеров.")

        return CommandResult(follow_up=True)


class ListTimersCommand(BaseCommand):
    """Список активных таймеров."""
    name = "list_timers"
    keywords = [
        "какие таймеры", "список таймеров",
        "активные таймеры", "сколько таймеров",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        mgr = self.ctx.timer_manager
        if not mgr:
            return CommandResult()
            
        active = mgr.get_active()

        if not active:
            self.speaker.say("Нет активных таймеров.")
        else:
            parts = []
            for t in active:
                remaining = _format_duration(t["remaining_sec"])
                parts.append(f"{t['label']}, осталось {remaining}")
            self.speaker.say(
                f"Активных таймеров: {len(active)}. "
                + "; ".join(parts) + "."
            )

        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Команды: Скриншот
# ═══════════════════════════════════════════

class ScreenshotCommand(BaseCommand):
    name = "screenshot"
    keywords = ["скриншот", "снимок экрана", "сделай скриншот", "скрин"]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        try:
            from PIL import ImageGrab
            screenshots_dir = Path("screenshots")
            screenshots_dir.mkdir(exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = screenshots_dir / f"screenshot_{timestamp}.png"
            img = ImageGrab.grab()
            img.save(filepath)
            self.speaker.say("Скриншот сохранён.")
            notifier.notify("📸 Скриншот", str(filepath))
            os.startfile(str(filepath))
        except Exception as e:
            self.speaker.say("Не удалось сделать скриншот.")
            self.logger.error("Screenshot: %s", e)
        return CommandResult(follow_up=True)
    
class ClearNotesCommand(BaseCommand):
    name = "clear_notes"
    keywords = [
        "удали заметки", "очисти заметки",
        "забудь всё", "удалить заметки",
    ]
    priority = 15

    def execute(self, text: str, params: dict) -> CommandResult:
        _notes_mgr.clear()
        self.speaker.say("Заметки удалены.")
        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Команды: Буфер обмена
# ═══════════════════════════════════════════

class ClipboardCommand(BaseCommand):
    name = "clipboard"
    keywords = [
        "что в буфере", "буфер обмена",
        "прочитай буфер", "что скопировано",
    ]
    priority = 7

    def execute(self, text: str, params: dict) -> CommandResult:
        try:
            import pyperclip
            content = pyperclip.paste()
            if content:
                short = content[:200] + ("..." if len(content) > 200 else "")
                self.speaker.say(f"В буфере: {short}")
            else:
                self.speaker.say("Буфер пуст.")
        except Exception:
            self.speaker.say("Не удалось прочитать буфер.")
        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════

def _format_duration(seconds: int) -> str:
    """Форматирование длительности для озвучки с правильными окончаниями."""
    if seconds < 60:
        return f"{seconds} {_pluralize(seconds, 'секунду', 'секунды', 'секунд')}"
    elif seconds < 3600:
        minutes = seconds // 60
        remaining = seconds % 60
        result = f"{minutes} {_pluralize(minutes, 'минуту', 'минуты', 'минут')}"
        if remaining:
            result += f" {remaining} {_pluralize(remaining, 'секунду', 'секунды', 'секунд')}"
        return result
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        result = f"{hours} {_pluralize(hours, 'час', 'часа', 'часов')}"
        if minutes:
            result += f" {minutes} {_pluralize(minutes, 'минуту', 'минуты', 'минут')}"
        return result


def _pluralize(n: int, form1: str, form2: str, form5: str) -> str:
    """
    Правильное окончание для русских числительных.
    
    _pluralize(1, 'минуту', 'минуты', 'минут') → 'минуту'
    _pluralize(2, 'минуту', 'минуты', 'минут') → 'минуты'
    _pluralize(5, 'минуту', 'минуты', 'минут') → 'минут'
    _pluralize(21, 'минуту', 'минуты', 'минут') → 'минуту'
    """
    n = abs(n) % 100
    if 11 <= n <= 19:
        return form5
    n = n % 10
    if n == 1:
        return form1
    if 2 <= n <= 4:
        return form2
    return form5