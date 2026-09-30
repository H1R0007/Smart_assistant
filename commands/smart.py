# commands/smart.py

"""
LLM-фоллбэк с контекстом диалога и базой знаний.

v3.2:
- Автоматический поиск по базе знаний перед запросом LLM
- LLM получает релевантные записи как контекст
- Фильтрация мусора Whisper
- Контекст последних действий
"""

from commands.base import BaseCommand, CommandResult
class SmartCommand(BaseCommand):
    name = "smart_llm"
    keywords = []
    priority = -100

    GARBAGE = {
        "турочек", "тодумие", "крой", "мммм",
        "ааааа", "эээ", "ну", "так", "ага",
        "хм", "ох", "ой", "ммм", "угу",
    }

    MIN_QUERY_LENGTH = 4

    # Слова-маркеры вопросов которые могут быть про записи
    KNOWLEDGE_HINTS = [
        "когда", "где", "какой", "какая", "какое",
        "сколько", "что я", "мой", "моя", "моё",
        "напомни", "расскажи про", "что там с",
        "пароль", "адрес", "номер", "ссылка",
    ]

    def match(self, text: str) -> tuple[bool, dict]:
        text = text.strip()

        if not text:
            return False, {}

        if text.lower() in self.GARBAGE:
            return False, {}

        if len(text) < self.MIN_QUERY_LENGTH:
            return False, {}

        words = text.split()
        if len(words) >= 3 and len(set(words)) == 1:
            return False, {}

        return True, {"query": text}

    def execute(self, text: str, params: dict) -> CommandResult:
        query = params.get("query", text)

        llm = getattr(self.ctx, 'llm', None)
        if llm is None:
            self.speaker.say("Не поняла команду.")
            return CommandResult()

        # ── Шаг 1: Проверяем базу знаний ──
        kb = getattr(self.ctx, 'knowledge', None)
        kb_context = ""

        if kb and self._might_need_knowledge(query):
            kb_context = self._search_knowledge(kb, query)

        # ── Шаг 2: Добавляем контекст действий ──
        action_context = self._get_action_context()

        # ── Шаг 3: Формируем запрос ──
        enriched_query = self._build_query(query, kb_context, action_context)

        self.speaker.beep()
        response = llm.ask(enriched_query)

        if response:
            # response уже очищен от markdown для голоса
            # raw_response содержит оригинал с markdown для GUI
            raw_response = llm.get_last_raw_response()
            self.speaker.say(response, raw_text=raw_response)
        else:
            self.speaker.say("Не удалось получить ответ.")

        return CommandResult(follow_up=True)

    def _might_need_knowledge(self, query: str) -> bool:
        """
        Определить стоит ли искать в базе знаний.
        
        Не все вопросы требуют поиска:
        - "расскажи анекдот" → нет
        - "когда у меня деплой" → да
        - "что такое нейросеть" → нет
        - "какой пароль от сервера" → да
        """
        q = query.lower()

        # Явные маркеры личных вопросов
        for hint in self.KNOWLEDGE_HINTS:
            if hint in q:
                return True

        # Если вопрос короткий и не содержит маркеров — не ищем
        if len(query.split()) <= 3:
            return False

        # По умолчанию — ищем (лучше найти лишнее чем пропустить)
        return True

    def _search_knowledge(self, kb, query: str) -> str:
        """
        Поиск по базе знаний для контекста LLM.
        
        Возвращает отформатированный контекст или пустую строку.
        """
        try:
            results = kb.search(query, limit=3)

            if not results:
                return ""

            # Фильтруем: только реально релевантные
            relevant = []
            query_words = {
                w for w in query.lower().split() if len(w) > 2
            }

            for r in results:
                content_lower = r.entry.content.lower()
                # Прямое совпадение слов
                has_match = any(w in content_lower for w in query_words)
                # Высокий semantic score
                high_score = (
                    r.match_type in ("semantic", "hybrid")
                    and r.score > 0.4
                )

                if has_match or high_score:
                    relevant.append(r)

            if not relevant:
                return ""

            # Форматируем для LLM
            lines = []
            for r in relevant[:3]:
                age = r.entry.age_display
                lines.append(f"- {r.entry.content} ({age})")

            return "\n".join(lines)

        except Exception as e:
            self.logger.debug("KB search in smart: %s", e)
            return ""

    def _get_action_context(self) -> str:
        """Контекст последних действий."""
        ctx = self.ctx.context.get()

        if not ctx.is_fresh:
            return ""

        context_hints = {
            "open_app": f"Пользователь только что открыл {ctx.target}",
            "search": f"Пользователь только что искал '{ctx.target}'",
            "weather": f"Обсуждали погоду в {ctx.target}",
        }

        return context_hints.get(ctx.action, "")

    @staticmethod
    def _build_query(
        query: str,
        kb_context: str,
        action_context: str,
    ) -> str:
        """Собрать финальный запрос для LLM."""
        parts = [query]

        if kb_context:
            parts.append(
                f"\n(Мои личные записи по теме:\n{kb_context}\n"
                f"Используй их если они релевантны вопросу.)"
            )

        if action_context:
            parts.append(f"\n(Контекст: {action_context})")

        return "\n".join(parts)