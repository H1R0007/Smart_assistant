"""
Интеграция с LLM (Ollama / OpenAI).

Архитектура:
1. Оптимизированный system prompt с примерами
2. Минимальная история (2 последних обмена)
3. Проверка Ollama только при старте (не при каждом запросе)
4. Retry с backoff
5. Постобработка ответа (удаление мусора)
6. Метрики производительности
7. Предзагрузка модели с проверкой

Каждое решение обосновано конкретной проблемой.
"""

import os
import re
import subprocess
import logging
import time
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Метрики
# ═══════════════════════════════════════════

@dataclass
class LLMStats:
    """Статистика запросов к LLM."""
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    total_time: float = 0.0
    total_tokens: int = 0

    @property
    def avg_time(self) -> float:
        return self.total_time / max(self.successful, 1)

    @property
    def avg_tokens(self) -> int:
        return int(self.total_tokens / max(self.successful, 1))

    def summary(self) -> str:
        return (
            f"LLM: {self.successful}/{self.total_requests} ok, "
            f"avg {self.avg_time:.1f}s, avg {self.avg_tokens} tok"
        )


# ═══════════════════════════════════════════
#  System prompt
# ═══════════════════════════════════════════

DEFAULT_SYSTEM_PROMPT = """Ты — голосовой ассистент Пятница. Твои ответы озвучиваются вслух.

СТРОГИЕ ПРАВИЛА:
1. Отвечай ТОЛЬКО по существу. Максимум 2 предложения.
2. НЕ представляйся. НЕ говори "Как ваш ассистент" или "Я — голосовой ассистент".
3. НЕ извиняйся. НЕ говори "к сожалению", "извините", "простите".
4. НЕ используй markdown: звёздочки, решётки, списки, бэктики.
5. НЕ повторяй вопрос пользователя.
6. Если не знаешь точный ответ — дай приблизительный.
7. Числа пиши словами если они короткие (пять, десять).
8. НЕ используй эмодзи.

ПРИМЕРЫ:
Пользователь: "расскажи анекдот"
Ответ: "Штирлиц стрелял вслепую. Слепая упала."

Пользователь: "что такое нейросеть"  
Ответ: "Это математическая модель, вдохновлённая работой мозга, которая учится находить закономерности в данных."

Пользователь: "сколько километров до луны"
Ответ: "Около 384 тысяч километров."

Пользователь: "кто президент франции"
Ответ: "Эммануэль Макрон."

ВОЗМОЖНОСТИ АССИСТЕНТА (для справки, если пользователь спрашивает):
- Открывать/закрывать приложения
- Говорить время, дату, погоду
- Ставить таймеры и напоминания
- Делать скриншоты, заметки
- Искать в интернете
- Управлять громкостью, медиа, яркостью
- Управлять окнами
- Считать, переводить"""


