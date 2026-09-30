# tests/test_all_commands.py

"""
Полная карта возможностей ассистента + unit-тесты.

Запуск:
    python -m pytest tests/test_all_commands.py -v
    
Или без pytest:
    python tests/test_all_commands.py

Каждый тест проверяет:
1. match() — правильно ли распознаётся фраза
2. extract_params() — правильные ли параметры
3. is_valid_candidate() — валидация
4. Негативные случаи — НЕ должно срабатывать
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.base import (
    BaseCommand, CommandResult, normalize_text,
    stem_russian
)


# ═══════════════════════════════════════════
#  Мок-контекст для тестов
# ═══════════════════════════════════════════

class MockSpeaker:
    def __init__(self):
        self.last_said = ""
        self.said_history = []

    def say(self, text):
        self.last_said = text
        self.said_history.append(text)

    def say_streamed(self, text):
        self.say(text)

    def beep(self):
        pass

    def set_rate(self, rate):
        pass

    def set_volume(self, volume):
        pass


class MockConfig:
    def get(self, *keys, default=None):
        defaults = {
            ("listener", "wake_word"): "Пятница",
            ("weather", "city"): "Moscow",
            ("folders", "projects"): "C:/Projects",
        }
        return defaults.get(keys, default)

    def get_str(self, *keys, default=""):
        result = self.get(*keys, default=default)
        return str(result) if result else default

    def get_int(self, *keys, default=0):
        return default

    def get_float(self, *keys, default=0.0):
        return default

    def get_bool(self, *keys, default=False):
        return default

    @property
    def apps(self):
        return {}

    @property
    def process_names(self):
        return {}


class MockLLM:
    def __init__(self):
        self._history = [
            {"role": "assistant", "content": "Тестовый ответ от LLM."}
        ]

    def ask(self, query):
        return f"Ответ на: {query}"

    def get_last_response(self):
        return "Тестовый ответ от LLM."

    def clear_history(self):
        self._history.clear()


def make_ctx():
    ctx = MagicMock()
    ctx.speaker = MockSpeaker()
    ctx.config = MockConfig()
    ctx.llm = MockLLM()
    ctx._recognizer = None
    return ctx


# ═══════════════════════════════════════════
#  Тесты: base.py
# ═══════════════════════════════════════════

class TestNormalizeText(unittest.TestCase):
    """Нормализация текста перед матчингом."""

    def test_basic(self):
        self.assertEqual(normalize_text("  Привет  "), "привет")

    def test_punctuation(self):
        self.assertEqual(normalize_text("Привет!"), "привет")
        self.assertEqual(normalize_text("Время."), "время")
        self.assertEqual(normalize_text("...Стоп..."), "стоп")

    def test_commas(self):
        # Whisper часто добавляет запятые
        self.assertEqual(
            normalize_text("таймер, на 5 минут"),
            "таймер на 5 минут"
        )

    def test_multiple_spaces(self):
        self.assertEqual(
            normalize_text("открой   блокнот"),
            "открой блокнот"
        )

    def test_quotes(self):
        self.assertEqual(
            normalize_text('"Привет"'),
            "привет"
        )


class TestStemRussian(unittest.TestCase):
    """Стемминг русских слов."""

    def test_nouns(self):
        self.assertEqual(stem_russian("телеграму"), "телеграм")
        self.assertEqual(stem_russian("блокнота"), "блокнот")
        self.assertEqual(stem_russian("калькулятором"), "калькулятор")

    def test_short_words(self):
        # Короткие слова не стемятся
        self.assertEqual(stem_russian("код"), "код")
        self.assertEqual(stem_russian("вк"), "вк")

    def test_already_stemmed(self):
        self.assertEqual(stem_russian("блокнот"), "блокнот")
        self.assertEqual(stem_russian("хром"), "хром")


class TestExactMatch(unittest.TestCase):
    """Точное совпадение с границами слов."""

    def test_single_word(self):
        self.assertTrue(BaseCommand._exact_match("время", "время"))
        self.assertTrue(BaseCommand._exact_match("время", "покажи время"))
        self.assertTrue(
            BaseCommand._exact_match("время", "время сейчас")
        )

    def test_word_boundary(self):
        # "крой" НЕ должен найтись внутри "открой"
        self.assertFalse(BaseCommand._exact_match("крой", "открой"))
        # "код" НЕ внутри "кодировка"
        self.assertFalse(
            BaseCommand._exact_match("код", "кодировка")
        )

    def test_phrase(self):
        self.assertTrue(
            BaseCommand._exact_match(
                "сколько времени", "сколько времени сейчас"
            )
        )
        self.assertFalse(
            BaseCommand._exact_match(
                "закрой окно", "открой блокнот"
            )
        )


class TestExtractNumber(unittest.TestCase):
    """Извлечение чисел из текста."""

    def test_digits(self):
        self.assertEqual(BaseCommand._extract_number("через 5 минут"), 5)
        self.assertEqual(BaseCommand._extract_number("100 процентов"), 100)

    def test_words(self):
        self.assertEqual(BaseCommand._extract_number("через пять минут"), 5)
        self.assertEqual(BaseCommand._extract_number("через десять"), 10)

    def test_composite(self):
        self.assertEqual(
            BaseCommand._extract_number("двадцать пять"), 25
        )
        self.assertEqual(
            BaseCommand._extract_number("сто двадцать"), 120
        )

    def test_none(self):
        self.assertIsNone(BaseCommand._extract_number("нет чисел"))


# ═══════════════════════════════════════════
#  Тесты: Приложения
# ═══════════════════════════════════════════

class TestOpenApp(unittest.TestCase):
    """Открытие приложений."""

    def setUp(self):
        from commands.apps import OpenAppCommand
        self.cmd = OpenAppCommand(make_ctx())

    def test_single_app(self):
        """'открой блокнот' → блокнот."""
        matched, params = self.cmd.match("открой блокнот")
        self.assertTrue(matched)
        self.assertEqual(len(params["apps"]), 1)
        self.assertEqual(params["apps"][0]["app"], "notepad.exe")

    def test_multiple_apps(self):
        """'открой блокнот и калькулятор' → 2 приложения."""
        matched, params = self.cmd.match("открой блокнот и калькулятор")
        self.assertTrue(matched)
        self.assertEqual(len(params["apps"]), 2)

    def test_stemming(self):
        """'открой телеграму' → телеграм (стемминг)."""
        matched, params = self.cmd.match("открой телеграму")
        self.assertTrue(matched)
        # Должен найти через стемминг

    def test_word_boundary(self):
        """'код' не внутри 'кодировка'."""
        matched, params = self.cmd.match("открой кодировку")
        # Не должен найти "код" внутри "кодировку"
        apps = params.get("apps", [])
        code_found = any(a["app"] == "code" for a in apps)
        self.assertFalse(code_found)

    def test_no_app(self):
        """'открой что-то непонятное' → невалидный кандидат."""
        matched, params = self.cmd.match("открой абракадабру")
        self.assertFalse(self.cmd.is_valid_candidate(params))

    def test_keywords(self):
        """Разные ключевые слова."""
        for kw in ["открой", "запусти", "включи"]:
            matched, params = self.cmd.match(f"{kw} блокнот")
            self.assertTrue(matched, f"Не сработало: '{kw} блокнот'")


class TestCloseApp(unittest.TestCase):
    """Закрытие приложений."""

    def setUp(self):
        from commands.apps import CloseAppCommand
        self.cmd = CloseAppCommand(make_ctx())

    def test_basic(self):
        matched, params = self.cmd.match("закрой блокнот")
        self.assertTrue(matched)
        self.assertEqual(params["apps"][0]["app"], "notepad.exe")

    def test_context(self):
        """Контекстное закрытие 'закрой его'."""
        from core.context import get_context
        ctx = get_context()
        ctx.update("open_app", "notepad.exe")

        matched, params = self.cmd.match("закрой его")
        self.assertTrue(matched)
        self.assertTrue(params.get("is_context"))


# ═══════════════════════════════════════════
#  Тесты: Веб
# ═══════════════════════════════════════════

class TestWebSearch(unittest.TestCase):
    """Веб-поиск."""

    def setUp(self):
        from commands.web import WebSearchCommand
        self.cmd = WebSearchCommand(make_ctx())

    def test_basic_search(self):
        """'найди котиков' → Google."""
        matched, params = self.cmd.match("найди котиков")
        self.assertTrue(matched)
        self.assertEqual(params["query"], "котиков")
        self.assertIsNone(params["service"])

    def test_youtube_search(self):
        """'найди на ютубе котиков' → YouTube."""
        matched, params = self.cmd.match("найди на ютубе котиков")
        self.assertTrue(matched)
        self.assertIsNotNone(params["service"])
        self.assertEqual(params["service"].name, "YouTube")

    def test_youtube_direct(self):
        """'ютуб котики' → YouTube."""
        matched, params = self.cmd.match("ютуб котики")
        self.assertTrue(matched)
        self.assertEqual(params["service"].name, "YouTube")

    def test_google(self):
        """'загугли python tutorial'."""
        matched, params = self.cmd.match("загугли python tutorial")
        self.assertTrue(matched)
        self.assertIn("python", params["query"])

    def test_no_query(self):
        """'найди' без запроса → невалидный."""
        matched, params = self.cmd.match("найди")
        self.assertFalse(self.cmd.is_valid_candidate(params))


class TestOpenSite(unittest.TestCase):
    """Открытие сайтов."""

    def setUp(self):
        from commands.web import OpenSiteCommand
        self.cmd = OpenSiteCommand(make_ctx())

    def test_known_site(self):
        """'открой ютуб' → youtube.com."""
        matched, params = self.cmd.match("открой ютуб")
        self.assertTrue(matched)
        self.assertIn("youtube", params["url"])

    def test_direct_url(self):
        """'открой github.com' → https://github.com."""
        matched, params = self.cmd.match("открой github.com")
        self.assertTrue(matched)
        self.assertEqual(params["url"], "https://github.com")

    def test_section(self):
        """'открой ютуб тренды' → trending."""
        matched, params = self.cmd.match("открой ютуб тренды")
        self.assertTrue(matched)
        self.assertIn("trending", params["url"])

    def test_unknown_site(self):
        """'открой что-то неизвестное' → невалидный."""
        matched, params = self.cmd.match("открой дырку в стене")
        self.assertFalse(self.cmd.is_valid_candidate(params))


