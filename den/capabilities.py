"""Deterministic, local capabilities callable by the orchestrator."""

import ast
import math
import operator
import re


STOPWORDS = set("a an and are as at be by can do does for from how i in is it me my of on or the to what with you your".split())


def terms(text):
    return set(re.findall(r"[^\W_]+", text.casefold())) - STOPWORDS


def retrieve(prompt, memories):
    query = terms(prompt)
    scored = []
    for memory in memories:
        words = terms(memory["text"])
        overlap = query & words
        if overlap:
            scored.append((len(overlap) / math.sqrt(max(len(words), 1)), memory))
    scored.sort(key=lambda item: item[0], reverse=True)
    chosen = [memory for _, memory in scored[:3]]
    if not chosen and re.search(r"\bremember about me\b|\bmy (?:saved )?memories\b", prompt, re.I):
        chosen = memories[:3]
    return [{"id": item["id"], "text": item["text"]} for item in chosen]


def calculator_expression(prompt):
    if prompt == "/calc" or prompt.startswith("/calc "):
        return prompt[5:].strip()
    match = re.fullmatch(r"calculate\s+([0-9\s.+*/%()^-]+)\??", prompt, re.I)
    return match.group(1).strip() if match else None


BINARY_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
              ast.Pow: operator.pow}


def calculate(expression):
    if not 0 < len(expression) <= 160 or not re.fullmatch(r"[0-9\s.+*/%()^-]+", expression):
        raise ValueError("Use a numeric expression, for example /calc (18 + 6) / 3.")
    try:
        tree = ast.parse(expression.replace("^", "**"), mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ValueError("The calculator could not parse that expression.") from exc
    if len(list(ast.walk(tree))) > 80:
        raise ValueError("This expression is too long.")

    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            result = node.value
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            result = -value if isinstance(node.op, ast.USub) else value
        elif isinstance(node, ast.BinOp) and type(node.op) in BINARY_OPS:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 32:
                raise ValueError("Use an exponent between -32 and 32.")
            result = BINARY_OPS[type(node.op)](left, right)
        else:
            raise ValueError("Use numbers, parentheses and arithmetic operators.")
        if isinstance(result, complex) or abs(result) > 1e100 or not math.isfinite(result):
            raise ValueError("The result is outside the supported numeric range.")
        return result

    try:
        result = evaluate(tree.body)
    except (ZeroDivisionError, OverflowError) as exc:
        raise ValueError("This expression has no finite result.") from exc
    return str(result) if isinstance(result, int) else format(result, ".12g")
