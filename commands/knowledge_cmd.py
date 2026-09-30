# commands/knowledge_cmd.py

"""
Голосовые команды для базы знаний.

Команды:
- Запомни / Запиши (с тегами и приоритетом)
- Найди в базе / Что я записывал про...
- Спроси базу (RAG)
- Что я делал сегодня (активность)
- Удали заметку
- Статистика базы
"""

import datetime
import logging

from commands.base import BaseCommand, CommandResult
from commands.system import _DangerousCommand

logger = logging.getLogger(__name__)


def _get_kb(ctx):
    """Получить KnowledgeBase из контекста."""
    return getattr(ctx, 'knowledge', None)


# ═══════════════════════════════════════════
#  Запоминание
# ═══════════════════════════════════════════

class RememberCommand(BaseCommand):
    """
    Запомнить информацию.
    
    "запомни что завтра встреча с Сашей"
    "запиши важное: сервер на 192.168.1.100"
    "запомни факт: столица Франции — Париж"
    "запомни рабочее: деплой в пятницу в 15:00"
    """
    name = "kb_remember"
    keywords = [
        "запомни", "запиши", "запомнить", "записать",
        "сохрани в базу", "добавь в базу",
        "сохрани", "запомни что",
    ]
    priority = 17

    # Маппинг слов на теги
    TAG_HINTS = {
        "работа": "работа",
        "рабочее": "работа",
        "рабочий": "работа",
        "проект": "работа,проект",
        "покупк": "покупки",
        "купить": "покупки",
        "магазин": "покупки",
        "важно": "",   # → importance=1
        "критично": "", # → importance=2
        "срочно": "",   # → importance=2
    }

    TYPE_HINTS = {
        "факт": "fact",
        "заметк": "note",
        "идея": "thought",
        "идею": "thought",
        "ссылк": "bookmark",
        "закладк": "bookmark",
    }

    def match(self, text: str) -> tuple[bool, dict]:
        t = text.lower().strip()
        
        # НЕ перехватываем команды для создания макросов и бэкапов
        ignore_phrases = [
            "как макрос", "эти действия", "последовательность",
            "ритуал", "базу", "резервную копию",
        ]
        
        for kw in self.keywords:
            if kw in t:
                # Если после ключевого слова идёт что-то из ignore_phrases - пропускаем
                if any(phrase in t for phrase in ignore_phrases):
                    return False, {}
                    
        return super().match(text)

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords)

        # Убираем связующие слова
        prefixes = [
            "что ", "мне ", "мне что ", "следующее ",
            "пожалуйста ", "в базу ",
        ]
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]

        # Определяем теги
        tags = []
        importance = 0
        entry_type = "note"

        text_lower = text.lower()
        for hint, tag in self.TAG_HINTS.items():
            if hint in text_lower:
                if tag:
                    tags.append(tag)
                if hint == "важно":
                    importance = 1
                elif hint in ("критично", "срочно"):
                    importance = 2

        for hint, etype in self.TYPE_HINTS.items():
            if hint in text_lower:
                entry_type = etype
                # Убираем слово-подсказку из текста
                cleaned = cleaned.replace(hint, "").strip()
                break

        # Убираем слова-маркеры из текста
        marker_words = {
            "важное", "важно", "важную", "важный",
            "критично", "критичное", "срочно", "срочное",
            "рабочее", "рабочий", "рабочая",
        }
        words = cleaned.split()
        words = [w for w in words if w.lower() not in marker_words]
        cleaned = " ".join(words)

        # Убираем двоеточие в начале
        cleaned = cleaned.lstrip(":").strip()

        return {
            "content": cleaned,
            "tags": ",".join(set(tags)),
            "importance": importance,
            "entry_type": entry_type,
        }

    def is_valid_candidate(self, params: dict) -> bool:
        return len(params.get("content", "").strip()) > 2

    def execute(self, text: str, params: dict) -> CommandResult:
        kb = _get_kb(self.ctx)
        if not kb:
            self.speaker.say("База знаний не подключена.")
            return CommandResult()

        content = params["content"].strip()
        tags = params.get("tags", "")
        importance = params.get("importance", 0)
        entry_type = params.get("entry_type", "note")

        entry_id = kb.remember(
            content=content,
            entry_type=entry_type,
            tags=tags,
            importance=importance,
        )

        # Формируем ответ
        response = f"Запомнила: {content}."
        if tags:
            response += f" Теги: {tags}."
        if importance > 0:
            response += " Помечено как важное."

        self.speaker.say(response)

        # Сохраняем контекст
        ctx = self.ctx.context
        ctx.update("kb_remember", content, {
            "entry_id": entry_id,
        })

        return CommandResult(follow_up=True)


