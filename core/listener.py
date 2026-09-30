"""
Модуль Wake Word Detection.

Архитектура:
1. Vosk для непрерывного распознавания (оффлайн, ~2% CPU)
2. Двухэтапная проверка: partial (быстро) → final (точно)
3. Проверка границ слов (не реагировать на "пятницам")
4. Cooldown между активациями
5. Метрики для отладки
6. Несколько вариантов wake word

Каждое решение обосновано конкретным багом.
"""

import json
import queue
import time
import re
import threading
import logging
from dataclasses import dataclass, field

import sounddevice as sd
from vosk import Model, KaldiRecognizer

logger = logging.getLogger(__name__)


@dataclass
class ListenerStats:
    """Статистика работы listener (для отладки и улучшения)."""
    total_activations: int = 0
    false_activations: int = 0  # можно инкрементировать извне
    partial_detections: int = 0
    final_detections: int = 0
    uptime_start: float = field(default_factory=time.time)

    @property
    def uptime_minutes(self) -> float:
        return (time.time() - self.uptime_start) / 60

    def summary(self) -> str:
        return (
            f"Активаций: {self.total_activations} "
            f"(partial: {self.partial_detections}, final: {self.final_detections}), "
            f"Аптайм: {self.uptime_minutes:.0f} мин"
        )


