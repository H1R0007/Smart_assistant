# commands/info.py

"""
Информационные команды: время, дата, погода, система.

v3.1:
- Погода: fallback на второй сервис, кэширование
- Время: расширенный матчинг ("подскажи время", "сколько сейчас")
- Дата: корректные окончания числительных
- Система: температура CPU, сеть, аптайм
- Батарея: оставшееся время работы
- IP: локальный + внешний
"""

import datetime
import time
import socket
import logging
import psutil
import requests
from commands.base import BaseCommand, CommandResult
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Кэш погоды
# ═══════════════════════════════════════════

class WeatherCache:
    """
    Кэш погодных данных.
    
    Зачем: wttr.in отвечает 1-3 секунды. Если спросить погоду
    дважды за 10 минут — незачем ходить в сеть снова.
    """
    
    def __init__(self, ttl_seconds: int = 600):
        self._cache: dict[str, tuple[str, float]] = {}
        self._ttl = ttl_seconds
    
    def get(self, city: str) -> str | None:
        key = city.lower()
        if key in self._cache:
            data, timestamp = self._cache[key]
            if time.time() - timestamp < self._ttl:
                return data
            del self._cache[key]
        return None
    
    def set(self, city: str, data: str) -> None:
        self._cache[city.lower()] = (data, time.time())


# ═══════════════════════════════════════════
#  Время
# ═══════════════════════════════════════════

class TimeCommand(BaseCommand):
    name = "time"
    description = "Говорить текущее время"
    category = "Информация"
    keywords = [
        "который час", "сколько времени", "сколько время",
        "текущее время", "подскажи время", "покажи время",
        "сколько сейчас",
    ]
    priority = 7

    def match(self, text: str) -> tuple[bool, dict]:
        t = text.lower().strip()

        # Прямые фразы — всегда матч
        direct_phrases = [
            "который час", "сколько времени", "сколько время",
            "текущее время", "подскажи время", "покажи время",
            "сколько сейчас", "скажи время",
        ]
        for phrase in direct_phrases:
            if phrase in t:
                return True, {}

        # Одиночное "время" — только если это всё сообщение
        if t == "время":
            return True, {}

        return False, {}

    def execute(self, text: str, params: dict) -> CommandResult:
        now = datetime.datetime.now()
        h, m = now.hour, now.minute

        # Красивое произношение минут
        if m == 0:
            time_str = f"{h} ровно"
        elif m < 10:
            time_str = f"{h} ноль {m}"
        else:
            time_str = f"{h} {m}"

        self.speaker.say(f"Сейчас {time_str}.")
        return CommandResult()


# ═══════════════════════════════════════════
#  Дата
# ═══════════════════════════════════════════

class DateCommand(BaseCommand):
    name = "date"
    description = "Говорить дату"
    category = "Информация"
    keywords = [
        "какое число", "какой день", "день недели",
        "сегодняшняя дата", "какая дата", "какое сегодня",
        "что за день",
    ]
    priority = 8

    MONTHS = [
        "", "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря"
    ]
    WEEKDAYS = [
        "понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье"
    ]

    def match(self, text: str) -> tuple[bool, dict]:
        t = text.lower()
        date_phrases = [
            "какое число", "какой день", "день недели",
            "какая дата", "сегодняшняя дата", "какое сегодня",
            "что за день", "какой сегодня день",
            "число сегодня", "сегодняшнее число",
        ]
        for phrase in date_phrases:
            if phrase in t:
                return True, {}
        return False, {}

    def execute(self, text: str, params: dict) -> CommandResult:
        now = datetime.datetime.now()
        weekday = self.WEEKDAYS[now.weekday()]
        month = self.MONTHS[now.month]
        year = now.year

        # Полная дата если спрашивают "какая дата"
        if "дата" in text.lower() or "число" in text.lower():
            self.speaker.say(
                f"Сегодня {weekday}, {now.day} {month} {year} года."
            )
        else:
            self.speaker.say(f"Сегодня {weekday}, {now.day} {month}.")

        return CommandResult()


# ═══════════════════════════════════════════
#  Погода
# ═══════════════════════════════════════════

