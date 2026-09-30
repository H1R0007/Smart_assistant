# commands/math_cmd.py

"""
Математические вычисления.

v3.1:
- Безопасный AST-парсер вместо eval
- Проценты ("20 процентов от 500")
- Конвертация единиц (км → мили, кг → фунты)
- Более умный парсинг выражений
"""

import ast
import re
import operator
import logging

from commands.base import BaseCommand, CommandResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Безопасный вычислитель
# ═══════════════════════════════════════════

class SafeCalculator:
    """
    Вычислитель математических выражений через AST.

    Поддерживает: +, -, *, /, **, ()
    НЕ поддерживает: вызовы функций, импорты, атрибуты

    Безопасность: парсит AST дерево и вычисляет рекурсивно,
    никогда не вызывает eval/exec.
    """

    # Допустимые бинарные операторы
    OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
    }

    # Допустимые унарные операторы
    UNARY_OPERATORS = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    @staticmethod
    def calculate(expression: str) -> float | int:
        """
        Вычислить математическое выражение безопасно.

        Args:
            expression: строка вида "2 + 2 * 3"

        Returns:
            Результат вычисления

        Raises:
            ValueError: если выражение невалидно или небезопасно
        """
        if not expression or not expression.strip():
            raise ValueError("Пустое выражение")

        # Убираем пробелы для парсинга
        expr = expression.strip()

        try:
            tree = ast.parse(expr, mode='eval')
        except SyntaxError as e:
            raise ValueError(f"Синтаксическая ошибка: {e}")

        return SafeCalculator._eval_node(tree.body)

    @staticmethod
    def _eval_node(node) -> float | int:
        """Рекурсивное вычисление AST-ноды."""
        # Число
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"Недопустимая константа: {node.value}")

        # Бинарная операция
        if isinstance(node, ast.BinOp):
            op_func = SafeCalculator.OPERATORS.get(type(node.op))
            if op_func is None:
                raise ValueError(
                    f"Недопустимая операция: {type(node.op).__name__}"
                )

            left = SafeCalculator._eval_node(node.left)
            right = SafeCalculator._eval_node(node.right)

            # Защита от деления на ноль
            if isinstance(node.op, (ast.Div, ast.FloorDiv)) and right == 0:
                raise ValueError("Деление на ноль")

            # Защита от слишком больших степеней
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("Слишком большая степень")

            return op_func(left, right)

        # Унарная операция (-5, +3)
        if isinstance(node, ast.UnaryOp):
            op_func = SafeCalculator.UNARY_OPERATORS.get(type(node.op))
            if op_func is None:
                raise ValueError(
                    f"Недопустимая операция: {type(node.op).__name__}"
                )
            operand = SafeCalculator._eval_node(node.operand)
            return op_func(operand)

        # Всё остальное — запрещено
        raise ValueError(
            f"Недопустимая конструкция: {type(node).__name__}"
        )


# ═══════════════════════════════════════════
#  Конвертация единиц
# ═══════════════════════════════════════════

CONVERSIONS = {
    # (из, в): множитель
    ("км", "мили"): 0.621371,
    ("мили", "км"): 1.60934,
    ("м", "футы"): 3.28084,
    ("футы", "м"): 0.3048,
    ("кг", "фунты"): 2.20462,
    ("фунты", "кг"): 0.453592,
    ("литры", "галлоны"): 0.264172,
    ("галлоны", "литры"): 3.78541,
    ("цельсий", "фаренгейт"): None,  # Специальная формула
    ("фаренгейт", "цельсий"): None,
    ("см", "дюймы"): 0.393701,
    ("дюймы", "см"): 2.54,
}

UNIT_ALIASES = {
    "километров": "км", "километр": "км", "километры": "км",
    "миль": "мили", "миля": "мили",
    "метров": "м", "метр": "м", "метры": "м",
    "футов": "футы", "фут": "футы",
    "килограмм": "кг", "килограммов": "кг",
    "фунтов": "фунты", "фунт": "фунты",
    "литров": "литры", "литр": "литры",
    "галлонов": "галлоны", "галлон": "галлоны",
    "градусов цельсия": "цельсий", "цельсия": "цельсий",
    "градусов фаренгейта": "фаренгейт", "фаренгейта": "фаренгейт",
    "сантиметров": "см", "сантиметр": "см",
    "дюймов": "дюймы", "дюйм": "дюймы",
}