# ═══════════════════════════════════════════
#  Поиск
# ═══════════════════════════════════════════

class SearchKnowledgeCommand(BaseCommand):
    """
    Поиск по базе знаний.
    
    "найди в базе молоко"
    "что я записывал про деплой"
    """
    name = "kb_search"
    keywords = [
        "найди в базе", "найдите в базе",
        "поищи в базе", "поищите в базе",
        "что я записывал", "что записано про",
        "найди в заметках", "поищи в заметках",
        "что я запоминал",
        "что я записывал про", "заметка про",
    ]
    priority = 17

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords)
        words = cleaned.split()
        stop_words = {"про", "о", "насчёт", "насчет", "на", "по"}
        words = [w for w in words if w not in stop_words]
        return {"query": " ".join(words).strip()}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("query", "").strip())

    def execute(self, text: str, params: dict) -> CommandResult:
        kb = _get_kb(self.ctx)
        if not kb:
            self.speaker.say("База знаний не подключена.")
            return CommandResult()

        query = params["query"]
        results = kb.search(query, limit=5)

        if not results:
            self.speaker.say(f"Ничего не нашла про {query}.")
            return CommandResult(follow_up=True)

        # Фильтруем по релевантности: только результаты
        # где query реально присутствует в тексте (FTS/hybrid)
        # или semantic score достаточно высок
        relevant = self._filter_relevant(results, query)

        if not relevant:
            self.speaker.say(f"Ничего конкретного про {query} не нашла.")
            return CommandResult(follow_up=True)

        # Формируем ответ для голоса
        response = self._format_voice_response(relevant, query)
        self.speaker.say(response)

        # Сохраняем контекст для follow-up команд
        ctx = self.ctx.context
        ctx.update("kb_search", query, {
            "results": [r.entry.id for r in relevant],
            "last_entry_id": relevant[0].entry.id if relevant else None,
        })

        return CommandResult(follow_up=True)

    @staticmethod
    def _filter_relevant(
        results: list,
        query: str,
    ) -> list:
        """
        Отфильтровать нерелевантные результаты.
        
        Проблема: semantic search может вернуть записи
        с низкой реальной релевантностью. FTS через OR
        может вернуть записи где есть только одно слово.
        
        Решение: для каждого результата проверяем
        содержит ли он хотя бы одно значимое слово запроса.
        Semantic-only результаты оставляем только с высоким score.
        """
        query_words = set(query.lower().split())
        # Убираем слишком короткие слова (предлоги)
        query_words = {w for w in query_words if len(w) > 2}

        filtered = []
        for r in results:
            content_lower = r.entry.content.lower()

            # Прямое вхождение слова запроса
            has_word_match = any(
                w in content_lower for w in query_words
            )

            if has_word_match:
                filtered.append(r)
            elif r.match_type == "semantic" and r.score > 0.45:
                # Высокая семантическая схожесть без прямого совпадения
                filtered.append(r)
            elif r.match_type == "hybrid":
                # Hybrid = был и в FTS и в semantic
                filtered.append(r)

        return filtered

    @staticmethod
    def _format_voice_response(results: list, query: str) -> str:
        """
        Форматирование для голоса.
        
        Правила:
        1. Один результат → полный текст + когда
        2. 2-3 результата → краткий текст каждого
        3. Больше → первые 3 + "и ещё N"
        4. Каждый пункт ≤ 60 символов
        5. Не начинать с "Нашла N" — бесполезная информация
        """
        if len(results) == 1:
            r = results[0]
            age = r.entry.age_display
            content = r.entry.content
            if len(content) > 80:
                content = content[:77] + "..."
            return f"По запросу {query}: {content}. Записано {age}."

        parts = []
        show_count = min(len(results), 3)

        for r in results[:show_count]:
            content = r.entry.content
            if len(content) > 60:
                content = content[:57] + "..."
            age = r.entry.age_display
            parts.append(f"{content}, {age}")

        response = ". ".join(parts)

        if len(results) > show_count:
            response += f". И ещё {len(results) - show_count}."

        return response


