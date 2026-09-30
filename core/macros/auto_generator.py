# core/macros/auto_generator.py

"""
Автоматический детектор кандидатов в макросы + запись в файл.

Архитектура:
- Анализирует найденные паттерны из PatternEngine
- Фильтрует кандидатов (длина, confidence, дубликаты)
- При согласии пользователя — пишет в data/generated_macros.yaml
- Перезагружает макросы в MacroCommand

Почему отдельный файл, а не config.yaml:
- yaml.dump() уничтожает комментарии и форматирование
- Пользовательские настройки и автогенерированные макросы —
  разные категории, не должны смешиваться
- Проще откатить: удалил файл — все авто-макросы исчезли

Файл data/generated_macros.yaml подгружается loader.py
вместе с основным конфигом.
"""

import os
import time
import shutil
import logging
from pathlib import Path
from typing import Optional

import yaml

from core.macros.generator import (
    generate_macro_from_pattern,
    generate_macro_from_session,
    generate_macro_from_sequence,
)

logger = logging.getLogger(__name__)


# Путь к файлу автогенерированных макросов
GENERATED_MACROS_PATH = "data/generated_macros.yaml"

# Максимум предложений автоматизации в день.
# Слишком часто → пользователь начнёт игнорировать.
MAX_AUTO_SUGGESTIONS_PER_DAY = 2

# Минимальная confidence для предложения автоматизации.
# Выше чем для обычных предложений (0.5):
# создание макроса — "навсегда", ошибка дороже.
MIN_AUTOMATION_CONFIDENCE = 0.7

# Минимальная длина последовательности для автоматизации
MIN_SEQUENCE_LENGTH = 2


