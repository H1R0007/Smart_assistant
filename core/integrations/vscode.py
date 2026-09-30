# core/integrations/vscode.py

"""
Интеграция с VS Code через CLI.

VS Code CLI (`code`) поддерживает:
- code .              → открыть текущую директорию
- code path/to/file   → открыть файл
- code path/to/dir    → открыть папку как проект
- code --goto file:42 → открыть на строке
- code --diff a b     → показать diff
- code --reuse-window  → в текущем окне

Поиск проектов:
- Сканирует projects_dir (из конфига)
- Ищет по имени (нечёткое сравнение)

Зависимость: VS Code должен быть установлен
и `code` доступен в PATH.
"""

import os
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class VSCodeIntegration:
    """
    Управление VS Code.

    Использование:
        vsc = VSCodeIntegration(projects_dir="C:/Projects")
        vsc.open_project("assistant")
        vsc.open_file("C:/Projects/assistant/main.py")
    """

    def __init__(
        self,
        projects_dir: str = "",
        code_cmd: str = "code",
    ):
        self._projects_dir = projects_dir
        self._code_cmd = code_cmd
        self._available = self._check_available()

    def _check_available(self) -> bool:
        """Проверить что `code` доступен."""
        try:
            result = subprocess.run(
                [self._code_cmd, "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @property
    def is_available(self) -> bool:
        return self._available

    def open_file(self, path: str, line: int = 0) -> bool:
        """
        Открыть файл в VS Code.

        Args:
            path: Путь к файлу
            line: Номер строки (0 = не указывать)
        """
        if not self._available:
            logger.warning("VS Code CLI недоступен")
            return False

        path = os.path.expanduser(path)
        if not os.path.exists(path):
            logger.warning("Файл не найден: %s", path)
            return False

        try:
            args = [self._code_cmd]
            if line > 0:
                args.extend(["--goto", f"{path}:{line}"])
            else:
                args.append(path)

            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("VS Code: открыт %s", path)
            return True

        except Exception as e:
            logger.error("VS Code open error: %s", e)
            return False

    def open_project(self, name: str) -> bool:
        """
        Открыть проект по имени.

        Ищет папку в projects_dir по нечёткому совпадению.
        """
        if not self._projects_dir:
            logger.warning("projects_dir не настроен")
            return False

        project_path = self._find_project(name)
        if not project_path:
            logger.warning(
                "Проект '%s' не найден в %s",
                name, self._projects_dir,
            )
            return False

        return self.open_folder(str(project_path))

    def open_folder(self, path: str) -> bool:
        """Открыть папку как проект."""
        if not self._available:
            return False

        path = os.path.expanduser(path)
        if not os.path.isdir(path):
            logger.warning("Папка не найдена: %s", path)
            return False

        try:
            subprocess.Popen(
                [self._code_cmd, path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info(
                "VS Code: открыт проект %s", path,
            )
            return True

        except Exception as e:
            logger.error("VS Code open error: %s", e)
            return False

    def list_projects(self) -> list[str]:
        """Список проектов в projects_dir."""
        if not self._projects_dir:
            return []

        base = Path(self._projects_dir)
        if not base.is_dir():
            return []

        return sorted([
            d.name for d in base.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    def _find_project(self, name: str) -> Path | None:
        """Найти проект по имени (нечёткий поиск)."""
        base = Path(self._projects_dir)
        if not base.is_dir():
            return None

        name_lower = name.lower().strip()

        # Точное совпадение
        exact = base / name
        if exact.is_dir():
            return exact

        # Регистронезависимый поиск
        for d in base.iterdir():
            if not d.is_dir():
                continue
            if d.name.lower() == name_lower:
                return d

        # Частичное совпадение
        for d in base.iterdir():
            if not d.is_dir():
                continue
            if name_lower in d.name.lower():
                return d

        return None


def create_vscode(config) -> VSCodeIntegration:
    """Создать VSCodeIntegration из конфигурации."""
    integrations = config.get("integrations") or {}
    vsc_config = integrations.get("vscode") or {}

    return VSCodeIntegration(
        projects_dir=vsc_config.get("projects_dir", ""),
        code_cmd=vsc_config.get("code_cmd", "code"),
    )