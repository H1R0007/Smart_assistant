# commands/reminder.py

"""
Напоминания с произвольным текстом.

v3.1 — Полная переработка:
- Интеграция с TimerManager из utility.py (единая система)
- Напоминания на конкретное время ("напомни в 15:30")
- Список активных напоминаний
- Отмена напоминаний
- Более надёжный парсинг времени
- Повторяющиеся напоминания ("напомни каждые 30 минут")
"""

import re
import time
import datetime
import logging
import threading

from commands.base import BaseCommand, CommandResult
from commands.utility import _format_duration
from core import notifier

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Менеджер напоминаний
# ═══════════════════════════════════════════

class ReminderManager:
    """
    Управление напоминаниями.

    Отличие от TimerManager:
    - Хранит текст сообщения
    - Поддерживает абсолютное время (в 15:30)
    - Поддерживает повторения
    - Отдельный список от таймеров
    """

    def __init__(self):
        self._reminders: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def add(
        self,
        seconds: int,
        message: str,
        callback: callable,
        repeating: bool = False,
    ) -> str:
        """
        Добавить напоминание.

        Args:
            seconds: через сколько секунд напомнить
            message: текст напоминания
            callback: функция для вызова (speaker.say)
            repeating: повторять ли

        Returns:
            ID напоминания
        """
        with self._lock:
            self._counter += 1
            reminder_id = f"reminder_{self._counter}"

        cancel_event = threading.Event()

        def _run():
            while True:
                cancelled = cancel_event.wait(timeout=seconds)
                if cancelled:
                    break
                callback(message)
                if not repeating:
                    break
                # Обновляем end_time для следующего цикла
                with self._lock:
                    if reminder_id in self._reminders:
                        self._reminders[reminder_id]["end_time"] = (
                            time.time() + seconds
                        )

            with self._lock:
                self._reminders.pop(reminder_id, None)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        with self._lock:
            self._reminders[reminder_id] = {
                "thread": thread,
                "cancel": cancel_event,
                "end_time": time.time() + seconds,
                "message": message,
                "repeating": repeating,
                "interval": seconds if repeating else 0,
            }

        return reminder_id

    def cancel_last(self) -> str | None:
        """Отменить последнее напоминание."""
        with self._lock:
            if not self._reminders:
                return None
            reminder_id = max(self._reminders.keys())
            reminder = self._reminders.pop(reminder_id)

        reminder["cancel"].set()
        return reminder["message"]

    def cancel_all(self) -> int:
        """Отменить все напоминания."""
        with self._lock:
            reminders = dict(self._reminders)
            self._reminders.clear()

        for reminder in reminders.values():
            reminder["cancel"].set()

        return len(reminders)

    def get_active(self) -> list[dict]:
        """Список активных напоминаний."""
        with self._lock:
            now = time.time()
            result = []
            for rid, r in self._reminders.items():
                remaining = max(0, r["end_time"] - now)
                result.append({
                    "id": rid,
                    "message": r["message"],
                    "remaining_sec": int(remaining),
                    "repeating": r["repeating"],
                })
            return result

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._reminders)


_reminder_mgr = ReminderManager()


def get_reminder_manager() -> ReminderManager:
    return _reminder_mgr


# ═══════════════════════════════════════════
#  Парсинг времени
# ═══════════════════════════════════════════

def _parse_relative_time(text: str) -> int | None:
    """
    Парсинг относительного времени.

    "через 5 минут" → 300
    "через полчаса" → 1800
    "через час" → 3600
    "через 2 часа 30 минут" → 9000
    """
    from commands.base import BaseCommand

    total = 0
    found = False

    # Полчаса — специальный случай
    if "полчаса" in text:
        return 1800

    # Полтора часа
    if "полтора часа" in text:
        return 5400

    # Ищем паттерн "N единица"
    patterns = [
        (r'(\d+)\s*(?:час|часа|часов)', 3600),
        (r'(\d+)\s*(?:минут|минуты|минуту)', 60),
        (r'(\d+)\s*(?:секунд|секунды|секунду)', 1),
    ]

    for pattern, multiplier in patterns:
        m = re.search(pattern, text)
        if m:
            total += int(m.group(1)) * multiplier
            found = True

    # Если нашли хотя бы одну единицу — возвращаем
    if found:
        return total

    # Пробуем просто число (без единицы → минуты)
    num = BaseCommand._extract_number(text)
    if num:
        return num * 60

    return None


