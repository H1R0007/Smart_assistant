# tests/test_macro_generator.py

"""
Тесты генерации макросов из паттернов.

Запуск: python -m pytest tests/test_macro_generator.py -v
"""

import os
import sys
import time
import tempfile
import unittest
from unittest.mock import MagicMock
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.macros.generator import (
    generate_macro_from_sequence,
    generate_macro_from_pattern,
    generate_macro_from_session,
    _clean_targets,
    _time_to_period,
    MIN_STEPS,
    MAX_STEPS,
)
from core.macros.auto_generator import MacroAutoGenerator


# ═══════════════════════════════════════════
#  Мок-данные
# ═══════════════════════════════════════════

@dataclass
class MockSeqPattern:
    antecedent: list
    consequent: str
    action: str = "app_switch"
    confidence: float = 0.8
    lift: float = 2.5
    occurrences: int = 10
    support: float = 0.5
    avg_gap_seconds: float = 30
    description: str = ""

    def __post_init__(self):
        if not self.description:
            self.description = " → ".join(self.antecedent) + f" → {self.consequent}"

    @property
    def is_strong(self):
        return self.confidence >= 0.5 and self.lift >= 1.5 and self.occurrences >= 3


@dataclass
class MockTmpPattern:
    target: str
    peak_minute: int
    action: str = "app_switch"
    confidence: float = 0.7
    regularity: float = 0.8
    recency: float = 0.9
    bandwidth_minutes: float = 15
    day_type: str = "weekday"
    sample_count: int = 10
    days_observed: int = 7

    @property
    def peak_time_str(self):
        h, m = divmod(self.peak_minute, 60)
        return f"{h:02d}:{m:02d}"

    def is_active_now(self):
        return False


@dataclass
class MockRecord:
    action: str = "app_switch"
    target: str = ""
    timestamp: float = 0.0
    duration_sec: float = 0.0
    details: str = ""
    window_title: str = ""
    id: int = 0


# ═══════════════════════════════════════════
#  Тесты generator.py
# ═══════════════════════════════════════════

class TestCleanTargets(unittest.TestCase):
    """Очистка последовательности."""

    def test_remove_empty(self):
        result = _clean_targets(["Chrome", "", "Telegram"])
        self.assertEqual(result, ["Chrome", "Telegram"])

    def test_remove_skip_targets(self):
        result = _clean_targets(["Chrome", "Проводник", "Telegram"])
        self.assertEqual(result, ["Chrome", "Telegram"])

    def test_collapse_duplicates(self):
        result = _clean_targets(["Chrome", "Chrome", "Telegram"])
        self.assertEqual(result, ["Chrome", "Telegram"])

    def test_keep_non_adjacent_duplicates(self):
        result = _clean_targets(["Chrome", "Telegram", "Chrome"])
        self.assertEqual(result, ["Chrome", "Telegram", "Chrome"])


class TestGenerateFromSequence(unittest.TestCase):
    """Генерация макроса из списка targets."""

    def test_basic(self):
        macro = generate_macro_from_sequence(["Chrome", "Telegram"])
        self.assertIsNotNone(macro)
        self.assertEqual(len(macro["steps"]), 2)
        self.assertIn("keywords", macro)
        self.assertIn("name", macro)

    def test_too_few_steps(self):
        macro = generate_macro_from_sequence(["Chrome"])
        self.assertIsNone(macro)

    def test_max_steps_trimmed(self):
        targets = ["Chrome", "Telegram", "VS Code", "Discord",
                    "Spotify", "Steam", "Word"]
        macro = generate_macro_from_sequence(targets)
        self.assertIsNotNone(macro)
        self.assertLessEqual(len(macro["steps"]), MAX_STEPS)

    def test_target_mapping(self):
        """VS Code → code, Chrome → chrome."""
        macro = generate_macro_from_sequence(["VS Code", "Chrome"])
        targets = [s["target"] for s in macro["steps"]]
        self.assertIn("code", targets)
        self.assertIn("chrome", targets)

    def test_heavy_app_delay(self):
        """VS Code получает delay, лёгкие — нет."""
        macro = generate_macro_from_sequence(
            ["VS Code", "Telegram", "Chrome"]
        )
        vs_step = macro["steps"][0]
        self.assertIn("delay", vs_step)

    def test_with_time_hint(self):
        macro = generate_macro_from_sequence(
            ["Chrome", "Telegram"],
            time_hint="утро",
        )
        self.assertIn("утренний", macro["name"].lower())

    def test_custom_name(self):
        macro = generate_macro_from_sequence(
            ["Chrome", "Telegram"],
            name="Мой макрос",
        )
        self.assertEqual(macro["name"], "Мой макрос")

    def test_custom_id(self):
        macro = generate_macro_from_sequence(
            ["Chrome", "Telegram"],
            macro_id="my_macro",
        )
        self.assertEqual(macro["id"], "my_macro")

    def test_all_skip_targets(self):
        macro = generate_macro_from_sequence(["Проводник", "CMD"])
        self.assertIsNone(macro)

    def test_greeting_farewell(self):
        macro = generate_macro_from_sequence(["Chrome", "Telegram"])
        self.assertIn("greeting", macro)
        self.assertIn("farewell", macro)
        self.assertTrue(len(macro["greeting"]) > 0)


