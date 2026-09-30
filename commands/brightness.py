# commands/brightness.py

"""
Управление яркостью экрана.

v3.1:
- Установка конкретного значения ("яркость на 50")
- Текущая яркость ("какая яркость")
"""

from commands.base import BaseCommand, CommandResult


class BrightnessUpCommand(BaseCommand):
    name = "brightness_up"
    keywords = [
        "ярче", "увеличь яркость", "прибавь яркость", "светлее",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        amount = self._extract_number(text) or 20
        amount = min(amount, 50)

        try:
            import screen_brightness_control as sbc
            current = sbc.get_brightness()[0]
            new = min(100, current + amount)
            sbc.set_brightness(new)
            self.speaker.say(f"Яркость {new} процентов.")
        except ImportError:
            self.speaker.say("Модуль яркости не установлен.")
        except Exception as e:
            self.speaker.say("Не удалось изменить яркость.")
            self.logger.error("Brightness error: %s", e)
        return CommandResult(follow_up=True)


class BrightnessDownCommand(BaseCommand):
    name = "brightness_down"
    keywords = [
        "темнее", "уменьши яркость", "убавь яркость", "затемни",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        amount = self._extract_number(text) or 20
        amount = min(amount, 50)

        try:
            import screen_brightness_control as sbc
            current = sbc.get_brightness()[0]
            new = max(5, current - amount)
            sbc.set_brightness(new)
            self.speaker.say(f"Яркость {new} процентов.")
        except ImportError:
            self.speaker.say("Модуль яркости не установлен.")
        except Exception as e:
            self.speaker.say("Не удалось изменить яркость.")
            self.logger.error("Brightness error: %s", e)
        return CommandResult(follow_up=True)


class SetBrightnessCommand(BaseCommand):
    """Установить яркость на конкретное значение."""
    name = "set_brightness"
    keywords = [
        "яркость на", "поставь яркость",
        "установи яркость",
    ]
    priority = 9

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        num = self._extract_number(text)
        return {"percent": num}

    def execute(self, text: str, params: dict) -> CommandResult:
        percent = params.get("percent")

        if percent is None:
            self.speaker.say("На сколько процентов?")
            return CommandResult(follow_up=True)

        percent = max(5, min(100, percent))

        try:
            import screen_brightness_control as sbc
            sbc.set_brightness(percent)
            self.speaker.say(f"Яркость {percent} процентов.")
        except ImportError:
            self.speaker.say("Модуль яркости не установлен.")
        except Exception:
            self.speaker.say("Не удалось установить яркость.")
        return CommandResult(follow_up=True)


class CurrentBrightnessCommand(BaseCommand):
    """Текущая яркость."""
    name = "current_brightness"
    keywords = [
        "какая яркость", "текущая яркость",
        "уровень яркости",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        try:
            import screen_brightness_control as sbc
            current = sbc.get_brightness()[0]
            self.speaker.say(f"Яркость {current} процентов.")
        except ImportError:
            self.speaker.say("Модуль яркости не установлен.")
        except Exception:
            self.speaker.say("Не удалось определить яркость.")
        return CommandResult(follow_up=True)