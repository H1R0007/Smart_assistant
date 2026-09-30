# commands/media.py

"""
Управление медиа и громкостью.

v3.1:
- Единый input_sim вместо собственных SendInput структур
- COM-инициализация для pycaw
- Без дублирования кода клавиш
"""

import logging

from commands.base import BaseCommand, CommandResult
from core import input_sim
from core.input_sim import VK
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Управление громкостью через Windows API
# ═══════════════════════════════════════════

class VolumeControl:
    """
    Прямое управление громкостью Windows.

    Пробует pycaw (точный контроль в процентах).
    Fallback на VK_VOLUME_UP/DOWN (медиа-клавиши).
    """

    def __init__(self):
        self._pycaw_available = False
        self._volume_interface = None
        self._init_pycaw()

    def _init_pycaw(self):
        """Инициализация pycaw с COM."""
        try:
            import comtypes
            try:
                comtypes.CoInitialize()
            except OSError:
                pass

            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None
            )
            self._volume_interface = interface.QueryInterface(
                IAudioEndpointVolume
            )
            self._pycaw_available = True
            logger.debug("VolumeControl: pycaw инициализирован")
        except ImportError:
            logger.debug("VolumeControl: pycaw не установлен")
        except Exception as e:
            logger.debug("VolumeControl: pycaw ошибка: %s", e)

    def get_volume(self) -> int | None:
        if self._pycaw_available:
            try:
                level = self._volume_interface.GetMasterVolumeLevelScalar()
                return int(round(level * 100))
            except Exception:
                pass
        return None

    def set_volume(self, percent: int) -> bool:
        percent = max(0, min(100, percent))
        if self._pycaw_available:
            try:
                self._volume_interface.SetMasterVolumeLevelScalar(
                    percent / 100.0, None
                )
                return True
            except Exception:
                pass
        return False

    def change_volume(self, delta: int) -> int | None:
        current = self.get_volume()
        if current is not None:
            new = max(0, min(100, current + delta))
            if self.set_volume(new):
                return new

        # Fallback через input_sim
        steps = max(1, abs(delta) // 2)
        if delta > 0:
            input_sim.volume_up(steps)
        else:
            input_sim.volume_down(steps)
        return None

    def is_muted(self) -> bool | None:
        if self._pycaw_available:
            try:
                return bool(self._volume_interface.GetMute())
            except Exception:
                pass
        return None

    def set_mute(self, muted: bool) -> bool:
        if self._pycaw_available:
            try:
                self._volume_interface.SetMute(muted, None)
                return True
            except Exception:
                pass
        input_sim.mute_toggle()
        return True

# ═══════════════════════════════════════════
#  Команды громкости
# ═══════════════════════════════════════════

class VolumeUpCommand(BaseCommand):
    name = "volume_up"
    keywords = [
        "громче", "прибавь громкость", "увеличь громкость",
        "прибавь звук", "погромче", "увеличь звук",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        vol = self.ctx.volume_control
        if not vol: return CommandResult()
        amount = self._extract_number(text)
        if amount is None:
            amount = 10
        amount = min(amount, 50)

        new_level = vol.change_volume(amount)

        if new_level is not None:
            self.speaker.say(f"Громкость {new_level} процентов.")
        else:
            self.speaker.say("Делаю громче.")
        return CommandResult(follow_up=True)


class VolumeDownCommand(BaseCommand):
    name = "volume_down"
    keywords = [
        "тише", "убавь громкость", "уменьши громкость",
        "убавь звук", "потише", "уменьши звук",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        vol = self.ctx.volume_control
        if not vol: return CommandResult()
        amount = self._extract_number(text)
        if amount is None:
            amount = 10
        amount = min(amount, 50)

        new_level = vol.change_volume(-amount)

        if new_level is not None:
            self.speaker.say(f"Громкость {new_level} процентов.")
        else:
            self.speaker.say("Делаю тише.")
        return CommandResult(follow_up=True)


class SetVolumeCommand(BaseCommand):
    name = "set_volume"
    keywords = [
        "поставь громкость на", "установи громкость на",
        "громкость на", "звук на",
    ]
    priority = 9

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        num = self._extract_number(text)
        return {"percent": num}

    def execute(self, text: str, params: dict) -> CommandResult:
        percent = params.get("percent")
        if percent is None:
            self.speaker.say("Не поняла, на сколько процентов.")
            return CommandResult(follow_up=True)

        percent = max(0, min(100, percent))
        vol = self.ctx.volume_control
        if not vol: return CommandResult()

        if vol.set_volume(percent):
            self.speaker.say(f"Громкость {percent} процентов.")
        else:
            self.speaker.say("Не удалось установить громкость.")
        return CommandResult(follow_up=True)


class MuteCommand(BaseCommand):
    name = "mute"
    keywords = [
        "выключи звук", "без звука", "мут", "замьють", "mute",
    ]
    priority = 9

    def execute(self, text: str, params: dict) -> CommandResult:
        vol = self.ctx.volume_control
        if not vol: return CommandResult()
        current_mute = vol.is_muted()

        if current_mute is True:
            vol.set_mute(False)
            level = vol.get_volume()
            if level is not None:
                self.speaker.say(
                    f"Звук включён. Громкость {level} процентов."
                )
            else:
                self.speaker.say("Звук включён.")
        else:
            vol.set_mute(True)
            self.speaker.say("Звук выключен.")
        return CommandResult(follow_up=True)


class UnmuteCommand(BaseCommand):
    name = "unmute"
    keywords = [
        "включи звук", "верни звук", "убери мут",
        "анмьють", "unmute",
    ]
    priority = 9

    def execute(self, text: str, params: dict) -> CommandResult:
        vol = self.ctx.volume_control
        if not vol: return CommandResult()
        vol.set_mute(False)
        level = vol.get_volume()
        if level is not None:
            self.speaker.say(
                f"Звук включён. Громкость {level} процентов."
            )
        else:
            self.speaker.say("Звук включён.")
        return CommandResult(follow_up=True)


class CurrentVolumeCommand(BaseCommand):
    name = "current_volume"
    keywords = [
        "какая громкость", "текущая громкость",
        "уровень громкости", "сколько громкость",
    ]
    priority = 9

    def execute(self, text: str, params: dict) -> CommandResult:
        vol = self.ctx.volume_control
        if not vol: return CommandResult()
        level = vol.get_volume()
        muted = vol.is_muted()

        if level is not None:
            msg = f"Громкость {level} процентов"
            if muted:
                msg += ", звук выключен"
            self.speaker.say(msg + ".")
        else:
            self.speaker.say("Не удалось определить громкость.")
        return CommandResult(follow_up=True)