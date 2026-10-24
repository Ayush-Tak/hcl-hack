"""Exact arithmetic for the agent. The model never does maths in its head."""

import ast
import operator

from app.tools.registry import Ctx, schema, tool

# No ** or %: our sums never need them, and ** would let "9**9**9" hang the server.
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
MAX_EXPRESSION_CHARS = 200


def evaluate(expression: str) -> float:
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise ValueError("expression too long")
    cleaned = expression.replace(",", "").replace("Rs", "").replace("₹", "")
    return _walk(ast.parse(cleaned, mode="eval").body)


def _walk(node: ast.expr) -> float:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_walk(node.left), _walk(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_walk(node.operand))
    raise ValueError("only numbers and + - * / ( ) are allowed")


@tool(
    "calculate",
    "Evaluate an arithmetic expression exactly, e.g. '148250 - 2340 - 599 - 20000'. "
    "Use this for every sum, difference, percentage or comparison; never calculate yourself.",
    schema(
        {
            "expression": {
                "type": "string",
                "description": "Numbers and + - * / ( ) only",
            },
            "label": {
                "type": "string",
                "description": "What the result means, e.g. 'balance left after bills'",
            },
        },
        ["expression"],
    ),
)
def calculate(ctx: Ctx, args: dict) -> dict:
    try:
        value = evaluate(args["expression"])
    except (ValueError, SyntaxError, ZeroDivisionError) as exc:
        return {"error": str(exc)}
    return {
        "expression": args["expression"],
        "label": args.get("label", ""),
        "value": round(value, 2),
    }