def _try_convert(text: str) -> str | None:
    """
    Попробовать конвертировать единицы.

    "100 км в мили" → "100 километров = 62.14 миль"
    "30 градусов цельсия в фаренгейт" → "30°C = 86°F"
    """
    # Паттерн: число единица в единицу
    pattern = r'(\d+(?:\.\d+)?)\s+(.+?)\s+в\s+(.+)'
    m = re.search(pattern, text.lower())
    if not m:
        return None

    value = float(m.group(1))
    from_raw = m.group(2).strip()
    to_raw = m.group(3).strip()

    # Нормализуем единицы
    from_unit = UNIT_ALIASES.get(from_raw, from_raw)
    to_unit = UNIT_ALIASES.get(to_raw, to_raw)

    key = (from_unit, to_unit)

    if key not in CONVERSIONS:
        return None

    multiplier = CONVERSIONS[key]

    # Специальные формулы
    if from_unit == "цельсий" and to_unit == "фаренгейт":
        result = value * 9 / 5 + 32
        return f"{value} градусов Цельсия = {result:.1f} градусов Фаренгейта"
    elif from_unit == "фаренгейт" and to_unit == "цельсий":
        result = (value - 32) * 5 / 9
        return f"{value} градусов Фаренгейта = {result:.1f} градусов Цельсия"

    if multiplier:
        result = value * multiplier
        return f"{value} {from_unit} = {result:.2f} {to_unit}"

    return None


# ═══════════════════════════════════════════
#  Команда
# ═══════════════════════════════════════════

class MathCommand(BaseCommand):
    name = "math"
    keywords = [
        "посчитай", "вычисли", "калькулятор", "сколько будет",
        "чему равно", "посчитать",
    ]
    priority = 11

    def extract_params(self, text: str, matched_keyword: str = "") -> dict:
        cleaned = self._clean_text(text, self.keywords + ["пожалуйста"])

        # Проверяем конвертацию
        conversion = _try_convert(cleaned)
        if conversion:
            return {"conversion": conversion}

        # Проверяем проценты
        percent = self._try_percent(cleaned)
        if percent:
            return {"result": percent}

        # Обычное выражение
        expr = self._text_to_expression(cleaned)
        return {"expression": expr, "raw": cleaned}

    def execute(self, text: str, params: dict) -> CommandResult:
        # Конвертация
        conversion = params.get("conversion")
        if conversion:
            self.speaker.say(conversion)
            return CommandResult(follow_up=True)

        # Готовый результат (проценты)
        result = params.get("result")
        if result:
            self.speaker.say(result)
            return CommandResult(follow_up=True)

        # Вычисление выражения
        expr = params.get("expression", "")
        raw = params.get("raw", expr)

        if not expr:
            self.speaker.say("Не поняла, что посчитать.")
            return CommandResult()

        try:
            result = SafeCalculator.calculate(expr)

            # Форматирование
            if isinstance(result, float):
                if result == int(result):
                    result = int(result)
                else:
                    result = round(result, 4)

            self.speaker.say(f"{raw} равно {result}.")

        except ValueError as e:
            self.logger.debug("Math error: %s (expr=%s)", e, expr)
            self.speaker.say("Не удалось посчитать.")
        except Exception as e:
            self.logger.error("Math unexpected: %s", e)
            self.speaker.say("Ошибка вычисления.")

        return CommandResult(follow_up=True)

    @staticmethod
    def _text_to_expression(text: str) -> str:
        """Преобразовать текст в математическое выражение."""
        replacements = [
            # Порядок важен: длинные первыми
            ("умножить на", "*"),
            ("разделить на", "/"),
            ("делить на", "/"),
            ("поделить на", "/"),
            ("в степени", "**"),
            ("степень", "**"),
            ("плюс", "+"),
            ("минус", "-"),
            ("умножить", "*"),
            ("разделить", "/"),
            ("делить", "/"),
            ("на", "*"),  # "два на два" = 2*2
        ]

        expr = text
        for word, op in replacements:
            expr = expr.replace(word, f" {op} ")

        # Убираем всё кроме цифр, операторов, скобок, точки
        expr = re.sub(r'[^0-9+\-*/.()\s]', '', expr).strip()

        # Убираем лишние пробелы вокруг операторов
        expr = re.sub(r'\s+', ' ', expr)

        return expr

    @staticmethod
    def _try_percent(text: str) -> str | None:
        """
        Вычислить проценты.

        "20 процентов от 500" → "20% от 500 = 100"
        "сколько процентов 50 от 200" → "50 от 200 = 25%"
        """
        # "N процентов от M"
        m = re.search(
            r'(\d+(?:\.\d+)?)\s*процент\w*\s+от\s+(\d+(?:\.\d+)?)',
            text
        )
        if m:
            percent = float(m.group(1))
            base = float(m.group(2))
            result = base * percent / 100
            result = int(result) if result == int(result) else round(result, 2)
            return f"{percent} процентов от {base} равно {result}."

        # "сколько процентов N от M"
        m = re.search(
            r'сколько\s+процент\w*\s+(\d+(?:\.\d+)?)\s+от\s+(\d+(?:\.\d+)?)',
            text
        )
        if m:
            part = float(m.group(1))
            whole = float(m.group(2))
            if whole == 0:
                return "Деление на ноль."
            result = (part / whole) * 100
            result = int(result) if result == int(result) else round(result, 2)
            return f"{part} от {whole} это {result} процентов."

        return None