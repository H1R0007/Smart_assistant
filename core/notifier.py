"""
Windows уведомления через системный трей.

Простой модуль-синглтон.
Инициализируется через init(tray) при запуске.
"""

import logging

logger = logging.getLogger(__name__)

_tray = None


def init(tray) -> None:
    """Связать notifier с TrayIcon."""
    global _tray
    _tray = tray
    logger.debug("Notifier инициализирован")


def notify(title: str, message: str) -> None:
    """
    Показать уведомление Windows.
    
    Потокобезопасный (tray.notify использует lock).
    Если tray не инициализирован — только логирует.
    """
    logger.debug("Уведомление: %s — %s", title, message)
    if _tray:
        try:
            _tray.notify(title, message)
        except Exception as e:
            logger.debug("Notify error: %s", e)