def _parse_absolute_time(text: str) -> int | None:
    """
    Парсинг абсолютного времени.

    "в 15:30" → секунд до 15:30
    "в 3 часа" → секунд до 15:00 или 03:00
    "в полдень" → секунд до 12:00
    """
    now = datetime.datetime.now()

    # "в HH:MM"
    m = re.search(r'в\s+(\d{1,2})[:\.](\d{2})', text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            return int((target - now).total_seconds())

    # "в N часов/часа"
    m = re.search(r'в\s+(\d{1,2})\s*(?:час|часа|часов)', text)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            target = now.replace(hour=h, minute=0, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            return int((target - now).total_seconds())

    # Специальные слова
    specials = {
        "полдень": (12, 0),
        "полночь": (0, 0),
        "утром": (9, 0),
        "днём": (14, 0),
        "вечером": (19, 0),
    }

    for word, (h, mi) in specials.items():
        if word in text:
            target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            return int((target - now).total_seconds())

    return None

def _format_target_time(seconds: int) -> str:
    """Форматирование целевого времени."""
    target = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
    return target.strftime("%H:%M")


# ═══════════════════════════════════════════
#  Команды
# ═══════════════════════════════════════════

class ReminderCommand(BaseCommand):
    """
    Создать напоминание.

    Относительное время:
    - "напомни через 5 минут купить молоко"
    - "напомни через час что встреча"
    - "напомни через полчаса выключить плиту"

    Абсолютное время:
    - "напомни в 15:30 позвонить маме"
    - "напомни в 3 часа про совещание"

    Повторяющиеся:
    - "напоминай каждые 30 минут пить воду"
    """
    name = "reminder"
    keywords = [
        "напомни", "напомнить", "напоминай",
        "напомни мне", "напомни через", "напомни в",
    ]
    priority = 13

    def match(self, text: str) -> tuple[bool, dict]:
        from commands.base import normalize_text
        t = normalize_text(text)

        # Проверяем что это напоминание
        if not any(kw in t for kw in ["напомн", "напоминай"]):
            return False, {}

        params = self._parse_reminder(t)

        if params.get("seconds"):
            return True, params

        return False, {}

    def _parse_reminder(self, text: str) -> dict:
        """Парсинг текста напоминания."""
        repeating = "каждые" in text or "каждый" in text or "напоминай" in text

        # Извлекаем сообщение
        message = self._extract_message(text)

        # Абсолютное время ("в 15:30")
        if " в " in text and ":" in text:
            seconds = _parse_absolute_time(text)
            if seconds:
                return {
                    "seconds": seconds,
                    "message": message or "время пришло",
                    "repeating": False,  # Абсолютное время не повторяется
                    "is_absolute": True,
                }

        # Абсолютное время ("в 3 часа")
        if re.search(r'в\s+\d{1,2}\s*(?:час|часа|часов)', text):
            seconds = _parse_absolute_time(text)
            if seconds:
                return {
                    "seconds": seconds,
                    "message": message or "время пришло",
                    "repeating": False,
                    "is_absolute": True,
                }

        # Относительное время
        seconds = _parse_relative_time(text)
        if seconds:
            return {
                "seconds": seconds,
                "message": message or "время вышло",
                "repeating": repeating,
                "is_absolute": False,
            }

        return {}

    def _extract_message(self, text: str) -> str:
        """Извлечь текст сообщения из напоминания."""
        # Убираем всё до содержимого
        # Паттерны: "через X минут что ...", "через X минут о ...",
        #           "через X минут ...", "в HH:MM ..."
        patterns = [
            r'(?:через\s+.+?(?:минут|секунд|час\w*))\s+(?:что\s+|о\s+|про\s+)?(.*)',
            r'(?:в\s+\d{1,2}[:.]\d{2})\s+(?:что\s+|о\s+|про\s+)?(.*)',
            r'(?:в\s+\d{1,2}\s*час\w*)\s+(?:что\s+|о\s+|про\s+)?(.*)',
            r'(?:каждые\s+.+?(?:минут|секунд|час\w*))\s+(?:что\s+|о\s+|про\s+)?(.*)',
            r'напомн\w*\s+(?:мне\s+)?(?:что\s+|о\s+|про\s+)(.*)',
        ]

        for pat in patterns:
            m = re.search(pat, text)
            if m and m.group(1):
                msg = m.group(1).strip()
                # Убираем мусор с конца
                msg = msg.rstrip(" .,!?")
                if msg and len(msg) > 1:
                    return msg

        return ""

    def execute(self, text: str, params: dict) -> CommandResult:
        seconds = params.get("seconds", 60)
        message = params.get("message", "время вышло")
        repeating = params.get("repeating", False)
        is_absolute = params.get("is_absolute", False)

        mgr = get_reminder_manager()

        def _notify(msg):
            self.speaker.say(f"Напоминание: {msg}!")
            notifier.notify("🔔 Напоминание", msg)

        mgr.add(seconds, message, _notify, repeating=repeating)

        # Формируем ответ
        if is_absolute:
            target_time = _format_target_time(seconds)
            self.speaker.say(
                f"Напомню в {target_time}: {message}."
            )
        elif repeating:
            interval = _format_duration(seconds)
            self.speaker.say(
                f"Буду напоминать каждые {interval}: {message}."
            )
        else:
            duration = _format_duration(seconds)
            self.speaker.say(f"Напомню через {duration}: {message}.")

        return CommandResult(follow_up=True)


class CancelReminderCommand(BaseCommand):
    """Отменить напоминание."""
    name = "cancel_reminder"
    keywords = [
        "отмени напоминание", "отменить напоминание",
        "убери напоминание", "хватит напоминать",
        "отмени все напоминания", "больше не напоминай",
    ]
    priority = 14

    def execute(self, text: str, params: dict) -> CommandResult:
        mgr = get_reminder_manager()

        if any(w in text for w in ["все", "всё", "больше не"]):
            count = mgr.cancel_all()
            if count:
                self.speaker.say(f"Отменила {count} напоминаний.")
            else:
                self.speaker.say("Нет активных напоминаний.")
        else:
            message = mgr.cancel_last()
            if message:
                self.speaker.say(f"Напоминание отменено: {message}.")
            else:
                self.speaker.say("Нет активных напоминаний.")

        return CommandResult(follow_up=True)


class ListRemindersCommand(BaseCommand):
    """Список активных напоминаний."""
    name = "list_reminders"
    keywords = [
        "какие напоминания", "список напоминаний",
        "активные напоминания", "что напомнить",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        mgr = get_reminder_manager()
        active = mgr.get_active()

        if not active:
            self.speaker.say("Нет активных напоминаний.")
        else:
            parts = []
            for r in active:
                remaining = _format_duration(r["remaining_sec"])
                repeat_mark = " (повтор)" if r["repeating"] else ""
                parts.append(
                    f"{r['message']}, через {remaining}{repeat_mark}"
                )
            self.speaker.say(
                f"Напоминаний: {len(active)}. "
                + "; ".join(parts) + "."
            )

        return CommandResult(follow_up=True)