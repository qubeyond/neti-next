"""Проверяет одну повторную оценку на замороженных доменных ответах."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.modules.answers.domain.evidence import parse_answer
from agent.shared.errors import AgentError
from evals.compare_search import EVAL_DIR, read_jsonl
from evals.metrics import case_outcome
from evals.run_web_baseline import grade, usage_summary


def rejudge(row: dict, key: dict) -> dict:
    fixed = {**row, **parse_answer(row["generation"]["answer"], row["sources"])}
    try:
        fixed["grade"], fixed["judge"] = grade(
            row["search"]["query"], fixed, key, max_tokens=3072, reasoning_enabled=False
        )
    except AgentError as failure:
        fixed["grade"], fixed["judge"] = {"error": str(failure)}, None
    return {
        "id": row["id"],
        "before": case_outcome(row),
        "after": case_outcome(fixed),
        "answer": fixed["answer"],
        "citations": fixed["citations"],
        "grade": fixed["grade"],
        "judge": fixed["judge"],
        "judge_attempted": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("compact", "holdout", "table", "reading"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--live", required=True, action="store_true")
    args = parser.parse_args()
    reports = {
        name: json.loads(getattr(args, name).read_text(encoding="utf-8"))
        for name in ("compact", "holdout", "table", "reading")
    }
    groups = (
        (
            reports["compact"]["runs"]["compact"],
            ("dorm-nspu-sgugit", "dorm-nsu-sgugit"),
        ),
        (reports["holdout"]["runs"]["compact"], ("sgups-career-audience",)),
        (reports["table"]["runs"]["table"], ("sgups-dorm-address",)),
        (reports["holdout"]["runs"]["control"], ("nsu-library-login",)),
        ([pair["control"] for pair in reports["reading"]["pairs"]], ("vki-deadline",)),
    )
    selected = [
        next(row for row in group if row["id"] == case_id)
        for group, ids in groups
        for case_id in ids
    ]
    if [case_outcome(row) for row in selected] != ["judge_failed"] * 4 + [
        "strict_pass",
        "critical_error",
    ]:
        parser.error("Снимки не соответствуют исходам эксперимента.")
    gold = {
        row["id"]: row
        for suffix in ("", "-extension")
        for row in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(rejudge, row, gold[row["id"]]) for row in selected]
        rows = [job.result() for job in jobs]
    print(
        json.dumps(
            {
                "retry": usage_summary(rows[:4], "judge"),
                "controls": usage_summary(rows[4:], "judge"),
                "runs": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
