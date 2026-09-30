# tests/test_macros/test_auto_generator.py

"""
Тесты MacroAutoGenerator.

Покрытие:
- find_candidate_from_session: базовый, фильтрация,
  дедупликация, порядок
- _is_duplicate: точный, frozenset, wait-шаги
- save_macro: создание, бэкап, отказ дубликата
"""

import time
import os

import yaml
from unittest.mock import MagicMock

from core.macros.auto_generator import MacroAutoGenerator
from tests.test_macros.helpers import MockActivityRecord


# ═══════════════════════════════════════════
#  find_candidate_from_session
# ═══════════════════════════════════════════

class TestFindCandidateFromSession:
    """Тесты генерации кандидата из сессии."""

    def setup_method(self):
        self.storage = MagicMock()
        self.gen = MacroAutoGenerator(self.storage)

    def test_basic_session_creates_candidate(self):
        """Из 3 приложений создаётся валидный кандидат."""
        now = time.time()
        records = [
            MockActivityRecord(
                target="Chrome", timestamp=now - 300,
            ),
            MockActivityRecord(
                target="VS Code", timestamp=now - 200,
            ),
            MockActivityRecord(
                target="Terminal", timestamp=now - 100,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records, time_hint="утро",
        )

        assert candidate is not None
        assert "macro" in candidate
        assert "proposal_text" in candidate
        assert "explanation" in candidate
        assert candidate["confidence"] == 1.0
        assert candidate["pattern_id"] == "manual_session"

        macro = candidate["macro"]
        assert "name" in macro
        assert "steps" in macro
        assert "keywords" in macro

        # Шаги без wait
        action_steps = [
            s for s in macro["steps"]
            if s["action"] != "wait"
        ]
        assert len(action_steps) == 3
        assert action_steps[0]["target"] == "Chrome"
        assert action_steps[1]["target"] == "VS Code"
        assert action_steps[2]["target"] == "Terminal"

    def test_filters_skip_targets(self):
        """Системные приложения отфильтровываются."""
        now = time.time()
        records = [
            MockActivityRecord(
                target="explorer", timestamp=now - 400,
            ),
            MockActivityRecord(
                target="Chrome", timestamp=now - 300,
            ),
            MockActivityRecord(
                target="desktop", timestamp=now - 200,
            ),
            MockActivityRecord(
                target="VS Code", timestamp=now - 100,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records,
        )

        assert candidate is not None
        macro = candidate["macro"]
        action_steps = [
            s for s in macro["steps"]
            if s["action"] != "wait"
        ]
        targets = [s["target"] for s in action_steps]
        assert "explorer" not in targets
        assert "desktop" not in targets
        assert targets == ["Chrome", "VS Code"]

    def test_deduplicates_consecutive(self):
        """Подряд идущие дубликаты схлопываются."""
        now = time.time()
        records = [
            MockActivityRecord(
                target="Chrome", timestamp=now - 400,
            ),
            MockActivityRecord(
                target="Chrome", timestamp=now - 350,
            ),
            MockActivityRecord(
                target="VS Code", timestamp=now - 200,
            ),
            MockActivityRecord(
                target="VS Code", timestamp=now - 100,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records,
        )

        assert candidate is not None
        macro = candidate["macro"]
        action_steps = [
            s for s in macro["steps"]
            if s["action"] != "wait"
        ]
        assert len(action_steps) == 2

    def test_too_few_steps_returns_none(self):
        """Менее MIN_STEPS → None."""
        now = time.time()
        records = [
            MockActivityRecord(
                target="Chrome", timestamp=now - 100,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records,
        )
        assert candidate is None

    def test_all_skip_targets_returns_none(self):
        """Все приложения системные → None."""
        now = time.time()
        records = [
            MockActivityRecord(
                target="explorer", timestamp=now - 200,
            ),
            MockActivityRecord(
                target="desktop", timestamp=now - 100,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records,
        )
        assert candidate is None

    def test_time_hint_in_name(self):
        """time_hint добавляет префикс к имени."""
        now = time.time()
        records = [
            MockActivityRecord(
                target="Chrome", timestamp=now - 200,
            ),
            MockActivityRecord(
                target="VS Code", timestamp=now - 100,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records, time_hint="утро",
        )

        assert candidate is not None
        assert "утренн" in candidate["macro"]["name"]

    def test_reverse_order_sorted_correctly(self):
        """Records в обратном порядке сортируются."""
        now = time.time()
        records = [
            MockActivityRecord(
                target="Terminal", timestamp=now - 100,
            ),
            MockActivityRecord(
                target="VS Code", timestamp=now - 200,
            ),
            MockActivityRecord(
                target="Chrome", timestamp=now - 300,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records,
        )

        assert candidate is not None
        macro = candidate["macro"]
        action_steps = [
            s for s in macro["steps"]
            if s["action"] != "wait"
        ]
        assert action_steps[0]["target"] == "Chrome"
        assert action_steps[1]["target"] == "VS Code"
        assert action_steps[2]["target"] == "Terminal"

    def test_duplicate_existing_returns_none(
        self, tmp_macros_file,
    ):
        """Дубликат существующего макроса → None."""
        self.gen._generated_path = tmp_macros_file

        # Сохраняем существующий макрос
        # Формат: dict {macro_id: macro_data}
        existing = {
            "existing_macro": {
                "name": "existing",
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Chrome",
                    },
                    {"action": "wait", "seconds": 2},
                    {
                        "action": "open_app",
                        "target": "VS Code",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(existing, f)

        now = time.time()
        records = [
            MockActivityRecord(
                target="Chrome", timestamp=now - 200,
            ),
            MockActivityRecord(
                target="VS Code", timestamp=now - 100,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records,
        )
        assert candidate is None

    def test_macro_has_id_field(self):
        """Сгенерированный макрос содержит поле id."""
        now = time.time()
        records = [
            MockActivityRecord(
                target="Chrome", timestamp=now - 200,
            ),
            MockActivityRecord(
                target="VS Code", timestamp=now - 100,
            ),
        ]

        candidate = self.gen.find_candidate_from_session(
            records,
        )

        assert candidate is not None
        assert "id" in candidate["macro"]
        assert candidate["macro"]["id"].startswith("auto_")


# ═══════════════════════════════════════════
#  _is_duplicate
# ═══════════════════════════════════════════

class TestDuplicateDetection:
    """Тесты определения дубликатов."""

    def setup_method(self):
        self.storage = MagicMock()
        self.gen = MacroAutoGenerator(self.storage)

    def test_exact_duplicate_detected(
        self, tmp_macros_file,
    ):
        """Точное совпадение targets → дубликат."""
        self.gen._generated_path = tmp_macros_file

        existing = {
            "macro1": {
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Chrome",
                    },
                    {
                        "action": "open_app",
                        "target": "VS Code",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(existing, f)

        assert self.gen._is_duplicate(
            ["Chrome", "VS Code"],
        )

    def test_order_independent_duplicate(
        self, tmp_macros_file,
    ):
        """
        Другой порядок тоже дубликат (frozenset).
        Chrome+VSCode ≈ VSCode+Chrome.
        """
        self.gen._generated_path = tmp_macros_file

        existing = {
            "macro1": {
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Chrome",
                    },
                    {
                        "action": "open_app",
                        "target": "VS Code",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(existing, f)

        assert self.gen._is_duplicate(
            ["VS Code", "Chrome"],
        )

    def test_wait_steps_ignored_in_comparison(
        self, tmp_macros_file,
    ):
        """wait-шаги не влияют на сравнение."""
        self.gen._generated_path = tmp_macros_file

        existing = {
            "macro1": {
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Chrome",
                    },
                    {"action": "wait", "seconds": 5},
                    {
                        "action": "open_app",
                        "target": "VS Code",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(existing, f)

        assert self.gen._is_duplicate(
            ["Chrome", "VS Code"],
        )

    def test_different_targets_not_duplicate(
        self, tmp_macros_file,
    ):
        """Разные targets → не дубликат."""
        self.gen._generated_path = tmp_macros_file

        existing = {
            "macro1": {
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Chrome",
                    },
                    {
                        "action": "open_app",
                        "target": "Terminal",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(existing, f)

        assert not self.gen._is_duplicate(
            ["Chrome", "VS Code"],
        )

    def test_empty_existing_not_duplicate(self):
        """Нет файла → не дубликат."""
        assert not self.gen._is_duplicate(
            ["Chrome", "VS Code"],
        )


# ═══════════════════════════════════════════
#  save_macro
# ═══════════════════════════════════════════

class TestSaveMacro:
    """Тесты сохранения макросов."""

    def setup_method(self):
        self.storage = MagicMock()
        self.gen = MacroAutoGenerator(self.storage)

    def test_save_creates_file(self, tmp_macros_file):
        """Сохранение создаёт YAML с корректной структурой."""
        self.gen._generated_path = tmp_macros_file

        macro = {
            "id": "test_macro_1",
            "name": "тестовый макрос",
            "keywords": ["тест"],
            "description": "Chrome → VS Code",
            "steps": [
                {
                    "action": "open_app",
                    "target": "Chrome",
                },
                {"action": "wait", "seconds": 2},
                {
                    "action": "open_app",
                    "target": "VS Code",
                },
            ],
        }

        result = self.gen.save_macro(macro)

        assert result is True
        assert os.path.exists(tmp_macros_file)

        with open(
            tmp_macros_file, "r", encoding="utf-8",
        ) as f:
            data = yaml.safe_load(f)

        # Формат: {macro_id: macro_data}
        assert "test_macro_1" in data
        assert data["test_macro_1"]["name"] == (
            "тестовый макрос"
        )

    def test_save_appends_to_existing(
        self, tmp_macros_file,
    ):
        """Новый макрос добавляется к существующим."""
        self.gen._generated_path = tmp_macros_file

        initial = {
            "old_macro": {
                "name": "старый",
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Notepad",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(initial, f)

        macro = {
            "id": "new_macro",
            "name": "новый",
            "keywords": ["новый"],
            "steps": [
                {
                    "action": "open_app",
                    "target": "Chrome",
                },
                {
                    "action": "open_app",
                    "target": "VS Code",
                },
            ],
        }

        result = self.gen.save_macro(macro)

        assert result is True

        with open(
            tmp_macros_file, "r", encoding="utf-8",
        ) as f:
            data = yaml.safe_load(f)

        assert len(data) == 2
        assert "old_macro" in data
        assert "new_macro" in data

    def test_save_creates_backup(self, tmp_macros_file):
        """При сохранении создаётся бэкап."""
        self.gen._generated_path = tmp_macros_file

        initial = {
            "old_macro": {
                "name": "old",
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Notepad",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(initial, f)

        macro = {
            "id": "new_macro",
            "name": "new",
            "keywords": ["new"],
            "steps": [
                {
                    "action": "open_app",
                    "target": "Chrome",
                },
                {
                    "action": "open_app",
                    "target": "VS Code",
                },
            ],
        }

        result = self.gen.save_macro(macro)

        assert result is True

        backup_path = tmp_macros_file + ".bak"
        assert os.path.exists(backup_path)

        with open(backup_path, "r") as f:
            backup_data = yaml.safe_load(f)
        assert "old_macro" in backup_data
        assert "new_macro" not in backup_data

    def test_save_rejects_duplicate_by_targets(
        self, tmp_macros_file,
    ):
        """Дубликат по шагам не сохраняется."""
        self.gen._generated_path = tmp_macros_file

        existing = {
            "existing": {
                "name": "existing",
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Chrome",
                    },
                    {
                        "action": "open_app",
                        "target": "VS Code",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(existing, f)

        macro = {
            "id": "dupe_macro",
            "name": "duplicate",
            "keywords": ["dupe"],
            "steps": [
                {
                    "action": "open_app",
                    "target": "Chrome",
                },
                {
                    "action": "open_app",
                    "target": "VS Code",
                },
            ],
        }

        result = self.gen.save_macro(macro)

        assert result is False

    def test_save_rejects_duplicate_by_id(
        self, tmp_macros_file,
    ):
        """Дубликат по ID не сохраняется."""
        self.gen._generated_path = tmp_macros_file

        existing = {
            "same_id": {
                "name": "existing",
                "steps": [
                    {
                        "action": "open_app",
                        "target": "Notepad",
                    },
                ],
            },
        }
        with open(tmp_macros_file, "w") as f:
            yaml.dump(existing, f)

        macro = {
            "id": "same_id",
            "name": "another",
            "keywords": ["another"],
            "steps": [
                {
                    "action": "open_app",
                    "target": "Chrome",
                },
                {
                    "action": "open_app",
                    "target": "Terminal",
                },
            ],
        }

        result = self.gen.save_macro(macro)

        assert result is False

    def test_save_generates_id_if_missing(
        self, tmp_macros_file,
    ):
        """Если id нет — генерируется автоматически."""
        self.gen._generated_path = tmp_macros_file

        macro = {
            "name": "no id macro",
            "keywords": ["no id"],
            "steps": [
                {
                    "action": "open_app",
                    "target": "Chrome",
                },
                {
                    "action": "open_app",
                    "target": "VS Code",
                },
            ],
        }

        result = self.gen.save_macro(macro)

        assert result is True

        with open(
            tmp_macros_file, "r", encoding="utf-8",
        ) as f:
            data = yaml.safe_load(f)

        keys = list(data.keys())
        assert len(keys) == 1
        assert keys[0].startswith("auto_")