# ═══════════════════════════════════════════
#  Тесты: Окна
# ═══════════════════════════════════════════

class TestWindowCommands(unittest.TestCase):
    """Управление окнами."""

    def setUp(self):
        from commands.window_control import (
            MinimizeWindowCommand, MaximizeWindowCommand,
            CloseWindowCommand, SwitchWindowCommand,
            SnapWindowCommand, ListWindowsCommand,
        )
        ctx = make_ctx()
        self.minimize = MinimizeWindowCommand(ctx)
        self.maximize = MaximizeWindowCommand(ctx)
        self.close = CloseWindowCommand(ctx)
        self.switch = SwitchWindowCommand(ctx)
        self.snap = SnapWindowCommand(ctx)
        self.list_win = ListWindowsCommand(ctx)

    def test_minimize_all(self):
        matched, params = self.minimize.match("сверни всё")
        self.assertTrue(matched)
        self.assertEqual(params["target"], "__all__")

    def test_minimize_specific(self):
        matched, params = self.minimize.match("сверни блокнот")
        self.assertTrue(matched)
        self.assertIn("блокнот", params["target"])

    def test_minimize_current(self):
        matched, params = self.minimize.match("сверни")
        self.assertTrue(matched)
        self.assertEqual(params["target"], "")

    def test_maximize(self):
        matched, _ = self.maximize.match("разверни окно")
        self.assertTrue(matched)

    def test_close_window(self):
        matched, _ = self.close.match("закрой окно")
        self.assertTrue(matched)

    def test_switch_to(self):
        matched, params = self.switch.match("переключись на хром")
        self.assertTrue(matched)
        self.assertIn("хром", params["target"])

    def test_snap_left(self):
        matched, params = self.snap.match("окно влево")
        self.assertTrue(matched)
        self.assertEqual(params["position"], "left")

    def test_snap_corner(self):
        matched, params = self.snap.match(
            "в правый верхний угол"
        )
        self.assertTrue(matched)
        self.assertEqual(params["position"], "top-right")

    def test_snap_no_position(self):
        matched, params = self.snap.match("окно куда-нибудь")
        self.assertFalse(self.snap.is_valid_candidate(params))

    def test_list(self):
        matched, _ = self.list_win.match("какие окна открыты")
        self.assertTrue(matched)


