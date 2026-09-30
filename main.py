# main.py

"""
Голосовой ассистент v3.2.

Изменения:
- Звуковой feedback "я думаю" во время Whisper
- Graceful shutdown (сигналы + cleanup)
- AssistantContext как формализованный контракт (v3.2)
- Единый механизм остановки через _shutdown_event (v3.2)
- [PROACTIVE] Проактивные предложения через ProactiveManager
"""

import logging
import sys
import signal
import threading
import ctypes
import os
import time

from core.config import Config
from core.listener import Listener
from core.speaker import Speaker
from core.recognizer import Recognizer
from core.router import CommandRouter
from core.assistant_context import AssistantContext  # Изменение v3.2
from core.llm import LLM
from core.tray import TrayIcon
from core import notifier
from core.knowledge import KnowledgeBase
from core.proactive import ProactiveManager

from commands.utility import TimerManager
from commands.media import VolumeControl
from commands.info import WeatherCache
from core.ipc.server import IPCServer


def hide_console():
    try:
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0
        )
    except Exception:
        pass


def setup_logging(config: Config):
    log_level = getattr(
        logging,
        config.get_str("logging", "level", default="INFO"),
        logging.INFO
    )
    log_file = config.get_str("logging", "file", default="assistant.log")

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    
    # Подавляем спам от httpx и urllib3
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)


