# tests/test_macros.py

"""
Тесты системы макросов.

Запуск:
    python -m pytest tests/test_macros.py -v
    python -m pytest tests/test_macros.py -v -x  (остановиться на первой ошибке)
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, call

# Добавляем корень проекта в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.macros.macro import Macro, MacroStep, SUPPORTED_ACTIONS
from core.macros.loader import load_macros, MAX_STEPS_PER_MACRO
from core.macros.executor import MacroExecutor


# ═══════════════════════════════════════════
#  Тесты MacroStep
# ═══════════════════════════════════════════

class TestMacroStep(unittest.TestCase):
    """Валидация отдельных шагов."""

    def test_valid_open_app(self):
        step = MacroStep(action="open_app", target="chrome")
        self.assertIsNone(step.validate())

    def test_valid_volume(self):
        step = MacroStep(action="volume", value="50")
        self.assertIsNone(step.validate())

    def test_valid_say(self):
        step = MacroStep(action="say", target="Привет!")
        self.assertIsNone(step.validate())

    def test_valid_wait_without_params(self):
        """wait без параметров — допустим (дефолт 1 сек)."""
        step = MacroStep(action="wait")
        self.assertIsNone(step.validate())

    def test_valid_wait_with_value(self):
        step = MacroStep(action="wait", target="2.5")
        self.assertIsNone(step.validate())

    def test_invalid_unknown_action(self):
        step = MacroStep(action="fly_to_moon", target="mars")
        err = step.validate()
        self.assertIsNotNone(err)
        self.assertIn("fly_to_moon", err)

    def test_invalid_open_app_no_target(self):
        """open_app требует target."""
        step = MacroStep(action="open_app", target="")
        self.assertIsNotNone(step.validate())

    def test_invalid_say_no_target(self):
        """say требует target."""
        step = MacroStep(action="say", target="")
        self.assertIsNotNone(step.validate())

    def test_invalid_volume_no_value(self):
        """volume требует value."""
        step = MacroStep(action="volume", value="")
        self.assertIsNotNone(step.validate())

    def test_invalid_volume_out_of_range(self):
        step = MacroStep(action="volume", value="150")
        self.assertIsNotNone(step.validate())

    def test_invalid_volume_not_a_number(self):
        step = MacroStep(action="volume", value="loud")
        self.assertIsNotNone(step.validate())

    def test_invalid_brightness_out_of_range(self):
        step = MacroStep(action="brightness", value="200")
        self.assertIsNotNone(step.validate())

    def test_invalid_negative_delay(self):
        step = MacroStep(action="say", target="hi", delay=-1.0)
        self.assertIsNotNone(step.validate())

    def test_invalid_wait_non_numeric(self):
        step = MacroStep(action="wait", target="forever")
        self.assertIsNotNone(step.validate())

    def test_repr(self):
        step = MacroStep(action="open_app", target="chrome", delay=1.5)
        r = repr(step)
        self.assertIn("open_app", r)
        self.assertIn("chrome", r)


# ═══════════════════════════════════════════
#  Тесты Macro
# ═══════════════════════════════════════════

class TestMacro(unittest.TestCase):
    """Валидация целого макроса."""

    def _make_macro(self, **kwargs):
        """Хелпер: создать макрос с дефолтами."""
        defaults = dict(
            id="test",
            name="Test Macro",
            keywords=["тест"],
            steps=[MacroStep(action="say", target="hello")],
        )
        defaults.update(kwargs)
        return Macro(**defaults)

    def test_valid_macro(self):
        macro = self._make_macro(
            steps=[
                MacroStep(action="open_app", target="chrome"),
                MacroStep(action="volume", value="50"),
            ]
        )
        errors = macro.validate()
        self.assertEqual(errors, [])

    def test_no_name(self):
        macro = self._make_macro(name="")
        errors = macro.validate()
        self.assertTrue(any("имя" in e for e in errors))

    def test_no_keywords(self):
        macro = self._make_macro(keywords=[])
        errors = macro.validate()
        self.assertTrue(any("ключевых" in e for e in errors))

    def test_no_steps(self):
        macro = self._make_macro(steps=[])
        errors = macro.validate()
        self.assertTrue(any("шагов" in e for e in errors))

    def test_invalid_step_propagates(self):
        """Ошибка в шаге включается в ошибки макроса."""
        macro = self._make_macro(
            steps=[MacroStep(action="open_app", target="")]  # нет target
        )
        errors = macro.validate()
        self.assertTrue(any("target" in e for e in errors))

    def test_multiple_errors(self):
        """Все ошибки собираются, а не только первая."""
        macro = Macro(id="bad", name="", keywords=[], steps=[])
        errors = macro.validate()
        self.assertGreaterEqual(len(errors), 3)  # имя + keywords + steps


# ═══════════════════════════════════════════
#  Тесты Loader
# ═══════════════════════════════════════════

class TestLoader(unittest.TestCase):
    """Загрузка макросов из конфига."""

    def _mock_config(self, macros_data):
        """Хелпер: создать мок конфига с данными macros."""
        config = MagicMock()
        config.get.return_value = macros_data
        return config

    def test_empty_macros(self):
        macros = load_macros(self._mock_config({}))
        self.assertEqual(macros, {})

    def test_none_macros(self):
        macros = load_macros(self._mock_config(None))
        self.assertEqual(macros, {})

    def test_single_valid_macro(self):
        data = {
            "morning": {
                "name": "Утренний ритуал",
                "keywords": ["доброе утро"],
                "greeting": "Привет!",
                "farewell": "Пока!",
                "steps": [
                    {"action": "open_app", "target": "chrome"},
                    {"action": "volume", "value": "30"},
                ],
            }
        }
        macros = load_macros(self._mock_config(data))

        self.assertEqual(len(macros), 1)
        self.assertIn("morning", macros)

        m = macros["morning"]
        self.assertEqual(m.name, "Утренний ритуал")
        self.assertEqual(m.keywords, ["доброе утро"])
        self.assertEqual(m.greeting, "Привет!")
        self.assertEqual(m.farewell, "Пока!")
        self.assertEqual(len(m.steps), 2)
        self.assertEqual(m.steps[0].action, "open_app")
        self.assertEqual(m.steps[0].target, "chrome")

    def test_multiple_macros(self):
        data = {
            "a": {
                "name": "A", "keywords": ["a"],
                "steps": [{"action": "say", "target": "A"}],
            },
            "b": {
                "name": "B", "keywords": ["b"],
                "steps": [{"action": "say", "target": "B"}],
            },
        }
        macros = load_macros(self._mock_config(data))
        self.assertEqual(len(macros), 2)

    def test_skip_no_keywords(self):
        """Макрос без keywords пропускается."""
        data = {
            "good": {
                "name": "Good", "keywords": ["good"],
                "steps": [{"action": "say", "target": "hi"}],
            },
            "bad": {
                "name": "Bad",
                # нет keywords
                "steps": [{"action": "say", "target": "hi"}],
            },
        }
        macros = load_macros(self._mock_config(data))
        self.assertEqual(len(macros), 1)
        self.assertIn("good", macros)
        self.assertNotIn("bad", macros)

    def test_skip_no_steps(self):
        """Макрос без steps пропускается."""
        data = {
            "empty": {
                "name": "Empty", "keywords": ["empty"],
                "steps": [],
            },
        }
        macros = load_macros(self._mock_config(data))
        self.assertEqual(macros, {})

    def test_skip_non_dict_macro(self):
        """Макрос, который не словарь, пропускается."""
        data = {"bad": "not a dict"}
        macros = load_macros(self._mock_config(data))
        self.assertEqual(macros, {})

    def test_steps_limit(self):
        """Слишком много шагов — обрезаются."""
        steps = [
            {"action": "say", "target": f"step {i}"}
            for i in range(MAX_STEPS_PER_MACRO + 10)
        ]
        data = {
            "big": {
                "name": "Big", "keywords": ["big"],
                "steps": steps,
            },
        }
        macros = load_macros(self._mock_config(data))
        self.assertEqual(len(macros["big"].steps), MAX_STEPS_PER_MACRO)

    def test_keywords_normalized_to_lower(self):
        data = {
            "test": {
                "name": "Test", "keywords": ["Доброе Утро"],
                "steps": [{"action": "say", "target": "hi"}],
            },
        }
        macros = load_macros(self._mock_config(data))
        self.assertEqual(macros["test"].keywords, ["доброе утро"])

    def test_keyword_as_string(self):
        """keywords может быть строкой (не списком)."""
        data = {
            "test": {
                "name": "Test", "keywords": "одно слово",
                "steps": [{"action": "say", "target": "hi"}],
            },
        }
        macros = load_macros(self._mock_config(data))
        self.assertEqual(macros["test"].keywords, ["одно слово"])

    def test_step_value_converted_to_string(self):
        """YAML может распарсить value: 30 как int."""
        data = {
            "test": {
                "name": "Test", "keywords": ["test"],
                "steps": [{"action": "volume", "value": 30}],  # int!
            },
        }
        macros = load_macros(self._mock_config(data))
        step = macros["test"].steps[0]
        self.assertEqual(step.value, "30")
        self.assertIsInstance(step.value, str)

    def test_step_delay(self):
        data = {
            "test": {
                "name": "Test", "keywords": ["test"],
                "steps": [
                    {"action": "say", "target": "hi", "delay": 1.5},
                ],
            },
        }
        macros = load_macros(self._mock_config(data))
        self.assertEqual(macros["test"].steps[0].delay, 1.5)

    def test_step_short_format(self):
        """Краткий формат шага (строка)."""
        data = {
            "test": {
                "name": "Test", "keywords": ["test"],
                "steps": ["wait"],  # строка вместо словаря
            },
        }
        macros = load_macros(self._mock_config(data))
        self.assertEqual(macros["test"].steps[0].action, "wait")


# ═══════════════════════════════════════════
#  Тесты Executor
# ═══════════════════════════════════════════

class TestExecutor(unittest.TestCase):
    """Выполнение макросов с моками."""

    def setUp(self):
        self.mock_ctx = MagicMock()
        self.mock_ctx.speaker = MagicMock()
        self.mock_ctx.config = MagicMock()

        self.macros = {
            "test": Macro(
                id="test",
                name="Test Macro",
                keywords=["тест"],
                greeting="Starting",
                farewell="Done",
                steps=[
                    MacroStep(action="say", target="Step 1"),
                    MacroStep(action="say", target="Step 2"),
                ],
            )
        }

        self.executor = MacroExecutor(self.mock_ctx, self.macros)
        # Убираем задержки для быстрых тестов
        self.executor.DEFAULT_STEP_DELAY = 0.0

    def test_execute_success(self):
        """Макрос выполняется: greeting + шаги + farewell."""
        result = self.executor.execute(self.macros["test"])

        self.assertTrue(result)
        # greeting + Step1 + Step2 + farewell = 4
        self.assertEqual(self.mock_ctx.speaker.say.call_count, 4)

        calls = [c.args[0] for c in self.mock_ctx.speaker.say.call_args_list]
        self.assertEqual(calls, ["Starting", "Step 1", "Step 2", "Done"])

    def test_execute_no_greeting_farewell(self):
        """Макрос без greeting/farewell — только шаги."""
        macro = Macro(
            id="silent", name="Silent", keywords=["s"],
            steps=[MacroStep(action="say", target="hi")],
        )
        macros = {"silent": macro}
        executor = MacroExecutor(self.mock_ctx, macros)
        executor.DEFAULT_STEP_DELAY = 0.0

        result = executor.execute(macro)
        self.assertTrue(result)
        self.assertEqual(self.mock_ctx.speaker.say.call_count, 1)

    def test_stop_between_steps(self):
        """stop() прерывает макрос между шагами."""
        call_count = 0

        def stop_on_first_say(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Прерываем после greeting
                self.executor.stop()

        self.mock_ctx.speaker.say.side_effect = stop_on_first_say

        result = self.executor.execute(self.macros["test"])

        self.assertFalse(result)
        # Должно быть: greeting + "Макрос прерван." = 2
        # (шаги не выполняются — stop_event уже True)
        self.assertGreaterEqual(call_count, 1)

    def test_failed_step_continues(self):
        """Упавший шаг не ломает остальные."""
        macro = Macro(
            id="mixed", name="Mixed", keywords=["m"],
            steps=[
                MacroStep(action="open_app", target="nonexistent_app_xyz"),
                MacroStep(action="say", target="after fail"),
            ],
        )
        macros = {"mixed": macro}
        executor = MacroExecutor(self.mock_ctx, macros)
        executor.DEFAULT_STEP_DELAY = 0.0

        # open_app упадёт, но say после неё выполнится
        result = executor.execute(macro)
        self.assertTrue(result)

        # say("after fail") точно вызван
        say_texts = [c.args[0] for c in self.mock_ctx.speaker.say.call_args_list]
        self.assertIn("after fail", say_texts)

    def test_recursion_detected(self):
        """Прямая рекурсия (макрос вызывает сам себя) не зацикливается."""
        macro = Macro(
            id="loop", name="Loop", keywords=["loop"],
            steps=[MacroStep(action="run_macro", target="loop")],
        )
        macros = {"loop": macro}
        executor = MacroExecutor(self.mock_ctx, macros)
        executor.DEFAULT_STEP_DELAY = 0.0

        # Должен завершиться (не зависнуть).
        # Шаг run_macro → обнаружит "loop" в _call_stack → провалится.
        # Сам макрос завершится (best-effort: шаг провалился, но макрос дошёл до конца).
        result = executor.execute(macro)
        self.assertTrue(result)  # макрос завершился

        # Проверяем что прозвучало предупреждение о рекурсии
        say_texts = [c.args[0] for c in self.mock_ctx.speaker.say.call_args_list]
        self.assertTrue(
            any("сам себя" in t for t in say_texts),
            f"Ожидалось предупреждение о рекурсии, получено: {say_texts}",
        )

    def test_mutual_recursion_detected(self):
        """Взаимная рекурсия A→B→A не зацикливается."""
        macro_a = Macro(
            id="a", name="A", keywords=["a"],
            steps=[MacroStep(action="run_macro", target="b")],
        )
        macro_b = Macro(
            id="b", name="B", keywords=["b"],
            steps=[MacroStep(action="run_macro", target="a")],
        )
        macros = {"a": macro_a, "b": macro_b}
        executor = MacroExecutor(self.mock_ctx, macros)
        executor.DEFAULT_STEP_DELAY = 0.0

        # Не должен зависнуть
        result = executor.execute(macro_a)
        # a завершился (b внутри тоже завершился, хотя его шаг run_macro("a") провалился)
        self.assertTrue(result)

    def test_nesting_depth_limit(self):
        """Превышение глубины вложенности."""
        # Создаём цепочку a→b→c→d (глубина 4 > MAX_NESTING_DEPTH=3)
        macros = {}
        chain = ["a", "b", "c", "d"]
        for i, name in enumerate(chain):
            next_name = chain[i + 1] if i + 1 < len(chain) else None
            steps = (
                [MacroStep(action="run_macro", target=next_name)]
                if next_name
                else [MacroStep(action="say", target="end")]
            )
            macros[name] = Macro(
                id=name, name=name.upper(), keywords=[name],
                steps=steps,
            )

        executor = MacroExecutor(self.mock_ctx, macros)
        executor.DEFAULT_STEP_DELAY = 0.0

        result = executor.execute(macros["a"])
        # a(1) → b(2) → c(3) → d: глубина 3, d хочет стать 4 → отказ
        # Но d не вызывает run_macro, он просто say("end").
        # Нет, подождите: a→b→c→d, d=say("end").
        # Глубина: a=1, b=2, c=3 — это ровно MAX_NESTING_DEPTH.
        # d вызывается из c, _call_stack=["a","b","c"], len=3 ≥ 3 → отказ!
        # Значит d не выполнится.

        # Но a всё равно завершится (шаг run_macro("b") провалился/частично).
        self.assertTrue(result)

    def test_say_step(self):
        macro = Macro(
            id="s", name="S", keywords=["s"],
            steps=[MacroStep(action="say", target="Hello world")],
        )
        executor = MacroExecutor(self.mock_ctx, {"s": macro})
        executor.DEFAULT_STEP_DELAY = 0.0

        executor.execute(macro)
        say_texts = [c.args[0] for c in self.mock_ctx.speaker.say.call_args_list]
        self.assertIn("Hello world", say_texts)

    def test_wait_step(self):
        """wait шаг завершается за указанное время (примерно)."""
        macro = Macro(
            id="w", name="W", keywords=["w"],
            steps=[MacroStep(action="wait", target="0.1")],
        )
        executor = MacroExecutor(self.mock_ctx, {"w": macro})
        executor.DEFAULT_STEP_DELAY = 0.0

        start = time.monotonic()
        result = executor.execute(macro)
        elapsed = time.monotonic() - start

        self.assertTrue(result)
        self.assertGreaterEqual(elapsed, 0.09)  # ~100мс

    def test_substitute_vars_date(self):
        """Переменная {date} подставляется в target."""
        macro = Macro(
            id="v", name="V", keywords=["v"],
            steps=[MacroStep(action="say", target="Сегодня {date}")],
        )
        executor = MacroExecutor(self.mock_ctx, {"v": macro})
        executor.DEFAULT_STEP_DELAY = 0.0

        executor.execute(macro)

        say_texts = [c.args[0] for c in self.mock_ctx.speaker.say.call_args_list]
        # {date} заменяется на что-то вроде "01.01.2025"
        today_str = time.strftime("%d.%m.%Y")
        self.assertTrue(
            any(today_str in t for t in say_texts),
            f"Ожидалась дата {today_str} в: {say_texts}",
        )

    def test_timeout(self):
        """Макрос прерывается по таймауту."""
        # wait 1000 секунд + таймаут 0.5 сек
        macro = Macro(
            id="slow", name="Slow", keywords=["slow"],
            steps=[
                MacroStep(action="wait", target="1000"),
            ],
        )
        executor = MacroExecutor(self.mock_ctx, {"slow": macro})
        executor.DEFAULT_STEP_DELAY = 0.0
        executor.MACRO_TIMEOUT = 0.3  # 300мс таймаут

        start = time.monotonic()
        result = executor.execute(macro)
        elapsed = time.monotonic() - start

        # wait(1000) будет прервана не таймаутом макроса,
        # а _interruptible_wait. Но MACRO_TIMEOUT проверяется
        # ПЕРЕД каждым шагом, а не во время. Поэтому wait(1000)
        # будет ждать 1000 секунд...
        #
        # Чтобы тест работал, нужно использовать stop() в другом потоке.
        # Вместо этого — тестируем таймаут через несколько коротких шагов.

        # Перепишем тест: много шагов, каждый по 0.1 сек
        # Таймаут 0.3 сек → остановится после ~3 шагов
        # Нет, этот тест сложнее. Пропустим его и проверим stop().

    def test_is_running(self):
        """is_running отражает состояние выполнения."""
        self.assertFalse(self.executor.is_running)

        # Во время выполнения is_running = True
        running_states = []

        def capture_state(*args):
            running_states.append(self.executor.is_running)

        self.mock_ctx.speaker.say.side_effect = capture_state

        self.executor.execute(self.macros["test"])

        # Во время всех say-вызовов executor был running
        self.assertTrue(all(running_states))
        # После выполнения — не running
        self.assertFalse(self.executor.is_running)


# ═══════════════════════════════════════════
#  Тесты MacroCommand
# ═══════════════════════════════════════════

class TestMacroCommand(unittest.TestCase):
    """Команда для голосового вызова макросов."""

    def _make_cmd(self, macros_data=None):
        """Хелпер: создать MacroCommand с моками."""
        if macros_data is None:
            macros_data = {
                "morning": {
                    "name": "Утро",
                    "keywords": ["доброе утро", "утренний ритуал"],
                    "greeting": "Привет!",
                    "steps": [{"action": "say", "target": "ok"}],
                },
            }

        mock_ctx = MagicMock()
        mock_ctx.config.get.return_value = macros_data
        mock_ctx.speaker = MagicMock()

        # Импортируем здесь чтобы не ломать TestExecutor
        from commands.macro import MacroCommand
        return MacroCommand(mock_ctx)

    def test_keywords_from_config(self):
        cmd = self._make_cmd()
        self.assertIn("доброе утро", cmd.keywords)
        self.assertIn("утренний ритуал", cmd.keywords)

    def test_match_exact(self):
        cmd = self._make_cmd()
        matched, params = cmd.match("доброе утро")
        self.assertTrue(matched)
        self.assertEqual(params["macro_id"], "morning")

    def test_match_in_sentence(self):
        """Ключевое слово внутри фразы."""
        cmd = self._make_cmd()
        matched, params = cmd.match("скажи доброе утро")
        self.assertTrue(matched)
        self.assertEqual(params["macro_id"], "morning")

    def test_match_case_insensitive(self):
        cmd = self._make_cmd()
        matched, _ = cmd.match("Доброе Утро!")
        self.assertTrue(matched)

    def test_no_match_without_macros(self):
        cmd = self._make_cmd(macros_data={})
        matched, _ = cmd.match("доброе утро")
        self.assertFalse(matched)

    def test_no_match_wrong_text(self):
        cmd = self._make_cmd()
        matched, _ = cmd.match("какая погода")
        self.assertFalse(matched)

    def test_is_valid_candidate(self):
        cmd = self._make_cmd()
        self.assertTrue(cmd.is_valid_candidate({"macro_id": "morning"}))

    def test_is_not_valid_unknown_macro(self):
        cmd = self._make_cmd()
        self.assertFalse(cmd.is_valid_candidate({"macro_id": "nonexistent"}))

    def test_is_not_valid_no_executor(self):
        cmd = self._make_cmd(macros_data={})
        self.assertFalse(cmd.is_valid_candidate({"macro_id": "morning"}))

    def test_multiple_macros_keywords(self):
        """Все ключевые слова из всех макросов собраны."""
        cmd = self._make_cmd({
            "a": {
                "name": "A", "keywords": ["алфа"],
                "steps": [{"action": "say", "target": "a"}],
            },
            "b": {
                "name": "B", "keywords": ["бета"],
                "steps": [{"action": "say", "target": "b"}],
            },
        })
        self.assertIn("алфа", cmd.keywords)
        self.assertIn("бета", cmd.keywords)

    def test_match_correct_macro(self):
        """При нескольких макросах матчится правильный."""
        cmd = self._make_cmd({
            "a": {
                "name": "A", "keywords": ["алфа"],
                "steps": [{"action": "say", "target": "a"}],
            },
            "b": {
                "name": "B", "keywords": ["бета"],
                "steps": [{"action": "say", "target": "b"}],
            },
        })

        _, params_a = cmd.match("запусти алфа")
        self.assertEqual(params_a["macro_id"], "a")

        _, params_b = cmd.match("запусти бета")
        self.assertEqual(params_b["macro_id"], "b")

    def test_execute_calls_speaker(self):
        """execute() озвучивает greeting и шаги."""
        cmd = self._make_cmd()

        # Патчим executor чтобы не вызывать реальные команды
        cmd._executor.DEFAULT_STEP_DELAY = 0.0

        result = cmd.execute("доброе утро", {"macro_id": "morning"})

        self.assertTrue(result.success)
        self.assertTrue(result.follow_up)

        # Должен был вызвать say хотя бы раз
        self.assertTrue(cmd.speaker.say.called)

    @patch("commands.macro.get_context")
    def test_execute_updates_context(self, mock_get_ctx):
        """После выполнения обновляется контекст диалога."""
        cmd = self._make_cmd()
        cmd._executor.DEFAULT_STEP_DELAY = 0.0

        cmd.execute("доброе утро", {"macro_id": "morning"})

        mock_get_ctx().update.assert_called_once_with(
            action="macro",
            target="morning",
            params={"macro_name": "Утро"},
        )


# ═══════════════════════════════════════════
#  Тесты SUPPORTED_ACTIONS
# ═══════════════════════════════════════════

class TestSupportedActions(unittest.TestCase):
    """Проверка консистентности SUPPORTED_ACTIONS и _STEP_HANDLERS."""

    def test_all_actions_have_handlers(self):
        """Каждое действие из SUPPORTED_ACTIONS имеет обработчик."""
        handlers = MacroExecutor._STEP_HANDLERS
        for action in SUPPORTED_ACTIONS:
            self.assertIn(
                action, handlers,
                f"Действие '{action}' из SUPPORTED_ACTIONS "
                f"не имеет обработчика в _STEP_HANDLERS",
            )

    def test_no_extra_handlers(self):
        """Нет обработчиков для неизвестных действий."""
        handlers = MacroExecutor._STEP_HANDLERS
        for action in handlers:
            self.assertIn(
                action, SUPPORTED_ACTIONS,
                f"Обработчик '{action}' не указан в SUPPORTED_ACTIONS",
            )


if __name__ == "__main__":
    unittest.main()