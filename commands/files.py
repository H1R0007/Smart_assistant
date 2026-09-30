# commands/files.py

"""
Быстрый доступ к папкам и файлам.

v3.1:
- Расширенный список известных папок
- Открытие вложенных папок ("открой папку проекты в документах")
- Содержимое папки ("что на рабочем столе")
- Последние изменённые файлы в папке
- Размер папки
"""

import os
import datetime
from pathlib import Path
from commands.base import BaseCommand, CommandResult, stem_russian


# ═══════════════════════════════════════════
#  Реестр папок
# ═══════════════════════════════════════════

class FolderRegistry:
    """Маппинг голосовых команд на системные папки."""

    # Алиас → (имя папки в home, отображаемое имя)
    KNOWN_FOLDERS = {
        "загрузки": ("Downloads", "загрузки"),
        "скачанное": ("Downloads", "загрузки"),
        "скачанные": ("Downloads", "загрузки"),
        "документы": ("Documents", "документы"),
        "рабочий стол": ("Desktop", "рабочий стол"),
        "рабочем столе": ("Desktop", "рабочий стол"),
        "музыку": ("Music", "музыка"),
        "музыка": ("Music", "музыка"),
        "изображения": ("Pictures", "изображения"),
        "картинки": ("Pictures", "изображения"),
        "фото": ("Pictures", "изображения"),
        "фотографии": ("Pictures", "изображения"),
        "видео": ("Videos", "видео"),
        "видеозаписи": ("Videos", "видео"),
    }

    @staticmethod
    def resolve(text: str, config=None) -> tuple[str | None, str]:
        """
        Найти папку по тексту.

        Returns: (path, display_name) или (None, "")
        """
        text_lower = text.lower()
        user_home = Path.home()

        # 1. Специальные папки из конфига
        if config:
            projects = config.get("folders", "projects", default="")
            if projects and any(
                w in text_lower for w in ["проект", "проекты"]
            ):
                return str(projects), "проекты"

        # 2. Известные папки
        for alias, (folder_name, display) in FolderRegistry.KNOWN_FOLDERS.items():
            if alias in text_lower:
                path = user_home / folder_name
                return str(path), display

        # 3. Стемминг
        text_stemmed = " ".join(stem_russian(w) for w in text_lower.split())
        for alias, (folder_name, display) in FolderRegistry.KNOWN_FOLDERS.items():
            if stem_russian(alias) in text_stemmed:
                path = user_home / folder_name
                return str(path), display

        return None, ""


# ═══════════════════════════════════════════
#  Команды
# ═══════════════════════════════════════════

class OpenFolderCommand(BaseCommand):
    name = "open_folder"
    keywords = [
        "открой папку", "открой загрузки", "открой документы",
        "открой рабочий стол", "покажи папку", "открой музыку",
        "открой изображения", "открой видео", "открой фото",
        "открой картинки",
    ]
    priority = 9

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        path, name = FolderRegistry.resolve(text, self.config)
        return {"path": path, "name": name}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("path"))

    def execute(self, text: str, params: dict) -> CommandResult:
        path = params.get("path")
        name = params.get("name", path)

        if not path:
            self.speaker.say("Какую папку открыть?")
            return CommandResult(follow_up=True)

        if os.path.exists(path):
            os.startfile(path)
            self.speaker.say(f"Открываю {name}.")
        else:
            self.speaker.say(f"Папка {name} не найдена.")

        return CommandResult(follow_up=True)