# ═══════════════════════════════════════════
#  Тесты: Медиа и громкость
# ═══════════════════════════════════════════

class TestMediaCommands(unittest.TestCase):
    """Медиа-команды."""

    def setUp(self):
        from commands.media import (
            PlayPauseCommand, NextTrackCommand,
            VolumeUpCommand, SetVolumeCommand,
            MuteCommand, CurrentVolumeCommand,
        )
        ctx = make_ctx()
        self.play = PlayPauseCommand(ctx)
        self.next = NextTrackCommand(ctx)
        self.vol_up = VolumeUpCommand(ctx)
        self.set_vol = SetVolumeCommand(ctx)
        self.mute = MuteCommand(ctx)
        self.current = CurrentVolumeCommand(ctx)

    def test_play(self):
        matched, _ = self.play.match("пауза")
        self.assertTrue(matched)

    def test_next(self):
        matched, _ = self.next.match("следующий трек")
        self.assertTrue(matched)

    def test_next_context(self):
        """'дальше' без контекста — матчится."""
        matched, _ = self.next.match("дальше")
        self.assertTrue(matched)

    def test_next_no_false(self):
        """'дальше по тексту' — НЕ медиа."""
        matched, _ = self.next.match("дальше по тексту")
        self.assertFalse(matched)

    def test_volume_up(self):
        matched, _ = self.vol_up.match("громче")
        self.assertTrue(matched)

    def test_set_volume(self):
        matched, params = self.set_vol.match(
            "поставь громкость на 50"
        )
        self.assertTrue(matched)
        self.assertEqual(params["percent"], 50)

    def test_mute(self):
        matched, _ = self.mute.match("выключи звук")
        self.assertTrue(matched)


