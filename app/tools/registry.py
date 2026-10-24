"""Tool registry and the policy gate every tool call passes through.

The model chooses tools and arguments; this module decides whether the call may run.
The customer is always taken from the session context, never from model arguments.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.trace import Trace

logger = logging.getLogger(__name__)

# read: no side effects. write: low-risk record (a complaint, a dispute). action: moves money or
# changes a card, so it only ever creates a pending action that needs the customer's OTP.
TIERS = ("read", "write", "action")
FORBIDDEN_ARGS = {"customer_id", "customer", "account_id", "user_id", "role"}


@dataclass
class Ctx:
    customer_id: str
    trace: Trace
    session: dict = field(
        default_factory=dict
    )  # per-conversation flags, e.g. scam_risk


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    tier: str
    fn: "ToolFn"


ToolFn = Callable[[Ctx, dict], dict]
TOOLS: dict[str, Tool] = {}


def schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
    }


def tool(
    name: str, description: str, params: dict | None = None, tier: str = "read"
) -> Callable[[ToolFn], ToolFn]:
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r} for tool {name}")

    def register(fn: ToolFn) -> ToolFn:
        TOOLS[name] = Tool(name, description, params or schema(), tier, fn)
        return fn

    return register


def _check_args(t: Tool, args: dict) -> str | None:
    props = t.params.get("properties", {})
    for key in t.params.get("required", []):
        if args.get(key) in (None, ""):
            return f"missing required argument '{key}'"
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            return f"unknown argument '{key}'"
        if "enum" in spec and value not in spec["enum"]:
            return f"'{key}' must be one of {spec['enum']}"
        if spec.get("type") in ("integer", "number"):
            try:
                args[key] = (
                    float(value) if spec["type"] == "number" else int(float(value))
                )
            except (TypeError, ValueError):
                return f"'{key}' must be a number"
            if args[key] < 0:
                return f"'{key}' cannot be negative"
    return None


def execute(ctx: Ctx, name: str, args: dict | None) -> dict:
    """Gate, run and trace one tool call. Never raises; errors come back as data for the model."""
    args = dict(args or {})
    t = TOOLS.get(name)
    if t is None:
        ctx.trace.add("gate", name, decision="deny", reason="unknown tool")
        return {"error": f"Unknown tool {name}. Use only the tools provided."}
    stripped = sorted(k for k in args if k in FORBIDDEN_ARGS)
    for key in stripped:
        args.pop(key)
    problem = _check_args(t, args)
    if problem:
        ctx.trace.add("gate", name, decision="deny", reason=problem, args=args)
        return {"error": problem}
    ctx.trace.add("gate", name, decision="allow", tier=t.tier, stripped_args=stripped)
    with ctx.trace.span("tool", name, args=args, tier=t.tier) as out:
        try:
            result = t.fn(ctx, args)
        except Exception as exc:
            # A bug in one tool must not end the conversation: keep the stack trace in the
            # log and hand the model an error it can explain to the customer.
            logger.exception("tool %s failed (trace %s)", name, ctx.trace.id)
            result = {"error": f"{name} failed: {type(exc).__name__}"}
            out["ok"] = False
        out["result"] = result
    return result


def declarations(names: list[str] | None = None) -> list[Tool]:
    return [t for n, t in TOOLS.items() if names is None or n in names]
