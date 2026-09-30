# core/macros/generator.py

"""
Генерация макросов из паттернов и сессий.

v3: Ключевые улучшения:
1. Умное определение границ сессии (по паузам в активности)
2. Обогащённые шаги (volume, brightness, open_url, не только open_app)
3. Группировка связанных действий
4. Фильтрация шума (короткие переключения, системные)
5. Подготовка описания для голосового подтверждения имени

Формат макроса (dict → YAML):
{
    "id": "auto_1234567890",
    "name": "утренняя рабочая сессия",
    "steps": [
        {"action": "volume", "target": "30", "value": "30"},
        {"action": "open_url", "target": "https://gmail.com"},
        {"action": "open_app", "target": "VS Code"},
    ],
    "keywords": ["утренняя рабочая сессия"],
    "description": "Громкость 30% → gmail.com → VS Code",
    "created_at": "2025-01-15 09:30",
    "source": "session",
}
"""

import json
import time
import logging
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Фильтры
# ═══════════════════════════════════════════

# Приложения-шум (системные, не осмысленные)
SKIP_TARGETS = {
    "", "explorer", "explorer.exe",
    "taskbar", "desktop", "shell_traywnd",
    "progman", "applicationframehost",
    "searchhost", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost",
    "lockapp", "systemsettings",
    "рабочий стол", "проводник",
}

# Команды которые имеет смысл включать в макрос
MACRO_WORTHY_COMMANDS = {
    "volume", "brightness", "open_app",
    "web_search", "open_url",
}

# Команды-шум (не включать)
SKIP_COMMANDS = {
    "time", "weather", "status", "help",
    "create_macro", "macro", "habits",
    "what_can_you_do", "stop", "pause",
}

# ═══════════════════════════════════════════
#  Лимиты
# ═══════════════════════════════════════════

MIN_STEPS = 2
MAX_STEPS = 15
DEFAULT_WAIT = 2  # секунды между шагами

# Определение границ сессии
SESSION_GAP_MINUTES = 10  # Пауза >= N минут = граница сессии
MIN_APP_DURATION = 5      # Мин. секунд в приложении


# ═══════════════════════════════════════════
#  Публичные функции
# ═══════════════════════════════════════════

def generate_macro_from_session(
    activity_records: list,
    time_hint: str | None = None,
    llm=None,
) -> dict | None:
    """
    Создать макрос из записей активности.

    v3: анализирует ВСЕ типы действий, определяет
    границы сессии автоматически.

    Args:
        activity_records: записи из activity_log
            (объекты с .action, .target, .timestamp,
             .details, .duration_sec)
        time_hint: "утро"/"день"/"вечер" для имени

    Returns:
        dict (YAML-совместимый) или None
    """
    # Определяем границы последней сессии
    session = _find_last_session(activity_records)

    if not session:
        logger.debug("Не удалось определить сессию")
        return None

    # Извлекаем обогащённые шаги
    steps = _extract_enriched_steps(session)

    if len(steps) < MIN_STEPS:
        logger.debug(
            "Мало шагов для макроса: %d < %d",
            len(steps), MIN_STEPS,
        )
        return None

    if len(steps) > MAX_STEPS:
        steps = steps[:MAX_STEPS]

    # Описание и имя
    description_parts = []
    app_targets = []
    for s in steps:
        desc = _step_description(s)
        if desc:
            description_parts.append(desc)
        if s["action"] in ("open_app", "open_url"):
            app_targets.append(
                s["target"].split("/")[2]
                if "://" in s["target"]
                else s["target"]
            )

    name = _generate_name(
        name = _generate_name_smart(
        app_targets or description_parts, time_hint,
        llm=llm,
    )
    )
    keywords = _generate_keywords(
        app_targets or description_parts, name,
    )
    description = " → ".join(description_parts[:8])

    full_steps = _add_waits(steps)
    macro_id = f"auto_{int(time.time())}"

    return {
        "id": macro_id,
        "name": name,
        "keywords": keywords,
        "description": description,
        "steps": full_steps,
        "step_count": len(steps),
        "session_duration_min": _session_duration_min(session),
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "source": "session",
    }


