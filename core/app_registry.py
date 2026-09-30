# core/app_registry.py

"""
Единый реестр приложений.

Объединяет:
- Алиасы для запуска (раньше в apps.py)
- Имена процессов для закрытия (раньше в apps.py)
- Подстроки заголовков окон (раньше в window_control.py)
- Канонические имена для статистики (раньше в activity_stats.py)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

@dataclass
class AppInfo:
    """Информация о приложении."""
    canonical_name: str           # Например: "Chrome"
    launch_command: str           # Например: "chrome" (или "notepad.exe")
    process_name: str             # Например: "chrome.exe"
    voice_aliases: List[str]      # Что говорит пользователь: ["хром", "браузер"]
    window_title_hints: List[str] # Что искать в заголовке окна: ["google chrome", "chrome"]
    warn_before_close: bool = False # Предупреждать ли перед закрытием?

class UnifiedAppRegistry:
    """Централизованное хранилище всех приложений."""

    # Дефолтный список, объединяющий все старые словари
    DEFAULT_APPS = [
        # Браузеры
        AppInfo("Chrome", "chrome", "chrome.exe", 
                ["браузер", "хром", "chrome", "гугл хром", "гугл"], 
                ["google chrome", "chrome", "браузер", "browser"], True),
        AppInfo("Firefox", "firefox", "firefox.exe", 
                ["firefox", "фаерфокс", "фаерфоксе"], 
                ["mozilla firefox", "firefox"], True),
        AppInfo("Edge", "msedge", "msedge.exe", 
                ["edge", "эдж"], 
                ["microsoft edge", "edge"], True),
        
        # Системные
        AppInfo("Блокнот", "notepad.exe", "notepad.exe", 
                ["блокнот", "блокноте", "notepad"], 
                ["notepad", "блокнот"]),
        AppInfo("Проводник", "explorer.exe", "explorer.exe", 
                ["проводник", "проводнике", "файлы", "папки", "explorer"], 
                ["проводник", "explorer"]),
        AppInfo("Калькулятор", "calc.exe", "CalculatorApp.exe", 
                ["калькулятор", "calculator"], 
                ["calculator", "калькулятор"]),
        AppInfo("Диспетчер задач", "taskmgr.exe", "taskmgr.exe", 
                ["диспетчер задач", "диспетчер"], 
                ["task manager", "диспетчер задач"]),
        AppInfo("Настройки", "ms-settings:", "SystemSettings.exe", 
                ["настройки", "параметры"], 
                ["settings", "параметры"]),
        
        # Разработка
        AppInfo("VS Code", "code", "Code.exe", 
                ["vs code", "вс код", "вскод", "редактор кода", "код", "коде", "vscode"], 
                ["visual studio code", "vs code"], True),
        AppInfo("Terminal", "wt.exe", "WindowsTerminal.exe", 
                ["терминал", "терминале", "windows terminal"], 
                ["terminal", "windows terminal", "windowsterminal"]),
        AppInfo("CMD", "cmd.exe", "cmd.exe", 
                ["консоль", "командная строка", "cmd"], 
                ["cmd", "command prompt", "командная строка"]),
        AppInfo("PowerShell", "powershell.exe", "powershell.exe", 
                ["powershell", "повершел"], 
                ["powershell", "pwsh"]),
        
        # Графика
        AppInfo("Paint", "mspaint.exe", "mspaint.exe", 
                ["paint", "пейнт"], 
                ["paint"]),
        
        # Офис
        AppInfo("Word", "winword", "WINWORD.EXE", 
                ["ворд", "ворде", "word"], 
                ["word", "document"], True),
        AppInfo("Excel", "excel", "EXCEL.EXE", 
                ["эксель", "экселе", "excel"], 
                ["excel"], True),
        AppInfo("PowerPoint", "powerpnt", "POWERPNT.EXE", 
                ["powerpoint", "поверпоинт"], 
                ["powerpoint"], True),
        
        # Мессенджеры
        AppInfo("Telegram", "telegram", "Telegram.exe", 
                ["телеграм", "телеграме", "telegram", "телега", "телеге"], 
                ["telegram"]),
        AppInfo("Discord", "discord", "Discord.exe", 
                ["дискорд", "дискорде", "discord"], 
                ["discord"]),
        
        # Развлечения
        AppInfo("Steam", "steam", "steam.exe", 
                ["стим", "steam"], 
                ["steam"]),
        AppInfo("Spotify", "spotify", "Spotify.exe", 
                ["spotify", "спотифай"], 
                ["spotify"]),
    ]

    def __init__(self, config=None):
        self.apps: List[AppInfo] = list(self.DEFAULT_APPS)
        
        # Индексы для быстрого поиска
        self._by_alias: Dict[str, AppInfo] = {}
        self._by_process: Dict[str, AppInfo] = {}
        self._by_canonical: Dict[str, AppInfo] = {}
        
        self._build_indices()
        
        # Если есть конфиг, подмешиваем пользовательские алиасы
        if config:
            self._load_from_config(config)

    def _build_indices(self):
        self._by_alias.clear()
        self._by_process.clear()
        self._by_canonical.clear()
        
        for app in self.apps:
            self._by_canonical[app.canonical_name.lower()] = app
            self._by_process[app.process_name.lower()] = app
            for alias in app.voice_aliases:
                self._by_alias[alias.lower()] = app

    def _load_from_config(self, config):
        """Интеграция пользовательских настроек из config.yaml"""
        custom_apps = config.get("apps", default={})
        custom_processes = config.get("process_names", default={})
        
        for alias, cmd in custom_apps.items():
            alias_lower = alias.lower()
            # Если алиас уже есть, обновляем команду запуска
            if alias_lower in self._by_alias:
                self._by_alias[alias_lower].launch_command = cmd
            else:
                # Создаём новое приложение
                process = custom_processes.get(cmd, cmd if cmd.endswith(".exe") else f"{cmd}.exe")
                new_app = AppInfo(
                    canonical_name=alias.capitalize(),
                    launch_command=cmd,
                    process_name=process,
                    voice_aliases=[alias_lower],
                    window_title_hints=[alias_lower],
                    warn_before_close=False
                )
                self.apps.append(new_app)
        
        self._build_indices()

    def get_by_alias(self, alias: str) -> Optional[AppInfo]:
        """Найти приложение по голосовому алиасу."""
        return self._by_alias.get(alias.lower())

    def get_by_canonical(self, name: str) -> Optional[AppInfo]:
        """Найти приложение по каноническому имени."""
        return self._by_canonical.get(name.lower())

    def get_all_aliases(self) -> Dict[str, str]:
        """Для обратной совместимости: dict[alias, launch_command]"""
        return {alias: app.launch_command for alias, app in self._by_alias.items()}

    def get_window_aliases(self) -> Dict[str, List[str]]:
        """Для обратной совместимости с WindowManager"""
        result = {}
        for app in self.apps:
            for alias in app.voice_aliases:
                if alias not in result:
                    result[alias] = app.window_title_hints
        return result


# Singleton для приложения (создаётся один раз)
_global_registry = None

def get_app_registry(config=None) -> UnifiedAppRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = UnifiedAppRegistry(config)
    return _global_registry