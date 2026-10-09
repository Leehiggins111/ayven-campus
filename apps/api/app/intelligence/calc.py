"""Restricted arithmetic. Important totals are not left to a model."""

from __future__ import annotations

import ast
import re
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


def expression_from_objective(objective: str) -> str | None:
    """Recognise ordinary arithmetic wording; evaluation stays restricted."""
    text = (objective or "").lower().replace("×", "*").replace("÷", "/")
    number_words = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
    number_words.update(dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10))))
    word_pattern = r"\b(?:" + "|".join(number_words) + r")(?:[ -]+(?:" + "|".join(number_words) + r"))*\b"
    def replace_number(match):
        words = re.split(r"[ -]+", match.group(0))
        values = [number_words[word] for word in words]
        if len(values) == 1:
            return str(values[0])
        if len(values) == 2 and values[0] >= 20 and values[0] % 10 == 0 and values[1] < 10:
            return str(sum(values))
        return "UNPARSED_NUMBER"
    text = re.sub(word_pattern, replace_number, text)
    if re.search(r"\b(?:UNPARSED_NUMBER|hundred|thousand|million|point|dozen)\b", text, re.I):
        return None
    number = r"(\d+(?:\.\d+)?)"
    for pattern, symbol, reverse in (
        (r"multiply\s+" + number + r"\s+by\s+" + number, "*", False),
        (r"product of\s+" + number + r"\s+and\s+" + number, "*", False),
        (r"divide\s+" + number + r"\s+by\s+" + number, "/", False),
        (r"add\s+" + number + r"\s+(?:and|to)\s+" + number, "+", False),
        (r"subtract\s+" + number + r"\s+from\s+" + number, "-", True),
    ):
        match = re.search(pattern, text)
        if match:
            left, right = match.groups()
            return f"{right}{symbol}{left}" if reverse else f"{left}{symbol}{right}"

    match = re.search(r"sum of\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)", text)
    if match:
        return f"{match.group(1)}+{match.group(2)}"
    for word, symbol in (("multiplied by", "*"), ("times", "*"), ("divided by", "/"), ("plus", "+"), ("minus", "-"), ("take away", "-")):
        text = re.sub(r"\b" + word + r"\b", symbol, text)
    text = re.sub(r"(?<=\d)\s*x\s*(?=\d)", "*", text)
    found = re.search(r"(?<![\w.])[-+]?[()\d.]+(?:\s*[+*/-]\s*[-+]?[()\d.]+)+", text)
    return re.sub(r"\s+", "", found.group(0)) if found else None