def generate_macro_from_pattern(
    seq_pattern,
    temporal_pattern=None,
) -> dict | None:
    """Создать макрос из sequence-паттерна."""
    targets = list(seq_pattern.antecedent) + [seq_pattern.consequent]

    targets = [
        t for t in targets
        if t.lower() not in SKIP_TARGETS
    ]

    if len(targets) < MIN_STEPS:
        return None

    steps = [
        {"action": "open_app", "target": t}
        for t in targets
    ]
    full_steps = _add_waits(steps)

    time_hint = None
    if temporal_pattern:
        hour = temporal_pattern.peak_minute // 60
        if 5 <= hour < 12:
            time_hint = "утро"
        elif 12 <= hour < 18:
            time_hint = "день"
        else:
            time_hint = "вечер"

    name = _generate_name(targets, time_hint)
    keywords = _generate_keywords(targets, name)
    macro_id = f"auto_{int(time.time())}"

    return {
        "id": macro_id,
        "name": name,
        "keywords": keywords,
        "description": seq_pattern.description,
        "steps": full_steps,
        "step_count": len(steps),
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "source": "pattern",
    }


def generate_macro_from_sequence(
    targets: list[str],
    name: str | None = None,
) -> dict | None:
    """Простой хелпер: список targets → макрос."""
    filtered = [
        t for t in targets
        if t.lower() not in SKIP_TARGETS
    ]

    if len(filtered) < MIN_STEPS:
        return None

    steps = [
        {"action": "open_app", "target": t}
        for t in filtered
    ]
    full_steps = _add_waits(steps)

    if not name:
        name = _generate_name(filtered)

    keywords = _generate_keywords(filtered, name)
    macro_id = f"auto_{int(time.time())}"

    return {
        "id": macro_id,
        "name": name,
        "keywords": keywords,
        "description": " → ".join(filtered),
        "steps": full_steps,
        "step_count": len(steps),
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "source": "manual",
    }


# ═══════════════════════════════════════════
#  Определение границ сессии
# ═══════════════════════════════════════════

def _find_last_session(records: list) -> list:
    """
    Найти последнюю логическую сессию в записях.

    Трёхуровневая стратегия (от строгой к мягкой):

    1. Пауза ≥ SESSION_GAP_MINUTES (10 мин) → чёткая граница.
       Типичные причины: обед, совещание, ушёл от компьютера.

    2. Пауза ≥ 5 мин → умеренная граница (fallback).
       Типичные причины: перерыв на кофе, телефон.

    3. Нет пауз ≥ 5 мин → последние 30 мин или 20 записей
       (что короче). Защищает от включения 4-часовой
       непрерывной активности в один макрос.

    Returns: список записей последней сессии (ASC по timestamp)
    """
    if not records:
        return []

    sorted_records = sorted(
        records, key=lambda r: r.timestamp,
    )

    # Стратегия 1: пауза ≥ SESSION_GAP_MINUTES (10 мин)
    idx = _find_gap_from_end(
        sorted_records, SESSION_GAP_MINUTES * 60,
    )
    if idx is not None:
        session = sorted_records[idx:]
        logger.debug(
            "Сессия (стратегия 1: gap ≥ %d мин): "
            "%d записей, %.0f мин",
            SESSION_GAP_MINUTES, len(session),
            _session_duration_min(session),
        )
        return session

    # Стратегия 2: пауза ≥ 5 мин
    FALLBACK_GAP = 300
    idx = _find_gap_from_end(sorted_records, FALLBACK_GAP)
    if idx is not None:
        session = sorted_records[idx:]
        logger.debug(
            "Сессия (стратегия 2: gap ≥ 5 мин): "
            "%d записей, %.0f мин",
            len(session),
            _session_duration_min(session),
        )
        return session

    # Стратегия 3: последние N минут/записей
    MAX_SESSION_MINUTES = 30
    MAX_SESSION_RECORDS = 20

    cutoff_time = (
        sorted_records[-1].timestamp
        - MAX_SESSION_MINUTES * 60
    )
    recent = [
        r for r in sorted_records
        if r.timestamp >= cutoff_time
    ]

    if len(recent) > MAX_SESSION_RECORDS:
        recent = sorted_records[-MAX_SESSION_RECORDS:]

    if len(recent) < MIN_STEPS:
        if len(sorted_records) >= MIN_STEPS:
            recent = sorted_records[-MIN_STEPS:]
        else:
            return []

    logger.debug(
        "Сессия (стратегия 3: последние %d мин / %d записей): "
        "%d записей, %.0f мин",
        MAX_SESSION_MINUTES, MAX_SESSION_RECORDS,
        len(recent),
        _session_duration_min(recent),
    )
    return recent

