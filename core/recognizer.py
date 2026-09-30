"""
Модуль распознавания речи (STT).

Архитектура:
1. Калибровка шума — один раз при запуске (не при каждом listen)
2. Запись с пре-буфером — не теряем первые слоги
3. Адаптивный VAD — подстраивается под уровень шума
4. Whisper через numpy (без временных файлов)
5. Фильтрация галлюцинаций

Каждое решение обосновано конкретной проблемой.
"""

import logging
import time
import collections
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    logger.warning("faster-whisper не установлен")


class Recognizer:
    """
    Распознавание речи с микрофона.
    
    Жизненный цикл:
    1. __init__: загрузка Whisper + первичная калибровка шума
    2. listen(): запись аудио → распознавание → текст
    3. recalibrate(): повторная калибровка (если нужно)
    """

    # ══════════════════════════════════════════════
    #  Параметры (вынесены для прозрачности)
    # ══════════════════════════════════════════════

    # Запись
    SAMPLE_RATE = 16000
    CHUNK_MS = 50                   # Размер чанка в мс (50мс = 20 чанков/сек)
    PREBUFFER_MS = 500              # Пре-буфер: сколько мс хранить ДО начала речи
    
    # VAD (Voice Activity Detection)
    NOISE_MULTIPLIER = 2.5          # speech_threshold = noise_level * это
    MIN_THRESHOLD = 0.003           # Минимальный порог (абсолютная тишина)
    MAX_THRESHOLD = 0.05            # Максимальный порог (шумная комната)
    SPEECH_CONFIRM_MS = 100         # Сколько мс голоса нужно для подтверждения
    SILENCE_TO_STOP_MS = 1300       # Сколько мс тишины = конец фразы
    MIN_SPEECH_MS = 600             # Минимальная длина речи
    
    # Whisper
    BEAM_SIZE = 3                   # Ширина поиска (3 = баланс скорость/качество)
    NO_SPEECH_THRESHOLD = 0.5       # Порог "нет речи" в сегменте
    MAX_AUDIO_SEC = 8.0             # Максимум секунд аудио для Whisper
    
    # Калибровка
    CALIBRATION_DURATION = 0.5      # Длительность замера шума (сек)
    RECALIBRATE_INTERVAL = 300      # Перекалибровка каждые N секунд

    def __init__(
        self,
        language: str = "ru",
        model_size: str = "medium",
        use_whisper: bool = True,
        mic_index: int | None = None
    ):
        self._language = language
        self._mic_index = mic_index
        self._use_whisper = use_whisper and WHISPER_AVAILABLE

        # Состояние калибровки
        self._noise_level = 0.003
        self._speech_threshold = 0.008
        self._last_calibration = 0

        if self._use_whisper:
            logger.info("Загрузка Whisper '%s'...", model_size)
            self._model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=8,
            )
            logger.info("Whisper '%s' загружен", model_size)

            # Быстрая модель для коротких команд
            self._fast_model = None
            if model_size != "small":
                try:
                    logger.info("Загрузка Whisper 'small' (быстрая)...")
                    self._fast_model = WhisperModel(
                        "small",
                        device="cpu",
                        compute_type="int8",
                        cpu_threads=8,
                    )
                    logger.info("Whisper 'small' загружен")
                except Exception as e:
                    logger.warning("Быстрая модель не загружена: %s", e)
        else:
            self._model = None
            self._fast_model = None
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()

        # Первичная калибровка
        self._calibrate_noise()

    # ══════════════════════════════════════════════
    #  Публичный API
    # ══════════════════════════════════════════════

    def listen(self, timeout: int = 5, phrase_limit: int = 10) -> str | None:
        """
        Слушает микрофон и возвращает распознанный текст.
        
        Args:
            timeout: сколько ждать начала речи (сек)
            phrase_limit: максимальная длина фразы (сек)
            
        Returns:
            Распознанный текст в нижнем регистре или None
        """
        # Перекалибровка если давно не делали
        now = time.time()
        if now - self._last_calibration > self.RECALIBRATE_INTERVAL:
            self._calibrate_noise()

        if self._use_whisper:
            return self._listen_whisper(timeout, phrase_limit)
        else:
            return self._listen_google(timeout, phrase_limit)

    def recalibrate(self) -> None:
        """Принудительная перекалибровка шума."""
        self._calibrate_noise()

    # ══════════════════════════════════════════════
    #  Запись аудио
    # ══════════════════════════════════════════════

    def _record(
        self,
        max_duration: float,
        wait_for_speech_timeout: float
    ) -> np.ndarray | None:
        """
        Умная запись с микрофона.
        
        Алгоритм:
        1. Крутим пре-буфер (последние PREBUFFER_MS мс)
        2. Ждём начало речи (до wait_for_speech_timeout)
        3. Подтверждаем речь (SPEECH_CONFIRM_MS мс подряд)
        4. Записываем пока идёт речь
        5. Останавливаемся после паузы (SILENCE_TO_STOP_MS мс)
        6. Приклеиваем пре-буфер к началу (для первых слогов)
        
        Почему пре-буфер:
        - Между beep() и началом record() проходит ~100мс
        - Пользователь начинает говорить сразу после бипа
        - Без пре-буфера первый слог теряется
          "открой" → "крой", "состояние" → "стояние"
        """
        chunk_samples = int(self.SAMPLE_RATE * self.CHUNK_MS / 1000)
        
        # Пре-буфер: кольцевой буфер последних N чанков
        prebuffer_size = int(self.PREBUFFER_MS / self.CHUNK_MS)
        prebuffer = collections.deque(maxlen=prebuffer_size)
        
        # Текущий порог (может отличаться от калиброванного)
        threshold = self._speech_threshold
        
        # Состояние записи
        audio_chunks = []
        speech_started = False
        speech_start_time = None
        silence_ms = 0.0
        start_time = time.time()
        
        # Подтверждение начала речи
        confirm_needed = int(self.SPEECH_CONFIRM_MS / self.CHUNK_MS)
        confirm_counter = 0
        
        # Скользящее среднее для сглаживания (убирает одиночные пики)
        volume_window = collections.deque(maxlen=3)

        try:
            with sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype=np.float32,
                device=self._mic_index,
                blocksize=chunk_samples
            ) as stream:

                while True:
                    elapsed = time.time() - start_time

                    # Таймаут ожидания речи
                    if not speech_started and elapsed > wait_for_speech_timeout:
                        logger.debug("Таймаут: речь не началась за %.0f сек", wait_for_speech_timeout)
                        return None

                    # Общий лимит
                    if speech_started:
                        speech_duration = time.time() - speech_start_time
                        if speech_duration > max_duration:
                            logger.debug("Лимит записи: %.1f сек", speech_duration)
                            break

                    # Читаем чанк
                    chunk, overflowed = stream.read(chunk_samples)
                    if overflowed:
                        logger.debug("Audio overflow")
                    chunk = chunk.flatten()
                    
                    # Громкость (RMS для более точной оценки)
                    volume = float(np.sqrt(np.mean(chunk ** 2)))
                    volume_window.append(volume)
                    avg_volume = sum(volume_window) / len(volume_window)

                    if not speech_started:
                        # ── Фаза ожидания ──
                        prebuffer.append(chunk)
                        
                        if avg_volume > threshold:
                            confirm_counter += 1
                            if confirm_counter >= confirm_needed:
                                # Речь подтверждена
                                speech_started = True
                                speech_start_time = time.time()
                                silence_ms = 0.0
                                
                                # Приклеиваем пре-буфер
                                for pre_chunk in prebuffer:
                                    audio_chunks.append(pre_chunk)
                                
                                logger.debug(
                                    "Речь (пре-буфер: %dмс, vol=%.4f, thr=%.4f)",
                                    len(prebuffer) * self.CHUNK_MS,
                                    avg_volume, threshold
                                )
                        else:
                            # Плавный сброс (не резкий, чтобы "п...пауза" не сбрасывала)
                            confirm_counter = max(0, confirm_counter - 1)
                    else:
                        # ── Фаза записи ──
                        audio_chunks.append(chunk)
                        
                        if avg_volume > threshold * 0.5:
                            # Звук есть (порог ниже для продолжения — 
                            # начать говорить сложнее чем продолжать)
                            silence_ms = 0.0
                        else:
                            silence_ms += self.CHUNK_MS
                        
                        # Проверка окончания
                        speech_ms = (time.time() - speech_start_time) * 1000
                        if (speech_ms >= self.MIN_SPEECH_MS 
                                and silence_ms >= self.SILENCE_TO_STOP_MS):
                            logger.debug(
                                "Стоп: речь %.0fмс, пауза %.0fмс",
                                speech_ms, silence_ms
                            )
                            break

        except sd.PortAudioError as e:
            logger.error("Ошибка микрофона: %s", e)
            return None

        if not audio_chunks:
            return None

        audio = np.concatenate(audio_chunks)

        # Обрезаем хвост тишины (оставляем 100мс для Whisper VAD)
        trim_samples = int(self.SAMPLE_RATE * max(0, silence_ms / 1000 - 0.1))
        if trim_samples > 0 and len(audio) > trim_samples:
            audio = audio[:-trim_samples]

        # Финальная проверка длины
        duration = len(audio) / self.SAMPLE_RATE
        if duration < self.MIN_SPEECH_MS / 1000:
            logger.debug("Слишком короткая запись: %.2f сек", duration)
            return None

        # Ограничиваем для Whisper
        max_samples = int(self.SAMPLE_RATE * self.MAX_AUDIO_SEC)
        if len(audio) > max_samples:
            audio = audio[:max_samples]
            logger.debug("Обрезано до %.1f сек", self.MAX_AUDIO_SEC)

        return audio

    # ══════════════════════════════════════════════
    #  Обработка аудио
    # ══════════════════════════════════════════════

    def _process_audio(self, audio: np.ndarray) -> np.ndarray:
        """
        Обработка аудио перед распознаванием.
        
        1. High-pass фильтр 80Hz — убирает гул вентиляторов, кондиционеров
        2. Нормализация — выравнивает громкость для Whisper
        
        Почему не делаем шумоподавление:
        - Whisper сам обучен на шумных данных
        - Агрессивное шумоподавление искажает речь
        - VAD filter в Whisper достаточно
        """
        # High-pass фильтр
        try:
            from scipy.signal import butter, lfilter
            b, a = butter(2, 80.0 / (self.SAMPLE_RATE / 2), btype='high')
            audio = lfilter(b, a, audio).astype(np.float32)
        except ImportError:
            pass  # scipy не обязателен

        # Нормализация (пиковая, не RMS — сохраняем динамику)
        peak = np.abs(audio).max()
        if peak > 0.001:
            audio = audio / peak * 0.85
        
        return audio

    # ══════════════════════════════════════════════
    #  Whisper
    # ══════════════════════════════════════════════

    def _listen_whisper(self, timeout: int, phrase_limit: int) -> str | None:
        """Полный цикл: запись → обработка → распознавание → очистка."""
        try:
            logger.info("Слушаю...")

            # 1. Запись
            audio = self._record(
                max_duration=min(phrase_limit, self.MAX_AUDIO_SEC),
                wait_for_speech_timeout=timeout
            )

            if audio is None:
                return None

            duration = len(audio) / self.SAMPLE_RATE
            logger.info("Записано: %.1f сек", duration)

            # 2. Обработка
            audio = self._process_audio(audio)

            # 3. Распознавание
            text = self._transcribe(audio)
            
            if text is None:
                return None

            # 4. Очистка и валидация
            text = self._clean_and_validate(text)
            
            if text:
                logger.info("Распознано: '%s'", text)
                return text
            
            return None

        except Exception as e:
            logger.error("Ошибка распознавания: %s", e)
            return None

    def _transcribe(self, audio: np.ndarray) -> str | None:
        """
        Распознавание через Whisper.

        Принимает numpy array напрямую (faster-whisper >= 0.9.0).
        
        Стратегия выбора модели:
        - Аудио < 3 сек → small (быстрая, ~1-2 сек)
        - Аудио >= 3 сек → medium (точная, ~5-7 сек)
        """
        try:
            duration = len(audio) / self.SAMPLE_RATE
            
            # Выбор модели
            if (self._fast_model is not None
                    and duration < 3.0):
                model = self._fast_model
                model_name = "small"
            else:
                model = self._model
                model_name = "medium"

            start_time = time.time()

            segments, info = model.transcribe(
                audio,
                language=self._language,
                beam_size=self.BEAM_SIZE,
                best_of=1,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=300,
                    threshold=0.3,
                ),
                condition_on_previous_text=False,
                no_speech_threshold=self.NO_SPEECH_THRESHOLD,
            )

            text_parts = []
            for segment in segments:
                if segment.no_speech_prob > 0.7:
                    logger.debug(
                        "Пропущен сегмент (no_speech=%.2f): '%s'",
                        segment.no_speech_prob, segment.text
                    )
                    continue
                text_parts.append(segment.text.strip())

            elapsed = time.time() - start_time
            logger.info(
                "Whisper [%s]: %.2f сек (аудио %.1f сек)",
                model_name, elapsed, duration
            )

            return " ".join(text_parts).strip() if text_parts else None

        except Exception as e:
            logger.error("Whisper transcribe error: %s", e)
            return None

    # ══════════════════════════════════════════════
    #  Очистка текста
    # ══════════════════════════════════════════════

    def _clean_and_validate(self, text: str) -> str | None:
        """
        Очистка и валидация распознанного текста.
        
        Этапы:
        1. Нормализация регистра и пунктуации
        2. Фильтрация галлюцинаций Whisper
        3. Проверка минимальной длины
        4. Проверка на повторения
        """
        if not text:
            return None
        
        # 1. Нормализация
        text = text.lower().strip()
        text = text.rstrip(".,!?;:…–—-")
        text = text.lstrip(".,!?;:…–—-")
        # Убираем множественные пробелы
        text = " ".join(text.split())
        
        # 2. Галлюцинации (Whisper генерирует эти фразы на тишине/шуме)
        hallucinations = {
            # Русские
            "продолжение следует", "подписывайтесь на канал",
            "спасибо за просмотр", "спасибо за внимание",
            "субтитры создавал", "субтитры", "редактор субтитров",
            "всем привет", "ставьте лайки",
            # Английские (Whisper иногда переключается)
            "thank you", "thanks for watching", "bye",
            "you", "the end", "music",
            "subscribe", "like and subscribe",
            # Символы
            "♪", "♫", "...",
        }
        
        for h in hallucinations:
            if text == h or text == h + ".":
                logger.debug("Галлюцинация отфильтрована: '%s'", text)
                return None
        
        # Начинается с галлюцинации
        for h in hallucinations:
            if text.startswith(h + " ") and len(text) < len(h) + 10:
                logger.debug("Галлюцинация в начале: '%s'", text)
                return None
        
        # 3. Минимальная длина
        if len(text) <= 1:
            return None
        # Одиночные бессмысленные слова
        if len(text) <= 3 and text not in {"да", "нет", "стоп", "пока"}:
            logger.debug("Слишком короткое: '%s'", text)
            return None
        
        # 4. Повторения ("да да да да" → "да")
        words = text.split()
        if len(words) >= 3 and len(set(words)) == 1:
            text = words[0]
        
        # 5. Проверка на нерусский текст
        # Если больше половины символов — латиница, возможно галлюцинация
        if text:
            cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
            latin = sum(1 for c in text if 'a' <= c <= 'z')
            total_alpha = cyrillic + latin
            if total_alpha > 0 and latin / total_alpha > 0.7 and len(text) > 5:
                logger.debug("Вероятно не русский: '%s'", text)
                return None
        
        return text

    # ══════════════════════════════════════════════
    #  Калибровка шума
    # ══════════════════════════════════════════════

    def _calibrate_noise(self) -> None:
        """
        Замер уровня фонового шума.
        
        Вызывается:
        - Один раз при запуске
        - Автоматически каждые RECALIBRATE_INTERVAL секунд
        - Вручную через recalibrate()
        
        Почему НЕ при каждом listen():
        - Экономия 300-500мс на каждой команде
        - Фоновый шум меняется редко (минуты, не секунды)
        - Хвост beep-звука может попасть в замер → завышенный порог
        """
        try:
            logger.debug("Калибровка шума...")
            samples = int(self.SAMPLE_RATE * self.CALIBRATION_DURATION)
            
            noise = sd.rec(
                samples,
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype=np.float32,
                device=self._mic_index
            )
            sd.wait()
            
            # RMS вместо mean(abs) — более устойчивая метрика
            self._noise_level = float(np.sqrt(np.mean(noise ** 2)))
            
            # Вычисляем порог с ограничениями
            raw_threshold = self._noise_level * self.NOISE_MULTIPLIER
            self._speech_threshold = np.clip(
                raw_threshold,
                self.MIN_THRESHOLD,
                self.MAX_THRESHOLD
            )
            
            self._last_calibration = time.time()
            
            logger.info(
                "Шум: %.5f, порог речи: %.5f",
                self._noise_level, self._speech_threshold
            )
            
        except Exception as e:
            logger.warning("Калибровка не удалась: %s (используем дефолт)", e)
            self._noise_level = 0.003
            self._speech_threshold = 0.008

    # ══════════════════════════════════════════════
    #  Fallback: Google API
    # ══════════════════════════════════════════════

    def _listen_google(self, timeout: int, phrase_limit: int) -> str | None:
        """Запасной вариант через Google Speech API."""
        import speech_recognition as sr
        try:
            with sr.Microphone(device_index=self._mic_index) as source:
                logger.info("Слушаю (Google)...")
                self._recognizer.adjust_for_ambient_noise(source, duration=0.3)
                audio = self._recognizer.listen(
                    source, timeout=timeout, phrase_time_limit=phrase_limit
                )
            lang_code = "ru-RU" if self._language == "ru" else "en-US"
            text = self._recognizer.recognize_google(audio, language=lang_code)
            return text.lower().strip()
        except Exception as e:
            logger.debug("Google API: %s", e)
            return None