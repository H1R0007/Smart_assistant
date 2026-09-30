# core/window_utils.py

"""
Общие Win32 утилиты для работы с окнами и мониторами.
Используются в window_control.py и work_start.py
"""

import ctypes
import ctypes.wintypes
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)
user32 = ctypes.windll.user32

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]

@dataclass
class MonitorInfo:
    """Информация о мониторе."""
    handle: int
    x: int
    y: int
    width: int
    height: int
    is_primary: bool

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

class MonitorManager:
    """Получение информации о подключённых мониторах."""
    @staticmethod
    def get_monitors() -> list[MonitorInfo]:
        monitors: list[MonitorInfo] = []

        def _callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(hMonitor, ctypes.byref(info))
            work = info.rcWork
            monitors.append(MonitorInfo(
                handle=hMonitor,
                x=work.left,
                y=work.top,
                width=work.right - work.left,
                height=work.bottom - work.top,
                is_primary=bool(info.dwFlags & 1),
            ))
            return True

        enum_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.POINTER(RECT), ctypes.c_double,
        )

        user32.EnumDisplayMonitors(None, None, enum_proc_type(_callback), 0)
        monitors.sort(key=lambda m: (not m.is_primary, m.x))
        return monitors