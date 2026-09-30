"""
Модуль конфигурации.

Архитектура:
1. Глубокая копия defaults (без побочных эффектов)
2. Валидация типов и значений при загрузке
3. Подстановка переменных среды ${VAR}
4. Типизированные property с документацией
5. Логирование источника каждого значения
6. Защита от дублирования секций

Каждое решение обосновано конкретной проблемой.
"""

import os
import copy
import re
import yaml
import logging
from typing import Any
from pathlib import Path

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Значения по умолчанию
# ═══════════════════════════════════════════

DEFAULTS = {
    "listener": {
        "model_path": "models/vosk-model-small-ru-0.22",
        "wake_word": "ассистент",
        "alternative_words": [],
    },
    "recognizer": {
        "language": "ru",
        "timeout": 5,
        "phrase_limit": 10,
        "use_whisper": True,
        "model_size": "medium",
    },
    "speaker": {
        "voice": "ru-RU-SvetlanaNeural",
        "rate": "+0%",
        "volume": "+0%",
        "cache": True,
    },
    "llm": {
        "provider": "none",
        "max_tokens": 100,
        "max_history": 4,
        "system_prompt": "",
        "ollama": {
            "model": "gemma3:4b",
            "base_url": "http://localhost:11434",
        },
        "openai": {
            "model": "gpt-4o-mini",
            "api_key": "",
        },
    },
    "assistant": {
        "follow_up_seconds": 4.0,
    },
    "apps": {},
    "process_names": {},
    "folders": {
        "projects": "",
    },
    "macros": {},
    "weather": {
        "city": "Moscow",
    },
    "logging": {
        "level": "INFO",
        "file": "assistant.log",
    },
    "app": {
        "hide_console": False,
        "show_notifications": True,
    },
}


# ═══════════════════════════════════════════
#  Валидация
# ═══════════════════════════════════════════

# Правила валидации: ключ → (тип, допустимые значения или None)
VALIDATION_RULES = {
    ("recognizer", "language"): (str, {"ru", "en", "de", "fr", "es", "zh", "ja"}),
    ("recognizer", "timeout"): (int, range(1, 31)),
    ("recognizer", "phrase_limit"): (int, range(1, 61)),
    ("recognizer", "model_size"): (str, {"tiny", "base", "small", "medium", "large-v3"}),
    ("recognizer", "use_whisper"): (bool, None),
    ("speaker", "voice"): (str, None),
    ("speaker", "rate"): (str, None),
    ("speaker", "cache"): (bool, None),
    ("llm", "provider"): (str, {"none", "ollama", "openai"}),
    ("llm", "max_tokens"): (int, range(10, 2001)),
    ("llm", "max_history"): (int, range(0, 21)),
    ("assistant", "follow_up_seconds"): ((int, float), None),
    ("logging", "level"): (str, {"DEBUG", "INFO", "WARNING", "ERROR"}),
    ("app", "hide_console"): (bool, None),
    ("app", "show_notifications"): (bool, None),
}