# ═══════════════════════════════════════════
#  Тесты: Информация
# ═══════════════════════════════════════════

class TestInfoCommands(unittest.TestCase):
    """Информационные команды."""

    def setUp(self):
        from commands.info import (
            TimeCommand, DateCommand, WeatherCommand,
            SystemInfoCommand, BatteryCommand, IPCommand,
        )
        ctx = make_ctx()
        self.time = TimeCommand(ctx)
        self.date = DateCommand(ctx)
        self.weather = WeatherCommand(ctx)
        self.system = SystemInfoCommand(ctx)
        self.battery = BatteryCommand(ctx)
        self.ip = IPCommand(ctx)

    def test_time_direct(self):
        for phrase in [
            "который час", "сколько времени",
            "подскажи время", "время",
        ]:
            matched, _ = self.time.match(phrase)
            self.assertTrue(matched, f"Не сработало: '{phrase}'")

    def test_time_no_false(self):
        """'состояние системы время' — НЕ время."""
        matched, _ = self.time.match("состояние системы время")
        self.assertFalse(matched)

    def test_date(self):
        for phrase in [
            "какое число", "какой день", "какая дата",
        ]:
            matched, _ = self.date.match(phrase)
            self.assertTrue(matched, f"Не сработало: '{phrase}'")

    def test_weather(self):
        matched, _ = self.weather.match("какая погода")
        self.assertTrue(matched)

    def test_weather_no_temp_alone(self):
        """'температура процессора' — НЕ погода."""
        matched, _ = self.weather.match("температура процессора")
        self.assertFalse(matched)

    def test_system(self):
        matched, _ = self.system.match("состояние системы")
        self.assertTrue(matched)


# ═══════════════════════════════════════════
#  Тесты: Математика
# ═══════════════════════════════════════════

class TestMath(unittest.TestCase):
    """Математические вычисления."""

    def setUp(self):
        from commands.math_cmd import MathCommand, SafeCalculator
        self.cmd = MathCommand(make_ctx())
        self.calc = SafeCalculator

    def test_basic_math(self):
        self.assertEqual(self.calc.calculate("2 + 2"), 4)
        self.assertEqual(self.calc.calculate("10 - 3"), 7)
        self.assertEqual(self.calc.calculate("5 * 6"), 30)
        self.assertEqual(self.calc.calculate("15 / 3"), 5.0)

    def test_power(self):
        self.assertEqual(self.calc.calculate("2 ** 10"), 1024)

    def test_parentheses(self):
        self.assertEqual(self.calc.calculate("(2 + 3) * 4"), 20)

    def test_division_by_zero(self):
        with self.assertRaises(ValueError):
            self.calc.calculate("5 / 0")

    def test_large_power(self):
        with self.assertRaises(ValueError):
            self.calc.calculate("2 ** 1000")

    def test_no_code_injection(self):
        with self.assertRaises(ValueError):
            self.calc.calculate("__import__('os').system('ls')")

    def test_match(self):
        matched, _ = self.cmd.match("посчитай два плюс два")
        self.assertTrue(matched)

    def test_percent(self):
        matched, params = self.cmd.match(
            "посчитай 20 процентов от 500"
        )
        self.assertTrue(matched)
        self.assertIn("result", params)