def _find_gap_from_end(
    records: list, gap_seconds: float,
) -> int | None:
    """
    Найти индекс начала последней сессии.

    Идёт с конца записей, ищет паузу ≥ gap_seconds.
    Возвращает индекс первой записи ПОСЛЕ паузы.
    None если пауза не найдена.
    """
    for i in range(len(records) - 1, 0, -1):
        gap = records[i].timestamp - records[i - 1].timestamp
        if gap >= gap_seconds:
            return i
    return None

def _session_duration_min(records: list) -> float:
    """Длительность сессии в минутах."""
    if len(records) < 2:
        return 0.0
    return (
        records[-1].timestamp - records[0].timestamp
    ) / 60


# ═══════════════════════════════════════════
#  Обогащённое извлечение шагов
# ═══════════════════════════════════════════

def _extract_enriched_steps(records: list) -> list[dict]:
    """
    Извлечь осмысленные шаги из ВСЕХ типов активности.

    Анализирует:
    - app_switch → open_app (с фильтрацией коротких)
    - command:volume → volume
    - command:brightness → brightness
    - command:web_search → open_url
    - command:open_app → open_app (с target из details)

    Порядок: хронологический.

    Дедупликация:
    - Подряд идущие одинаковые шаги → один шаг
    - open_app: каждое приложение максимум 1 раз
      (убирает рабочие циклы IDE↔браузер↔терминал)
    """
    steps = []
    prev_step_sig = None
    seen_apps: set[str] = set()

    for record in records:
        extracted = _record_to_step(record)
        if not extracted:
            continue

        step_sig = (
            extracted["action"],
            extracted.get("target", ""),
        )

        # Дедупликация подряд идущих
        if step_sig == prev_step_sig:
            continue

        # Глобальная дедупликация для open_app:
        # Chrome → VS Code → Terminal → Chrome(skip)
        # Рабочий цикл превращается в список уникальных
        # приложений в порядке первого появления.
        if extracted["action"] == "open_app":
            app_key = extracted["target"].lower()
            if app_key in seen_apps:
                continue
            seen_apps.add(app_key)

        steps.append(extracted)
        prev_step_sig = step_sig

    return steps


def _record_to_step(record) -> dict | None:
    """
    Преобразовать одну запись activity_log в шаг макроса.

    Returns: dict шага или None
    """
    action = record.action
    target = (record.target or "").strip()
    details_str = getattr(record, 'details', "") or ""

    # ── app_switch → open_app ──
    if action == "app_switch":
        if target.lower() in SKIP_TARGETS:
            return None

        # Фильтруем слишком короткие переключения
        duration = getattr(record, 'duration_sec', 0) or 0
        if 0 < duration < MIN_APP_DURATION:
            return None

        return {"action": "open_app", "target": target}

    # ── command → разные шаги ──
    if action == "command":
        return _command_to_step(target, details_str)

    return None


def _command_to_step(
    command_name: str,
    details_str: str,
) -> dict | None:
    """
    Преобразовать запись команды в шаг макроса.

    Не все команды подходят:
    - "какое время" → бессмысленно повторять
    - "громкость 30" → полезно в ритуале
    """
    if command_name in SKIP_COMMANDS:
        return None

    if command_name not in MACRO_WORTHY_COMMANDS:
        return None

    details = {}
    if details_str:
        try:
            details = json.loads(details_str)
        except (json.JSONDecodeError, TypeError):
            pass

    # ── volume ──
    if command_name == "volume":
        level = details.get("level")
        if level is not None:
            return {
                "action": "volume",
                "target": str(level),
                "value": str(level),
            }

    # ── brightness ──
    if command_name == "brightness":
        level = details.get("level")
        if level is not None:
            return {
                "action": "brightness",
                "target": str(level),
                "value": str(level),
            }

    # ── open_app (из команды) ──
    if command_name == "open_app":
        app = details.get("app", "")
        if app and app.lower() not in SKIP_TARGETS:
            return {"action": "open_app", "target": app}

    # ── web_search → open_url ──
    if command_name == "web_search":
        url = details.get("url", "")
        query = details.get("query", "")
        if url:
            return {"action": "open_url", "target": url}
        elif query:
            search_url = (
                f"https://www.google.com/search"
                f"?q={quote_plus(query)}"
            )
            return {"action": "open_url", "target": search_url}

    # ── open_url ──
    if command_name == "open_url":
        url = details.get("url", "")
        if url:
            return {"action": "open_url", "target": url}

    return None