class Config:
    """
    Менеджер конфигурации.
    
    Загружает настройки из YAML файла, валидирует,
    подставляет переменные среды, предоставляет
    типизированный доступ.
    
    Потокобезопасность: read-only после инициализации.
    """

    def __init__(self, config_path: str = "config.yaml"):
        """
        Args:
            config_path: путь к файлу конфигурации.
        """
        self._config_path = config_path
        # Глубокая копия — изменения не затронут DEFAULTS
        self._config = copy.deepcopy(DEFAULTS)
        self._load_errors = []
        self._sources = {}  # ключ → "default" | "file" | "env"

        if os.path.exists(config_path):
            self._load(config_path)
        else:
            logger.warning(
                "Конфиг '%s' не найден, используются значения по умолчанию",
                config_path
            )

        # Подстановка переменных среды
        self._substitute_env_vars(self._config)

        # Валидация
        self._validate()

        # Проверка путей
        self._validate_paths()

        # Проверка макросов
        self._validate_macros()

        # Логирование
        if self._load_errors:
            for err in self._load_errors:
                logger.warning("Конфиг: %s", err)

    # ══════════════════════════════════════════════
    #  Загрузка
    # ══════════════════════════════════════════════

    def _load(self, path: str) -> None:
        """Загрузить и объединить с defaults."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_content = f.read()

            # Проверка на дублирование ключей
            self._check_duplicate_keys(raw_content)

            user_config = yaml.safe_load(raw_content)

            if user_config and isinstance(user_config, dict):
                self._deep_merge(self._config, user_config)
                logger.info("Конфигурация загружена: %s", path)
            else:
                logger.warning("Конфиг пуст или невалиден: %s", path)

        except yaml.YAMLError as e:
            self._load_errors.append(f"Ошибка парсинга YAML: {e}")
            logger.error("Ошибка парсинга %s: %s", path, e)
        except Exception as e:
            self._load_errors.append(f"Ошибка загрузки: {e}")
            logger.error("Ошибка загрузки %s: %s", path, e)

    def _check_duplicate_keys(self, content: str) -> None:
        """
        Предупредить о дублирующихся секциях верхнего уровня.
        
        YAML молча берёт последнее значение при дублировании.
        Это частый источник багов.
        """
        # Ищем ключи верхнего уровня (без отступа, с двоеточием)
        top_keys = re.findall(r'^([a-zA-Z_]\w*):', content, re.MULTILINE)

        seen = set()
        for key in top_keys:
            if key in seen:
                self._load_errors.append(
                    f"Дублирующаяся секция '{key}' — "
                    f"будет использовано последнее значение"
                )
            seen.add(key)

    def _deep_merge(self, base: dict, override: dict) -> None:
        """
        Рекурсивное объединение словарей.
        
        Правила:
        - dict + dict → рекурсивное объединение
        - всё остальное → override заменяет base
        - Список заменяется целиком (не конкатенируется)
        """
        for key, value in override.items():
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    # ══════════════════════════════════════════════
    #  Переменные среды
    # ══════════════════════════════════════════════

    def _substitute_env_vars(self, obj: Any, path: str = "") -> None:
        """
        Рекурсивная подстановка ${VAR_NAME} из переменных среды.
        
        Работает для ЛЮБОГО строкового значения, не только api_key.
        
        Примеры:
            ${OPENAI_API_KEY} → значение переменной
            ${HOME}/models → /home/user/models
            ${UNDEFINED} → "" (пустая строка + предупреждение)
        """
        if isinstance(obj, dict):
            for key, value in obj.items():
                current_path = f"{path}.{key}" if path else key
                if isinstance(value, str):
                    new_value = self._replace_env_in_string(value, current_path)
                    if new_value != value:
                        obj[key] = new_value
                elif isinstance(value, dict):
                    self._substitute_env_vars(value, current_path)
                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, str):
                            value[i] = self._replace_env_in_string(
                                item, f"{current_path}[{i}]"
                            )

    def _replace_env_in_string(self, value: str, path: str) -> str:
        """Заменить ${VAR} на значение переменной среды."""
        def _replacer(match):
            var_name = match.group(1)
            env_value = os.environ.get(var_name)
            if env_value is None:
                self._load_errors.append(
                    f"Переменная среды ${{{var_name}}} не задана "
                    f"(используется в {path})"
                )
                return ""
            self._sources[path] = "env"
            return env_value

        return re.sub(r'\$\{(\w+)\}', _replacer, value)

    # ══════════════════════════════════════════════
    #  Валидация
    # ══════════════════════════════════════════════

    def _validate(self) -> None:
        """Валидация типов и допустимых значений."""
        for key_path, (expected_type, allowed_values) in VALIDATION_RULES.items():
            value = self.get(*key_path)
            if value is None:
                continue

            # Проверка типа
            if not isinstance(value, expected_type):
                # Попробуем привести тип
                try:
                    if expected_type == int and isinstance(value, str):
                        converted = int(value)
                        self._set_nested(key_path, converted)
                        value = converted
                    elif expected_type == float and isinstance(value, (str, int)):
                        converted = float(value)
                        self._set_nested(key_path, converted)
                        value = converted
                    elif expected_type == bool and isinstance(value, str):
                        converted = value.lower() in ("true", "1", "yes", "да")
                        self._set_nested(key_path, converted)
                        value = converted
                    else:
                        type_name = (
                            expected_type.__name__
                            if hasattr(expected_type, '__name__')
                            else " | ".join(t.__name__ for t in expected_type)
                        )
                        self._load_errors.append(
                            f"{'.'.join(key_path)}: ожидается {type_name}, "
                            f"получено {type(value).__name__} ({value!r})"
                        )
                        continue
                except (ValueError, TypeError):
                    self._load_errors.append(
                        f"{'.'.join(key_path)}: невозможно привести "
                        f"{value!r} к {expected_type.__name__}"
                    )
                    continue

            # Проверка допустимых значений
            if allowed_values is not None:
                if isinstance(allowed_values, set):
                    if value not in allowed_values:
                        self._load_errors.append(
                            f"{'.'.join(key_path)}: '{value}' не входит в "
                            f"{allowed_values}"
                        )
                elif isinstance(allowed_values, range):
                    if value not in allowed_values:
                        self._load_errors.append(
                            f"{'.'.join(key_path)}: {value} вне диапазона "
                            f"[{allowed_values.start}, {allowed_values.stop - 1}]"
                        )
    
    def _validate_macros(self) -> None:
        """
        Базовая структурная валидация секции macros.

        Проверяет типы полей, не семантику (SUPPORTED_ACTIONS и т.д.).
        Глубокая валидация — в core/macros/loader.py.
        """
        macros = self._config.get("macros", {})
        if not macros or not isinstance(macros, dict):
            return

        for macro_id, data in macros.items():
            prefix = f"macros.{macro_id}"

            if not isinstance(data, dict):
                self._load_errors.append(
                    f"{prefix}: ожидается словарь, "
                    f"получено {type(data).__name__}"
                )
                continue

            if not data.get("keywords"):
                self._load_errors.append(
                    f"{prefix}: отсутствуют keywords — "
                    f"макрос нельзя будет вызвать голосом"
                )

            steps = data.get("steps")
            if not steps:
                self._load_errors.append(
                    f"{prefix}: отсутствуют steps"
                )
            elif not isinstance(steps, list):
                self._load_errors.append(
                    f"{prefix}.steps: ожидается список, "
                    f"получено {type(steps).__name__}"
                )

    def _validate_paths(self) -> None:
        """Проверка существования важных путей."""
        model_path = self.get("listener", "model_path")
        if model_path and not os.path.exists(model_path):
            self._load_errors.append(
                f"Vosk модель не найдена: {model_path}"
            )

    def _set_nested(self, keys: tuple, value: Any) -> None:
        """Установить значение по вложенному ключу."""
        obj = self._config
        for key in keys[:-1]:
            obj = obj.setdefault(key, {})
        obj[keys[-1]] = value

    # ══════════════════════════════════════════════
    #  Публичный API
    # ══════════════════════════════════════════════

    def get(self, *keys, default: Any = None) -> Any:
        """
        Получить значение по цепочке ключей.
        
        Примеры:
            config.get("speaker", "voice")
            config.get("llm", "ollama", "model")
            config.get("nonexistent", default="fallback")
        """
        result = self._config
        for key in keys:
            if isinstance(result, dict) and key in result:
                result = result[key]
            else:
                return default
        return result

    def get_int(self, *keys, default: int = 0) -> int:
        """Получить целое число."""
        value = self.get(*keys, default=default)
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def get_float(self, *keys, default: float = 0.0) -> float:
        """Получить число с плавающей точкой."""
        value = self.get(*keys, default=default)
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def get_bool(self, *keys, default: bool = False) -> bool:
        """Получить булево значение."""
        value = self.get(*keys, default=default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "да")
        return bool(value)

    def get_str(self, *keys, default: str = "") -> str:
        """Получить строку."""
        value = self.get(*keys, default=default)
        return str(value) if value is not None else default

    # ══════════════════════════════════════════════
    #  Типизированные свойства
    # ══════════════════════════════════════════════

    @property
    def listener(self) -> dict:
        """Настройки wake word detection."""
        return self._config.get("listener", {})

    @property
    def recognizer(self) -> dict:
        """Настройки распознавания речи."""
        return self._config.get("recognizer", {})

    @property
    def speaker(self) -> dict:
        """Настройки синтеза речи."""
        return self._config.get("speaker", {})

    @property
    def llm(self) -> dict:
        """Настройки LLM."""
        return self._config.get("llm", {})

    @property
    def apps(self) -> dict:
        """Алиасы приложений."""
        return self._config.get("apps", {})

    @property
    def process_names(self) -> dict:
        """Имена процессов для taskkill."""
        return self._config.get("process_names", {})

    @property
    def logging_config(self) -> dict:
        """Настройки логирования."""
        return self._config.get("logging", {})

    @property
    def has_errors(self) -> bool:
        """Были ли ошибки при загрузке."""
        return len(self._load_errors) > 0

    @property
    def errors(self) -> list[str]:
        """Список ошибок загрузки."""
        return list(self._load_errors)

    # ══════════════════════════════════════════════
    #  Отладка
    # ══════════════════════════════════════════════

    def dump(self) -> str:
        """Дамп текущей конфигурации (для отладки)."""
        import json

        # Маскируем чувствительные данные
        safe_config = copy.deepcopy(self._config)
        self._mask_sensitive(safe_config)

        return json.dumps(safe_config, ensure_ascii=False, indent=2)

    @staticmethod
    def _mask_sensitive(obj: dict) -> None:
        """Замаскировать пароли и ключи."""
        sensitive_keys = {"api_key", "password", "secret", "token"}
        for key, value in obj.items():
            if isinstance(value, dict):
                Config._mask_sensitive(value)
            elif isinstance(value, str) and key in sensitive_keys and value:
                obj[key] = value[:4] + "***" + value[-4:] if len(value) > 8 else "***"