# ═══════════════════════════════════════════
#  Тесты: Диалог
# ═══════════════════════════════════════════

class TestConversation(unittest.TestCase):
    """Диалоговые команды."""

    def setUp(self):
        from commands.conversation import (
            GreetingCommand, StopCommand, HelpCommand,
            RepeatCommand, ThankYouCommand,
        )
        ctx = make_ctx()
        self.greeting = GreetingCommand(ctx)
        self.stop = StopCommand(ctx)
        self.help = HelpCommand(ctx)
        self.repeat = RepeatCommand(ctx)
        self.thanks = ThankYouCommand(ctx)

    def test_greeting_start(self):
        """'привет' в начале — да."""
        matched, _ = self.greeting.match("привет")
        self.assertTrue(matched)
        matched, _ = self.greeting.match("привет как дела")
        self.assertTrue(matched)

    def test_greeting_not_middle(self):
        """'передай привет маме' — нет!"""
        matched, _ = self.greeting.match("передай привет маме")
        self.assertFalse(matched)
        matched, _ = self.greeting.match("скажи привет")
        self.assertFalse(matched)

    def test_stop(self):
        matched, _ = self.stop.match("стоп")
        self.assertTrue(matched)

    def test_help(self):
        matched, _ = self.help.match("что ты умеешь")
        self.assertTrue(matched)

    def test_thanks(self):
        matched, _ = self.thanks.match("спасибо")
        self.assertTrue(matched)


# ═══════════════════════════════════════════
#  Тесты: Таймеры
# ═══════════════════════════════════════════

class TestTimers(unittest.TestCase):
    """Таймеры и напоминания."""

    def setUp(self):
        from commands.utility import (
            TimerCommand, CancelTimerCommand,
        )
        ctx = make_ctx()
        self.timer = TimerCommand(ctx)
        self.cancel = CancelTimerCommand(ctx)

    def test_timer_minutes(self):
        matched, params = self.timer.match("таймер на 5 минут")
        self.assertTrue(matched)
        self.assertEqual(params["seconds"], 300)

    def test_timer_seconds(self):
        matched, params = self.timer.match("таймер на 30 секунд")
        self.assertTrue(matched)
        self.assertEqual(params["seconds"], 30)

    def test_timer_hours(self):
        matched, params = self.timer.match("таймер на 2 часа")
        self.assertTrue(matched)
        self.assertEqual(params["seconds"], 7200)

    def test_cancel(self):
        matched, _ = self.cancel.match("отмени таймер")
        self.assertTrue(matched)


class TestReminders(unittest.TestCase):
    """Напоминания."""

    def setUp(self):
        from commands.reminder import ReminderCommand
        self.cmd = ReminderCommand(make_ctx())

    def test_relative(self):
        """'напомни через 5 минут купить молоко'."""
        matched, params = self.cmd.match(
            "напомни через 5 минут купить молоко"
        )
        self.assertTrue(matched)
        self.assertEqual(params["seconds"], 300)
        self.assertIn("молоко", params["message"])

    def test_absolute(self):
        """'напомни в 15:30 про встречу'."""
        matched, params = self.cmd.match(
            "напомни в 15:30 про встречу"
        )
        self.assertTrue(matched)
        self.assertGreater(params["seconds"], 0)

    def test_no_time(self):
        """'напомни что-то' без времени — не матчится."""
        matched, _ = self.cmd.match("напомни что-то")
        self.assertFalse(matched)


# ═══════════════════════════════════════════
#  Тесты: Заметки
# ═══════════════════════════════════════════

class TestNotes(unittest.TestCase):
    """Заметки."""

    def setUp(self):
        from commands.utility import NoteCommand, SearchNotesCommand
        ctx = make_ctx()
        self.note = NoteCommand(ctx)
        self.search = SearchNotesCommand(ctx)

    def test_add_note(self):
        matched, params = self.note.match(
            "запомни что завтра встреча"
        )
        self.assertTrue(matched)
        self.assertIn("встреча", params["note_text"])

    def test_short_note_invalid(self):
        """Слишком короткая заметка — невалидная."""
        matched, params = self.note.match("запомни а")
        self.assertFalse(self.note.is_valid_candidate(params))

    def test_search(self):
        matched, params = self.search.match(
            "найди в заметках встречу"
        )
        self.assertTrue(matched)
        self.assertTrue(self.search.is_valid_candidate(params))