class FolderContentsCommand(BaseCommand):
    """
    Содержимое папки.

    "что на рабочем столе"
    "что в загрузках"
    "что в папке документы"
    """
    name = "folder_contents"
    keywords = [
        "что на рабочем столе", "что в загрузках",
        "что в папке", "что в документах",
        "содержимое папки", "файлы в папке",
        "покажи что в",
    ]
    priority = 10

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        path, name = FolderRegistry.resolve(text, self.config)
        return {"path": path, "name": name}

    def is_valid_candidate(self, params: dict) -> bool:
        return bool(params.get("path"))

    def execute(self, text: str, params: dict) -> CommandResult:
        path = params.get("path")
        name = params.get("name", "папке")

        if not path or not os.path.exists(path):
            self.speaker.say("Папка не найдена.")
            return CommandResult(follow_up=True)

        try:
            entries = list(Path(path).iterdir())

            if not entries:
                self.speaker.say(f"Папка {name} пуста.")
                return CommandResult(follow_up=True)

            # Сортируем: сначала последние изменённые
            entries.sort(
                key=lambda e: e.stat().st_mtime, reverse=True
            )

            # Разделяем на папки и файлы
            dirs = [e for e in entries if e.is_dir()]
            files = [e for e in entries if e.is_file()]

            parts = [f"В {name}:"]

            # Показываем первые 5 файлов
            shown_files = files[:5]
            if shown_files:
                file_names = [f.stem for f in shown_files]
                parts.append(
                    f"файлы: {', '.join(file_names)}"
                )

            # Количество папок
            if dirs:
                parts.append(f"и {len(dirs)} папок")

            # Общее количество
            if len(entries) > 5:
                parts.append(f"всего {len(entries)} элементов")

            self.speaker.say(". ".join(parts) + ".")

        except PermissionError:
            self.speaker.say(f"Нет доступа к папке {name}.")
        except Exception as e:
            self.logger.error("Folder contents: %s", e)
            self.speaker.say("Не удалось прочитать папку.")

        return CommandResult(follow_up=True)


class RecentFilesCommand(BaseCommand):
    """
    Последние файлы.

    "последние файлы" — системная папка Recent
    "последние файлы в загрузках" — последние в конкретной папке
    """
    name = "recent_files"
    keywords = [
        "последние файлы", "недавние файлы",
        "недавние документы", "последние документы",
        "что скачивал", "последние загрузки",
    ]
    priority = 9

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        # Проверяем конкретную папку
        path, name = FolderRegistry.resolve(text, self.config)

        # "последние загрузки" → папка Downloads
        if not path and any(
            w in text.lower() for w in ["загруз", "скачива"]
        ):
            path = str(Path.home() / "Downloads")
            name = "загрузки"

        return {"path": path, "name": name}

    def execute(self, text: str, params: dict) -> CommandResult:
        path = params.get("path")
        name = params.get("name")

        if path and os.path.exists(path):
            # Последние файлы в конкретной папке
            self._show_recent_in_folder(path, name)
        else:
            # Системная папка "Недавние"
            recent_path = os.path.join(
                os.environ.get("APPDATA", ""),
                r"Microsoft\Windows\Recent"
            )
            if os.path.exists(recent_path):
                os.startfile(recent_path)
                self.speaker.say("Открываю недавние файлы.")
            else:
                self.speaker.say("Не удалось найти недавние файлы.")

        return CommandResult(follow_up=True)

    def _show_recent_in_folder(self, path: str, name: str) -> None:
        """Показать последние файлы в папке."""
        try:
            files = [
                f for f in Path(path).iterdir()
                if f.is_file() and not f.name.startswith(".")
            ]

            if not files:
                self.speaker.say(f"В {name} нет файлов.")
                return

            # Сортируем по дате изменения
            files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

            recent = files[:5]
            names = []
            for f in recent:
                # Время изменения
                mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
                age = datetime.datetime.now() - mtime

                if age.days > 0:
                    time_str = f"{age.days} дней назад"
                elif age.seconds > 3600:
                    time_str = f"{age.seconds // 3600} часов назад"
                else:
                    time_str = f"{age.seconds // 60} минут назад"

                names.append(f"{f.stem} ({time_str})")

            self.speaker.say(
                f"Последние в {name}: {'; '.join(names)}."
            )

        except Exception as e:
            self.logger.error("Recent files: %s", e)
            self.speaker.say("Не удалось прочитать папку.")


class OpenPathCommand(BaseCommand):
    """Открыть проекты или другую папку из конфига."""
    name = "open_path"
    keywords = ["открой проект", "открой проекты"]
    priority = 9

    def execute(self, text: str, params: dict) -> CommandResult:
        projects = self.config.get(
            "folders", "projects", default=r"C:\Projects"
        )
        if os.path.exists(projects):
            os.startfile(projects)
            self.speaker.say("Открываю проекты.")
        else:
            self.speaker.say("Папка проектов не найдена.")
        return CommandResult(follow_up=True)