class MacroAutoGenerator:
    """
    Детектор кандидатов в макросы + запись.

    Использование:
        gen = MacroAutoGenerator(storage)

        # Проверка кандидатов (вызывается из PatternEngine)
        candidate = gen.find_candidate(sequence_patterns, temporal_patterns)
        if candidate:
            speak(candidate["proposal_text"])
            if user_accepts:
                gen.save_macro(candidate["macro"])

        # Из сессии (голосовая команда)
        records = storage.get_activity(hours=1, action="app_switch")
        candidate = gen.find_candidate_from_session(records, "утро")
        if candidate:
            gen.save_macro(candidate["macro"])
    """

    def __init__(
        self,
        storage=None,
        generated_path: str = GENERATED_MACROS_PATH,
        llm=None,
    ):
        self._storage = storage
        self._generated_path = generated_path
        self._suggestions_today: list[float] = []

        # Кэш существующих макросов (для дедупликации)
        self._existing_macro_ids: set[str] = set()
        self._load_existing()
        self._llm = llm

    def _load_existing(self) -> None:
        """Загрузить ID существующих автогенерированных макросов."""
        if not os.path.exists(self._generated_path):
            return

        try:
            with open(
                self._generated_path, "r", encoding="utf-8",
            ) as f:
                data = yaml.safe_load(f) or {}
            self._existing_macro_ids = set(data.keys())
        except Exception as e:
            logger.debug("Could not load generated macros: %s", e)

    # ══════════════════════════════════════════════
    #  Поиск кандидатов
    # ══════════════════════════════════════════════

    def find_candidate(
        self,
        sequence_patterns: list,
        temporal_patterns: list = None,
    ) -> dict | None:
        """
        Найти лучшего кандидата в макросы среди паттернов.

        Returns:
            {
                "macro": dict,          # Готовый YAML макроса
                "proposal_text": str,   # Текст для озвучки
                "explanation": str,     # Обоснование
                "confidence": float,
                "pattern_id": str,
            }
            или None.
        """
        # Rate limit
        if not self._can_suggest():
            return None

        # Ищем среди последовательных паттернов
        best_candidate = None
        best_score = 0

        for sp in sequence_patterns:
            # Фильтры
            if sp.confidence < MIN_AUTOMATION_CONFIDENCE:
                continue
            if sp.occurrences < 5:
                continue

            total_steps = len(sp.antecedent) + 1
            if total_steps < MIN_SEQUENCE_LENGTH:
                continue

            # Собираем targets
            targets = list(sp.antecedent) + [sp.consequent]

            # Проверяем дубликаты
            if self._is_duplicate(targets):
                continue

            # Ищем совпадающий временной паттерн
            matching_temporal = None
            if temporal_patterns:
                matching_temporal = self._find_matching_temporal(
                    sp, temporal_patterns,
                )

            # Генерируем макрос
            macro = generate_macro_from_pattern(
                sp, matching_temporal,
            )
            if not macro:
                continue

            # Скоринг
            score = (
                sp.confidence
                * min(sp.lift / 3, 1.0)
                * (total_steps / 3)
            )

            if score > best_score:
                best_score = score
                best_candidate = {
                    "macro": macro,
                    "proposal_text": self._make_proposal_text(
                        macro, sp,
                    ),
                    "explanation": self._make_explanation(
                        sp, matching_temporal,
                    ),
                    "confidence": sp.confidence,
                    "pattern_id": sp.description,
                }

        if best_candidate:
            self._suggestions_today.append(time.time())

        return best_candidate

    def find_candidate_from_session(
        self,
        activity_records: list,
        time_hint: str | None = None,
    ) -> dict | None:
        """
        Создать кандидата из последней сессии.

        В отличие от find_candidate(), здесь нет
        порога confidence — пользователь явно попросил.

        Args:
            activity_records: записи из activity_log
            time_hint: "утро"/"день"/"вечер" для имени

        Returns:
            dict с ключами macro, proposal_text,
            explanation, confidence, pattern_id.
            Или None если не удалось.
        """
        macro = generate_macro_from_session(
            activity_records, time_hint,
            llm=self._llm,
        )
        if not macro:
            return None

        # Проверяем дубликат
        targets = [
            s.get("target", "")
            for s in macro.get("steps", [])
            if s.get("action") == "open_app"
        ]
        if self._is_duplicate(targets):
            logger.info(
                "Сессионный макрос — дубликат, пропускаем",
            )
            return None

        # Описание шагов для предложения
        steps_desc = ", ".join(targets[:5])
        if len(targets) > 5:
            steps_desc += f" и ещё {len(targets) - 5}"

        return {
            "macro": macro,
            "proposal_text": (
                f"Могу создать макрос «{macro['name']}» "
                f"с шагами: {steps_desc}. Сохранить?"
            ),
            "explanation": (
                f"Создано по запросу из последней сессии. "
                f"Запуск командой "
                f"«{macro.get('keywords', [''])[0]}»."
            ),
            "confidence": 1.0,
            "pattern_id": "manual_session",
        }

    # ══════════════════════════════════════════════
    #  Сохранение макроса
    # ══════════════════════════════════════════════

    def save_macro(self, macro: dict) -> bool:
        """
        Сохранить макрос в data/generated_macros.yaml.

        Создаёт бэкап перед записью.

        Returns: True если успешно.
        """
        macro_id = macro.pop("id", None)
        if not macro_id:
            macro_id = f"auto_{int(time.time())}"

        try:
            # Загружаем существующие
            existing = self._load_file()

            # Проверяем дубликат по ID
            if macro_id in existing:
                logger.warning(
                    "Макрос '%s' уже существует", macro_id,
                )
                return False

            # Проверяем дубликат по содержимому
            targets = [
                s.get("target", "")
                for s in macro.get("steps", [])
                if s.get("action") == "open_app"
            ]
            if self._is_duplicate(targets):
                logger.warning(
                    "Макрос '%s' — дубликат по шагам",
                    macro_id,
                )
                return False

            # Бэкап
            self._backup()

            # Добавляем
            existing[macro_id] = macro

            # Записываем
            self._save_file(existing)

            self._existing_macro_ids.add(macro_id)

            logger.info(
                "Макрос '%s' сохранён в %s",
                macro_id, self._generated_path,
            )
            return True

        except Exception as e:
            logger.error("Ошибка сохранения макроса: %s", e)
            return False

    def get_all_generated(self) -> dict:
        """Получить все автогенерированные макросы."""
        return self._load_file()

    def delete_macro(self, macro_id: str) -> bool:
        """Удалить автогенерированный макрос."""
        try:
            existing = self._load_file()
            if macro_id not in existing:
                return False

            self._backup()
            del existing[macro_id]
            self._save_file(existing)
            self._existing_macro_ids.discard(macro_id)

            logger.info(
                "Удалён макрос '%s'", macro_id,
            )
            return True

        except Exception as e:
            logger.error("Ошибка удаления макроса: %s", e)
            return False

    # ══════════════════════════════════════════════
    #  Вспомогательные
    # ══════════════════════════════════════════════

    def _can_suggest(self) -> bool:
        """Проверить rate limit."""
        now = time.time()
        today_start = now - (now % 86400)

        self._suggestions_today = [
            ts for ts in self._suggestions_today
            if ts > today_start
        ]

        return (
            len(self._suggestions_today)
            < MAX_AUTO_SUGGESTIONS_PER_DAY
        )

    def _is_duplicate(self, targets: list[str]) -> bool:
        """
        Проверить, не существует ли макрос с такими же шагами.

        Сравниваем по множеству targets (порядок не важен
        для определения дубликата —
        Chrome+Telegram ≈ Telegram+Chrome).
        """
        target_set = frozenset(t.lower() for t in targets if t)

        if not target_set:
            return False

        # Проверяем автогенерированные
        existing = self._load_file()
        for macro_data in existing.values():
            existing_targets = frozenset(
                s.get("target", "").lower()
                for s in macro_data.get("steps", [])
                if s.get("action") == "open_app"
            )
            if existing_targets == target_set:
                return True

        return False

    def _find_matching_temporal(
        self, seq_pattern, temporal_patterns,
    ):
        """Найти temporal паттерн для первого приложения."""
        first_target = seq_pattern.antecedent[0]
        for tp in temporal_patterns:
            if (
                tp.target == first_target
                and tp.confidence >= 0.4
            ):
                return tp
        return None

    def _make_proposal_text(
        self, macro: dict, pattern,
    ) -> str:
        """Текст предложения для озвучки."""
        steps = [
            s for s in macro.get("steps", [])
            if s.get("action") == "open_app"
        ]
        steps_desc = ", ".join(
            s.get("target", "?") for s in steps[:4]
        )
        extra = ""
        if len(steps) > 4:
            extra = f" и ещё {len(steps) - 4}"

        return (
            f"Я заметила повторяющуюся "
            f"последовательность: "
            f"{steps_desc}{extra}. "
            f"Создать макрос «{macro['name']}»?"
        )

    @staticmethod
    def _make_explanation(seq_pattern, temporal_pattern) -> str:
        desc = seq_pattern.description
        conf = int(seq_pattern.confidence * 100)
        text = (
            f"Последовательность {desc} встречается "
            f"в {conf}% случаев "
            f"({seq_pattern.occurrences} раз)."
        )
        if temporal_pattern:
            text += (
                f" Обычно около "
                f"{temporal_pattern.peak_time_str} "
                f"({temporal_pattern.day_type})."
            )
        return text

    # ══════════════════════════════════════════════
    #  Работа с файлом
    # ══════════════════════════════════════════════

    def _load_file(self) -> dict:
        """Загрузить generated_macros.yaml."""
        if not os.path.exists(self._generated_path):
            return {}

        try:
            with open(
                self._generated_path, "r", encoding="utf-8",
            ) as f:
                data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.warning(
                "Ошибка чтения %s: %s",
                self._generated_path, e,
            )
            return {}

    def _save_file(self, data: dict) -> None:
        """Записать generated_macros.yaml."""
        os.makedirs(
            os.path.dirname(self._generated_path) or ".",
            exist_ok=True,
        )

        with open(
            self._generated_path, "w", encoding="utf-8",
        ) as f:
            f.write(
                "# Автоматически сгенерированные макросы\n"
            )
            f.write(
                "# Этот файл управляется ассистентом.\n"
            )
            f.write(
                "# Вы можете редактировать, удалять "
                "или добавлять макросы.\n\n"
            )
            yaml.dump(
                data, f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
                width=120,
            )

    def _backup(self) -> None:
        """Бэкап перед записью."""
        if not os.path.exists(self._generated_path):
            return

        backup_path = self._generated_path + ".bak"
        try:
            shutil.copy2(self._generated_path, backup_path)
            logger.debug("Бэкап: %s", backup_path)
        except Exception as e:
            logger.warning(
                "Не удалось создать бэкап: %s", e,
            )