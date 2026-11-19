"""Bounded, dependency-free utilities for Katherine; no persistent state."""
from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any


_MAX_EXPRESSION_LENGTH = 180
_MAX_AST_NODES = 64
_MAX_ABS_RESULT = 1e100

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log,
    "log10": math.log10,
    "ln": math.log,
    "exp": math.exp,
    "radians": math.radians,
    "degrees": math.degrees,
    "factorial": math.factorial,
    "pow": pow,
}


class _SafeExpression:
    def __init__(self) -> None:
        self.nodes = 0

    def evaluate(self, node: ast.AST) -> int | float:
        self.nodes += 1
        if self.nodes > _MAX_AST_NODES:
            raise ValueError("La operación es demasiado compleja.")

        if isinstance(node, ast.Expression):
            return self.evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("Solo se permiten números.")
            return self._bounded(node.value)
        if isinstance(node, ast.Name):
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            raise ValueError(f"No reconozco la constante «{node.id}».")
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            return self._bounded(_UNARY_OPERATORS[type(node.op)](self.evaluate(node.operand)))
        if isinstance(node, ast.BinOp):
            left = self.evaluate(node.left)
            right = self.evaluate(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(right) > 100 or (abs(left) > 1 and right > 100):
                    raise ValueError("El exponente supera el límite permitido.")
                return self._bounded(operator.pow(left, right))
            operation = _BINARY_OPERATORS.get(type(node.op))
            if operation is None:
                raise ValueError("Ese operador no está permitido.")
            return self._bounded(operation(left, right))
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise ValueError("Esa función matemática no está permitida.")
            if node.keywords:
                raise ValueError("No se permiten argumentos con nombre.")
            arguments = [self.evaluate(item) for item in node.args]
            if len(arguments) > 8:
                raise ValueError("La función admite como máximo ocho argumentos.")
            if node.func.id == "factorial" and (
                len(arguments) != 1 or arguments[0] < 0
                or int(arguments[0]) != arguments[0] or arguments[0] > 170
            ):
                raise ValueError("factorial admite enteros entre 0 y 170.")
            return self._bounded(_FUNCTIONS[node.func.id](*arguments))
        raise ValueError("La expresión contiene una operación no permitida.")

    @staticmethod
    def _bounded(value: Any) -> int | float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("La operación no produjo un número.")
        number = float(value)
        if not math.isfinite(number) or abs(number) > _MAX_ABS_RESULT:
            raise ValueError("El resultado está fuera del rango permitido.")
        if number.is_integer() and abs(number) < 10**15:
            return int(number)
        return number


def _normalize_expression(value: str) -> str:
    expression = str(value or "").strip()
    if not expression:
        raise ValueError("Escribe una operación matemática.")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError("La expresión supera 180 caracteres.")
    expression = expression.replace("×", "*").replace("÷", "/").replace("−", "-")
    expression = expression.replace("^", "**")
    expression = re.sub(r"\bpi\b", "pi", expression, flags=re.IGNORECASE)
    expression = re.sub(r"\braiz\s*\(", "sqrt(", expression, flags=re.IGNORECASE)
    if (
        not re.search(r"[A-Za-z()]|,", expression.replace(",", "", 1))
        and expression.count(",") == 1
        and "." not in expression
    ):
        expression = expression.replace(",", ".")
    percent_of = re.fullmatch(
        r"\s*(-?\d+(?:\.\d+)?)\s*%\s*(?:de|of)\s*(-?\d+(?:\.\d+)?)\s*",
        expression,
        flags=re.IGNORECASE,
    )
    if percent_of:
        percentage = percent_of.group(1)
        amount = percent_of.group(2)
        expression = f"({percentage}/100)*({amount})"
    elif re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*%\s*", expression):
        expression = expression.strip().rstrip("%").strip()
        expression = f"({expression}/100)"

    return expression


def calculate_expression(value: str) -> int | float:
    """Evaluate a tiny mathematical grammar without eval or external processes."""
    expression = _normalize_expression(value)
    try:
        tree = ast.parse(expression, mode="eval")
        return _SafeExpression().evaluate(tree)
    except ZeroDivisionError as exc:
        raise ValueError("No se puede dividir entre cero.") from exc
    except OverflowError as exc:
        raise ValueError("El resultado está fuera del rango permitido.") from exc
    except (SyntaxError, TypeError) as exc:
        raise ValueError("No pude interpretar la operación. Revisa los signos y paréntesis.") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("La operación matemática no es válida.") from exc


def format_calculation_result(value: int | float) -> str:
    """Format a calculator result without losing useful precision."""
    if isinstance(value, int):
        return str(value)
    return format(float(value), ".12g")
