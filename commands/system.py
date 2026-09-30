# commands/system.py

"""
Системные команды: блокировка, выключение, перезагрузка, сон.

v3.1:
- Подтверждение для опасных команд
- Отмена выключения/перезагрузки
- Информация о планировщике задач
- Открытие системных утилит
"""

import subprocess
import ctypes
import logging
import os

from commands.base import BaseCommand, CommandResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Базовый класс для опасных команд
# ═══════════════════════════════════════════

class _DangerousCommand(BaseCommand):
    """
    Базовый класс для опасных команд.

    Требует голосового подтверждения перед выполнением.
    """
    fuzzy_enabled = False
    _confirm_timeout = 5
    _confirm_question = "Вы уверены?"
    _cancel_message = "Отменено."

    def execute(self, text: str, params: dict) -> CommandResult:
        self.speaker.say(self._confirm_question)

        recognizer = self.ctx.recognizer
        if recognizer is None:
            return self._do_execute()

        response = recognizer.listen(
            timeout=self._confirm_timeout,
            phrase_limit=3,
        )

        if response and self._is_positive_response(response):
            return self._do_execute()
        else:
            self.speaker.say(self._cancel_message)
            return CommandResult(follow_up=True)

    def _do_execute(self) -> CommandResult:
        raise NotImplementedError



# ═══════════════════════════════════════════
#  Команды
# ═══════════════════════════════════════════

class LockCommand(BaseCommand):
    name = "lock"
    keywords = ["заблокируй", "блокировка", "заблокировать", "залочь"]
    priority = 10
    fuzzy_enabled = False

    def execute(self, text: str, params: dict) -> CommandResult:
        self.speaker.say("Блокирую компьютер.")
        ctypes.windll.user32.LockWorkStation()
        return CommandResult(follow_up=False)


class ShutdownCommand(_DangerousCommand):
    name = "shutdown"
    keywords = [
        "выключи компьютер", "выключить компьютер",
        "завершение работы", "выруби компьютер",
    ]
    priority = 15
    _confirm_question = "Выключить компьютер? Скажи да для подтверждения."
    _cancel_message = "Выключение отменено."

    def _do_execute(self) -> CommandResult:
        self.speaker.say("Выключаю через 10 секунд.")
        subprocess.run(
            ["shutdown", "/s", "/t", "10"], capture_output=True
        )
        return CommandResult(follow_up=False)


class RestartCommand(_DangerousCommand):
    name = "restart"
    keywords = ["перезагрузи", "перезагрузить", "перезагрузка"]
    priority = 10
    _confirm_question = (
        "Перезагрузить компьютер? "
        "Скажи да для подтверждения."
    )
    _cancel_message = "Перезагрузка отменена."

    def _do_execute(self) -> CommandResult:
        self.speaker.say("Перезагружаю через 10 секунд.")
        subprocess.run(
            ["shutdown", "/r", "/t", "10"], capture_output=True
        )
        return CommandResult(follow_up=False)


class CancelShutdownCommand(BaseCommand):
    name = "cancel_shutdown"
    keywords = [
        "отмени выключение", "отмена выключения",
        "не выключай", "отмени перезагрузку",
    ]
    priority = 20
    fuzzy_enabled = False

    def execute(self, text: str, params: dict) -> CommandResult:
        subprocess.run(["shutdown", "/a"], capture_output=True)
        self.speaker.say("Отменено.")
        return CommandResult(follow_up=True)


class SleepCommand(_DangerousCommand):
    name = "sleep"
    keywords = ["спящий режим", "усыпи компьютер", "в спящий режим"]
    priority = 10
    _confirm_question = "Перейти в спящий режим?"
    _cancel_message = "Отменено."

    def _do_execute(self) -> CommandResult:
        self.speaker.say("Спящий режим.")
        subprocess.run(
            [
                "powershell", "-Command",
                "Add-Type -Assembly System.Windows.Forms; "
                "[System.Windows.Forms.Application]::SetSuspendState("
                "'Suspend', $false, $false)"
            ],
            capture_output=True
        )
        return CommandResult(follow_up=False)


class EmptyTrashCommand(BaseCommand):
    name = "empty_trash"
    keywords = ["очисти корзину", "очистить корзину"]
    priority = 10
    fuzzy_enabled = False

    def execute(self, text: str, params: dict) -> CommandResult:
        try:
            ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x07)
            self.speaker.say("Корзина очищена.")
        except Exception:
            self.speaker.say("Не удалось очистить корзину.")
        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Быстрый доступ к системным утилитам
# ═══════════════════════════════════════════

class OpenSystemToolCommand(BaseCommand):
    """
    Открыть системные утилиты голосом.

    "открой диспетчер устройств"
    "открой сетевые подключения"
    "открой управление дисками"
    """
    name = "system_tool"
    keywords = [
        "диспетчер устройств", "сетевые подключения",
        "управление дисками", "службы", "редактор реестра",
        "переменные среды", "брандмауэр", "центр обновления",
        "удаление программ", "автозагрузка",
    ]
    priority = 11
    fuzzy_enabled = False

    TOOLS = {
        "диспетчер устройств": "devmgmt.msc",
        "сетевые подключения": "ncpa.cpl",
        "управление дисками": "diskmgmt.msc",
        "службы": "services.msc",
        "редактор реестра": "regedit",
        "переменные среды": (
            "rundll32.exe sysdm.cpl,EditEnvironmentVariables"
        ),
        "брандмауэр": "firewall.cpl",
        "центр обновления": "ms-settings:windowsupdate",
        "удаление программ": "appwiz.cpl",
        "автозагрузка": "ms-settings:startupapps",
    }

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        t = text.lower()
        for tool_name, tool_cmd in self.TOOLS.items():
            if tool_name in t:
                return {"tool": tool_cmd, "name": tool_name}
        return {}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("tool"))

    def execute(self, text: str, params: dict) -> CommandResult:
        tool = params.get("tool")
        name = params.get("name")

        if not tool:
            self.speaker.say("Какую утилиту открыть?")
            return CommandResult(follow_up=True)

        try:
            if tool.startswith("ms-settings:"):
                os.startfile(tool)
            else:
                subprocess.Popen(
                    tool,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            self.speaker.say(f"Открываю {name}.")
        except Exception as e:
            self.logger.error("System tool %s: %s", tool, e)
            self.speaker.say(f"Не удалось открыть {name}.")

        return CommandResult(follow_up=True)