# ═══════════════════════════════════════════
#  Тесты: Система
# ═══════════════════════════════════════════

class TestSystem(unittest.TestCase):
    """Системные команды."""

    def setUp(self):
        from commands.system import (
            LockCommand, ShutdownCommand,
            CancelShutdownCommand, OpenSystemToolCommand,
        )
        ctx = make_ctx()
        self.lock = LockCommand(ctx)
        self.shutdown = ShutdownCommand(ctx)
        self.cancel = CancelShutdownCommand(ctx)
        self.tools = OpenSystemToolCommand(ctx)

    def test_lock(self):
        matched, _ = self.lock.match("заблокируй")
        self.assertTrue(matched)

    def test_shutdown(self):
        matched, _ = self.shutdown.match("выключи компьютер")
        self.assertTrue(matched)

    def test_cancel(self):
        matched, _ = self.cancel.match("отмени выключение")
        self.assertTrue(matched)

    def test_tool(self):
        matched, params = self.tools.match("диспетчер устройств")
        self.assertTrue(matched)
        self.assertEqual(params["tool"], "devmgmt.msc")

    def test_no_fuzzy_shutdown(self):
        """Shutdown НЕ матчится нечётко."""
        self.assertFalse(self.shutdown.fuzzy_enabled)


# ═══════════════════════════════════════════
#  Тесты: Перевод
# ═══════════════════════════════════════════

class TestTranslate(unittest.TestCase):
    """Перевод."""

    def setUp(self):
        from commands.translate import TranslateCommand
        self.cmd = TranslateCommand(make_ctx())

    def test_to_english(self):
        matched, params = self.cmd.match(
            "переведи на английский привет"
        )
        self.assertTrue(matched)
        self.assertEqual(params["target_lang"], "en")

    def test_auto_detect(self):
        """'переведи hello' → автоопределение en→ru."""
        matched, params = self.cmd.match("переведи hello")
        self.assertTrue(matched)
        self.assertEqual(params["target_lang"], "ru")

    def test_german(self):
        matched, params = self.cmd.match(
            "как по-немецки спасибо"
        )
        self.assertTrue(matched)
        self.assertEqual(params["target_lang"], "de")


# ═══════════════════════════════════════════
#  Тесты: Макросы
# ═══════════════════════════════════════════

class TestMacros(unittest.TestCase):
    """Макрокоманды."""

    def setUp(self):
        from commands.macros import MacroCommand
        self.cmd = MacroCommand(make_ctx())

    def test_work(self):
        matched, params = self.cmd.match("начинаем работу")
        self.assertTrue(matched)
        self.assertEqual(params["macro"].name, "work_start")

    def test_evening(self):
        matched, params = self.cmd.match("вечерний режим")
        self.assertTrue(matched)
        self.assertEqual(params["macro"].name, "evening")

    def test_cinema(self):
        matched, params = self.cmd.match("режим кино")
        self.assertTrue(matched)

    def test_unknown(self):
        matched, _ = self.cmd.match("режим несуществующий")
        self.assertFalse(matched)


# ═══════════════════════════════════════════
#  Тесты: Файлы
# ═══════════════════════════════════════════

class TestFiles(unittest.TestCase):
    """Работа с файлами."""

    def setUp(self):
        from commands.files import (
            OpenFolderCommand, FolderContentsCommand,
        )
        ctx = make_ctx()
        self.open = OpenFolderCommand(ctx)
        self.contents = FolderContentsCommand(ctx)

    def test_downloads(self):
        matched, params = self.open.match("открой загрузки")
        self.assertTrue(matched)
        self.assertIn("Downloads", params["path"])

    def test_desktop(self):
        matched, params = self.open.match("открой рабочий стол")
        self.assertTrue(matched)
        self.assertIn("Desktop", params["path"])

    def test_contents(self):
        matched, params = self.contents.match(
            "что на рабочем столе"
        )
        self.assertTrue(matched)


# ═══════════════════════════════════════════
#  Тесты: Настройки
# ═══════════════════════════════════════════