# ═══════════════════════════════════════════
#  RAG — вопрос по базе
# ═══════════════════════════════════════════

class AskKnowledgeCommand(BaseCommand):
    """
    Задать вопрос по базе знаний (RAG).
    
    "спроси базу когда встреча с Сашей"
    "что в базе про деплой"
    "по моим записям, когда дедлайн"
    """
    name = "kb_ask"
    keywords = [
        "спроси базу", "по моим записям",
        "что в базе", "ответь по базе",
        "из моих заметок",
    ]
    priority = 13

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords)
        words = cleaned.split()
        stop_words = {"про", "о", "насчёт", "насчет", "на", "по"}
        words = [w for w in words if w not in stop_words]
        return {"question": " ".join(words).strip()}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("question", "").strip())

    def execute(self, text: str, params: dict) -> CommandResult:
        kb = _get_kb(self.ctx)
        if not kb:
            self.speaker.say("База знаний не подключена.")
            return CommandResult()

        question = params["question"]

        self.speaker.beep()
        answer = kb.ask(question)

        if answer:
            self.speaker.say_streamed(answer)
        else:
            # Если RAG не нашёл — попробуем просто поиск
            results = kb.search(question, limit=3)
            if results:
                content = results[0].entry.content
                self.speaker.say(f"Точного ответа нет, но нашла: {content}.")
            else:
                self.speaker.say(
                    f"Не нашла ответа на {question} в базе."
                )

        return CommandResult(follow_up=True)

# ═══════════════════════════════════════════
#  Управление
# ═══════════════════════════════════════════

