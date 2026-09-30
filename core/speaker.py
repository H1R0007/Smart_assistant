"""
Модуль синтеза речи (TTS).

Архитектура:
1. Единый event loop в фоновом потоке (без asyncio.run на каждый вызов)
2. Очередь воспроизведения (потокобезопасная)
3. Кэш с LRU-ротацией
4. Предзагрузка частых фраз
5. beep через Sound (параллельно с music)
6. Ленивый fallback pyttsx3

Каждое решение обосновано конкретной проблемой.
"""

import asyncio
import hashlib
import os
import shutil
import tempfile
import logging
import time
import threading
import queue
from pathlib import Path

import edge_tts
import pygame
import numpy as np

logger = logging.getLogger(__name__)


class Speaker:
    """
    Голос ассистента.
    
    Потокобезопасный. Можно вызывать say() из любого потока.
    Внутри — очередь + фоновый поток воспроизведения.
    """

    # Фразы которые генерируем при запуске (предзагрузка)
    PRELOAD_PHRASES = [
        "Повтори, пожалуйста.",
        "Готово.",
        "Не поняла, повтори.",
        "Секунду...",
    ]

    # Максимум файлов в кэше
    CACHE_MAX_FILES = 200

    def __init__(
        self,
        voice: str = "ru-RU-SvetlanaNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        cache_enabled: bool = True
    ):
        self._voice = voice
        self._rate = rate
        self._volume = volume
        self._cache_enabled = cache_enabled

        # Pygame mixer
        self._mixer_initialized = False
        self._init_mixer()

        # Кэш
        self._temp_dir = tempfile.mkdtemp(prefix="assistant_tts_")
        self._cache_dir = os.path.join(self._temp_dir, "cache")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._cache_lock = threading.Lock()

        # Очередь воспроизведения
        self._play_queue = queue.Queue()
        self._is_speaking = False
        self._speaking_lock = threading.Lock()

        # Фоновый event loop для edge-tts
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="tts-event-loop"
        )
        self._loop_thread.start()

        # Звук активации
        self._activation_sound = self._generate_beep(800, 0.12)

        # Fallback pyttsx3 (ленивая инициализация)
        self._pyttsx_engine = None
        self._on_speak_callback = None

        # Предзагрузка частых фраз
        if cache_enabled:
            self._preload_common_phrases()

        logger.info("Speaker: voice=%s, rate=%s", voice, rate)

    # ══════════════════════════════════════════════
    #  Инициализация
    # ══════════════════════════════════════════════

    def _init_mixer(self):
        """Инициализация pygame mixer с обработкой ошибок."""
        try:
            if pygame.mixer.get_init():
                return
            pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=2048)
            self._mixer_initialized = True
        except Exception as e:
            logger.error("Не удалось инициализировать mixer: %s", e)
            self._mixer_initialized = False

    def _run_loop(self):
        """Фоновый event loop для async операций."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _generate_beep(self, freq: int, duration: float) -> pygame.mixer.Sound | None:
        """
        Генерирует звук активации.
        
        Используем Sound (не music) чтобы beep мог играть
        параллельно с речью без конфликтов.
        """
        if not self._mixer_initialized:
            return None
        try:
            sample_rate = 24000
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            # Плавное нарастание и затухание (убирает щелчки)
            fade_samples = int(sample_rate * 0.01)
            envelope = np.ones_like(t)
            envelope[:fade_samples] = np.linspace(0, 1, fade_samples)
            envelope[-fade_samples:] = np.linspace(1, 0, fade_samples)
            # Синусоида с затуханием
            decay = np.exp(-t * 8)
            tone = (np.sin(2 * np.pi * freq * t) * decay * envelope * 16000).astype(np.int16)
            return pygame.mixer.Sound(buffer=tone.tobytes())
        except Exception as e:
            logger.debug("Не удалось создать beep: %s", e)
            return None

    def _preload_common_phrases(self):
        """Предзагрузка частых фраз в кэш."""
        def _preload():
            for phrase in self.PRELOAD_PHRASES:
                try:
                    cache_path = self._get_cache_path(phrase)
                    if not os.path.exists(cache_path):
                        future = asyncio.run_coroutine_threadsafe(
                            self._generate_audio(phrase, cache_path),
                            self._loop
                        )
                        future.result(timeout=10)
                except Exception as e:
                    logger.debug("Preload '%s' failed: %s", phrase[:20], e)
            logger.debug("Предзагружено %d фраз", len(self.PRELOAD_PHRASES))

        threading.Thread(target=_preload, daemon=True, name="tts-preload").start()

    # ══════════════════════════════════════════════
    #  Публичный API
    # ══════════════════════════════════════════════

    def beep(self) -> None:
        """
        Короткий звук активации.
        
        НЕблокирующий. Использует Sound channel (не music),
        поэтому не конфликтует с речью.
        """
        if self._activation_sound:
            try:
                self._activation_sound.play()
                # Короткая пауза чтобы звук начал играть
                # но НЕ ждём окончания
                time.sleep(0.05)
            except Exception:
                pass

    def say(self, text: str, raw_text: str = None) -> None:
        """
        Произнести текст. Блокирует до окончания воспроизведения.
        
        Args:
            text: очищенный текст для озвучивания
            raw_text: оригинальный текст для GUI (с markdown).
                    Если None — в GUI пойдёт text.
        """
        if not text or not text.strip():
            return

        text = text.strip()
        logger.info("Говорю: %s", text[:80])

        if self._on_speak_callback:
            try:
                self._on_speak_callback(raw_text or text)
            except Exception as e:
                logger.debug("on_speak callback error: %s", e)

        with self._speaking_lock:
            self._is_speaking = True

        try:
            self._say_impl(text)
        finally:
            with self._speaking_lock:
                self._is_speaking = False
    
    def say_streamed(self, text: str, raw_text: str = None) -> None:
        """
        Произнести длинный текст по предложениям.
        
        Args:
            text: очищенный текст для голоса
            raw_text: оригинал для GUI
        """
        if not text or not text.strip():
            return

        # Callback с raw-текстом (один раз, целиком)
        if self._on_speak_callback:
            try:
                self._on_speak_callback(raw_text or text)
            except Exception as e:
                logger.debug("on_speak callback error: %s", e)

        sentences = self._split_sentences(text)

        if len(sentences) <= 1:
            with self._speaking_lock:
                self._is_speaking = True
            try:
                self._say_impl(text)
            finally:
                with self._speaking_lock:
                    self._is_speaking = False
            return

        with self._speaking_lock:
            self._is_speaking = True

        try:
            first_path = self._ensure_audio(sentences[0])
            if not first_path:
                self._say_impl(text)
                return

            for i, sentence in enumerate(sentences):
                current_path = self._ensure_audio(sentence)

                next_future = None
                if i + 1 < len(sentences):
                    next_future = self._generate_in_background(sentences[i + 1])

                if current_path:
                    self._play_file(current_path)

                if next_future:
                    try:
                        next_future.result(timeout=15)
                    except Exception:
                        pass
        finally:
            with self._speaking_lock:
                self._is_speaking = False

    @property
    def is_speaking(self) -> bool:
        """Говорит ли сейчас ассистент."""
        with self._speaking_lock:
            return self._is_speaking

    def set_rate(self, rate: str) -> None:
        """Изменить скорость речи. Кэш НЕ инвалидируется."""
        self._rate = rate
        logger.info("Скорость речи: %s", rate)

    def set_volume(self, volume: str) -> None:
        """Изменить громкость голоса."""
        self._volume = volume
        logger.info("Громкость голоса: %s", volume)
    
    def set_on_speak(self, callback) -> None:
        """Установить callback, вызываемый при каждом say().
        callback(text: str) — вызывается ДО воспроизведения."""
        self._on_speak_callback = callback

    def cleanup(self) -> None:
        """Очистка всех ресурсов."""
        logger.info("Speaker cleanup")

        # Останавливаем event loop
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=2)
        except Exception:
            pass

        # Останавливаем mixer
        try:
            if self._mixer_initialized:
                pygame.mixer.music.stop()
                pygame.mixer.quit()
                self._mixer_initialized = False
        except Exception:
            pass

        # Останавливаем pyttsx3
        if self._pyttsx_engine:
            try:
                self._pyttsx_engine.stop()
            except Exception:
                pass

        # Удаляем временные файлы
        try:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass

    # ══════════════════════════════════════════════
    #  Внутренняя реализация
    # ══════════════════════════════════════════════

    def _say_impl(self, text: str) -> None:
        """Реализация say(): генерация + воспроизведение."""
        audio_path = self._ensure_audio(text)

        if audio_path:
            self._play_file(audio_path)
        else:
            # edge-tts не сработал — fallback
            self._fallback_say(text)

    def _ensure_audio(self, text: str) -> str | None:
        """
        Получить путь к аудиофайлу.
        Из кэша или генерируем новый.
        """
        cache_path = self._get_cache_path(text)

        # Проверяем кэш
        if self._cache_enabled and os.path.exists(cache_path):
            return cache_path

        # Генерируем
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._generate_audio(text, cache_path),
                self._loop
            )
            future.result(timeout=15)

            if os.path.exists(cache_path):
                self._rotate_cache()
                return cache_path

        except Exception as e:
            logger.error("TTS генерация: %s", e)

        return None

    def _generate_in_background(self, text: str):
        """Запустить генерацию аудио в фоне. Возвращает Future."""
        cache_path = self._get_cache_path(text)
        if self._cache_enabled and os.path.exists(cache_path):
            # Уже в кэше — возвращаем завершённый future
            import concurrent.futures
            f = concurrent.futures.Future()
            f.set_result(cache_path)
            return f
        return asyncio.run_coroutine_threadsafe(
            self._generate_audio(text, cache_path),
            self._loop
        )

    async def _generate_audio(self, text: str, output_path: str) -> None:
        """Генерация аудио через edge-tts."""
        communicate = edge_tts.Communicate(
            text,
            self._voice,
            rate=self._rate,
            volume=self._volume
        )
        await communicate.save(output_path)

    def _play_file(self, path: str) -> None:
        """Воспроизвести аудиофайл. Блокирует до окончания."""
        if not self._mixer_initialized:
            self._init_mixer()
            if not self._mixer_initialized:
                return

        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()

            # Ждём окончания с периодической проверкой
            while pygame.mixer.music.get_busy():
                time.sleep(0.03)

        except Exception as e:
            logger.error("Воспроизведение: %s", e)
        finally:
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass

    # ══════════════════════════════════════════════
    #  Кэш
    # ══════════════════════════════════════════════

    def _get_cache_path(self, text: str) -> str:
        """
        Путь к кэшированному файлу.
        
        Ключ: hash(text + voice)
        НЕ включает rate/volume — чтобы кэш не инвалидировался
        при изменении скорости. Минус: при смене rate кэшированные
        файлы будут с прежней скоростью. Это приемлемо — кэш
        ротируется и новые файлы будут с новой скоростью.
        
        Обновление: включаем rate в ключ, но предзагруженные фразы
        всегда используют дефолтный rate.
        """
        key = hashlib.md5(
            f"{text}|{self._voice}|{self._rate}|{self._volume}".encode("utf-8")
        ).hexdigest()
        return os.path.join(self._cache_dir, f"{key}.mp3")

    def _rotate_cache(self) -> None:
        """Удалить старые файлы если кэш превысил лимит."""
        with self._cache_lock:
            try:
                cache_path = Path(self._cache_dir)
                files = list(cache_path.glob("*.mp3"))

                if len(files) <= self.CACHE_MAX_FILES:
                    return

                # Сортируем по времени доступа (старые первыми)
                files.sort(key=lambda f: f.stat().st_atime)

                # Удаляем 20% самых старых
                to_delete = len(files) - int(self.CACHE_MAX_FILES * 0.8)
                for f in files[:to_delete]:
                    try:
                        f.unlink()
                    except OSError:
                        pass

                logger.debug("Кэш: удалено %d файлов", to_delete)

            except Exception as e:
                logger.debug("Ротация кэша: %s", e)

    # ══════════════════════════════════════════════
    #  Fallback
    # ══════════════════════════════════════════════

    def _fallback_say(self, text: str) -> None:
        """
        Запасной TTS через pyttsx3 (оффлайн).
        Ленивая инициализация — engine создаётся один раз.
        """
        try:
            if self._pyttsx_engine is None:
                import pyttsx3
                self._pyttsx_engine = pyttsx3.init()
                # Ищем русский голос
                for v in self._pyttsx_engine.getProperty('voices'):
                    name = v.name.lower()
                    if any(w in name for w in ["russian", "irina", "pavel", "ru"]):
                        self._pyttsx_engine.setProperty('voice', v.id)
                        break
                self._pyttsx_engine.setProperty('rate', 170)
                logger.info("Fallback TTS (pyttsx3) инициализирован")

            self._pyttsx_engine.say(text)
            self._pyttsx_engine.runAndWait()

        except Exception as e:
            logger.error("Fallback TTS: %s", e)

    # ══════════════════════════════════════════════
    #  Утилиты
    # ══════════════════════════════════════════════

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """
        Разбить текст на предложения для потокового воспроизведения.
        
        Умнее чем простой split по ".":
        - "г. Москва" не разбивается
        - "т.д." не разбивается
        - Разбивается по ". ", "! ", "? "
        """
        import re
        # Разбиваем по точке/восклицательному/вопросительному + пробел
        # Но не по "г.", "т.", "д.", "т.д.", "и т.п."
        parts = re.split(r'(?<=[.!?])\s+(?=[А-ЯA-Z])', text)

        # Если не получилось разбить по заглавным — пробуем проще
        if len(parts) <= 1:
            parts = re.split(r'(?<=[.!?])\s+', text)

        return [p.strip() for p in parts if p.strip()]