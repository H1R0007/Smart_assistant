# core/gui/ipc_client.py

"""
WebSocket-клиент с callback-роутингом по msg_id
и поддержкой произвольных broadcast-событий.
"""

import asyncio
import json
import uuid
import logging

from PySide6.QtCore import QThread, Signal

import websockets

log = logging.getLogger(__name__)


class IPCClient(QThread):
    """
    Связь GUI ↔ Ядро.

    Сигналы:
        connected / disconnected — статус соединения
        status_updated(payload)  — broadcast: status_update
        broadcast_received(type, payload) — любой другой broadcast

    Методы:
        request(cmd, payload, callback) — запрос с точечным ответом
        send_command(cmd, payload)      — fire-and-forget (обратная совм.)
    """

    connected = Signal()
    disconnected = Signal()
    status_updated = Signal(dict)
    broadcast_received = Signal(str, dict)

    # Внутренний: маршрутизация ответов в main thread
    _response_arrived = Signal(str, dict)

    def __init__(self, uri: str = "ws://localhost:8765"):
        super().__init__()
        self.uri = uri
        self._running = True
        self._ws = None
        self._loop = None
        self._callbacks: dict[str, callable] = {}

        self._response_arrived.connect(self._dispatch)

    # ── Свойства ──

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    # ── QThread ──

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self):
        while self._running:
            try:
                async with websockets.connect(self.uri) as ws:
                    self._ws = ws
                    self.connected.emit()
                    async for message in ws:
                        self._handle_raw(message)
            except (websockets.exceptions.ConnectionClosed,
                    ConnectionRefusedError, OSError):
                self._ws = None
                self.disconnected.emit()
                await asyncio.sleep(2)
            except Exception as exc:
                log.debug("IPC connection error: %s", exc)
                self._ws = None
                self.disconnected.emit()
                await asyncio.sleep(2)

    # ── Приём ──

    def _handle_raw(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")
        payload = data.get("payload", {})
        msg_id = data.get("msg_id")

        if msg_id:
            # Ответ на конкретный запрос
            self._response_arrived.emit(msg_id, payload)
        elif msg_type == "status_update":
            self.status_updated.emit(payload)
        else:
            # Любой другой broadcast (command_executed, …)
            self.broadcast_received.emit(msg_type, payload)

    def _dispatch(self, msg_id: str, payload: dict):
        cb = self._callbacks.pop(msg_id, None)
        if cb:
            try:
                cb(payload)
            except Exception as exc:
                log.error("IPC callback error: %s", exc)

    # ── Отправка ──

    def request(self, cmd_type: str, payload: dict,
                callback: callable = None) -> str:
        msg_id = str(uuid.uuid4())
        if callback:
            self._callbacks[msg_id] = callback

        self._send_raw({
            "msg_id": msg_id,
            "type": cmd_type,
            "payload": payload,
        })
        return msg_id

    def send_command(self, cmd_type: str, payload: dict):
        """Обратная совместимость."""
        self.request(cmd_type, payload)

    def _send_raw(self, obj: dict):
        if self._ws and self._loop and not self._loop.is_closed():
            raw = json.dumps(obj)
            asyncio.run_coroutine_threadsafe(self._ws.send(raw), self._loop)

    # ── Жизненный цикл ──

    def stop(self):
        self._running = False
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.wait(2000)