class WeatherCommand(BaseCommand):
    name = "weather"
    description = "Узнавать погоду"
    category = "Информация"
    keywords = [
        "погода", "погоду", "температура на улице", "какая погода",
        "что с погодой", "прогноз погоды", "за окном",
    ]
    priority = 10

    # Маппинг русских названий городов
    CITY_ALIASES = {
        "москва": "Moscow",
        "москве": "Moscow",
        "питер": "Saint Petersburg",
        "питере": "Saint Petersburg",
        "петербург": "Saint Petersburg",
        "петербурге": "Saint Petersburg",
        "новосибирск": "Novosibirsk",
        "новосибирске": "Novosibirsk",
        "екатеринбург": "Yekaterinburg",
        "екатеринбурге": "Yekaterinburg",
        "казань": "Kazan",
        "казани": "Kazan",
        "сочи": "Sochi",
        "лондон": "London",
        "лондоне": "London",
        "нью-йорк": "New York",
        "нью-йорке": "New York",
        "париж": "Paris",
        "париже": "Paris",
        "берлин": "Berlin",
        "берлине": "Berlin",
        "токио": "Tokyo",
    }

    def match(self, text: str) -> tuple[bool, dict]:
        t = text.lower()
        # "температура" без контекста улицы может быть про систему
        if "температура" in t and "на улице" not in t and "погод" not in t:
            return False, {}
        return super().match(t)

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords + [
            "в городе", "в", "покажи", "скажи", "какая", "сейчас",
            "на улице", "расскажи", "про", "мне", "городе",
        ])

        city = None
        if cleaned:
            # Проверяем алиасы
            cleaned_lower = cleaned.lower().strip()
            for alias, eng_name in self.CITY_ALIASES.items():
                if alias in cleaned_lower:
                    city = eng_name
                    break
            if not city and cleaned.strip():
                city = cleaned.strip()

        return {"city": city}

    def execute(self, text: str, params: dict) -> CommandResult:
        city = (params.get("city")
                or self.config.get("weather", "city", default="Moscow"))
        cache = self.ctx.weather_cache
        cached = cache.get(city) if cache else None
        
        if cached:
            self.speaker.say(cached)
            return CommandResult(follow_up=True)

        weather_text = self._fetch_weather(city)

        if weather_text:
            city_display = self._display_city(city)
            response = f"Погода в {city_display}: {weather_text}."

            if cache:
                cache.set(city, response)
                
            self.speaker.say(response)
        else:
            self.speaker.say("Не удалось получить погоду.")

        return CommandResult(follow_up=True)

    def _fetch_weather(self, city: str) -> str | None:
        """Получить погоду с fallback."""
        # Основной: wttr.in
        result = self._try_wttr(city)
        if result:
            return result

        # Fallback: wttr.in с другим форматом
        result = self._try_wttr_v2(city)
        if result:
            return result

        return None

    def _try_wttr(self, city: str) -> str | None:
        try:
            resp = requests.get(
                f"https://wttr.in/{city}?format=%t+%C&lang=ru",
                timeout=5,
                headers={"User-Agent": "curl/7.0"}
            )
            if resp.ok and "Unknown" not in resp.text:
                return resp.text.strip()
        except Exception as e:
            self.logger.debug("wttr.in error: %s", e)
        return None

    def _try_wttr_v2(self, city: str) -> str | None:
        try:
            resp = requests.get(
                f"https://wttr.in/{city}?format=3&lang=ru",
                timeout=5,
                headers={"User-Agent": "curl/7.0"}
            )
            if resp.ok and "Unknown" not in resp.text:
                # Формат: "City: ⛅ +15°C"
                text = resp.text.strip()
                # Убираем название города (мы его и так знаем)
                if ":" in text:
                    text = text.split(":", 1)[1].strip()
                return text
        except Exception as e:
            self.logger.debug("wttr.in v2 error: %s", e)
        return None
    
    def update_context(self, params: dict) -> None:
        self.ctx.context.update("weather", params.get("city"), params)

    @staticmethod
    def _display_city(city: str) -> str:
        """Красивое название города для озвучки."""
        display_map = {
            "Moscow": "Москве",
            "Saint Petersburg": "Санкт-Петербурге",
            "Novosibirsk": "Новосибирске",
            "Yekaterinburg": "Екатеринбурге",
            "Kazan": "Казани",
            "Sochi": "Сочи",
            "London": "Лондоне",
            "New York": "Нью-Йорке",
            "Paris": "Париже",
            "Berlin": "Берлине",
            "Tokyo": "Токио",
        }
        return display_map.get(city, city)
    
    

