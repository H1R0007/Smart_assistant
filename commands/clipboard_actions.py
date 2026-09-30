# commands/clipboard_actions.py

"""
Работа с буфером обмена и текстом.

v3.2:
- Безопасный ввод через единый core.input_sim
- Трансформация текста в буфере (uppercase, lowercase)
- Подсчёт символов/слов
- Очистка буфера
"""

import time
import logging

import pyperclip
from commands.base import BaseCommand, CommandResult
from core.input_sim import send_hotkey, VK

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
#  Утилиты
# ═══════════════════════════════════════════

def _paste_from_clipboard() -> None:
    """Вставить содержимое буфера обмена (Ctrl+V)."""
    time.sleep(0.05)  # Даём время буферу обновиться
    send_hotkey(VK.CONTROL, VK.V)

def _select_all() -> None:
    """Выделить всё (Ctrl+A)."""
    send_hotkey(VK.CONTROL, VK.A)

# ═══════════════════════════════════════════
#  Команды (БЕЗ ИЗМЕНЕНИЙ В ЛОГИКЕ)
# ═══════════════════════════════════════════

class CopyTextCommand(BaseCommand):
    """Скопировать произнесённый текст в буфер обмена."""
    name = "copy_text"
    keywords = ["скопируй текст", "скопируй", "в буфер обмена"]
    priority = 8

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords + [
            "пожалуйста", "мне", "следующий"
        ])
        return {"text": cleaned}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("text", "").strip())

    def execute(self, text: str, params: dict) -> CommandResult:
        content = params.get("text", "").strip()
        if not content:
            self.speaker.say("Что скопировать?")
            return CommandResult()
        try:
            pyperclip.copy(content)
            preview = content[:50] + ("..." if len(content) > 50 else "")
            self.speaker.say(f"Скопировала: {preview}")
        except Exception:
            self.speaker.say("Не удалось скопировать.")
        return CommandResult(follow_up=True)

class TypeTextCommand(BaseCommand):
    """
    Набрать текст с клавиатуры (диктовка).
    Использует SendInput вместо keybd_event.
    """
    name = "type_text"
    keywords = ["напечатай", "набери", "напиши текст", "введи"]
    priority = 8

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords + [
            "пожалуйста", "мне", "текст"
        ])
        return {"text": cleaned}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("text", "").strip())

    def execute(self, text: str, params: dict) -> CommandResult:
        content = params.get("text", "").strip()
        if not content:
            self.speaker.say("Что напечатать?")
            return CommandResult()
        try:
            old_clipboard = ""
            try:
                old_clipboard = pyperclip.paste()
            except Exception:
                pass

            pyperclip.copy(content)
            _paste_from_clipboard()

            time.sleep(0.2)
            try:
                if old_clipboard:
                    pyperclip.copy(old_clipboard)
            except Exception:
                pass

            self.speaker.say("Напечатала.")
        except Exception as e:
            self.speaker.say("Не удалось напечатать.")
            self.logger.error("Type error: %s", e)
        return CommandResult(follow_up=True)

class TransformClipboardCommand(BaseCommand):
    """Трансформировать текст в буфере обмена."""
    name = "transform_clipboard"
    keywords = [
        "сделай заглавными", "в верхний регистр", "капслок",
        "сделай строчными", "в нижний регистр",
        "сколько символов", "сколько слов", "длина текста",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        t = text.lower()
        try:
            content = pyperclip.paste()
            if not content:
                self.speaker.say("Буфер пуст.")
                return CommandResult(follow_up=True)
        except Exception:
            self.speaker.say("Не удалось прочитать буфер.")
            return CommandResult(follow_up=True)

        if any(w in t for w in ["заглавн", "верхн", "капслок"]):
            pyperclip.copy(content.upper())
            self.speaker.say("Текст переведён в верхний регистр.")
        elif any(w in t for w in ["строчн", "нижн"]):
            pyperclip.copy(content.lower())
            self.speaker.say("Текст переведён в нижний регистр.")
        elif any(w in t for w in ["символ", "длина"]):
            chars = len(content)
            words = len(content.split())
            self.speaker.say(f"В буфере {chars} символов и {words} слов.")
        elif "слов" in t:
            words = len(content.split())
            self.speaker.say(f"В буфере {words} слов.")
        else:
            self.speaker.say("Не поняла, что сделать с текстом.")

        return CommandResult(follow_up=True)

class ClearClipboardCommand(BaseCommand):
    """Очистить буфер обмена."""
    name = "clear_clipboard"
    keywords = ["очисти буфер", "очистить буфер обмена"]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        try:
            pyperclip.copy("")
            self.speaker.say("Буфер очищен.")
        except Exception:
            self.speaker.say("Не удалось очистить буфер.")
        return CommandResult(follow_up=True)