def _step_description(step: dict) -> str:
    """Человекочитаемое описание шага."""
    action = step.get("action", "")
    target = step.get("target", "")

    if action == "open_app":
        return target
    elif action == "open_url":
        url = target
        if "://" in url:
            url = url.split("://", 1)[1]
        if url.startswith("www."):
            url = url[4:]
        if "/" in url:
            url = url.split("/")[0]
        if len(url) > 30:
            url = url[:27] + "..."
        return url
    elif action == "volume":
        return f"Громкость {target}%"
    elif action == "brightness":
        return f"Яркость {target}%"
    elif action == "say":
        return f"«{target[:20]}»"
    else:
        return ""


# ═══════════════════════════════════════════
#  Имя и keywords
# ═══════════════════════════════════════════
def _generate_name_smart(
    items: list[str],
    time_hint: str | None = None,
    llm=None,
) -> str:
    """
    Имя макроса: через LLM если доступен, иначе шаблон.

    LLM генерирует естественное название:
    "Chrome, VS Code, Terminal" → "утренняя разработка"
    вместо шаблонного "утренняя рабочая сессия".

    Fallback на _generate_name если LLM недоступен
    или вернул мусор.
    """
    if llm:
        apps = ", ".join(items[:5])
        time_ctx = (
            f" Время суток: {time_hint}." if time_hint else ""
        )

        prompt = (
            "Придумай короткое название (2-3 слова) "
            "для голосового макроса, который открывает: "
            f"{apps}.{time_ctx} "
            "Ответь ТОЛЬКО названием, без кавычек, "
            "пояснений и знаков препинания. "
            "Название на русском, легко произносимое."
        )

        try:
            name = llm.ask(prompt)
            if name:
                # Очистка от мусора LLM
                name = name.strip().strip(
                    '"\'«».,!?;:()'
                ).lower()
                # Убираем переносы строк (LLM иногда добавляет)
                name = name.split("\n")[0].strip()
                if 2 <= len(name) <= 40 and " " in name:
                    return name
        except Exception:
            pass

    # Fallback: шаблонное именование
    return _generate_name(items, time_hint)

def _generate_name(
    items: list[str],
    time_hint: str | None = None,
) -> str:
    """
    Имя макроса.

    Стратегия:
    - 1-2 элемента: перечисляем
    - 3+: "рабочая сессия" / "вечерний набор"
    - С time_hint: добавляем префикс
    """
    if not items:
        base = "новый макрос"
    elif len(items) == 1:
        base = items[0].lower()
    elif len(items) == 2:
        base = " и ".join(i.lower() for i in items if i)
    else:
        # Для 3+ элементов — обобщённое имя
        base = "рабочая сессия"

    if time_hint:
        prefix_map = {
            "утро": "утренняя" if "сессия" in base else "утренний",
            "день": "дневная" if "сессия" in base else "дневной",
            "вечер": "вечерняя" if "сессия" in base else "вечерний",
        }
        prefix = prefix_map.get(time_hint, "")
        if prefix:
            base = f"{prefix} {base}"

    return base


def _generate_keywords(
    items: list[str],
    name: str,
) -> list[str]:
    """Ключевые слова для голосового запуска."""
    keywords = [name]

    clean = [i for i in items if i]
    if len(clean) == 2:
        kw = f"{clean[0].lower()} и {clean[1].lower()}"
        if kw != name:
            keywords.append(kw)
    elif len(clean) >= 3:
        kw = f"запусти {name}"
        keywords.append(kw)

    return keywords


def _add_waits(steps: list[dict]) -> list[dict]:
    """Добавить паузы между шагами."""
    if len(steps) <= 1:
        return list(steps)

    result = []
    for i, step in enumerate(steps):
        result.append(step)
        if i < len(steps) - 1:
            result.append({
                "action": "wait",
                "seconds": DEFAULT_WAIT,
            })
    return result