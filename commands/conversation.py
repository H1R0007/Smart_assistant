# commands/conversation.py

"""
Диалоговые команды.

Изменения v3.1:
- GreetingCommand: только если приветствие В НАЧАЛЕ фразы
- RepeatCommand: через публичный API llm.get_last_response()
"""

import datetime
import random
from commands.base import BaseCommand, CommandResult


class GreetingCommand(BaseCommand):
    name = "greeting"
    keywords = ["привет", "здравствуй", "добрый день",
                "доброе утро", "добрый вечер", "здарова", "хай"]
    priority = 2

    def match(self, text: str) -> tuple[bool, dict]:
        """
        Матчим ТОЛЬКО если приветствие в начале фразы.

        "привет" → да
        "привет как дела" → да
        "передай привет маме" → НЕТ (привет не в начале)
        "скажи привет" → НЕТ
        """
        t = text.lower().strip()

        for kw in self.keywords:
            # Строго в начале
            if t == kw or t.startswith(kw + " ") or t.startswith(kw + ","):
                return True, {}

        return False, {}

    def execute(self, text: str, params: dict) -> CommandResult:
        hour = datetime.datetime.now().hour
        if hour < 6:
            g = "Доброй ночи"
        elif hour < 12:
            g = "Доброе утро"
        elif hour < 18:
            g = "Добрый день"
        else:
            g = "Добрый вечер"
        self.speaker.say(f"{g}! Чем помочь?")
        return CommandResult()


class StopCommand(BaseCommand):
    name = "stop"
    keywords = ["стоп", "хватит", "замолчи", "выключись",
                "до свидания", "отключись"]
    priority = 50

    def match(self, text: str) -> tuple[bool, dict]:
        t = text.lower().strip()

        # НЕ перехватываем если во всей фразе есть эти объекты
        no_intercept = [
            "музык", "играть", "воспроизвед",  # → stop_media
            "напоминать", "напоминан",          # → cancel_reminder
            "таймер", "таймеры",                 # → cancel_timer
            "видео", "ютуб", "youtube",         # → youtube / media
        ]

        # Если в тексте есть хоть одно слово исключение — это не чистый "стоп"
        if any(word in t for word in no_intercept):
            return False, {}

        # Дополнительно: если фраза длиннее 3 слов, скорее всего это 
        # не команда остановки ассистента (например "хватит говорить мне о погоде")
        # Но пока оставим только фильтр по словам.

        return super().match(text)

    def execute(self, text: str, params: dict) -> CommandResult:
        farewells = [
            "До свидания! Выключаюсь.",
            "Пока! Буду ждать.",
            "До встречи!",
        ]
        self.speaker.say(random.choice(farewells))
        return CommandResult(should_continue=False)


class HelpCommand(BaseCommand):
    name = "help"
    description = "Показать список возможностей"
    category = "Общие"
    keywords = ["помощь", "что ты умеешь", "что можешь", "помоги",
                "список команд", "твои возможности", "что умеешь", "справка"]
    priority = 5

    def execute(self, text: str, params: dict) -> CommandResult:
        # Быстрый сбор описаний без пересоздания экземпляров команд.
        # BaseCommand.__subclasses__() вернёт все классы, которые уже были
        # загружены при старте системы.
        categories = {}
        count = 0
        
        # Рекурсивно собираем все подклассы
        def get_all_subclasses(cls):
            all_subclasses = []
            for subclass in cls.__subclasses__():
                all_subclasses.append(subclass)
                all_subclasses.extend(get_all_subclasses(subclass))
            return all_subclasses

        for cmd_class in get_all_subclasses(BaseCommand):
            # Пропускаем приватные классы (начинаются с _)
            if cmd_class.__name__.startswith("_"):
                continue
                
            desc = getattr(cmd_class, 'description', '')
            priority = getattr(cmd_class, 'priority', 0)
            
            # Игнорируем команды без описания и LLM-фоллбэк
            if not desc or priority < 0:
                continue
                
            cat = getattr(cmd_class, 'category', 'Разное') or 'Разное'
            if cat not in categories:
                categories[cat] = []
                
            categories[cat].append(desc)
            count += 1

        if not categories:
            self.speaker.say(
                "Я умею много всего, но разработчик забыл добавить "
                "описания к командам."
            )
            return CommandResult()

        # Формируем ответ для голоса
        parts = [f"Я знаю {count} команд в {len(categories)} категориях."]
        
        for cat, items in categories.items():
            # Берём только первые 2 примера из каждой категории
            examples = ", ".join(items[:2])
            parts.append(f"В категории {cat}: {examples}")
            
        parts.append("Подробный список можно посмотреть в документации.")
        
        # Озвучиваем
        self.speaker.say(". ".join(parts))
        
        # Выводим в консоль
        print("\n" + "="*40)
        print(" СПРАВКА ПО КОМАНДАМ:")
        print("="*40)
        for cat, items in sorted(categories.items()):
            print(f"\n[{cat.upper()}]")
            # Убираем дубликаты описаний (если есть) и сортируем
            for item in sorted(list(set(items))):
                print(f"  - {item}")
        print("="*40 + "\n")
        
        return CommandResult(follow_up=True)


class ThankYouCommand(BaseCommand):
    name = "thanks"
    keywords = ["спасибо", "благодарю", "молодец", "круто", "отлично", "класс"]
    priority = 2

    def execute(self, text: str, params: dict) -> CommandResult:
        responses = [
            "Всегда пожалуйста!",
            "Рада помочь!",
            "Обращайтесь!",
        ]
        self.speaker.say(random.choice(responses))
        return CommandResult()


class HowAreYouCommand(BaseCommand):
    name = "how_are_you"
    keywords = ["как дела", "как ты", "как поживаешь"]
    priority = 2

    def execute(self, text: str, params: dict) -> CommandResult:
        self.speaker.say("Работаю стабильно! Чем помочь?")
        return CommandResult()


class WhoAreYouCommand(BaseCommand):
    name = "who_are_you"
    keywords = ["кто ты", "как тебя зовут", "твоё имя", "представься"]
    priority = 5

    def execute(self, text: str, params: dict) -> CommandResult:
        wake = self.config.get("listener", "wake_word", default="Ассистент")
        self.speaker.say(
            f"Я {wake}, голосовой ассистент. "
            f"Могу управлять компьютером и отвечать на вопросы."
        )
        return CommandResult()


class RepeatCommand(BaseCommand):
    name = "repeat"
    keywords = ["повтори", "повтори что сказала", "скажи ещё раз",
                "не расслышал", "повторить"]
    priority = 5

    def execute(self, text: str, params: dict) -> CommandResult:
        """Повтор через публичный API."""
        llm = getattr(self.ctx, 'llm', None)
        if llm:
            last = llm.get_last_response()
            if last:
                self.speaker.say(last)
                return CommandResult()

        self.speaker.say("Нечего повторять.")
        return CommandResult()