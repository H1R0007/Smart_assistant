# commands/activity_stats.py

"""
Команда: статистика активности пользователя.

Позволяет узнать:
- В каких приложениях работал и сколько ("что я делал сегодня")
- Время в конкретном приложении ("сколько работал в хроме")
- Какие команды выполнял ("какие команды я давал")
- Ответы на сложные вопросы через RAG ("что делал перед тем как открыть браузер")

Матчинг трёхуровневый:
1. Ключевые слова через BaseCommand (exact + fuzzy)
2. Regex-паттерны для сложных фраз с опциональными словами
3. Мягкий матч по стемам (≥2 совпадения)

Два режима получения данных:
- Агрегация из activity_log — точные цифры по времени и счётчикам
- RAG через entries с source='tracker' — сложные контекстные вопросы
"""

import re
from commands.base import BaseCommand, CommandResult, stem_russian
from core.app_registry import get_app_registry

class ActivityStatsCommand(BaseCommand):
    """Голосовая команда для получения статистики активности."""

    name = "activity_stats"
    description = "Статистика активности пользователя"

    # Приоритет 8: выше информационных (5–7), ниже системных (10+).
    # При конфликте с командой "открой приложение" (которая
    # содержит слово "приложение") — нас проверят раньше,
    # но наши ключевые слова достаточно специфичны.
    priority = 8

    fuzzy_enabled = True

    # ── Ключевые слова для exact/fuzzy матчинга (BaseCommand) ──
    # Каждая фраза проверяется как подпоследовательность слов:
    #   "что я делал" ✓ матчит "расскажи что я делал сегодня"
    #   "что делал"   ✓ матчит "расскажи что делал вчера"
    # Длинные (≥8 символов) также проверяются fuzzy для Whisper-ошибок.
    keywords = [
        # Прямые запросы
        "моя статистика", "моя активность",
        "мою статистику",
        # Вопросы о действиях (с "я" и без)
        "что я делал", "что делал",
        "чем я занимался", "чем занимался",
        # Команды
        "какие команды", "последние команды",
        # Императивные формы
        "покажи статистику", "покажи активность",
        "расскажи про активность",
        # Одиночные слова: fuzzy ловит склонения
        # ("статистику" ≈ "статистика", Levenshtein=1, sim=0.9)
        "статистика", "активность",
    ]

    # ── Regex для фраз, которые ломают keyword-матчинг ──
    # Нужны когда между ключевыми словами стоят
    # опциональные слова: "сколько Я работал" — слово "я"
    # между "сколько" и "работал" не даёт exact match.
    _EXTRA_PATTERNS_SRC = [
        # "сколько (я) работал/провёл/сидел/был (в ...)"
        r"сколько\s+(?:я\s+)?(?:работал|провёл|провел|сидел|был)",
        # "в каком приложении..."
        r"(?:в\s+каком|какое)\s+приложени",
        # "топ/рейтинг приложений"
        r"(?:топ|рейтинг)\s+приложени",
        # "покажи/расскажи ... статистику/активность"
        # Ловит "покажи мою статистику" (keyword "покажи статистику"
        # не сработает — "мою" разрывает подпоследовательность).
        r"(?:покажи|расскажи|скажи)\s+.*?(?:статистик|активност)",
    ]

    # ── Regex для извлечения имени приложения ──
    _APP_PATTERNS_SRC = [
        r"(?:работал|провёл|провел|сидел|был)\s+в\s+(\S+)",
        r"сколько.*?в\s+(\S+)",
        r"время\s+в\s+(\S+)",
    ]

    # ── Стемы для мягкого матча (уровень 3) ──
    # Подстроки, характерные для запросов об активности.
    # "статистик" найдётся в "статистика/у/и/ой".
    # Если ≥2 стема обнаружены — считаем совпадением.
    _SOFT_MATCH_STEMS = [
        "статистик", "активност", "работал",
        "делал", "занимал", "команд", "приложени",
    ]

    # ── RAG-маркеры ──
    # Слова, при наличии которых простая агрегация не справится —
    # нужен контекстный анализ через LLM.
    _RAG_MARKERS = ["перед", "после", "когда", "почему", "какой файл"]

    def __init__(self, ctx):
        super().__init__(ctx)

        # Компилируем regex один раз при создании команды.
        # При 15+ командах × 5 запросов за сеанс —
        # экономия ~15мс на повторной компиляции.
        self._extra_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self._EXTRA_PATTERNS_SRC
        ]
        self._app_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in self._APP_PATTERNS_SRC
        ]

    # ══════════════════════════════════════════════
    #  Доступ к базе знаний
    # ══════════════════════════════════════════════

    @property
    def _kb(self):
        """
        Ленивое получение KnowledgeBase из контекста.

        Property вместо кеширования в __init__: knowledge может
        быть подключена к ctx ПОСЛЕ создания команды
        (например, KnowledgeBase.start() вызывается позже).
        """
        return getattr(self.ctx, "knowledge", None)

    # ══════════════════════════════════════════════
    #  Матчинг
    # ══════════════════════════════════════════════

    def match(self, text: str) -> tuple[bool, dict]:
        """
        Трёхуровневый матчинг:

        1. Ключевые слова (exact + fuzzy) через BaseCommand.match()
           → "моя статистика", "что я делал", "активность"

        2. Regex-паттерны для фраз с опциональными словами
           → "сколько я работал в хроме"

        3. Мягкий матч по стемам (≥2 совпадения)
           → "покажи рейтинг моей активности"
              (стемы "активност" + "рейтинг" не в списке,
               но "покажи ... активност" уже поймается regex)

        Возвращает (True, params) при совпадении.
        extract_params() вызывается для определения типа запроса.
        """
        # ── Уровень 1: ключевые слова (BaseCommand) ──
        # super().match() нормализует текст внутри и вызывает
        # self.extract_params() — т.е. нашу переопределённую версию.
        matched, params = super().match(text)
        if matched:
            return True, params

        text_norm = text

        # ── Уровень 2: regex-паттерны ──
        for pattern in self._extra_patterns:
            if pattern.search(text_norm):
                params = self.extract_params(text_norm)
                self.logger.debug(
                    "Regex match: /%s/ → type=%s",
                    pattern.pattern[:40], params.get("query_type"),
                )
                return True, params

        # ── Уровень 3: мягкий матч по стемам ──
        # Два и более стема = достаточная уверенность.
        # Один стем ("работал") — слишком расплывчато.
        hits = sum(1 for s in self._SOFT_MATCH_STEMS if s in text_norm)
        if hits >= 2:
            params = self.extract_params(text_norm)
            self.logger.debug(
                "Soft stem match (%d hits) in '%s'",
                hits, text_norm[:50],
            )
            return True, params

        return False, {}

    # ══════════════════════════════════════════════
    #  Извлечение параметров
    # ══════════════════════════════════════════════

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        """
        Извлечь параметры запроса из текста.

        Вызывается из match() после нормализации текста.

        Returns:
            {
                "period_hours": float,     # за сколько часов назад смотреть
                "app_name": str | None,    # конкретное приложение
                "query_type": str,         # general / app_specific / commands / rag
            }

        Порядок проверок (от специфичного к общему):
        1. RAG-маркеры → сложный вопрос для LLM
        2. Имя приложения → статистика конкретного приложения
        3. Подстрока "команд" → статистика команд
        4. Всё остальное → общая сводка
        """
        text_lower = text.lower()
        period = self._extract_period(text_lower)

        # 1. Сложный вопрос → RAG
        # "перед", "после", "когда", "почему" — требуют анализа
        # последовательности событий, простая агрегация не поможет.
        if any(marker in text_lower for marker in self._RAG_MARKERS):
            return {
                "period_hours": period,
                "app_name": None,
                "query_type": "rag",
            }

        # 2. Конкретное приложение
        app_name = self._extract_app_name(text_lower)
        if app_name:
            return {
                "period_hours": period,
                "app_name": app_name,
                "query_type": "app_specific",
            }

        # 3. Вопрос про команды
        # "команд" — подстрока, ловит "команды", "команду", "командах"
        if "команд" in text_lower:
            return {
                "period_hours": period,
                "app_name": None,
                "query_type": "commands",
            }

        # 4. Общая сводка (дефолт)
        return {
            "period_hours": period,
            "app_name": None,
            "query_type": "general",
        }

    # ══════════════════════════════════════════════
    #  Валидация
    # ══════════════════════════════════════════════

    def is_valid_candidate(self, params: dict) -> bool:
        """
        Команда пригодна только если KnowledgeBase доступна.

        Без неё нет ни activity_log, ни entries с трекером.
        Лучше не матчить команду, чем ответить ошибкой.
        """
        return self._kb is not None

    # ══════════════════════════════════════════════
    #  Выполнение
    # ══════════════════════════════════════════════

    def execute(self, text: str, params: dict) -> CommandResult:
        """
        Выполнить команду и озвучить результат.

        Args:
            text:   Оригинальный текст пользователя (не нормализованный).
                    Передаётся в RAG как есть — LLM лучше работает
                    с естественным текстом.
            params: Параметры из extract_params().
        """
        kb = self._kb
        if kb is None:
            self.speaker.say(
                "База знаний не подключена, статистика недоступна."
            )
            return CommandResult(success=False)

        query_type = params.get("query_type", "general")
        period = params.get("period_hours", 24.0)

        try:
            # ── Выбор обработчика по типу запроса ──
            if query_type == "app_specific":
                response = self._app_stats(kb, params["app_name"], period)
            elif query_type == "commands":
                response = self._command_stats(kb, period)
            elif query_type == "rag":
                # Для RAG передаём оригинальный текст —
                # LLM и поисковый движок справятся с casing и пунктуацией.
                response = self._rag_answer(kb, text, period)
            else:
                response = self._general_stats(kb, period)

            self.speaker.say(response)

            # ── Обновляем контекст для follow-up ──
            # Если пользователь скажет "а вчера?" — роутер
            # увидит action='activity_stats' в контексте и
            # перенаправит сюда с изменённым периодом.
            self.ctx.context.update(
                action="activity_stats",
                target=params.get("app_name") or "",
                params=params,
            )

            self.logger.info(
                "Activity stats: type=%s, period=%.0fh, app=%s",
                query_type, period, params.get("app_name", "—"),
            )

        except Exception as e:
            self.logger.error("Ошибка статистики: %s", e, exc_info=True)
            self.speaker.say(
                "Произошла ошибка при получении статистики."
            )
            return CommandResult(success=False)

        # follow_up=True — ожидаем уточняющий вопрос
        # без повторного wake-word.
        return CommandResult(follow_up=True)

    # ══════════════════════════════════════════════
    #  Обработчики по типу запроса
    # ══════════════════════════════════════════════

    def _general_stats(self, kb, hours: float) -> str:
        """
        Общая сводка активности.

        Использует get_activity_summary() — агрегацию по СЫРЫМ данным
        activity_log. Точные цифры по времени и количествам.
        """
        summary = kb.get_activity_summary(hours)

        has_apps = bool(summary.get("top_apps"))
        has_cmds = bool(summary.get("total_commands"))

        if not has_apps and not has_cmds:
            return (
                f"За {self._format_period(hours)} "
                f"нет данных об активности."
            )

        parts = []

        # Топ приложений (до 3 для голоса — длиннее утомительно)
        if has_apps:
            app_parts = [
                f"{name} {self._format_minutes(mins)}"
                for name, mins in summary["top_apps"][:3]
            ]
            parts.append("приложения: " + ", ".join(app_parts))

        # Счётчик команд
        total_cmd = summary.get("total_commands", 0)
        if total_cmd:
            parts.append(f"команд выполнено: {total_cmd}")

        header = self._make_header(hours)
        return f"{header} " + ". ".join(parts) + "."

    def _app_stats(self, kb, app_name: str, hours: float) -> str:
        """
        Статистика по конкретному приложению.

        Включает среднюю длительность сессии для контекста.
        """
        summary = kb.get_activity_summary(hours)
        period_str = self._format_period(hours)

        app_lower = app_name.lower()
        for name, minutes in summary.get("top_apps", []):
            if (app_lower in name.lower()
                    or name.lower() in app_lower):

                response = (
                    f"За {period_str} ты работал в {name} "
                    f"{self._format_minutes(minutes)}."
                )

                # Добавляем среднюю длительность сессии
                try:
                    duration_stats = kb.get_app_duration_stats(
                        hours,
                    )
                    app_stats = duration_stats.get(name)
                    if app_stats and app_stats["sessions"] >= 3:
                        avg = app_stats["avg_min"]
                        sessions = app_stats["sessions"]
                        response += (
                            f" В среднем {int(avg)} мин. "
                            f"за сессию, "
                            f"всего {sessions} сессий."
                        )
                except Exception:
                    pass

                return response

        return (
            f"Не нашла данных о работе в {app_name} "
            f"за {period_str}."
        )

    def _command_stats(self, kb, hours: float) -> str:
        """Статистика по выполненным командам ассистента."""
        summary = kb.get_activity_summary(hours)
        total = summary.get("total_commands", 0)
        period_str = self._format_period(hours)

        if not total:
            return f"За {period_str} команд не было."

        # Последние 3 команды — для голосового ответа больше не нужно
        activities = kb.get_recent_activities(
            hours=hours, action="command", limit=3,
        )

        parts = [f"За {period_str} выполнено {total} команд."]

        if activities:
            cmd_names = [a.target for a in activities if a.target]
            if cmd_names:
                parts.append("Последние: " + ", ".join(cmd_names) + ".")

        return " ".join(parts)

    def _rag_answer(self, kb, question: str, hours: float) -> str:
        """
        Ответ на сложный вопрос через RAG.

        Передаёт оригинальный текст в ask_activity(), который:
        1. Ищет релевантные записи трекера (FTS + semantic)
        2. Формирует контекст из найденного
        3. Просит LLM ответить на основе контекста

        Если LLM отсутствует или ничего не нашлось — fallback
        на общую сводку (лучше хоть что-то, чем "не знаю").
        """
        answer = kb.ask_activity(question, hours=hours)
        if answer:
            return answer

        # Fallback: если RAG не смог ответить, покажем сводку.
        # "Что я делал перед закрытием блокнота?" → не нашли →
        # хотя бы покажем что вообще делал.
        self.logger.debug(
            "RAG fallback to general stats for: %s", question[:50],
        )
        return (
            "Не удалось найти точный ответ. "
            + self._general_stats(kb, hours)
        )

    # ══════════════════════════════════════════════
    #  Извлечение параметров из текста
    # ══════════════════════════════════════════════

    def _extract_period(self, text: str) -> float:
        """
        Извлечь временной период из текста (в часах).

        Приоритет:
        1. Явные слова ("сегодня", "вчера")
        2. Специальные ("полчаса", "полтора часа", "последний час")
        3. Паттерн "за (последние) N часов" (цифры)
        4. _extract_number() из BaseCommand (числительные)
        5. Дефолт: 24.0 часа
        """
        # ── Явные периоды ──
        if "сегодня" in text:
            return 24.0
        if "вчера" in text:
            # "Вчера" ≈ 24–48 часов назад.
            # Не идеально: API не поддерживает диапазоны,
            # но 48ч покрывает весь вчерашний день.
            return 48.0

        # ── Специальные длительности ──
        if "полчаса" in text:
            return 0.5
        if "полтора" in text and "час" in text:
            return 1.5
        # "за последний час" (без числа)
        if re.search(r"(?:за\s+)?последний\s+час\b", text):
            return 1.0

        # ── Паттерн "за N часов" (цифры) ──
        hour_patterns = [
            r"за\s+последн\w+\s+(\d+)\s*(?:час|часа|часов)",
            r"за\s+(\d+)\s*(?:час|часа|часов)",
            r"последн\w+\s+(\d+)\s*(?:час|часа|часов)",
        ]
        for pattern in hour_patterns:
            m = re.search(pattern, text)
            if m:
                return float(m.group(1))

        # ── Числительные прописью ("за пять часов") ──
        # _extract_number() из BaseCommand находит "пять" → 5.
        # Проверяем наличие "час" рядом, чтобы не путать
        # с числами из других частей фразы.
        if "час" in text:
            num = self._extract_number(text)
            if num and 1 <= num <= 168:  # до недели
                return float(num)

        # ── Дефолт ──
        return 24.0

    def _extract_app_name(self, text: str) -> str | None:
        """
        Извлечь имя приложения из пользовательского запроса.

        "сколько я работал в хроме" → "хроме" → APP_ALIASES → "Chrome"
        "время в vscode"           → "vscode" → APP_ALIASES → "VS Code"

        Стратегия:
        1. Regex ловит слово после "в" в контексте работы
        2. Прямой поиск в APP_ALIASES
        3. Стемминг + повторный поиск (ловит неучтённые склонения)
        4. Если алиас не найден — возвращаем с капитализацией

        Returns: каноническое имя или None
        """
        registry = get_app_registry()
        
        for pattern in self._app_patterns:
            m = pattern.search(text)
            if m:
                raw = m.group(1).strip("?.,!").lower()

                # Прямой поиск в реестре
                app_info = registry.get_by_alias(raw)
                if app_info:
                    return app_info.canonical_name

                # Стемминг как fallback
                stemmed = stem_russian(raw)
                app_info = registry.get_by_alias(stemmed)
                if app_info:
                    return app_info.canonical_name

                # Нет алиаса — возвращаем как есть
                return raw.capitalize()
        return None

    # ══════════════════════════════════════════════
    #  Форматирование для голоса
    # ══════════════════════════════════════════════

    @staticmethod
    def _format_minutes(minutes: float) -> str:
        """
        Форматировать минуты в читаемый вид.

        Сокращения ("мин.", "ч.") вместо полных слов,
        чтобы не мучиться со склонениями русского языка
        (1 минута / 2 минуты / 5 минут).
        """
        if minutes < 1:
            return "менее минуты"
        if minutes < 60:
            return f"{int(minutes)} мин."
        hours = int(minutes // 60)
        mins = int(minutes % 60)
        if mins == 0:
            return f"{hours} ч."
        return f"{hours} ч. {mins} мин."

    @staticmethod
    def _format_period(hours: float) -> str:
        """
        Форматировать период для голосового ответа.

        Естественные формы вместо "последние 24.0 часов".
        Порог на 5 часах: 2-4 → "часа", 5+ → "часов".
        """
        if hours <= 0.5:
            return "последние полчаса"
        if hours <= 1:
            return "последний час"
        if hours <= 1.5:
            return "последние полтора часа"
        if hours <= 2:
            return "последние 2 часа"
        if hours < 5:
            return f"последние {int(hours)} часа"
        if hours < 24:
            return f"последние {int(hours)} часов"
        if hours <= 24:
            return "сегодня"
        if hours <= 48:
            return "последние 2 дня"
        return f"последние {int(hours)} часов"

    @staticmethod
    def _make_header(hours: float) -> str:
        """
        Заголовок ответа с учётом периода.

        "За сегодня:" звучит неестественно, поэтому
        для «сегодня» используем отдельную форму.
        """
        if 23 <= hours <= 25:
            return "Сегодня:"
        if 47 <= hours <= 49:
            return "За последние 2 дня:"
        return f"За {ActivityStatsCommand._format_period(hours)}:"
    