class LLM:
    """
    Обёртка над LLM-провайдером.
    
    Потокобезопасность: да (requests thread-safe).
    """

    # ── Параметры ──
    REQUEST_TIMEOUT = 15        # Таймаут запроса (сек)
    MAX_RETRIES = 1             # Количество повторных попыток
    RETRY_DELAY = 0.5           # Задержка между попытками (сек)
    MAX_HISTORY_PAIRS = 2       # Пар user/assistant в истории
    OLLAMA_CHECK_TIMEOUT = 3    # Таймаут проверки Ollama (сек)
    WARMUP_TIMEOUT = 60         # Таймаут прогрева модели (сек)

    def __init__(self, config: dict):
        """
        Args:
            config: секция 'llm' из config.yaml
        """
        self._provider = config.get("provider", "none")
        self._max_tokens = config.get("max_tokens", 100)

        # System prompt
        custom_prompt = config.get("system_prompt", "").strip()
        if custom_prompt and len(custom_prompt) > 50:
            self._system_prompt = custom_prompt
        else:
            self._system_prompt = DEFAULT_SYSTEM_PROMPT

        # История
        max_history = config.get("max_history", 4)
        self.MAX_HISTORY_PAIRS = max(1, max_history // 2)
        self._history = []
        self._last_raw_response: str | None = None

        # Ollama
        ollama_cfg = config.get("ollama", {})
        self._ollama_url = ollama_cfg.get("base_url", "http://localhost:11434")
        self._ollama_model = ollama_cfg.get("model", "gemma3:4b")
        self._ollama_available = False

        # OpenAI
        openai_cfg = config.get("openai", {})
        self._openai_key = openai_cfg.get("api_key", "")
        self._openai_model = openai_cfg.get("model", "gpt-4o-mini")

        # Метрики
        self.stats = LLMStats()

        # Инициализация провайдера
        if self._provider == "ollama":
            self._init_ollama()

        logger.info(
            "LLM: provider=%s, model=%s, max_tokens=%d",
            self._provider,
            self._ollama_model if self._provider == "ollama" else self._openai_model,
            self._max_tokens
        )

    # ══════════════════════════════════════════════
    #  Инициализация Ollama
    # ══════════════════════════════════════════════

    def _init_ollama(self) -> None:
        """
        Инициализация Ollama при старте.
        
        1. Проверяем доступность
        2. Запускаем если нужно
        3. Прогреваем модель (загружаем в RAM)
        
        Делаем ЭТО ОДИН РАЗ при старте, не при каждом запросе.
        """
        # Шаг 1: Проверяем доступность
        if self._check_ollama():
            self._ollama_available = True
        else:
            # Шаг 2: Пробуем запустить
            if self._start_ollama():
                self._ollama_available = True
            else:
                logger.error("Ollama недоступна")
                return

        # Шаг 3: Прогрев
        self._warmup()

    def _check_ollama(self) -> bool:
        """Проверить что Ollama запущена и отвечает."""
        try:
            resp = requests.get(
                f"{self._ollama_url}/api/tags",
                timeout=self.OLLAMA_CHECK_TIMEOUT
            )
            if resp.ok:
                # Проверяем что нужная модель есть
                models = resp.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                has_model = any(
                    self._ollama_model in name
                    for name in model_names
                )
                if not has_model:
                    logger.warning(
                        "Модель '%s' не найдена в Ollama. Доступные: %s",
                        self._ollama_model, model_names
                    )
                return True
        except requests.ConnectionError:
            pass
        except Exception as e:
            logger.debug("Ollama check: %s", e)
        return False

    def _start_ollama(self) -> bool:
        """Попытка запустить Ollama."""
        logger.info("Запускаю Ollama...")
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            )
            # Ждём запуска
            for i in range(15):
                time.sleep(1)
                if self._check_ollama():
                    logger.info("Ollama запущена (за %d сек)", i + 1)
                    return True
            logger.error("Ollama не запустилась за 15 сек")
        except FileNotFoundError:
            logger.error("Ollama не установлена (команда 'ollama' не найдена)")
        except Exception as e:
            logger.error("Ошибка запуска Ollama: %s", e)
        return False

    def _warmup(self) -> None:
        """
        Прогрев модели — загрузить в RAM заранее.
        
        Без прогрева первый запрос будет 10-20 сек
        (загрузка модели + генерация).
        С прогревом первый запрос = 3-5 сек (только генерация).
        """
        logger.info("Прогрев модели '%s'...", self._ollama_model)
        try:
            resp = requests.post(
                f"{self._ollama_url}/api/generate",
                json={
                    "model": self._ollama_model,
                    "prompt": "Привет",
                    "stream": False,
                    "options": {"num_predict": 1}
                },
                timeout=self.WARMUP_TIMEOUT
            )
            if resp.ok:
                logger.info("Модель прогрета и в памяти")
            else:
                logger.warning(
                    "Прогрев вернул %d: %s",
                    resp.status_code, resp.text[:100]
                )
        except Exception as e:
            logger.warning("Прогрев не удался: %s", e)

    # ══════════════════════════════════════════════
    #  Публичный API
    # ══════════════════════════════════════════════

    def ask(self, query: str) -> str | None:
        """
        Задать вопрос LLM.
        
        Args:
            query: текст вопроса
            
        Returns:
            Очищенный ответ или None при ошибке
        """
        if not query or not query.strip():
            return None

        query = query.strip()
        self.stats.total_requests += 1

        if self._provider == "ollama":
            if not self._ollama_available:
                # Пробуем переподключиться
                if self._check_ollama():
                    self._ollama_available = True
                else:
                    return None
            return self._ask_with_retry(self._ask_ollama, query)

        elif self._provider == "openai":
            return self._ask_with_retry(self._ask_openai, query)

        return None

    @property
    def available(self) -> bool:
        """Доступен ли LLM провайдер."""
        if self._provider == "ollama":
            return self._ollama_available
        elif self._provider == "openai":
            return bool(self._openai_key)
        return False

    def clear_history(self) -> None:
        """Очистить историю диалога."""
        self._history.clear()
        logger.debug("История LLM очищена")
    
    def get_last_response(self) -> str | None:
        """
        Получить последний ответ ассистента.

        Публичный API для RepeatCommand.
        Заменяет обращение к приватному _history.
        """
        for msg in reversed(self._history):
            if msg["role"] == "assistant":
                return msg["content"]
        return None
    
    def get_info(self) -> dict:
        """Публичная информация о провайдере и модели."""
        if self._provider == "ollama":
            model = self._ollama_model
        elif self._provider == "openai":
            model = self._openai_model
        else:
            model = "none"
        return {
            "provider": self._provider,
            "model": model,
            "max_tokens": self._max_tokens,
        }

    # ══════════════════════════════════════════════
    #  Запросы к провайдерам
    # ══════════════════════════════════════════════

    def _ask_with_retry(self, method, query: str) -> str | None:
        """
        Обёртка с retry.
        
        При ошибке 500/таймаут — одна повторная попытка
        через 0.5 сек. Часто помогает при временной
        перегрузке Ollama.
        """
        last_error = None

        for attempt in range(1 + self.MAX_RETRIES):
            try:
                result = method(query)
                if result is not None:
                    return result
            except requests.Timeout:
                last_error = "таймаут"
                logger.warning(
                    "LLM таймаут (попытка %d/%d)",
                    attempt + 1, 1 + self.MAX_RETRIES
                )
            except requests.ConnectionError:
                last_error = "нет соединения"
                if self._provider == "ollama":
                    self._ollama_available = False
                logger.error("LLM: нет соединения")
                break  # retry не поможет
            except Exception as e:
                last_error = str(e)
                logger.error("LLM ошибка: %s", e)

            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY)

        self.stats.failed += 1
        logger.error("LLM: все попытки провалились (%s)", last_error)
        return None

    def _ask_ollama(self, query: str) -> str | None:
        """Запрос к Ollama."""
        messages = self._build_messages(query)
        start = time.time()

        resp = requests.post(
            f"{self._ollama_url}/api/chat",
            json={
                "model": self._ollama_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "num_predict": self._max_tokens,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "num_ctx": 2048,
                    "repeat_penalty": 1.2,
                }
            },
            timeout=self.REQUEST_TIMEOUT
        )

        elapsed = time.time() - start

        if not resp.ok:
            logger.error("Ollama HTTP %d: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        raw_answer = data.get("message", {}).get("content", "").strip()
        self._last_raw_response = raw_answer

        if not raw_answer:
            return None

        # Метрики
        eval_count = data.get("eval_count", 0)
        self.stats.successful += 1
        self.stats.total_time += elapsed
        self.stats.total_tokens += eval_count

        # Очистка
        answer = self._clean_response(raw_answer)

        # Обновление истории
        self._update_history(query, answer)

        logger.info(
            "Ollama [%.1fs, %d tok]: %s",
            elapsed, eval_count, answer[:80]
        )
        return answer

    def _ask_openai(self, query: str) -> str | None:
        """Запрос к OpenAI API."""
        if not self._openai_key:
            logger.error("OpenAI API key не задан")
            return None

        messages = self._build_messages(query)
        start = time.time()

        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._openai_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": self._openai_model,
                "messages": messages,
                "max_tokens": self._max_tokens,
                "temperature": 0.7,
            },
            timeout=self.REQUEST_TIMEOUT
        )

        elapsed = time.time() - start

        if not resp.ok:
            logger.error("OpenAI HTTP %d: %s", resp.status_code, resp.text[:200])
            return None

        data = resp.json()
        raw_answer = data["choices"][0]["message"]["content"].strip()
        self._last_raw_response = raw_answer
        tokens = data.get("usage", {}).get("completion_tokens", 0)

        self.stats.successful += 1
        self.stats.total_time += elapsed
        self.stats.total_tokens += tokens

        answer = self._clean_response(raw_answer)
        self._update_history(query, answer)

        logger.info("OpenAI [%.1fs, %d tok]: %s", elapsed, tokens, answer[:80])
        return answer
    
    def get_last_raw_response(self) -> str | None:
        """Последний ответ ДО очистки (с markdown). Для GUI."""
        return self._last_raw_response

    # ══════════════════════════════════════════════
    #  История и сообщения
    # ══════════════════════════════════════════════

    def _build_messages(self, query: str) -> list[dict]:
        """
        Собрать массив сообщений для API.
        
        Структура:
        [system + dynamic_context] + [история] + [текущий user]
        """
        dynamic = self._build_dynamic_context()
        system = self._system_prompt + dynamic

        msgs = [{"role": "system", "content": system}]

        # Последние N пар
        history_limit = self.MAX_HISTORY_PAIRS * 2
        recent_history = self._history[-history_limit:] if self._history else []
        msgs.extend(recent_history)

        msgs.append({"role": "user", "content": query})
        return msgs

    def _update_history(self, query: str, response: str) -> None:
        """Обновить историю диалога."""
        self._history.append({"role": "user", "content": query})
        self._history.append({"role": "assistant", "content": response})

        # Обрезаем
        max_items = self.MAX_HISTORY_PAIRS * 2
        if len(self._history) > max_items:
            self._history = self._history[-max_items:]

    # ══════════════════════════════════════════════
    #  Постобработка ответа
    # ══════════════════════════════════════════════

    @staticmethod
    def _clean_response(text: str) -> str:
        """
        Глубокая очистка ответа LLM.
        
        Убирает:
        1. Markdown разметку
        2. Self-references ("Как ваш ассистент...")
        3. Disclaimers ("К сожалению...")
        4. Повторение вопроса
        5. Эмодзи
        6. Лишние пробелы и пунктуацию
        """
        if not text:
            return ""

        # 1. Markdown
        # **bold** и *italic*
        text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
        # ```code blocks```
        text = re.sub(r'```[\s\S]*?```', '', text)
        # `inline code`
        text = re.sub(r'`([^`]+)`', r'\1', text)
        # # Заголовки
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        # - Списки
        text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
        # Нумерованные списки
        text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)

        # 2. Self-references (удаляем целые предложения)
        self_patterns = [
            r'(?:^|\. )[^.]*(?:как (?:ваш|твой) ассистент)[^.]*\.',
            r'(?:^|\. )[^.]*(?:я (?:—|–|-) (?:голосовой |текстовый )?ассистент)[^.]*\.',
            r'(?:^|\. )[^.]*(?:меня зовут)[^.]*\.',
        ]
        for pattern in self_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # 3. Disclaimers (удаляем целые предложения)
        disclaimer_patterns = [
            r'(?:^|\. )[^.]*(?:к сожалению)[^.]*\.',
            r'(?:^|\. )[^.]*(?:извинит?е?|простит?е?)[^.]*\.',
            r'(?:^|\. )[^.]*(?:я не могу (?:предоставить|дать|найти))[^.]*\.',
            r'(?:^|\. )[^.]*(?:у меня нет (?:доступа|возможности|информации))[^.]*\.',
            r'(?:^|\. )[^.]*(?:рекомендую обратиться)[^.]*\.',
            r'(?:^|\. )[^.]*(?:для (?:более )?(?:подробной|актуальной) информации)[^.]*\.',
        ]
        for pattern in disclaimer_patterns:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        # 4. Эмодзи
        text = re.sub(
            r'[\U0001F600-\U0001F64F'
            r'\U0001F300-\U0001F5FF'
            r'\U0001F680-\U0001F6FF'
            r'\U0001F1E0-\U0001F1FF'
            r'\U00002702-\U000027B0'
            r'\U0001F900-\U0001F9FF]+',
            '', text
        )

        # 5. Переносы строк → пробелы
        text = text.replace('\n', ' ')

        # 6. Множественные пробелы и пунктуация
        text = re.sub(r'\s{2,}', ' ', text)
        text = re.sub(r'\.{2,}', '.', text)
        text = re.sub(r'^\.\s*', '', text)  # точка в начале
        text = re.sub(r'\.\s*$', '.', text)  # нормализовать конец

        text = text.strip()

        # 7. Если после очистки ничего не осталось
        if not text or len(text) < 2:
            return ""

        # 8. Убеждаемся что заканчивается точкой
        if text and text[-1] not in '.!?':
            text += '.'

        return text
    
    def _build_dynamic_context(self) -> str:
        """
        Динамическая часть system prompt — текущее время, дата.
        Добавляется к основному prompt при каждом запросе.
        """
        from datetime import datetime

        now = datetime.now()
        weekdays = [
            "понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье",
        ]
        months = [
            "", "января", "февраля", "марта", "апреля",
            "мая", "июня", "июля", "августа",
            "сентября", "октября", "ноября", "декабря",
        ]

        day_name = weekdays[now.weekday()]
        date_str = f"{now.day} {months[now.month]} {now.year}"
        time_str = now.strftime("%H:%M")

        parts = [
            f"\nТекущее время: {time_str}, {day_name}, {date_str}.",
        ]

        # Время суток для контекста
        hour = now.hour
        if 5 <= hour < 12:
            parts.append("Сейчас утро.")
        elif 12 <= hour < 17:
            parts.append("Сейчас день.")
        elif 17 <= hour < 22:
            parts.append("Сейчас вечер.")
        else:
            parts.append("Сейчас ночь.")

        return " ".join(parts)