class TestGenerateFromPattern(unittest.TestCase):
    """Генерация из SequencePattern."""

    def test_basic(self):
        pattern = MockSeqPattern(["Chrome"], "Telegram")
        macro = generate_macro_from_pattern(pattern)
        self.assertIsNotNone(macro)
        self.assertEqual(len(macro["steps"]), 2)

    def test_with_temporal(self):
        seq = MockSeqPattern(["Chrome"], "Telegram")
        tmp = MockTmpPattern("Chrome", peak_minute=540)  # 9:00
        macro = generate_macro_from_pattern(seq, tmp)
        self.assertIsNotNone(macro)
        self.assertIn("утренний", macro["name"].lower())

    def test_long_antecedent(self):
        pattern = MockSeqPattern(
            ["Chrome", "VS Code", "Terminal"],
            "Telegram",
        )
        macro = generate_macro_from_pattern(pattern)
        self.assertIsNotNone(macro)
        self.assertEqual(len(macro["steps"]), 4)


class TestGenerateFromSession(unittest.TestCase):
    """Генерация из сессии activity_records."""

    def test_basic(self):
        records = [
            MockRecord(action="app_switch", target="Chrome", timestamp=100),
            MockRecord(action="app_switch", target="Telegram", timestamp=130),
            MockRecord(action="app_switch", target="VS Code", timestamp=160),
        ]
        macro = generate_macro_from_session(records)
        self.assertIsNotNone(macro)
        self.assertEqual(len(macro["steps"]), 3)

    def test_skip_clipboard(self):
        records = [
            MockRecord(action="app_switch", target="Chrome"),
            MockRecord(action="clipboard", target="some text"),
            MockRecord(action="app_switch", target="Telegram"),
        ]
        macro = generate_macro_from_session(records)
        self.assertIsNotNone(macro)
        self.assertEqual(len(macro["steps"]), 2)

    def test_empty_session(self):
        records = [
            MockRecord(action="clipboard", target="text"),
        ]
        macro = generate_macro_from_session(records)
        self.assertIsNone(macro)


class TestTimeToPeriod(unittest.TestCase):
    def test_morning(self):
        self.assertEqual(_time_to_period(540), "утро")   # 9:00
    def test_afternoon(self):
        self.assertEqual(_time_to_period(840), "день")   # 14:00
    def test_evening(self):
        self.assertEqual(_time_to_period(1200), "вечер")  # 20:00
    def test_night(self):
        self.assertEqual(_time_to_period(120), "вечер")   # 2:00


# ═══════════════════════════════════════════
#  Тесты auto_generator.py
# ═══════════════════════════════════════════