class Assistant:
    """
    Главный класс ассистента.

    v3.2:
    - AssistantContext теперь формализован
    - Единый механизм состояния (property _running)
    """

    def __init__(self, config: Config):
        self._config = config
        self._shutdown_event = threading.Event()  # Единственный флаг остановки
        self._logger = logging.getLogger("assistant")

        # ── Компоненты ──

        self._speaker = Speaker(
            voice=config.get_str("speaker", "voice",
                                 default="ru-RU-SvetlanaNeural"),
            rate=config.get_str("speaker", "rate", default="+0%"),
            volume=config.get_str("speaker", "volume", default="+0%"),
            cache_enabled=config.get_bool("speaker", "cache", default=True),
        )

        self._recognizer = Recognizer(
            language=config.get_str("recognizer", "language", default="ru"),
            model_size=config.get_str("recognizer", "model_size",
                                      default="medium"),
            use_whisper=config.get_bool("recognizer", "use_whisper",
                                        default=True),
        )

        llm_config = config.get("llm", default={})
        provider = (llm_config.get("provider", "none")
                    if isinstance(llm_config, dict) else "none")
        self._llm = LLM(llm_config) if provider != "none" else None

        # ── База знаний ──
        self._knowledge = KnowledgeBase(
            db_path="data/knowledge.db",
            llm=self._llm,
        )

        self._timer_mgr = TimerManager()
                # Push таймеров в GUI каждую секунду (только если есть активные)
        self._timer_broadcast_thread = threading.Thread(
            target=self._broadcast_timers_loop, daemon=True, name="timer-push",
        )
        self._timer_broadcast_thread.start()
        self._volume_ctrl = VolumeControl()
        self._weather_cache = WeatherCache(ttl_seconds=600)

        # ── Формализованный контекст ──
        ctx = AssistantContext(
            speaker=self._speaker,
            config=config,
            llm=self._llm,
            knowledge=self._knowledge,
            recognizer=self._recognizer,
            # ПЕРЕДАЁМ СЕРВИСЫ:
            timer_manager=self._timer_mgr,
            volume_control=self._volume_ctrl,
            weather_cache=self._weather_cache,
        )

        if self._knowledge and hasattr(self._knowledge, 'tracker'):
            ctx.on_command_executed = self._knowledge.tracker.log_command
            # Push активного приложения в GUI
            original_callback = self._knowledge.tracker._activity_callback

            def _activity_with_broadcast(record):
                # Оригинальный callback (паттерны)
                if original_callback:
                    original_callback(record)
                # Broadcast в GUI
                if record.get("action") == "app_switch":
                    self._ipc_server.broadcast("active_app_update", {
                        "app": record.get("target", ""),
                        "title": record.get("window_title", ""),
                    })

            self._knowledge.tracker._activity_callback = _activity_with_broadcast

        explain = (config.get_bool("app", "explain_mode", default=False)
                   or os.environ.get("ASSISTANT_EXPLAIN") == "1")

        explain = (config.get_bool("app", "explain_mode", default=False)
                   or os.environ.get("ASSISTANT_EXPLAIN") == "1")

        self._router = CommandRouter(ctx, explain=explain)

        self._wake_word = config.get_str("listener", "wake_word",
                                         default="Ассистент")
        self._listener = Listener(
            model_path=config.get_str("listener", "model_path"),
            wake_word=self._wake_word,
            follow_up_seconds=config.get_float(
                "assistant", "follow_up_seconds", default=4.0
            ),
            alternative_words=config.get(
                "listener", "alternative_words", default=None
            ),
        )

        self._tray = TrayIcon(
            on_quit=self._quit,
            on_toggle_pause=self._toggle_pause,
            wake_word=self._wake_word,
        )

        # [PROACTIVE] Менеджер проактивных предложений
        self._proactive = None
        try:
            self._proactive = ProactiveManager(
                knowledge=self._knowledge,
                speaker=self._speaker,
                recognizer=self._recognizer,
                listener=self._listener,
                router=self._router,
                tray=self._tray,
            )
        except Exception as e:
            self._logger.error("ProactiveManager init error: %s", e)

        self._timeout = config.get_int("recognizer", "timeout", default=5)
        self._phrase_limit = config.get_int("recognizer", "phrase_limit",
                                            default=10)
        self._follow_up_seconds = config.get_float(
            "assistant", "follow_up_seconds", default=4.0
        )
        self._max_retries = 2

        # 1. Инициализируем IPC сервер
        self._ipc_server = IPCServer()
        self._start_time = time.time()
        self._gui_connected = False

        # 2. Регистрируем обработчики команд из GUI
        self._ipc_server.register_handler("execute_command", self._handle_gui_execute)
        self._ipc_server.register_handler("get_status", lambda p: {"status": "ok"})
        self._ipc_server.register_handler("get_chat_history", self._handle_gui_get_chat_history)
        self._ipc_server.register_handler("save_chat_message", self._handle_gui_save_chat_message)
        self._ipc_server.register_handler("clear_chat_history", self._handle_gui_clear_chat_history)
        self._ipc_server.register_handler("get_session_snapshot", self._handle_gui_get_session_snapshot)
        self._ipc_server.register_handler("get_macros", self._handle_gui_get_macros)
        self._ipc_server.register_handler("save_macro", self._handle_gui_save_macro)
        self._ipc_server.register_handler("search_knowledge", self._handle_gui_search_knowledge)
        self._ipc_server.register_handler("get_patterns", self._handle_gui_get_patterns)
        self._ipc_server.register_handler("get_config", self._handle_gui_get_config)
        self._ipc_server.register_handler("save_config", self._handle_gui_save_config)
        self._ipc_server.register_handler("delete_macro", self._handle_gui_delete_macro)
        self._ipc_server.register_handler("add_knowledge_entry", self._handle_gui_add_knowledge_entry)
        self._ipc_server.register_handler("delete_knowledge_entry", self._handle_gui_delete_knowledge_entry)
        self._ipc_server.register_handler("edit_knowledge_entry", self._handle_gui_edit_knowledge_entry)
        self._ipc_server.register_handler("get_timers", self._handle_gui_get_timers)
        self._ipc_server.register_handler("get_active_app", self._handle_gui_get_active_app)
        self._ipc_server.register_handler("get_commands", self._handle_gui_get_commands)
        self._ipc_server.register_handler("apply_setting", self._handle_gui_apply_setting)
        self._ipc_server.register_handler("get_recent_commands", self._handle_gui_get_recent_commands)
        self._ipc_server.register_handler("get_suggestion", self._handle_gui_get_suggestion)
        self._ipc_server.register_handler("preview_voice", self._handle_gui_preview_voice)
        self._speaker.set_on_speak(self._on_speaker_text)
    # ══════════════════════════════════════════════
    #  Жизненный цикл
    # ══════════════════════════════════════════════

    @property
    def _running(self) -> bool:
        """Единый источник правды о состоянии ассистента."""
        return not self._shutdown_event.is_set()

    def run(self) -> None:
        self._print_banner()
        self._setup_signal_handlers()

        notifier.init(self._tray)
        self._knowledge.start()

        # 3. Запускаем IPC сервер
        self._ipc_server.start()

        # Проверяем подключение GUI каждые 2 секунды
        self._gui_check_timer_thread = threading.Thread(
            target=self._check_gui_connection, daemon=True,
        )
        self._gui_check_timer_thread.start()

        if self._proactive:
            self._proactive.start()

        self._speaker.say(f"Привет! Скажи {self._wake_word} для активации.")
        print(f"\n🎤 Жду... (скажи '{self._wake_word}')\n")

        listener_thread = threading.Thread(
            target=self._run_listener,
            daemon=True,
            name="listener"
        )
        listener_thread.start()

        try:
            self._tray.set_listening()
            self._tray.run()
        except KeyboardInterrupt:
            self._logger.info("Ctrl+C")

        self._cleanup()
    
    def _check_gui_connection(self):
        """Скрыть трей ядра когда GUI подключён."""
        while self._running:
            has_clients = len(self._ipc_server._clients) > 0
            if has_clients and not self._gui_connected:
                self._gui_connected = True
                # Скрываем трей ядра — GUI покажет свой
                try:
                    if self._tray and self._tray._icon:
                        self._tray._icon.visible = False
                except Exception:
                    pass
            elif not has_clients and self._gui_connected:
                self._gui_connected = False
                # Показываем трей ядра обратно
                try:
                    if self._tray and self._tray._icon:
                        self._tray._icon.visible = True
                except Exception:
                    pass
            time.sleep(2)

    def _setup_signal_handlers(self):
        def _handler(signum, frame):
            sig_name = signal.Signals(signum).name
            self._logger.info("Получен сигнал %s", sig_name)
            self._quit()

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, _handler)

    def _print_banner(self) -> None:
        llm_info = "отключен"
        if self._llm:
            info = self._llm.get_info()
            llm_info = f"{info['provider']} ({info['model']})"

        cmd_count = self._router.command_count

        print("=" * 55)
        print("  🤖 ГОЛОСОВОЙ АССИСТЕНТ v3.2")
        print(f"  Wake word:  '{self._wake_word}'")
        print(f"  Whisper:    "
              f"{self._config.get_str('recognizer', 'model_size', default='?')}")
        print(f"  LLM:        {llm_info}")
        print(f"  Команд:     {cmd_count}")
        print(f"  Диалог:     {self._follow_up_seconds} сек")
        if self._proactive:
            print("  Проактив:   ВКЛ")
        if self._router.explain_mode:
            print("  ⚙ Explain-mode: ВКЛ")
        print("=" * 55)

        if self._config.has_errors:
            print("\n⚠ Предупреждения конфигурации:")
            for err in self._config.errors:
                print(f"  - {err}")
            print()

    def _cleanup(self) -> None:
        """Очистка ресурсов при выходе (защищена от двойного вызова)."""
        if self._shutdown_event.is_set():
            return  # Уже очищено
        
        self._logger.info("Завершение работы...")
        self._shutdown_event.set()

        try:
            self._timer_mgr.cancel_all()
        except Exception:
            pass

        try:
            self._listener.stop()
        except Exception:
            pass

        try:
            self._tray.stop()
        except Exception:
            pass

        if self._llm:
            self._logger.info(self._llm.stats.summary())
        self._logger.info(self._listener.stats.summary())

        if self._proactive:
            self._logger.info(self._proactive.stats.summary())
            try:
                self._proactive.stop()
            except Exception:
                pass

        try:
            self._knowledge.stop()
        except Exception:
            pass

        # 4. Останавливаем сервер при выходе
        if hasattr(self, '_ipc_server'):
            self._ipc_server.stop()
            
        self._speaker.cleanup()
        print("\n👋 Ассистент остановлен.")

    # ══════════════════════════════════════════════
    #  Callbacks
    # ══════════════════════════════════════════════

    def _quit(self) -> None:
        if not self._running:
            return
        self._shutdown_event.set()
        self._listener.stop()
        try:
            self._tray.stop()
        except Exception:
            pass

    def _toggle_pause(self, is_paused: bool) -> None:
        if is_paused:
            self._listener.pause()
            self._logger.info("Пауза (из трея)")
            print("⏸ Пауза")
        else:
            self._listener.resume()
            self._logger.info("Возобновлено (из трея)")
            print(f"▶ Жду... (скажи '{self._wake_word}')")

    # ══════════════════════════════════════════════
    #  Listener
    # ══════════════════════════════════════════════

    def _run_listener(self) -> None:
        try:
            self._listener.start(on_wake=self._handle_wake)
        except Exception as e:
            self._logger.error("Listener fatal: %s", e)
            self._tray.set_error("Микрофон недоступен")

    def _handle_wake(self) -> None:
        self._ipc_server.broadcast("status_update", {"state": "listening"}) # Отправка в GUI
        self._listener.pause()
        self._tray.set_active()
        self._listener.pause()
        self._tray.set_active()

        if self._proactive:
            self._proactive.begin_interaction()

        print("\n🔔 Активирован!")
        self._speaker.beep()

        self._router.reset_dialog()

        try:
            self._conversation_loop()

            if self._proactive:
                try:
                    self._proactive.try_after_command()
                except Exception as e:
                    self._logger.debug(
                        "After-command suggestion error: %s", e,
                    )

        except Exception as e:
            self._logger.error("Ошибка в диалоге: %s", e)
            self._speaker.say("Произошла ошибка.")

        finally:
            if self._proactive:
                self._proactive.end_interaction()

            if self._running:
                self._tray.set_listening()
                self._ipc_server.broadcast("status_update", {"state": "waiting"}) # Отправка в GUI
                print(f"\n🎤 Жду... (скажи '{self._wake_word}')")
                self._listener.resume()

    # ══════════════════════════════════════════════
    #  Цикл диалога
    # ══════════════════════════════════════════════

    def _conversation_loop(self) -> None:
        is_first = True
        retries = 0

        while self._running:
            if is_first:
                print("🎤 Слушаю команду...")
                timeout = self._timeout
            else:
                print("🎤 Слушаю ещё...")
                timeout = self._follow_up_seconds

            self._tray.set_active()
            text = self._recognizer.listen(
                timeout=timeout,
                phrase_limit=self._phrase_limit
            )

            if text is None:
                if is_first and retries < self._max_retries:
                    retries += 1
                    print(f"   ❌ Не расслышал "
                          f"({retries}/{self._max_retries})")
                    self._speaker.say("Повтори.")
                    continue
                else:
                    if not is_first:
                        print("   ⏸ Диалог завершён")
                    break

            retries = 0
            print(f"   📝 '{text}'")

            self._tray.set_processing()
            self._ipc_server.broadcast("status_update", {"state": "processing", "text": text}) # Отправка в GUI
            result = self._router.process(text)
            self._tray.increment_commands()

            self._ipc_server.broadcast("command_executed", {
                "text": text,
                "success": result.success,
                "source": "voice",
                "timestamp": time.time(),
            })

            if not result.should_continue:
                self._quit()
                return

            if result.follow_up:
                is_first = False
                continue
            else:
                break

    # ══════════════════════════════════════════════
    #  Утилиты
    # ══════════════════════════════════════════════

    def _log_system_info(self) -> None:
        import platform
        self._logger.info(
            "Система: %s %s, Python %s",
            platform.system(),
            platform.release(),
            platform.python_version()
        )
        try:
            import psutil
            ram = psutil.virtual_memory()
            self._logger.info(
                "RAM: %.1f GB (%.0f%% использовано)",
                ram.total / (1024 ** 3),
                ram.percent
            )
        except ImportError:
            pass
    
    # 5. Обработчик выполнения команд из GUI
    def _handle_gui_execute(self, payload: dict) -> dict:
        text = payload.get("text")
        if not text:
            return {"success": False, "error": "Empty text"}
            
        self._logger.info(f"Команда из GUI: {text}")
        result = self._router.process(text)
        
        return {
            "success": result.success,
            "follow_up": result.follow_up
        }
    
    def _handle_gui_get_macros(self, payload: dict) -> dict:
        """Отдаёт сырой словарь макросов из конфига для GUI."""
        self._logger.info("GUI запросил макросы")
        try:
            macros = self._config.get("macros", default={})
            if not isinstance(macros, dict):
                macros = {}
                
            self._logger.info(f"Отправляю GUI {len(macros)} макросов")
            return {"success": True, "macros": macros}
        except Exception as e:
            self._logger.error(f"Ошибка при сборе макросов для GUI: {e}")
            return {"success": False, "error": str(e)}
    
    def _handle_gui_save_macro(self, payload: dict) -> dict:
        """
        Сохраняет макрос из GUI в config.yaml и перезагружает плагин.
        payload = {"macro_data": {"my_id": {"name": "...", "steps": [...]}}}
        """
        macro_data = payload.get("macro_data")
        if not macro_data or not isinstance(macro_data, dict):
            return {"success": False, "error": "Invalid macro data"}

        self._logger.info(f"Сохранение макроса от GUI: {list(macro_data.keys())[0]}")

        # 1. Читаем текущий конфиг с диска, чтобы не потерять комментарии 
        # (в идеале использовать ruamel.yaml, но пока обойдемся стандартным yaml)
        import yaml
        config_path = self._config._config_path
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f) or {}
        except Exception as e:
            self._logger.error(f"Ошибка чтения конфига: {e}")
            raw_config = {}

        # 2. Обновляем секцию macros
        if "macros" not in raw_config:
            raw_config["macros"] = {}
            
        for m_id, m_body in macro_data.items():
            raw_config["macros"][m_id] = m_body

        # 3. Записываем обратно в файл
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(raw_config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        except Exception as e:
            self._logger.error(f"Ошибка записи конфига: {e}")
            return {"success": False, "error": f"Save error: {e}"}

        # 4. Обновляем в памяти текущего объекта Config
        # (чтобы не перезагружать всего ассистента)
        if "macros" not in self._config._config:
            self._config._config["macros"] = {}
        for m_id, m_body in macro_data.items():
            self._config._config["macros"][m_id] = m_body

        # 5. Дёргаем hot-reload для команды MacroCommand
        # Так как контекст теперь чистый, мы используем callback
        self._router.reload_command("macro")

        return {"success": True, "macro_id": list(macro_data.keys())[0]}
    
    def _handle_gui_delete_macro(self, payload: dict) -> dict:
        """Удаление макроса из config.yaml и/или generated_macros.yaml."""
        macro_id = payload.get("macro_id")
        if not macro_id:
            return {"success": False, "error": "No macro_id"}

        import yaml
        deleted = False

        # 1. Пробуем config.yaml
        config_path = self._config._config_path
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f) or {}

            if macro_id in raw_config.get("macros", {}):
                del raw_config["macros"][macro_id]
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(raw_config, f, allow_unicode=True,
                              sort_keys=False, default_flow_style=False)
                deleted = True
        except Exception as e:
            self._logger.error("delete_macro config error: %s", e)
            return {"success": False, "error": str(e)}

        # 2. Пробуем generated_macros.yaml
        gen_path = "data/generated_macros.yaml"
        if os.path.exists(gen_path):
            try:
                with open(gen_path, "r", encoding="utf-8") as f:
                    gen = yaml.safe_load(f) or {}
                if macro_id in gen:
                    del gen[macro_id]
                    with open(gen_path, "w", encoding="utf-8") as f:
                        yaml.dump(gen, f, allow_unicode=True,
                                  sort_keys=False, default_flow_style=False)
                    deleted = True
            except Exception:
                pass

        if not deleted:
            return {"success": False, "error": f"Макрос '{macro_id}' не найден"}

        # 3. Обновляем в памяти
        cfg_macros = self._config._config.get("macros")
        if isinstance(cfg_macros, dict):
            cfg_macros.pop(macro_id, None)

        self._router.reload_command("macro")
        self._logger.info("Макрос '%s' удалён из GUI", macro_id)
        return {"success": True}
    
    def _handle_gui_search_knowledge(self, payload: dict) -> dict:
        """Поиск/просмотр базы знаний с фильтрацией по типу и источнику."""
        query = payload.get("query", "")
        limit = payload.get("limit", 50)
        entry_type = payload.get("entry_type")   # str | None
        source = payload.get("source")           # str | None  ("user" = voice+gui)

        if not self._knowledge:
            return {"success": False, "error": "KB disabled"}

        try:
            if not query:
                # ── Последние записи с фильтрами ──
                fetch_limit = limit * 3 if source else limit
                entries = self._knowledge.storage.get_recent(
                    fetch_limit, entry_type=entry_type,
                )
                # Пост-фильтр по источнику
                if source:
                    if source == "user":
                        entries = [e for e in entries
                                if e.source in ("voice", "gui")]
                    else:
                        entries = [e for e in entries
                                if e.source == source]
                entries = entries[:limit]
                items = [self._entry_to_dict(e) for e in entries]
            else:
                # ── Гибридный поиск + пост-фильтр ──
                raw = self._knowledge.search(query, limit=limit * 2)
                items = []
                for sr in raw:
                    e = sr.entry
                    if entry_type and e.entry_type != entry_type:
                        continue
                    if source:
                        if source == "user" and e.source not in ("voice", "gui"):
                            continue
                        elif source != "user" and e.source != source:
                            continue
                    item = self._entry_to_dict(e)
                    item["score"] = round(sr.score, 3)
                    items.append(item)
                    if len(items) >= limit:
                        break

            return {
                "success": True,
                "results": items,
                "total": self._knowledge.storage.entry_count,
            }

        except Exception as e:
            self._logger.error("search_knowledge error: %s", e)
            return {"success": False, "error": str(e)}
    
    def _handle_gui_get_patterns(self, payload: dict) -> dict:
        """Паттерны привычек для GUI — полностью безопасная сериализация."""
        self._logger.info("GUI запросил паттерны поведения")

        if not self._knowledge:
            return {"success": False, "error": "KnowledgeBase disabled"}

        def _to_float(val, default=0.0):
            """'85%' → 0.85,  0.85 → 0.85,  '+5%' → 0.05,  мусор → default."""
            if isinstance(val, (int, float)):
                f = float(val)
                return f
            if isinstance(val, str):
                cleaned = val.replace('%', '').replace('+', '').strip()
                if not cleaned:
                    return default
                try:
                    f = float(cleaned)
                    # Если пришло "85" из "85%" — это 85%, переводим в 0..1
                    if f > 1.0 and '%' in val:
                        f = f / 100.0
                    return f
                except (ValueError, TypeError):
                    return default
            return default

        def _to_str(val, default=""):
            try:
                return str(val) if val is not None else default
            except Exception:
                return default

        try:
            # ── Статистика за 7 дней ──
            stats_7d_raw = self._knowledge.get_activity_summary(hours=168)
            safe_stats = []
            for item in stats_7d_raw.get("top_apps", []):
                try:
                    name = _to_str(item[0], "?") if len(item) > 0 else "?"
                    minutes = _to_float(item[1]) if len(item) > 1 else 0.0
                    safe_stats.append([name, minutes])
                except Exception:
                    continue

            # ── Паттерны ──
            safe_patterns = {"temporal": [], "sequences": []}

            if hasattr(self._knowledge, 'patterns'):
                try:
                    raw_habits = self._knowledge.patterns.get_habits()
                except Exception as e:
                    self._logger.debug("get_habits error: %s", e)
                    raw_habits = {}

                # Временные паттерны
                for p in raw_habits.get("temporal", []):
                    try:
                        entry = {}
                        if isinstance(p, dict):
                            entry = {
                                "target": _to_str(p.get("target"), "?"),
                                "when": _to_str(p.get("when")),
                                "day_type": _to_str(p.get("day_type"), "weekday"),
                            }
                        elif hasattr(p, "target"):
                            entry = {
                                "target": _to_str(getattr(p, "target", None), "?"),
                                "when": _to_str(getattr(p, "when", None)),
                                "day_type": _to_str(getattr(p, "day_type", None), "weekday"),
                            }
                        if entry:
                            safe_patterns["temporal"].append(entry)
                    except Exception:
                        continue

                # Последовательности
                for p in raw_habits.get("sequences", []):
                    try:
                        if isinstance(p, dict):
                            pattern_text = _to_str(p.get("pattern"), "?")
                            raw_conf = p.get("confidence", 0)
                        elif hasattr(p, "pattern"):
                            pattern_text = _to_str(getattr(p, "pattern", None), "?")
                            raw_conf = getattr(p, "confidence", 0)
                        else:
                            continue

                        safe_patterns["sequences"].append({
                            "pattern": pattern_text,
                            "confidence": round(_to_float(raw_conf), 3),
                        })
                    except Exception:
                        continue

            return {
                "success": True,
                "patterns": safe_patterns,
                "stats_7d": safe_stats,
            }

        except Exception as e:
            self._logger.error("get_patterns error: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}
        
    def _handle_gui_get_config(self, payload: dict) -> dict:
        """Отдаёт содержимое config.yaml как строку."""
        config_path = self._config._config_path
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
            return {"success": True, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_gui_save_config(self, payload: dict) -> dict:
        """Сохраняет новую строку в config.yaml."""
        content = payload.get("content")
        if not content:
            return {"success": False, "error": "Empty content"}
            
        config_path = self._config._config_path
        try:
            # Валидация YAML перед сохранением
            import yaml
            yaml.safe_load(content)
            
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
                
            self._logger.info("Конфиг обновлен из GUI. Требуется перезапуск для применения ВСЕХ настроек.")
            return {"success": True}
        except yaml.YAMLError as e:
            return {"success": False, "error": f"Ошибка синтаксиса YAML: {e}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        
    def _on_speaker_text(self, text: str):
        """Транслировать речь ассистента в GUI и сохранять в историю чата."""
        ts = time.time()
        self._ipc_server.broadcast("assistant_speak", {
            "text": text,
            "timestamp": ts,
        })
        # Персистентность
        if self._knowledge:
            try:
                self._knowledge.storage.add_chat_message(
                    "assistant", text, "voice", ts,
                )
            except Exception:
                pass
    
    def _entry_to_dict(self, entry) -> dict:
        """Конвертировать Entry в dict для передачи в GUI."""
        return {
            "id": entry.id,
            "content": entry.content,
            "entry_type": entry.entry_type,
            "source": entry.source,
            "tags": entry.tags,
            "importance": entry.importance,
            "age": entry.age_display,
            "created_at": entry.created_at,
        }
    
    def _handle_gui_add_knowledge_entry(self, payload: dict) -> dict:
        """Добавление записи в базу знаний из GUI."""
        content = payload.get("content", "").strip()
        if not content:
            return {"success": False, "error": "Empty content"}

        entry_type = payload.get("entry_type", "note")
        tags = payload.get("tags", "")
        importance = payload.get("importance", 0)

        try:
            entry_id = self._knowledge.remember(
                content=content,
                entry_type=entry_type,
                tags=tags,
                importance=importance,
                source="gui",
            )
            self._logger.info("KB entry #%d added from GUI", entry_id)
            return {"success": True, "entry_id": entry_id}
        except Exception as e:
            self._logger.error("add_knowledge_entry error: %s", e)
            return {"success": False, "error": str(e)}
    
    def _handle_gui_delete_knowledge_entry(self, payload: dict) -> dict:
        """Архивирование (мягкое удаление) записи из GUI."""
        entry_id = payload.get("entry_id")
        if not entry_id:
            return {"success": False, "error": "No entry_id"}

        try:
            self._knowledge.forget(entry_id)
            self._logger.info("KB entry #%d archived from GUI", entry_id)
            return {"success": True}
        except Exception as e:
            self._logger.error("delete_knowledge_entry error: %s", e)
            return {"success": False, "error": str(e)}
    
    def _handle_gui_edit_knowledge_entry(self, payload: dict) -> dict:
        """Редактирование записи базы знаний из GUI."""
        entry_id = payload.get("entry_id")
        if not entry_id:
            return {"success": False, "error": "No entry_id"}

        try:
            content = payload.get("content")
            tags = payload.get("tags")
            importance = payload.get("importance")

            if content is not None:
                self._knowledge.storage.update_entry(entry_id, content.strip())

            if tags is not None:
                self._knowledge.storage.update_tags(entry_id, tags.strip())

            if importance is not None:
                with self._knowledge.storage._write_lock:
                    conn = self._knowledge.storage._get_connection()
                    conn.execute(
                        "UPDATE entries SET importance=?, updated_at=? WHERE id=?",
                        (int(importance), __import__('time').time(), entry_id),
                    )
                    conn.commit()

            self._logger.info("KB entry #%d edited from GUI", entry_id)
            return {"success": True}
        except Exception as e:
            self._logger.error("edit_knowledge_entry error: %s", e)
            return {"success": False, "error": str(e)}
    
    def _handle_gui_get_timers(self, payload: dict) -> dict:
        """Активные таймеры для GUI."""
        try:
            timers = self._timer_mgr.get_active()
            return {"success": True, "timers": timers}
        except Exception:
            return {"success": True, "timers": []}

    def _handle_gui_get_active_app(self, payload: dict) -> dict:
        """Последнее активное приложение (из activity_log)."""
        if not self._knowledge:
            return {"success": True, "app": "", "title": ""}
        try:
            recent = self._knowledge.storage.get_activity(
                hours=0.5, action="app_switch", limit=1,
            )
            if recent:
                return {
                    "success": True,
                    "app": recent[0].target,
                    "title": recent[0].window_title,
                }
            return {"success": True, "app": "", "title": ""}
        except Exception:
            return {"success": True, "app": "", "title": ""}
    
    def _handle_gui_get_chat_history(self, payload: dict) -> dict:
        """Загрузить историю чата для GUI."""
        if not self._knowledge:
            return {"success": True, "messages": []}
        try:
            limit = payload.get("limit", 100)
            messages = self._knowledge.storage.get_chat_messages(limit=limit)
            return {"success": True, "messages": messages}
        except Exception as e:
            self._logger.error("get_chat_history error: %s", e)
            return {"success": True, "messages": []}

    def _handle_gui_save_chat_message(self, payload: dict) -> dict:
        """Сохранить сообщение чата из GUI."""
        if not self._knowledge:
            return {"success": False}
        try:
            self._knowledge.storage.add_chat_message(
                role=payload.get("role", "user"),
                content=payload.get("content", ""),
                source=payload.get("source", "gui"),
                timestamp=payload.get("timestamp", 0),
            )
            return {"success": True}
        except Exception:
            return {"success": False}

    def _handle_gui_clear_chat_history(self, payload: dict) -> dict:
        """Очистить историю чата."""
        if not self._knowledge:
            return {"success": True}
        try:
            self._knowledge.storage.clear_chat_messages()
            return {"success": True}
        except Exception:
            return {"success": False}

    def _handle_gui_get_session_snapshot(self, payload: dict) -> dict:
        """
        Snapshot текущей сессии ядра для GUI.
        Вызывается при подключении GUI, чтобы синхронизировать состояние.
        """
        import time as _t

        uptime = 0.0
        if hasattr(self, '_start_time'):
            uptime = _t.time() - self._start_time

        return {
            "success": True,
            "uptime": uptime,
            "commands_count": self._router._ctx.dialog.commands_in_session,
            "wake_word": self._wake_word,
            "llm_available": self._llm.available if self._llm else False,
            "llm_info": self._llm.get_info() if self._llm else {},
            "kb_stats": self._knowledge.stats if self._knowledge else {},
        }
    def _handle_gui_get_commands(self, payload: dict) -> dict:
        """Список всех команд для Command Palette."""
        from core.gui.command_palette import _AUTO_CATEGORIES, _auto_description

        commands = []
        seen = set()

        for cmd in self._router._commands:
            if cmd.name in seen or cmd.name == "smart_llm":
                continue
            seen.add(cmd.name)

            desc = getattr(cmd, 'description', '') or ''
            cat = getattr(cmd, 'category', '') or ''
            kw = list(cmd.keywords) if cmd.keywords else []

            if not desc:
                desc = _auto_description(cmd.name, kw)
            if not cat:
                cat = _AUTO_CATEGORIES.get(cmd.name, "Другое")

            commands.append({
                "name": cmd.name,
                "description": desc,
                "category": cat,
                "keywords": kw[:5],
            })

        commands.sort(key=lambda c: (c["category"], c["description"]))
        return {"success": True, "commands": commands}
    
    def _handle_gui_apply_setting(self, payload: dict) -> dict:
        """
        Применить настройку без перезапуска.
        
        Поддерживаемые path:
        - speaker.rate, speaker.volume — мгновенно
        - llm.system_prompt — мгновенно
        - llm.max_tokens — мгновенно
        - assistant.follow_up_seconds — мгновенно
        - proactive.enabled — мгновенно
        """
        path = payload.get("path", "")
        value = payload.get("value")

        if not path:
            return {"success": False, "error": "No path"}

        try:
            if path == "speaker.rate":
                self._speaker.set_rate(str(value))
                return {"success": True, "applied": True}

            elif path == "speaker.volume":
                self._speaker.set_volume(str(value))
                return {"success": True, "applied": True}

            elif path == "llm.system_prompt" and self._llm:
                self._llm._system_prompt = str(value)
                return {"success": True, "applied": True}

            elif path == "llm.max_tokens" and self._llm:
                self._llm._max_tokens = int(value)
                return {"success": True, "applied": True}

            elif path == "assistant.follow_up_seconds":
                self._follow_up_seconds = float(value)
                return {"success": True, "applied": True}

            elif path == "proactive.enabled" and self._proactive:
                self._proactive.set_enabled(bool(value))
                return {"success": True, "applied": True}

            else:
                return {"success": True, "applied": False,
                        "message": "Требуется перезапуск"}

        except Exception as e:
            self._logger.error("apply_setting error: %s", e)
            return {"success": False, "error": str(e)}
    
    def _handle_gui_get_recent_commands(self, payload: dict) -> dict:
        """Последние выполненные команды из activity_log."""
        if not self._knowledge:
            return {"success": True, "commands": []}
        try:
            limit = payload.get("limit", 20)
            records = self._knowledge.storage.get_activity(
                hours=24, action="command", limit=limit,
            )
            commands = []
            for r in records:
                commands.append({
                    "text": r.target or r.details,
                    "success": True,
                    "source": "voice",
                    "timestamp": r.timestamp,
                })
            return {"success": True, "commands": commands}
        except Exception:
            return {"success": True, "commands": []}
    
    def _handle_gui_get_suggestion(self, payload: dict) -> dict:
        """Получить pending проактивное предложение."""
        if not self._knowledge:
            return {"success": True, "text": ""}
        try:
            suggestion = self._knowledge.get_pending_suggestion()
            if suggestion:
                return {
                    "success": True,
                    "text": suggestion.text,
                    "action": suggestion.action,
                    "params": suggestion.action_params,
                }
            return {"success": True, "text": ""}
        except Exception:
            return {"success": True, "text": ""}
    
    def _handle_gui_preview_voice(self, payload: dict) -> dict:
        """Озвучить тестовую фразу с указанными параметрами."""
        import threading

        voice = payload.get("voice", self._speaker._voice)
        rate = payload.get("rate", self._speaker._rate)
        text = payload.get("text", "Привет! Я Пятница, твой голосовой ассистент.")

        def _preview():
            try:
                import edge_tts
                import asyncio
                import tempfile
                import os

                async def _gen():
                    comm = edge_tts.Communicate(text, voice, rate=rate)
                    tmp = tempfile.mktemp(suffix=".mp3")
                    await comm.save(tmp)
                    return tmp

                loop = asyncio.new_event_loop()
                tmp_path = loop.run_until_complete(_gen())
                loop.close()

                if os.path.exists(tmp_path):
                    self._speaker._play_file(tmp_path)
                    os.unlink(tmp_path)
            except Exception as e:
                self._logger.error("Voice preview error: %s", e)

        threading.Thread(target=_preview, daemon=True).start()
        return {"success": True}
    
    def _broadcast_timers_loop(self):
        """Транслирует состояние таймеров в GUI каждую секунду."""
        while self._running:
            time.sleep(1)
            timers = self._timer_mgr.get_active()
            if timers:
                self._ipc_server.broadcast("timers_update", {
                    "timers": timers,
                })
            elif getattr(self, '_last_timer_count', 0) > 0:
                # Последний таймер завершился — отправить пустой список
                self._ipc_server.broadcast("timers_update", {
                    "timers": [],
                })
            self._last_timer_count = len(timers)

def main():
    config = Config()
    setup_logging(config)

    logger = logging.getLogger("main")
    logger.info("=" * 40)
    logger.info("Запуск ассистента v3.2")

    if config.get_bool("app", "hide_console", default=False):
        hide_console()

    try:
        assistant = Assistant(config)
        assistant._log_system_info()
        assistant.run()
    except KeyboardInterrupt:
        print("\n👋 Прервано.")
    except Exception as e:
        logger.critical("Фатальная ошибка: %s", e, exc_info=True)
        print(f"\n💥 Фатальная ошибка: {e}")
        print("Подробности в assistant.log")
        input("Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()