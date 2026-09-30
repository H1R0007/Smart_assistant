"""
Автоматическая загрузка команд-плагинов.

Архитектура:
1. Сканирование пакета commands/
2. Поиск всех подклассов BaseCommand
3. Валидация (name, keywords, execute)
4. Проверка уникальности имён
5. Обнаружение конфликтов ключевых слов
6. Детальное логирование

Каждое решение обосновано конкретной проблемой.
"""

import importlib
import pkgutil
import logging
from collections import defaultdict

from commands.base import BaseCommand

logger = logging.getLogger(__name__)

def _determine_match_type(cls: type) -> str:
    """Определить тип матча один раз при загрузке (не при каждом запросе)."""
    for klass in cls.__mro__:
        if klass is BaseCommand:
            break
        if 'match' in klass.__dict__:
            return "custom"
    return "keyword"

def load_all_commands(ctx) -> list[BaseCommand]:
    """
    Загрузить все команды-плагины из пакета commands/.
    
    Args:
        ctx: AssistantContext с полями speaker, config, llm
        
    Returns:
        Список экземпляров команд, отсортированный по приоритету DESC
    """
    plugins = []
    names_seen = {}  # name → module_name (для проверки уникальности)
    errors = []

    # ── Шаг 1: Импортируем все модули ──
    package = importlib.import_module("commands")

    module_names = sorted(
        modname
        for importer, modname, ispkg in pkgutil.iter_modules(package.__path__)
        if modname != "base" and not modname.startswith("_")
    )

    for modname in module_names:
        full_name = f"commands.{modname}"
        try:
            module = importlib.import_module(full_name)
        except Exception as e:
            errors.append(f"Импорт {full_name}: {e}")
            logger.error("Ошибка импорта %s: %s", full_name, e)
            continue

        # ── Шаг 2: Находим все подклассы BaseCommand ──
        for attr_name in dir(module):
            attr = getattr(module, attr_name)

            # Фильтрация
            if not isinstance(attr, type):
                continue
            if not issubclass(attr, BaseCommand):
                continue
            if attr is BaseCommand:
                continue
            # Пропускаем приватные базовые классы (_DangerousCommand)
            if attr_name.startswith("_"):
                continue

            # ── Шаг 3: Валидация ──
            validation_error = _validate_command_class(attr, modname)
            if validation_error == "DISABLED":
                continue  # Просто пропускаем выключенную команду
            elif validation_error:
                errors.append(validation_error)
                continue

            # ── Шаг 4: Проверка уникальности имени ──
            if attr.name in names_seen:
                errors.append(
                    f"Дублирующееся имя '{attr.name}' "
                    f"в {modname} (уже в {names_seen[attr.name]})"
                )
                continue

            # ── Шаг 5: Создание экземпляра ──
            try:
                instance = attr(ctx)
                plugins.append(instance)
                names_seen[attr.name] = modname
            except Exception as e:
                errors.append(f"Создание {attr_name} из {modname}: {e}")
                logger.error("Ошибка создания %s: %s", attr_name, e)
            
            try:
                instance = attr(ctx)
                # Кэшируем тип матча (вместо рефлексии MRO при каждом запросе)
                instance._cached_match_type = _determine_match_type(attr)
                plugins.append(instance)
                names_seen[attr.name] = modname
            except Exception as e:
                errors.append(f"Создание {attr_name} из {modname}: {e}")
                logger.error("Ошибка создания %s: %s", attr_name, e)

    # ── Шаг 6: Сортировка по приоритету ──
    plugins.sort(key=lambda p: p.priority, reverse=True)

    # ── Шаг 7: Проверка конфликтов ключевых слов ──
    _check_keyword_conflicts(plugins)

    # ── Шаг 8: Логирование ──
    _log_loaded_commands(plugins, errors)

    return plugins


def _validate_command_class(cls, module_name: str) -> str | None:
    """
    Проверить что класс команды корректен.
    Возвращает строку ошибки или None если всё ok.
    Если возвращает "DISABLED", класс просто пропускается без ошибки.
    """
    # Проверяем флаг включения (по умолчанию True)
    if not getattr(cls, 'enabled', True):
        return "DISABLED"

    # Имя обязательно
    if not getattr(cls, 'name', ''):
        return f"{cls.__name__} в {module_name}: пустое имя (name)"

    # Keywords — список
    keywords = getattr(cls, 'keywords', None)
    if keywords is None:
        return f"{cls.__name__} в {module_name}: keywords = None"

    # execute должен быть переопределён
    if cls.execute is BaseCommand.execute:
        return f"{cls.__name__} в {module_name}: execute() не переопределён"

    return None

def _is_word_substring(short: str, long: str) -> bool:
    """Проверить что short входит в long как подстрока по границам слов."""
    short_words = short.split()
    long_words = long.split()
    short_len = len(short_words)
    for i in range(len(long_words) - short_len + 1):
        if long_words[i:i + short_len] == short_words:
            return True
    return False

def _check_keyword_conflicts(plugins: list[BaseCommand]) -> None:
    """
    Обнаружить потенциальные конфликты ключевых слов.
    
    Предупреждает, если короткое ключевое слово одной команды
    перехватывает длинное ключевое слово другой.
    Игнорирует команды, у которых переопределён метод match()
    (считаем, что разработчик там сам обработал коллизии).
    """
    keyword_map = []  # (keyword, command_name, priority, has_custom_match)

    for plugin in plugins:
        # Проверяем, переопределён ли метод match
        # Если переопределён, значит команда имеет сложную логику
        # и статический анализ по ключевым словам даст ложные срабатывания.
        has_custom_match = False
        for cls in type(plugin).__mro__:
            if cls is BaseCommand:
                break
            if 'match' in cls.__dict__:
                has_custom_match = True
                break

        for kw in plugin.keywords:
            keyword_map.append((kw, plugin.name, plugin.priority, has_custom_match))

    conflicts = []
    for i, (kw1, name1, pri1, custom1) in enumerate(keyword_map):
        for j, (kw2, name2, pri2, custom2) in enumerate(keyword_map):
            if i == j or name1 == name2:
                continue

            # Если первая команда имеет кастомный match(),
            # мы доверяем её логике и не бросаем варнинг.
            if custom1:
                continue

            if kw1 != kw2 and _is_word_substring(kw1, kw2):
                if pri1 > pri2:
                    conflict = (
                        f"'{kw1}' ({name1}, p={pri1}) "
                        f"перехватит '{kw2}' ({name2}, p={pri2})"
                    )
                    if conflict not in conflicts:
                        conflicts.append(conflict)

    if conflicts:
        logger.warning("Потенциальные конфликты ключевых слов:")
        for c in conflicts[:10]:
            logger.warning("  ⚠ %s", c)


def _log_loaded_commands(
    plugins: list[BaseCommand],
    errors: list[str]
) -> None:
    """Детальное логирование загруженных команд."""
    if errors:
        logger.warning("Ошибки загрузки команд (%d):", len(errors))
        for err in errors:
            logger.warning("  ✗ %s", err)

    logger.info("Загружено %d команд:", len(plugins))

    # Группируем по приоритету для наглядности
    by_priority = defaultdict(list)
    for p in plugins:
        by_priority[p.priority].append(p)

    for priority in sorted(by_priority.keys(), reverse=True):
        cmds = by_priority[priority]
        names = ", ".join(c.name for c in cmds)
        logger.debug("  [p=%d] %s", priority, names)