class TestAutoGenerator(unittest.TestCase):
    """Детектор кандидатов + сохранение."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_path = os.path.join(self.tmp_dir, "gen_macros.yaml")
        self.gen = MacroAutoGenerator(
            storage=None,
            generated_path=self.tmp_path,
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_no_patterns_no_candidate(self):
        result = self.gen.find_candidate([])
        self.assertIsNone(result)

    def test_weak_pattern_filtered(self):
        """Паттерн с confidence < MIN_AUTOMATION_CONFIDENCE → пропуск."""
        pattern = MockSeqPattern(
            ["Chrome"], "Telegram",
            confidence=0.4,  # < 0.7
            occurrences=10,
        )
        result = self.gen.find_candidate([pattern])
        self.assertIsNone(result)

    def test_strong_pattern_found(self):
        """Сильный паттерн → кандидат создаётся."""
        pattern = MockSeqPattern(
            ["Chrome"], "Telegram",
            confidence=0.8,
            occurrences=10,
            lift=2.5,
        )
        result = self.gen.find_candidate([pattern])
        self.assertIsNotNone(result)
        self.assertIn("macro", result)
        self.assertIn("proposal_text", result)

    def test_save_creates_file(self):
        """save_macro создаёт файл."""
        macro = generate_macro_from_sequence(["Chrome", "Telegram"])
        self.assertIsNotNone(macro)

        success = self.gen.save_macro(macro)
        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.tmp_path))

    def test_save_idempotent(self):
        """Повторное сохранение того же ID → отказ."""
        macro = generate_macro_from_sequence(
            ["Chrome", "Telegram"],
            macro_id="test_macro",
        )

        self.gen.save_macro(macro)

        macro2 = generate_macro_from_sequence(
            ["Chrome", "Telegram"],
            macro_id="test_macro",
        )
        success = self.gen.save_macro(macro2)
        self.assertFalse(success)  # дубликат

    def test_save_creates_backup(self):
        """При повторной записи создаётся .bak."""
        m1 = generate_macro_from_sequence(
            ["Chrome", "Telegram"], macro_id="m1"
        )
        self.gen.save_macro(m1)

        m2 = generate_macro_from_sequence(
            ["VS Code", "Discord"], macro_id="m2"
        )
        self.gen.save_macro(m2)

        self.assertTrue(os.path.exists(self.tmp_path + ".bak"))

    def test_delete_macro(self):
        """Удаление макроса."""
        macro = generate_macro_from_sequence(
            ["Chrome", "Telegram"], macro_id="to_delete"
        )
        self.gen.save_macro(macro)

        success = self.gen.delete_macro("to_delete")
        self.assertTrue(success)

        # Проверяем что файл обновлён
        remaining = self.gen.get_all_generated()
        self.assertNotIn("to_delete", remaining)

    def test_delete_nonexistent(self):
        self.assertFalse(self.gen.delete_macro("nope"))

    def test_rate_limit(self):
        """Не больше MAX_AUTO_SUGGESTIONS_PER_DAY."""
        self.gen._suggestions_today = [time.time()] * 10

        pattern = MockSeqPattern(
            ["Chrome"], "Telegram",
            confidence=0.9, occurrences=20, lift=3.0,
        )
        result = self.gen.find_candidate([pattern])
        self.assertIsNone(result)

    def test_duplicate_detection(self):
        """Не предлагать макрос с теми же шагами."""
        # Сохраняем Chrome + Telegram
        macro = generate_macro_from_sequence(
            ["Chrome", "Telegram"], macro_id="existing"
        )
        self.gen.save_macro(macro)

        # Паттерн с теми же targets
        pattern = MockSeqPattern(
            ["Chrome"], "Telegram",
            confidence=0.9, occurrences=20, lift=3.0,
        )
        result = self.gen.find_candidate([pattern])
        self.assertIsNone(result)

    def test_session_candidate(self):
        """find_candidate_from_session работает."""
        records = [
            MockRecord(action="app_switch", target="Chrome", timestamp=100),
            MockRecord(action="app_switch", target="Telegram", timestamp=130),
        ]
        result = self.gen.find_candidate_from_session(records)
        self.assertIsNotNone(result)
        self.assertIn("macro", result)


# ═══════════════════════════════════════════
#  Тесты CreateMacroCommand
# ═══════════════════════════════════════════

class TestCreateMacroCommand(unittest.TestCase):
    """Команда создания макроса."""

    def _make_cmd(self):
        from commands.create_macro import CreateMacroCommand

        mock_ctx = MagicMock()
        mock_ctx.speaker = MagicMock()
        mock_ctx.config = MagicMock()

        # Мок KnowledgeBase
        mock_kb = MagicMock()
        mock_kb.storage = MagicMock()
        mock_kb.macro_generator = MagicMock()
        mock_ctx.knowledge = mock_kb

        return CreateMacroCommand(mock_ctx)

    def test_match(self):
        cmd = self._make_cmd()
        matched, _ = cmd.match("создай макрос")
        self.assertTrue(matched)

    def test_match_alternative(self):
        cmd = self._make_cmd()
        matched, _ = cmd.match("запомни эти действия")
        self.assertTrue(matched)

    def test_no_match(self):
        cmd = self._make_cmd()
        matched, _ = cmd.match("какая погода")
        self.assertFalse(matched)

    def test_extract_params_default(self):
        cmd = self._make_cmd()
        params = cmd.extract_params("создай макрос")
        self.assertEqual(params["hours"], 1.0)

    def test_extract_params_today(self):
        cmd = self._make_cmd()
        params = cmd.extract_params("создай макрос из того что делал сегодня")
        self.assertEqual(params["hours"], 24.0)

    def test_is_valid(self):
        cmd = self._make_cmd()
        self.assertTrue(cmd.is_valid_candidate({}))

    def test_is_not_valid_no_kb(self):
        from commands.create_macro import CreateMacroCommand
        mock_ctx = MagicMock()
        mock_ctx.knowledge = None
        mock_ctx.speaker = MagicMock()
        mock_ctx.config = MagicMock()
        cmd = CreateMacroCommand(mock_ctx)
        self.assertFalse(cmd.is_valid_candidate({}))


if __name__ == "__main__":
    unittest.main()