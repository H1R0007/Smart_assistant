# commands/vscode_cmd.py

"""
VS Code: голосовые команды.

"открой проект [название]"
"открой файл [путь]"
"список проектов"
"открой vs code"

config.yaml:
  integrations:
    vscode:
      projects_dir: "C:/Projects"
"""

import logging
from commands.base import BaseCommand, CommandResult
logger = logging.getLogger(__name__)


class VSCodeCommand(BaseCommand):
    """Управление VS Code через CLI."""

    name = "vscode"
    description = "Открыть проект или файл в VS Code"
    priority = 8
    fuzzy_enabled = True

    keywords = [
        "открой проект",
        "открой в vs code",
        "список проектов",
        "какие проекты",
        "покажи проекты",
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._vsc = None
        try:
            from core.integrations.vscode import create_vscode
            self._vsc = create_vscode(self.config)
            if self._vsc.is_available:
                logger.info(
                    "VS Code CLI доступен. "
                    "Проекты: %s",
                    self._vsc._projects_dir or "не настроено",
                )
        except Exception as e:
            logger.debug("VS Code init: %s", e)

    def extract_params(
        self, text: str, matched_keyword: str = "",
    ) -> dict:
        text_lower = text.lower()

        if any(w in text_lower for w in (
            "список", "какие", "покажи",
        )):
            return {"action": "list"}

        name = self._clean_text(text, [
            matched_keyword,
            "в vs code", "в vscode",
            "проект", "файл",
        ])

        return {"action": "open", "name": name}

    def is_valid_candidate(self, params: dict) -> bool:
        if not self._vsc or not self._vsc.is_available:
            return False
        return True

    def execute(self, text: str, params: dict) -> CommandResult:
        action = params.get("action", "open")

        if action == "list":
            return self._handle_list()

        name = params.get("name", "")
        if not name:
            self.speaker.say("Какой проект открыть?")
            return CommandResult(follow_up=True)

        return self._handle_open(name)

    def _handle_open(self, name: str) -> CommandResult:
        """Открыть проект по имени."""
        if self._vsc.open_project(name):
            self.speaker.say(f"Открываю проект {name}.")
            self.ctx.context.update(
                "vscode", name, {"project": name},
            )
            return CommandResult(follow_up=True)

        # Пробуем как путь к файлу
        if self._vsc.open_file(name):
            self.speaker.say(f"Открываю файл.")
            return CommandResult(follow_up=True)

        self.speaker.say(
            f"Проект «{name}» не найден. "
            f"Скажи «список проектов» чтобы узнать "
            f"доступные."
        )
        return CommandResult(follow_up=True)

    def _handle_list(self) -> CommandResult:
        """Список доступных проектов."""
        projects = self._vsc.list_projects()

        if not projects:
            self.speaker.say(
                "Папка проектов не настроена или пуста. "
                "Укажи путь в конфиге: "
                "integrations, vscode, projects_dir."
            )
            return CommandResult(follow_up=True)

        # Озвучиваем первые 5
        names = projects[:5]
        text = ", ".join(names)
        extra = ""
        if len(projects) > 5:
            extra = f" и ещё {len(projects) - 5}"

        self.speaker.say(
            f"Доступные проекты: {text}{extra}."
        )
        return CommandResult(follow_up=True)