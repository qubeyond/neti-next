"""Проверяет быстрый повтор для сохранённых обрезанных ответов."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from evals.compare_search import EVAL_DIR, read_jsonl
from evals.metrics import answer_metrics, case_outcome
from evals.run_web_baseline import answer_case, distribution, usage_summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--live", required=True, action="store_true")
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    rows = baseline["runs"]
    candidates = [
        row
        for row in rows
        if row["status"] == "invalid"
        and (row.get("generation") or {}).get("finish_reason") == "length"
    ]
    if not candidates:
        parser.error("В снимке нет обрезанных невалидных ответов.")
    questions, gold = (
        {row["id"]: row for filename in names for row in read_jsonl(EVAL_DIR / filename)}
        for names in (
            ("questions.jsonl", "questions-extension.jsonl"),
            ("gold/answers.jsonl", "gold/answers-extension.jsonl"),
        )
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = [
            pool.submit(
                answer_case,
                questions[row["id"]],
                gold[row["id"]],
                row["search"],
                True,
                judge_max_tokens=3072,
                reasoning_enabled=False,
            )
            for row in candidates
        ]
        answers = [future.result() for future in pending]
        retried = {result["id"]: result for result in answers}
    after = {row["id"]: retried.get(row["id"], row) for row in rows}
    latency = []
    for row in rows:
        retry_generation = retried.get(row["id"], {}).get("generation") or {}
        if "answer_latency_ms" in row:
            latency.append(row["answer_latency_ms"] + retry_generation.get("latency_ms", 0))
    before_usage = usage_summary(rows, "generation")
    retry_usage = usage_summary(list(retried.values()), "generation")
    before_cost = before_usage["reported_cost_rub"]
    retry_cost = retry_usage["reported_cost_rub"]
    projected_cost = (
        str(Decimal(before_cost) + Decimal(retry_cost)) if before_cost and retry_cost else None
    )
    result = {
        "case_count": len(rows),
        "retry_count": len(candidates),
        "before_quality": answer_metrics(rows),
        "after_quality": answer_metrics(list(after.values())),
        "before_generation": before_usage,
        "projected_generation_cost_rub": projected_cost,
        "retry_generation": retry_usage,
        "retry_judge": usage_summary(list(retried.values()), "judge"),
        "before_answer_latency_ms": distribution(
            [row["answer_latency_ms"] for row in rows if "answer_latency_ms" in row]
        ),
        "projected_answer_latency_ms": distribution(latency),
        "recovered": [
            case_id for case_id, row in retried.items() if case_outcome(row) == "strict_pass"
        ],
        "retries": list(retried.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