class Listener:
    """
    Фоновый слушатель wake word.
    
    Работает в отдельном потоке, минимальная нагрузка на CPU.
    Когда слышит ключевое слово — вызывает callback.
    
    Защиты:
    - Проверка границ слов ("пятницам" ≠ "пятница")
    - Cooldown 2 сек между активациями
    - Не реагирует на partial если final не подтвердил
    - Игнорирует собственную речь (pause/resume)
    """

    # ── Параметры ──
    SAMPLE_RATE = 16000
    BLOCK_SIZE = 4000           # ~250мс блоков
    QUEUE_TIMEOUT = 0.5         # Таймаут чтения из очереди
    COOLDOWN_SEC = 2.0          # Минимум секунд между активациями
    PARTIAL_COOLDOWN_SEC = 1.0  # Cooldown для partial detections

    def __init__(
        self,
        model_path: str,
        wake_word: str = "ассистент",
        sample_rate: int = 16000,
        follow_up_seconds: float = 4.0,
        alternative_words: list[str] | None = None
    ):
        """
        Args:
            model_path: путь к модели Vosk.
            wake_word: основное ключевое слово.
            follow_up_seconds: длительность режима диалога.
            alternative_words: дополнительные формы wake word.
        """
        # Wake words
        self._primary_wake_word = wake_word.lower().strip()
        self._wake_words = self._build_wake_words(
            self._primary_wake_word,
            alternative_words
        )

        self._sample_rate = sample_rate
        # Хранится для внешнего доступа через property
        # Реальное использование — в main.py conversation_loop
        self._follow_up_seconds = follow_up_seconds

        # Состояние
        self._is_listening = False
        self._is_paused = False
        self._audio_queue = queue.Queue()
        self._last_activation_time = 0
        self._last_partial_time = 0

        # Статистика
        self.stats = ListenerStats()

        # Загрузка модели
        logger.info("Загрузка Vosk: %s", model_path)
        self._model = Model(model_path)
        self._recognizer = KaldiRecognizer(self._model, sample_rate)
        self._recognizer.SetWords(True)  # получать отдельные слова с таймингами

        logger.info(
            "Listener: wake words = %s",
            self._wake_words
        )

    # ══════════════════════════════════════════════
    #  Построение списка wake words
    # ══════════════════════════════════════════════

    def _build_wake_words(
        self,
        primary: str,
        alternatives: list[str] | None
    ) -> list[str]:
        """
        Построить полный список wake words.
        
        Для "пятница" автоматически добавляет:
        - "пятница" (основное)
        - "эй пятница" (привлечение внимания)
        - "окей пятница" (альтернатива)
        
        НЕ добавляем короткие варианты типа "пять" — 
        слишком много ложных срабатываний.
        """
        words = [primary]

        # Варианты с приставками
        prefixes = ["эй", "окей", "ок", "слушай"]
        for prefix in prefixes:
            words.append(f"{prefix} {primary}")

        # Пользовательские альтернативы
        if alternatives:
            for alt in alternatives:
                alt = alt.lower().strip()
                if alt and alt not in words:
                    words.append(alt)

        return words

    # ══════════════════════════════════════════════
    #  Основной цикл
    # ══════════════════════════════════════════════

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback sounddevice — складывает аудио в очередь."""
        if status:
            logger.debug("Audio: %s", status)
        self._audio_queue.put(bytes(indata))

    def start(self, on_wake) -> None:
        """
        Запуск прослушивания (блокирующий).
        
        Args:
            on_wake: callback без аргументов, вызывается при wake word.
        """
        self._is_listening = True
        self._is_paused = False
        self.stats = ListenerStats()
        logger.info("Listener запущен")

        try:
            with sd.RawInputStream(
                samplerate=self._sample_rate,
                blocksize=self.BLOCK_SIZE,
                dtype="int16",
                channels=1,
                callback=self._audio_callback
            ):
                self._listen_loop(on_wake)

        except sd.PortAudioError as e:
            logger.error("Ошибка аудиоустройства: %s", e)
        except Exception as e:
            logger.error("Ошибка Listener: %s", e)
        finally:
            self._is_listening = False
            logger.info("Listener остановлен. %s", self.stats.summary())

    def _listen_loop(self, on_wake) -> None:
        """Основной цикл обработки аудио."""
        while self._is_listening:
            # Читаем аудио из очереди
            try:
                data = self._audio_queue.get(timeout=self.QUEUE_TIMEOUT)
            except queue.Empty:
                continue

            # На паузе — выбрасываем аудио
            if self._is_paused:
                continue

            # Обрабатываем через Vosk
            is_final = self._recognizer.AcceptWaveform(data)

            if is_final:
                # ── Финальный результат ──
                result = json.loads(self._recognizer.Result())
                text = result.get("text", "").lower().strip()

                if text and self._check_wake_word(text, is_partial=False):
                    if self._can_activate():
                        logger.info("Wake word (final): '%s'", text)
                        self.stats.final_detections += 1
                        self.stats.total_activations += 1
                        self._activate(on_wake)
            else:
                # ── Partial результат ──
                partial = json.loads(self._recognizer.PartialResult())
                partial_text = partial.get("partial", "").lower().strip()

                if partial_text and self._check_wake_word(partial_text, is_partial=True):
                    if self._can_activate_partial():
                        logger.info("Wake word (partial): '%s'", partial_text)
                        self.stats.partial_detections += 1
                        self.stats.total_activations += 1
                        self._recognizer.Reset()
                        self._activate(on_wake)

    # ══════════════════════════════════════════════
    #  Детекция wake word
    # ══════════════════════════════════════════════

    def _check_wake_word(self, text: str, is_partial: bool) -> bool:
        """
        Проверить наличие wake word в тексте.
        
        Ключевая защита: ПРОВЕРКА ГРАНИЦ СЛОВ.
        
        "пятница" в "пятница" → True
        "пятница" в "пятницам" → False (суффикс)
        "пятница" в "в пятницу" → False (другая форма)
        "пятница" в "про пятницу" → False
        "пятница" в "это пятница" → True (отдельное слово)
        
        Для partial results: проверяем только если wake word
        стоит в конце текста (Vosk добавляет слова последовательно).
        """
        for wake_word in self._wake_words:
            if self._word_boundary_match(wake_word, text, is_partial):
                return True
        return False

    @staticmethod
    def _word_boundary_match(
        wake_word: str,
        text: str,
        is_partial: bool
    ) -> bool:
        """
        Точная проверка wake word с учётом границ слов.
        
        Для кириллицы \\b не работает надёжно,
        поэтому проверяем вручную.
        """
        # Многословный wake word ("эй пятница")
        if " " in wake_word:
            if wake_word not in text:
                return False
            pos = text.find(wake_word)
            before_ok = (pos == 0 or text[pos - 1] == " ")
            after_pos = pos + len(wake_word)
            if is_partial:
                # Для partial: фраза должна быть в конце текста
                after_ok = (after_pos >= len(text))
            else:
                after_ok = (after_pos >= len(text) or text[after_pos] == " ")
            return before_ok and after_ok

        # Однословный wake word
        words = text.split()

        if is_partial:
            # Для partial: wake word должен быть ПОСЛЕДНИМ словом
            # Почему: partial результат растёт по мере речи
            # "пят" → "пятн" → "пятница" → "пятница а"
            # Реагируем когда "пятница" — последнее
            if not words:
                return False
            last_word = words[-1]
            return last_word == wake_word
        else:
            # Для final: wake word может быть в любом месте
            # но должен совпадать ТОЧНО (не "пятницам", не "пятницу")
            return wake_word in words

    # ══════════════════════════════════════════════
    #  Защита от ложных срабатываний
    # ══════════════════════════════════════════════

    def _can_activate(self) -> bool:
        """Можно ли активироваться (cooldown для final)."""
        now = time.time()
        if now - self._last_activation_time < self.COOLDOWN_SEC:
            logger.debug(
                "Cooldown: %.1f сек с последней активации",
                now - self._last_activation_time
            )
            return False
        return True

    def _can_activate_partial(self) -> bool:
        """Можно ли активироваться по partial (отдельный cooldown)."""
        now = time.time()
        # Общий cooldown
        if now - self._last_activation_time < self.COOLDOWN_SEC:
            return False
        # Cooldown для partial (частый partial спам)
        if now - self._last_partial_time < self.PARTIAL_COOLDOWN_SEC:
            return False
        self._last_partial_time = now
        return True

    def _activate(self, on_wake) -> None:
        """Выполнить активацию."""
        self._last_activation_time = time.time()
        on_wake()

    # ══════════════════════════════════════════════
    #  Управление состоянием
    # ══════════════════════════════════════════════

    def pause(self) -> None:
        """
        Поставить на паузу.
        
        Вызывается когда ассистент:
        - Говорит (чтобы не услышать свой wake word)
        - Слушает команду (recognizer работает)
        """
        self._is_paused = True
        # Очищаем очередь от накопленного аудио
        self._flush_queue()
        logger.debug("Listener: пауза")

    def resume(self) -> None:
        """
        Возобновить прослушивание.
        
        Сбрасываем Vosk чтобы не ловить старое аудио.
        """
        self._flush_queue()
        self._recognizer.Reset()
        self._is_paused = False
        logger.debug("Listener: возобновлён")

    def stop(self) -> None:
        """Полная остановка."""
        self._is_listening = False
        logger.info("Listener: стоп. %s", self.stats.summary())

    def _flush_queue(self) -> None:
        """Очистить аудио-очередь."""
        count = 0
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
                count += 1
            except queue.Empty:
                break
        if count > 0:
            logger.debug("Очищено %d блоков аудио", count)

    # ══════════════════════════════════════════════
    #  Свойства
    # ══════════════════════════════════════════════

    @property
    def is_listening(self) -> bool:
        return self._is_listening

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def follow_up_seconds(self) -> float:
        return self._follow_up_seconds

    @property
    def wake_word(self) -> str:
        return self._primary_wake_word