# ═══════════════════════════════════════════
#  Системная информация
# ═══════════════════════════════════════════

class SystemInfoCommand(BaseCommand):
    name = "system_info"
    keywords = [
        "состояние системы", "инфо о системе", "системная информация",
        "загрузка процессора", "сколько оперативки",
        "информация о системе", "статус системы",
    ]
    priority = 12

    def execute(self, text: str, params: dict) -> CommandResult:
        t = text.lower()

        # Определяем что именно спрашивают
        parts = []

        # Всегда показываем CPU и RAM
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        parts.append(f"Процессор: {cpu} процентов")
        parts.append(
            f"Оперативка: {ram.percent} процентов "
            f"из {ram.total // (1024**3)} гигабайт"
        )

        # Диск — если спрашивают подробности или "состояние системы"
        if any(w in t for w in ["диск", "система", "состояние", "инфо"]):
            disk = psutil.disk_usage('C:/')
            free_gb = disk.free // (1024**3)
            parts.append(
                f"Диск C: {disk.percent} процентов, "
                f"свободно {free_gb} гигабайт"
            )

        # Аптайм
        if any(w in t for w in ["система", "состояние", "инфо"]):
            boot = datetime.datetime.fromtimestamp(psutil.boot_time())
            uptime = datetime.datetime.now() - boot
            hours = int(uptime.total_seconds() // 3600)
            minutes = int((uptime.total_seconds() % 3600) // 60)
            if hours > 0:
                parts.append(f"Аптайм: {hours} часов {minutes} минут")
            else:
                parts.append(f"Аптайм: {minutes} минут")

        # Сеть
        if any(w in t for w in ["сеть", "система", "состояние"]):
            net = psutil.net_io_counters()
            sent_mb = net.bytes_sent // (1024**2)
            recv_mb = net.bytes_recv // (1024**2)
            parts.append(
                f"Сеть: отправлено {sent_mb} мегабайт, "
                f"получено {recv_mb} мегабайт"
            )

        self.speaker.say(". ".join(parts) + ".")
        return CommandResult()


# ═══════════════════════════════════════════
#  Батарея
# ═══════════════════════════════════════════

class BatteryCommand(BaseCommand):
    name = "battery"
    keywords = [
        "заряд батареи", "заряд аккумулятора", "аккумулятор",
        "батарея", "сколько заряда", "уровень заряда",
    ]
    priority = 7

    def execute(self, text: str, params: dict) -> CommandResult:
        battery = psutil.sensors_battery()
        if battery is None:
            self.speaker.say("Батарея не обнаружена. Скорее всего, стационарный компьютер.")
        else:
            pct = int(battery.percent)
            plugged = "на зарядке" if battery.power_plugged else "от батареи"

            parts = [f"Заряд {pct} процентов, {plugged}"]

            # Оставшееся время
            if not battery.power_plugged and battery.secsleft > 0:
                hours = battery.secsleft // 3600
                minutes = (battery.secsleft % 3600) // 60
                if hours > 0:
                    parts.append(
                        f"осталось примерно {hours} часов {minutes} минут"
                    )
                else:
                    parts.append(f"осталось примерно {minutes} минут")

            self.speaker.say(". ".join(parts) + ".")

        return CommandResult()


# ═══════════════════════════════════════════
#  IP адрес
# ═══════════════════════════════════════════

class IPCommand(BaseCommand):
    name = "ip_address"
    keywords = ["мой ip", "мой айпи", "ip адрес", "айпи адрес"]
    priority = 7

    def execute(self, text: str, params: dict) -> CommandResult:
        parts = []

        # Локальный IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            local_spoken = local_ip.replace(".", " точка ")
            parts.append(f"Локальный: {local_spoken}")
        except Exception:
            pass

        # Внешний IP
        try:
            resp = requests.get(
                "https://api.ipify.org?format=json", timeout=5
            )
            ip = resp.json()["ip"]
            ip_spoken = ip.replace(".", " точка ")
            parts.append(f"Внешний: {ip_spoken}")
        except Exception:
            pass

        if parts:
            self.speaker.say(". ".join(parts) + ".")
        else:
            self.speaker.say("Не удалось определить IP.")

        return CommandResult()