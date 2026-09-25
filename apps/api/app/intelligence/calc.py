"""Restricted arithmetic. Important totals are not left to a model."""

from __future__ import annotations

import ast
from decimal import Decimal, ROUND_HALF_UP

_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}


class CalcError(ValueError):
    pass


def money(value: Decimal | int | str) -> str:
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def to_pence(pounds: str) -> int:
    return int((Decimal(pounds) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _eval(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval(node.left), _eval(node.right))
    raise CalcError("expression is not restricted arithmetic")


def eval_arithmetic(expression: str) -> str:
    """Evaluate + - * / on numeric literals. Names, calls, and powers are rejected."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalcError("invalid arithmetic") from exc
    if not isinstance(tree, ast.Expression):
        raise CalcError("invalid arithmetic")
    return money(_eval(tree.body))


def sum_pence(amounts: list[int]) -> int:
    """Independent integer check used by the verifier. Not the expression parser."""
    total = 0
    for amount in amounts:
        if not isinstance(amount, int):
            raise CalcError("pence must be integers")
        total += amount
    return total
