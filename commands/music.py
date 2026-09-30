# commands/music.py

"""
Управление музыкой: Яндекс Музыка и общие медиа-команды.

"включи музыку"
"поставь на паузу" / "пауза"
"следующий трек" / "дальше"
"предыдущий трек" / "назад"
"что играет" / "что за трек"
"найди в яндекс музыке [запрос]"
"стоп музыка"

Работает через системные медиа-клавиши →
совместим с любым плеером (Яндекс Музыка,
YouTube, VLC, Spotify и т.д.)
"""

import logging
from commands.base import BaseCommand, CommandResult
logger = logging.getLogger(__name__)


class MusicCommand(BaseCommand):
    """Управление музыкой и Яндекс Музыкой."""

    name = "music_control"
    description = "Управление музыкой и Яндекс Музыкой"
    priority = 10
    fuzzy_enabled = True

    keywords = [
        "включи музыку",
        "поставь на паузу",
        "пауза",
        "продолжи воспроизведение",
        "следующий трек",
        "дальше",
        "следующая песня",
        "предыдущий трек",
        "назад",
        "предыдущая песня",
        "что играет",
        "что за трек",
        "что за песня",
        "какая песня",
        "стоп музыка",
        "останови музыку",
        "найди в яндекс музыке",
        "открой яндекс музыку",
        "запусти яндекс музыку",
    ]

    def extract_params(
        self, text: str, matched_keyword: str = "",
    ) -> dict:
        text_lower = text.lower()

        # Определяем действие
        if any(w in text_lower for w in (
            "пауза", "поставь на паузу",
        )):
            return {"action": "pause"}

        if any(w in text_lower for w in (
            "продолжи", "включи музыку",
        )):
            return {"action": "play"}

        if any(w in text_lower for w in (
            "следующий", "дальше", "следующая",
        )):
            return {"action": "next"}

        if any(w in text_lower for w in (
            "предыдущий", "назад", "предыдущая",
        )):
            return {"action": "prev"}

        if any(w in text_lower for w in (
            "что играет", "что за трек",
            "что за песня", "какая песня",
        )):
            return {"action": "current"}

        if any(w in text_lower for w in (
            "стоп", "останови",
        )):
            return {"action": "stop"}

        if any(w in text_lower for w in (
            "открой яндекс", "запусти яндекс",
        )):
            return {"action": "open"}

        if "найди" in text_lower:
            query = self._clean_text(text, [
                matched_keyword,
                "в яндекс музыке",
                "в яндекс музыку",
                "яндекс музыка",
                "найди",
            ])
            return {"action": "search", "query": query}

        return {"action": "play"}

    def execute(self, text: str, params: dict) -> CommandResult:
        from core.integrations import yandex_music as ym

        action = params.get("action", "play")

        if action == "play":
            if not ym.is_yandex_music_active():
                self.speaker.say(
                    "Запускаю Яндекс Музыку.",
                )
                ym.open_yandex_music()
                import time
                time.sleep(2)
            ym.play_pause()
            self.speaker.say("Воспроизведение.")

        elif action == "pause":
            ym.play_pause()
            self.speaker.say("Пауза.")

        elif action == "next":
            ym.next_track()
            self.speaker.say("Следующий трек.")

        elif action == "prev":
            ym.prev_track()
            self.speaker.say("Предыдущий трек.")

        elif action == "stop":
            ym.stop()
            self.speaker.say("Музыка остановлена.")

        elif action == "current":
            track = ym.get_current_track()
            if track:
                artist = track.get("artist", "")
                title = track.get("title", "")
                if artist and title:
                    self.speaker.say(
                        f"Сейчас играет: {artist} — {title}."
                    )
                elif title:
                    self.speaker.say(
                        f"Сейчас играет: {title}."
                    )
                else:
                    self.speaker.say(
                        "Не удалось определить трек."
                    )
            else:
                self.speaker.say(
                    "Не вижу активного плеера. "
                    "Скажи «включи музыку» чтобы запустить."
                )

        elif action == "open":
            ym.open_yandex_music()
            self.speaker.say("Открываю Яндекс Музыку.")

        elif action == "search":
            query = params.get("query", "")
            if query:
                ym.search_yandex_music(query)
                self.speaker.say(
                    f"Ищу в Яндекс Музыке: {query}."
                )
            else:
                self.speaker.say(
                    "Что найти в Яндекс Музыке?"
                )
                return CommandResult(follow_up=True)

        self.ctx.context.update(
            "music", action, params,
        )

        return CommandResult(follow_up=True)