class KnowledgeStatsCommand(BaseCommand):
    """Статистика базы знаний."""
    name = "kb_stats"
    keywords = [
        "статистика базы", "сколько записей",
        "размер базы знаний", "база знаний",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        kb = _get_kb(self.ctx)
        if not kb:
            self.speaker.say("База знаний не подключена.")
            return CommandResult()

        stats = kb.stats
        parts = [
            f"В базе {stats['entries']} записей",
            f"{stats['embeddings']} проиндексировано",
            f"{stats['activities']} событий в логе",
        ]
        self.speaker.say(". ".join(parts) + ".")
        return CommandResult(follow_up=True)


class ForgetCommand(_DangerousCommand):
    """Удалить последнюю заметку."""
    name = "kb_forget"
    keywords = [
        "забудь последнее", "удали последнюю запись",
        "удали последнюю заметку",
    ]
    priority = 10
    
    # Добавляем настройки для DangerousCommand
    _confirm_timeout = 5
    _confirm_question = "Вы уверены, что хотите удалить последнюю запись?"
    _cancel_message = "Удаление отменено."

    def _do_execute(self) -> CommandResult:
        kb = self.ctx.knowledge
        if not kb:
            self.speaker.say("База знаний не подключена.")
            return CommandResult()

        recent = kb.storage.get_recent(1)
        if not recent:
            self.speaker.say("База пуста.")
            return CommandResult(follow_up=True)

        entry = recent[0]
        kb.forget(entry.id)
        
        # Обрезаем длинный текст для озвучки
        content = entry.content
        if len(content) > 40:
            content = content[:37] + "..."
            
        self.speaker.say(f"Удалено: {content}.")
        return CommandResult(follow_up=True)

class BackupKnowledgeCommand(BaseCommand):
    """Бэкап базы знаний."""
    name = "kb_backup"
    keywords = [
        "бэкап базы", "сохрани базу",
        "резервная копия базы",
    ]
    priority = 8

    def execute(self, text: str, params: dict) -> CommandResult:
        kb = _get_kb(self.ctx)
        if not kb:
            self.speaker.say("База знаний не подключена.")
            return CommandResult()

        path = kb.storage.backup()
        self.speaker.say(f"Бэкап создан.")
        return CommandResult(follow_up=True)

class ClipboardTrackingCommand(BaseCommand):
    """
    Включить/выключить автосохранение буфера обмена.
    
    "следи за буфером обмена"
    "не следи за буфером"
    """
    name = "kb_clipboard_tracking"
    keywords = [
        "следи за буфером", "отслеживай буфер",
        "сохраняй буфер", "запоминай буфер",
        "не следи за буфером", "не отслеживай буфер",
        "перестань следить за буфером",
    ]
    priority = 10

    def execute(self, text: str, params: dict) -> CommandResult:
        kb = _get_kb(self.ctx)
        if not kb:
            self.speaker.say("База знаний не подключена.")
            return CommandResult()

        t = text.lower()
        disable_words = ["не следи", "не отслеживай", "перестань", "выключи"]

        if any(w in t for w in disable_words):
            kb.tracker.enable_clipboard_tracking(False)
            self.speaker.say("Отслеживание буфера выключено.")
        else:
            kb.tracker.enable_clipboard_tracking(True)
            self.speaker.say(
                "Буду сохранять интересное из буфера обмена. "
                "Скажи 'не следи за буфером' чтобы отключить."
            )

        return CommandResult(follow_up=True)

class KnowledgeContextCommand(BaseCommand):
    """
    Контекстные операции после поиска/запоминания.
    
    После "найди в базе деплой":
    - "удали это" / "удали первое" → удаляет найденное
    - "подробнее" → полный текст первого результата
    
    После "запомни ...":
    - "удали это" → удаляет только что записанное
    - "добавь тег работа" → добавляет тег
    """
    name = "kb_context"
    keywords = [
        "удали это", "удали первое", "удали последнее",
        "подробнее", "подробности",
        "добавь тег",
    ]
    priority = 18
    fuzzy_enabled = False

    def match(self, text: str) -> tuple[bool, dict]:
        t = text.lower().strip()
        ctx = self.ctx.context.get()

        if not ctx.is_fresh:
            return False, {}

        if ctx.action not in ("kb_search", "kb_remember"):
            return False, {}

        # Удаление
        if any(w in t for w in ["удали это", "удали первое", "удали последнее"]):
            entry_id = ctx.params.get("last_entry_id") or ctx.params.get("entry_id")
            if entry_id:
                return True, {"action": "delete", "entry_id": entry_id}

        # Подробнее
        if any(w in t for w in ["подробнее", "подробности", "подробней"]):
            entry_id = ctx.params.get("last_entry_id") or ctx.params.get("entry_id")
            if entry_id:
                return True, {"action": "details", "entry_id": entry_id}

        # Добавить тег
        if "добавь тег" in t:
            entry_id = ctx.params.get("entry_id") or ctx.params.get("last_entry_id")
            tag = t.replace("добавь тег", "").strip()
            if entry_id and tag:
                return True, {"action": "add_tag", "entry_id": entry_id, "tag": tag}

        return False, {}

    def execute(self, text: str, params: dict) -> CommandResult:
        kb = _get_kb(self.ctx)
        if not kb:
            self.speaker.say("База знаний не подключена.")
            return CommandResult()

        action = params.get("action")
        entry_id = params.get("entry_id")

        if action == "delete":
            entry = kb.storage.get_entry(entry_id)
            if entry:
                kb.forget(entry_id)
                content = entry.content
                if len(content) > 40:
                    content = content[:37] + "..."
                self.speaker.say(f"Удалено: {content}.")
            else:
                self.speaker.say("Запись не найдена.")

        elif action == "details":
            entry = kb.storage.get_entry(entry_id)
            if entry:
                parts = [entry.content]
                if entry.tags:
                    parts.append(f"Теги: {entry.tags}")
                parts.append(f"Записано {entry.age_display}")
                if entry.importance > 0:
                    parts.append("Помечено как важное")

                # Связанные записи
                related = kb.storage.get_related(entry_id)
                if related:
                    rel_names = [r.content[:30] for r in related[:2]]
                    parts.append(f"Связано с: {', '.join(rel_names)}")

                self.speaker.say(". ".join(parts) + ".")
            else:
                self.speaker.say("Запись не найдена.")

        elif action == "add_tag":
            tag = params.get("tag", "")
            entry = kb.storage.get_entry(entry_id)
            if entry:
                existing = entry.tags
                new_tags = f"{existing},{tag}" if existing else tag
                kb.storage.update_tags(entry_id, new_tags)
                self.speaker.say(f"Тег '{tag}' добавлен.")
            else:
                self.speaker.say("Запись не найдена.")

        return CommandResult(follow_up=True)