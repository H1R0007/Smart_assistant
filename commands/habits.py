# commands/habits.py

"""
Команда: привычки и паттерны пользователя.

"Какие у меня привычки?"
"Что я обычно делаю?"
"Мои паттерны"
"Почему ты это предложил?"
"""

import re
from commands.base import BaseCommand, CommandResult, normalize_text
class HabitsCommand(BaseCommand):
    """Голосовая команда для получения информации о привычках."""

    name = "habits"
    description = "Привычки и паттерны пользователя"
    priority = 7
    fuzzy_enabled = True

    keywords = [
        "мои привычки", "какие у меня привычки",
        "что я обычно делаю", "мои паттерны",
        "что я обычно открываю",
        "расскажи про мои привычки",
        "проанализируй мои действия",
    ]

    def __init__(self, ctx):
        super().__init__(ctx)

    @property
    def _engine(self):
        """Ленивый доступ к PatternEngine."""
        kb = getattr(self.ctx, "knowledge", None)
        if kb is None:
            return None
        return getattr(kb, "patterns", None)

    def is_valid_candidate(self, params: dict) -> bool:
        return self._engine is not None

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        text_lower = text.lower()

        # Запрос объяснения
        if any(w in text_lower for w in ["почему", "зачем", "откуда"]):
            return {"mode": "explain"}

        # Конкретный тип
        if any(w in text_lower for w in ["последовательност", "после чего"]):
            return {"mode": "sequences"}

        if any(w in text_lower for w in ["время", "когда", "расписани"]):
            return {"mode": "temporal"}

        return {"mode": "full"}

    def execute(self, text: str, params: dict) -> CommandResult:
        engine = self._engine
        if engine is None:
            self.speaker.say("Система анализа привычек не подключена.")
            return CommandResult(success=False)

        mode = params.get("mode", "full")

        try:
            if mode == "explain":
                response = self._explain(engine)
            elif mode == "sequences":
                response = self._sequences_only(engine)
            elif mode == "temporal":
                response = self._temporal_only(engine)
            else:
                response = self._full_report(engine)

            self.speaker.say(response)

            self.ctx.context.update(
                action="habits",
                target=mode,
                params=params,
            )

            self.logger.info("Habits query: mode=%s", mode)

        except Exception as e:
            self.logger.error("Ошибка анализа привычек: %s", e)
            self.speaker.say("Не удалось проанализировать привычки.")
            return CommandResult(success=False)

        return CommandResult(follow_up=True)

    # ══════════════════════════════════════════════
    #  Режимы ответа
    # ══════════════════════════════════════════════

    def _full_report(self, engine) -> str:
        """Полный отчёт с изменениями привычек."""
        if not engine.is_analyzed:
            return (
                "Данных пока мало. Через несколько дней "
                "смогу найти закономерности."
            )

        insights = engine.get_insights()

        # Добавляем изменения привычек
        changes = engine.get_habit_changes()
        if changes:
            changes_text = " ".join(changes[:3])
            insights += (
                f" Заметила изменения: {changes_text}"
            )

        return insights

    def _temporal_only(self, engine) -> str:
        """Только временные паттерны."""
        habits = engine.get_habits()
        temporal = habits.get("temporal", [])

        if not temporal:
            return "Пока не обнаружила регулярных временных привычек."

        items = []
        for h in temporal[:5]:
            day = "по будням" if h["day_type"] == "weekday" else "по выходным"
            items.append(f"{h['target']} около {h['when']} {day}")

        return "Твои временные привычки: " + ". ".join(items) + "."

    def _sequences_only(self, engine) -> str:
        """Только последовательности."""
        habits = engine.get_habits()
        seqs = habits.get("sequences", [])

        if not seqs:
            return "Устойчивых последовательностей пока не нашла."

        items = [s["pattern"] for s in seqs[:5]]
        return "Частые последовательности: " + ", ".join(items) + "."

    def _explain(self, engine) -> str:
        """Объяснение последнего предложения."""
        # Ищем последнее показанное предложение
        recent = engine._suggestions._recent
        shown = [
            s for s in recent
            if s.shown_at > 0
        ]

        if not shown:
            return "Я пока ничего не предлагала, нечего объяснять."

        last = shown[-1]
        return last.explanation