class TestSettings(unittest.TestCase):
    """Настройки ассистента."""

    def setUp(self):
        from commands.settings import (
            SpeakFasterCommand, ClearHistoryCommand,
            CurrentSettingsCommand,
        )
        ctx = make_ctx()
        self.faster = SpeakFasterCommand(ctx)
        self.clear = ClearHistoryCommand(ctx)
        self.current = CurrentSettingsCommand(ctx)

    def test_faster(self):
        matched, _ = self.faster.match("говори быстрее")
        self.assertTrue(matched)

    def test_clear_history(self):
        matched, _ = self.clear.match("очисти историю")
        self.assertTrue(matched)

    def test_current(self):
        matched, _ = self.current.match("текущие настройки")
        self.assertTrue(matched)


# ═══════════════════════════════════════════
#  Тесты: Smart LLM
# ═══════════════════════════════════════════

class TestSmartLLM(unittest.TestCase):
    """LLM fallback."""

    def setUp(self):
        from commands.smart import SmartCommand
        self.cmd = SmartCommand(make_ctx())

    def test_normal_query(self):
        matched, params = self.cmd.match(
            "расскажи про искусственный интеллект"
        )
        self.assertTrue(matched)
        self.assertEqual(
            params["query"],
            "расскажи про искусственный интеллект"
        )

    def test_garbage(self):
        """Мусор НЕ отправляется в LLM."""
        for garbage in ["ммм", "ааааа", "эээ", "ну"]:
            matched, _ = self.cmd.match(garbage)
            self.assertFalse(
                matched, f"Мусор '{garbage}' прошёл в LLM"
            )

    def test_too_short(self):
        matched, _ = self.cmd.match("аб")
        self.assertFalse(matched)

    def test_repeating(self):
        """'да да да' — не отправлять."""
        matched, _ = self.cmd.match("да да да")
        self.assertFalse(matched)


# ═══════════════════════════════════════════
#  Тесты: Негативные (ложные срабатывания)
# ═══════════════════════════════════════════

class TestFalsePositives(unittest.TestCase):
    """
    Проверка что команды НЕ срабатывают ложно.
    
    Это самые важные тесты — ложное срабатывание
    хуже чем несрабатывание.
    """

    def setUp(self):
        ctx = make_ctx()
        from commands.conversation import GreetingCommand
        from commands.info import TimeCommand, WeatherCommand
        from commands.media import NextTrackCommand
        from commands.system import ShutdownCommand

        self.greeting = GreetingCommand(ctx)
        self.time = TimeCommand(ctx)
        self.weather = WeatherCommand(ctx)
        self.next_track = NextTrackCommand(ctx)
        self.shutdown = ShutdownCommand(ctx)

    def test_greeting_in_middle(self):
        """'передай привет' — НЕ приветствие."""
        self.assertFalse(
            self.greeting.match("передай привет маме")[0]
        )

    def test_time_in_compound(self):
        """'состояние системы' содержит 'время'? нет."""
        self.assertFalse(
            self.time.match("покажи состояние системы")[0]
        )

    def test_weather_temp(self):
        """'температура процессора' — НЕ погода."""
        self.assertFalse(
            self.weather.match("температура процессора")[0]
        )

    def test_next_in_context(self):
        """'дальше по тексту' — НЕ следующий трек."""
        self.assertFalse(
            self.next_track.match("дальше по тексту")[0]
        )

    def test_shutdown_fuzzy_disabled(self):
        """Shutdown не должен fuzzy-матчиться."""
        self.assertFalse(self.shutdown.fuzzy_enabled)


# ═══════════════════════════════════════════
#  Тесты: Роутер
# ═══════════════════════════════════════════

class TestRouter(unittest.TestCase):
    """Маршрутизатор команд."""

    def test_priority_order(self):
        """Команды с высоким приоритетом проверяются первыми."""
        from commands import load_all_commands
        ctx = make_ctx()
        commands = load_all_commands(ctx)

        priorities = [c.priority for c in commands]
        # Должны быть отсортированы по убыванию
        self.assertEqual(
            priorities,
            sorted(priorities, reverse=True)
        )

    def test_unique_names(self):
        """Все имена команд уникальны."""
        from commands import load_all_commands
        ctx = make_ctx()
        commands = load_all_commands(ctx)

        names = [c.name for c in commands]
        self.assertEqual(len(names), len(set(names)))


# ═══════════════════════════════════════════
#  Запуск
# ═══════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  ТЕСТЫ ГОЛОСОВОГО АССИСТЕНТА v3.1")
    print("=" * 60)
    print()

    unittest.main(verbosity=2)