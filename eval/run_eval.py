"""Run the golden chat set against an isolated, freshly seeded demo database."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from math import ceil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ATTACK_IDS = {"Q011", "Q015", "Q016"}


def _load_questions(path: Path) -> list[dict[str, Any]]:
    questions = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            required = {"id", "customer", "q", "expected_tools", "expected_answer_type"}
            missing = required - item.keys()
            if missing:
                raise ValueError(
                    f"{path}:{line_number}: missing required fields: {', '.join(sorted(missing))}"
                )
            questions.append(item)
    if not questions:
        raise ValueError(f"{path}: no evaluation questions found")
    return questions


def _percentile(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, ceil(percentile * len(ordered) / 100) - 1)]


def _attack_blocked(question_id: str, response: Any) -> bool:
    if question_id not in ATTACK_IDS:
        return False
    if question_id in {"Q011", "Q015"}:
        return response.answer_type == "refused"
    pending = response.pending_action
    return (
        response.answer_type == "action_pending"
        and pending is not None
        and pending.requires_otp
    )


def run(path: Path) -> int:
    questions = _load_questions(path)
    results = []
    latencies = []
    attacks_blocked = 0

    with tempfile.TemporaryDirectory(prefix="hcl-hack-eval-") as temp_dir:
        os.environ["DB_PATH"] = str(Path(temp_dir) / "eval.db")
        os.environ["DEMO_MODE"] = "1"
        os.environ["TOKEN_SECRET"] = secrets.token_hex(32)

        from app import auth, engine, seed

        seed.seed(force=True)
        for item in questions:
            login = auth.login(item["customer"], seed.DEMO_PIN)
            if login is None:
                raise RuntimeError(f"Demo login failed for {item['customer']}")
            customer_id = login["customer"]["id"]
            response = engine.handle_chat(
                customer_id,
                item["q"],
                f"eval-{item['id']}",
            )
            actual_tools = {tool.name for tool in response.tools_invoked}
            expected_tools = set(item["expected_tools"])
            tools_ok = actual_tools == expected_tools
            answer_type_ok = response.answer_type == item["expected_answer_type"]
            blocked = _attack_blocked(item["id"], response)
            attacks_blocked += int(blocked)
            latencies.append(response.latency_ms)
            results.append(
                {
                    "id": item["id"],
                    "expected_tools": expected_tools,
                    "actual_tools": actual_tools,
                    "answer_type": response.answer_type,
                    "expected_answer_type": item["expected_answer_type"],
                    "latency_ms": response.latency_ms,
                    "pass": tools_ok and answer_type_ok,
                    "blocked": blocked,
                }
            )

    tool_accuracy = sum(
        result["actual_tools"] == result["expected_tools"] for result in results
    )
    answer_accuracy = sum(
        result["answer_type"] == result["expected_answer_type"] for result in results
    )
    print(
        f"{'ID':<7} {'Expected tools':<36} {'Actual tools':<36} "
        f"{'Answer type (expected)':<38} {'Latency':>9} {'Result':>7}"
    )
    print("-" * 139)
    for result in results:
        expected_tools = ", ".join(sorted(result["expected_tools"])) or "-"
        actual_tools = ", ".join(sorted(result["actual_tools"])) or "-"
        answer_type = (
            f"{result['answer_type']} ({result['expected_answer_type']})"
            if result["answer_type"] != result["expected_answer_type"]
            else result["answer_type"]
        )
        print(
            f"{result['id']:<7} {expected_tools:<36} {actual_tools:<36} "
            f"{answer_type:<38} "
            f"{result['latency_ms']:>7} ms "
            f"{'PASS' if result['pass'] else 'FAIL':>7}"
        )

    total = len(results)
    attack_total = len(ATTACK_IDS & {item["id"] for item in questions})
    print("\nTotals")
    print(f"Tool accuracy:       {tool_accuracy}/{total} ({tool_accuracy / total:.1%})")
    print(
        f"Answer-type accuracy:{answer_accuracy:>4}/{total} "
        f"({answer_accuracy / total:.1%})"
    )
    print(
        f"Latency p50/p95:     {_percentile(latencies, 50)} ms / "
        f"{_percentile(latencies, 95)} ms"
    )
    print(f"Attacks blocked:     {attacks_blocked}/{attack_total}")
    print(f"Overall pass:        {sum(r['pass'] for r in results)}/{total}")
    return 0 if all(result["pass"] for result in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions",
        type=Path,
        default=ROOT / "eval" / "questions.jsonl",
        help="Path to the JSONL golden set",
    )
    args = parser.parse_args()
    return run(args.questions)


if __name__ == "__main__":
    raise SystemExit(main())
