# core/ipc/server.py

import asyncio
import json
import logging
import threading
from typing import Dict, Any, Callable
import websockets

logger = logging.getLogger(__name__)

class IPCServer:
    """
    WebSocket сервер для связи ядра ассистента с GUI.
    Работает в отдельном фоновом потоке.
    """
    def __init__(self, host: str = 'localhost', port: int = 8765):
        self.host = host
        self.port = port
        self._loop: asyncio.AbstractEventLoop = None
        self._thread: threading.Thread = None
        self._clients = set()
        
        # Реестр обработчиков команд: type -> handler_function
        self._handlers: Dict[str, Callable] = {}
        self._running = False

    def register_handler(self, msg_type: str, handler: Callable):
        """Регистрация обработчика для входящих сообщений от GUI."""
        self._handlers[msg_type] = handler

    def start(self):
        """Запуск сервера в фоновом потоке."""
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(target=self._run_server_thread, daemon=True, name="IPCServer")
        self._thread.start()
        logger.info(f"IPC Server запущен на ws://{self.host}:{self.port}")

    def stop(self):
        """Остановка сервера."""
        self._running = False
        if self._loop and self._loop.is_running():
            # Безопасная остановка asyncio loop из другого потока
            self._loop.call_soon_threadsafe(self._stop_loop)
        if self._thread:
            self._thread.join(timeout=2)

    def _stop_loop(self):
        """Останавливает event loop."""
        for task in asyncio.all_tasks(self._loop):
            task.cancel()
        self._loop.stop()

    def broadcast(self, msg_type: str, payload: Dict[str, Any]):
        """Безопасная отправка сообщения всем подключенным GUI клиентам из любого потока."""
        if not self._clients or not self._loop or not self._loop.is_running():
            return

        message = json.dumps({
            "type": msg_type,
            "payload": payload
        })
        
        # Отправляем задачу в asyncio event loop
        asyncio.run_coroutine_threadsafe(self._broadcast_async(message), self._loop)

    async def _broadcast_async(self, message: str):
        if not self._clients:
            return
        
        # websockets.broadcast - это правильный способ рассылки в новых версиях
        websockets.broadcast(self._clients, message)

    def _run_server_thread(self):
        """Точка входа для фонового потока. Инициализирует и запускает event loop."""
        # Создаем новый цикл для этого потока
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        
        # Запускаем асинхронную функцию сервера внутри этого цикла
        try:
            self._loop.run_until_complete(self._serve_async())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"IPC Server error: {e}")
        finally:
            # Очистка
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    async def _serve_async(self):
        """Асинхронная корутина запуска сервера."""
        async with websockets.serve(self._handle_client, self.host, self.port):
            # Бесконечный цикл, пока _running == True
            while self._running:
                await asyncio.sleep(0.5)

    async def _handle_client(self, websocket):
        """Обработка одного подключения (GUI)."""
        self._clients.add(websocket)
        logger.info(f"GUI подключен. Всего клиентов: {len(self._clients)}")
        
        # Сразу отправляем текущий статус при подключении
        try:
            await websocket.send(json.dumps({
                "type": "status_update", 
                "payload": {"state": "connected"}
            }))

            async for message in websocket:
                await self._process_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.remove(websocket)
            logger.info("GUI отключен.")

    async def _process_message(self, websocket, message: str):
        """Парсинг и маршрутизация входящего сообщения от GUI."""
        try:
            data = json.loads(message)
            msg_id = data.get("msg_id")
            msg_type = data.get("type")
            payload = data.get("payload", {})

            handler = self._handlers.get(msg_type)
            if handler:
                # Вызываем обработчик (синхронный код ядра)
                # Чтобы не блокировать asyncio, выполняем в executor
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, handler, payload)
                
                # Отправляем ответ
                if msg_id:
                    response = {
                        "msg_id": msg_id,
                        "type": "command_result",
                        "payload": result or {}
                    }
                    await websocket.send(json.dumps(response))
            else:
                logger.warning(f"IPC: Неизвестный тип сообщения: {msg_type}")

        except Exception as e:
            logger.error(f"IPC Message processing error: {e}", exc_info=True)