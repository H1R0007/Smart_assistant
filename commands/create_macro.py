# commands/create_macro.py

"""
Команда: создание макроса из последних действий.

"Создай макрос из того что я делал"
"Запомни эти действия как макрос"
"Сохрани последовательность"

v3: Улучшения:
- Умное определение границ сессии (не фиксированный час)
- Голосовое именование с fallback
- Обогащённые шаги (volume, url, не только open_app)
- Подробное описание перед сохранением
"""

import time
import logging
from commands.base import BaseCommand, CommandResult
logger = logging.getLogger(__name__)


class CreateMacroCommand(BaseCommand):
    """Создание макроса из последней сессии."""

    name = "create_macro"
    description = "Создать макрос из последних действий"
    priority = 9
    fuzzy_enabled = True

    keywords = [
        "создай макрос",
        "запомни как макрос",
        "сохрани как макрос",
        "запомни эти действия",
        "сохрани последовательность",
        "создай ритуал",
        "создай макрос из того что я делал",
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._recognizer = getattr(ctx, "_recognizer", None)

    @property
    def _kb(self):
        return getattr(self.ctx, "knowledge", None)

    @property
    def _auto_gen(self):
        kb = self._kb
        if not kb:
            return None
        return getattr(kb, "macro_generator", None)

    def is_valid_candidate(self, params: dict) -> bool:
        return (
            self._kb is not None
            and self._auto_gen is not None
        )

    def extract_params(
        self, text: str, matched_keyword: str = "",
    ) -> dict:
        text_lower = text.lower()

        # Временной период определяется умнее:
        # По умолчанию — загружаем 4 часа,
        # а _find_last_session сам определит границы.
        # "сегодня" = 24 часа, "последний час" = 1 час
        hours = 4.0  # Достаточно для определения сессии
        if "сегодня" in text_lower:
            hours = 24.0
        elif "последн" in text_lower:
            num = self._extract_number(text_lower)
            if num and 1 <= num <= 24:
                hours = float(num)

        return {"hours": hours}

    def execute(self, text: str, params: dict) -> CommandResult:
        kb = self._kb
        auto_gen = self._auto_gen

        if not kb or not auto_gen:
            self.speaker.say("Система макросов не подключена.")
            return CommandResult(success=False)

        hours = params.get("hours", 4.0)

        try:
            # ── Получаем активность ──
            # Загружаем ВСЕ типы действий, не только app_switch
            records = kb.storage.get_activity(
                hours=hours,
                limit=200,  # Больше для определения сессии
            )

            if len(records) < 2:
                self.speaker.say(
                    "Недостаточно действий для создания "
                    "макроса. Поработай немного и попроси "
                    "снова."
                )
                return CommandResult(follow_up=True)

            # ── Время суток ──
            hour = time.localtime().tm_hour
            if 5 <= hour < 12:
                time_hint = "утро"
            elif 12 <= hour < 18:
                time_hint = "день"
            else:
                time_hint = "вечер"

            # ── Генерируем кандидата ──
            # generator.py сам определит границы сессии
            candidate = auto_gen.find_candidate_from_session(
                records, time_hint=time_hint,
            )

            if not candidate:
                self.speaker.say(
                    "Не удалось собрать макрос "
                    "из последних действий. "
                    "Возможно, действия слишком "
                    "однообразные или похожий макрос "
                    "уже существует."
                )
                return CommandResult(follow_up=True)

            # ── Описание и подтверждение ──
            macro = candidate["macro"]
            step_count = macro.get("step_count", len([
                s for s in macro["steps"]
                if s.get("action") != "wait"
            ]))
            duration = macro.get("session_duration_min", 0)

            # Подробное описание
            desc = macro.get("description", "")
            if duration > 0:
                self.speaker.say(
                    f"Нашла сессию длительностью "
                    f"{int(duration)} минут. "
                    f"{step_count} действий: {desc}. "
                    f"Сохранить как макрос?"
                )
            else:
                self.speaker.say(
                    f"Могу создать макрос из "
                    f"{step_count} действий: {desc}. "
                    f"Сохранить?"
                )

            response = self._listen()
            if not response or not self._is_positive_response(response):
                self.speaker.say("Хорошо, не сохраняю.")
                return CommandResult(follow_up=True)

            # ── Именование ──
            final_name = self._ask_for_name(macro)
            if final_name:
                macro["name"] = final_name
                macro["keywords"] = [final_name]
                # Добавляем "запусти {имя}"
                macro["keywords"].append(
                    f"запусти {final_name}",
                )

            # ── Сохранение ──
            # Сохраняем keyword ДО save_macro (pop удалит id)
            keyword = macro.get("keywords", ["макрос"])[0]

            success = auto_gen.save_macro(macro)

            if success:
                self.speaker.say(
                    f"Макрос «{keyword}» сохранён! "
                    f"Скажи «{keyword}» чтобы запустить."
                )
                self._reload_macros()
            else:
                self.speaker.say(
                    "Не удалось сохранить макрос. "
                    "Возможно, похожий уже существует."
                )

            self.ctx.context.update(
                action="create_macro",
                target=keyword,
                params=params,
            )

        except Exception as e:
            logger.error(
                "Ошибка создания макроса: %s",
                e, exc_info=True,
            )
            self.speaker.say(
                "Произошла ошибка при создании макроса."
            )
            return CommandResult(success=False)

        return CommandResult(follow_up=True)

    # ══════════════════════════════════════════════
    #  Именование
    # ══════════════════════════════════════════════

    def _ask_for_name(self, macro: dict) -> str | None:
        """
        Спросить у пользователя имя для макроса.

        Сценарий:
        1. Предлагаем автоимя
        2. Спрашиваем "подходит или назовёшь по-другому?"
        3. Если "да"/таймаут → автоимя
        4. Если что-то другое → используем как имя

        Whisper может исказить имя, но:
        - Пользователь увидит его в YAML и может поправить
        - Ключевое слово регистронезависимое
        - Для MVP это приемлемо

        Returns: новое имя или None (оставить автоимя)
        """
        auto_name = macro.get("name", "")

        self.speaker.say(
            f"Назову его «{auto_name}». "
            f"Подходит, или скажи своё название?"
        )

        response = self._listen(timeout=6)

        if not response:
            # Таймаут → оставляем автоимя
            return None

        response_lower = response.lower().strip()

        # Согласие → оставляем автоимя
        if self._is_positive_response(response_lower):
            return None

        # Отказ без альтернативы
        if self._is_negative_response(response_lower):
            self.speaker.say(
                "Хорошо, оставлю название по умолчанию."
            )
            return None

        # Всё остальное → новое имя
        # Очищаем от мусора Whisper
        clean_name = self._clean_name(response)

        if clean_name and len(clean_name) >= 2:
            self.speaker.say(f"Отлично, назову «{clean_name}».")
            return clean_name

        # Не удалось распознать → автоимя
        return None

    @staticmethod
    def _clean_name(raw: str) -> str:
        """
        Очистить распознанное имя от мусора.

        "назови его утренний набор" → "утренний набор"
        "пусть будет рабочий" → "рабочий"
        "назови утренний" → "утренний"
        """
        text = raw.lower().strip()

        # Убираем типичные вводные
        prefixes = [
            "назови его ", "назови это ",
            "пусть будет ", "название ",
            "назови ", "назовём ",
            "пускай будет ", "давай ",
            "имя ", "пусть ",
        ]
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):]
                break

        return text.strip()

    # ══════════════════════════════════════════════
    #  Вспомогательные
    # ══════════════════════════════════════════════

    def _listen(self, timeout: int = 5) -> str | None:
        """Распознать ответ пользователя."""
        if not self._recognizer:
            return None
        try:
            return self._recognizer.listen(
                timeout=timeout,
                phrase_limit=5,
            )
        except Exception:
            return None

    def _reload_macros(self) -> None:
        """Hot-reload MacroCommand через публичный callback."""
        success = self.ctx.reload_command("macro")
        if not success:
            self.logger.warning(
                "Hot-reload macro: команда недоступна "
                "после перезапуска",
            )