"""Сравнивает режимы генерации и инструкции на фиксированном контексте."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evals.compare_search import EVAL_DIR, read_jsonl
from evals.metrics import answer_metrics, case_outcome
from evals.run_web_baseline import answer_case, usage_summary

CASE_IDS = (
    "vki-start",
    "dorm-nsu-sgugit",
    "nspu-library-renew-count",
    "nspu-master-docs",
    "sgugit-dorm-photo",
    "nspu-schedule",
    "sgups-dorm-total",
    "sib-remote-assignment-channels",
)
DOMAIN_INSTRUCTION = (
    " Сначала сообщи все запрошенные факты. Для сравнения назови обе исходные величины "
    "или даты, направление и величину разницы. Для срока сохрани часовой пояс и год, "
    "если они указаны. Добавляй оговорки, если они влияют на ответ или относятся к "
    "запрошенному факту. Полный подтверждённый ответ помечай answered, а если не "
    "хватает запрошенной части, помечай partial и назови пробел."
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--case", action="append", help="ID вопроса из снимка.")
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument(
        "--domain-prompt",
        action="store_true",
        help="Сравнить инструкции при том же режиме рассуждений.",
    )
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    runs = {row["id"]: row for row in baseline["runs"]}
    case_ids = tuple(args.case) if args.case else CASE_IDS
    if not 1 <= len(case_ids) <= 12 or len(set(case_ids)) != len(case_ids):
        parser.error("Укажи от 1 до 12 разных кейсов.")
    if not set(case_ids) <= runs.keys():
        parser.error("Каждый кейс должен присутствовать в снимке.")
    questions = {
        row["id"]: row
        for filename in ("questions.jsonl", "questions-extension.jsonl")
        for row in read_jsonl(EVAL_DIR / filename)
    }
    gold = {
        row["id"]: row
        for filename in ("answers.jsonl", "answers-extension.jsonl")
        for row in read_jsonl(EVAL_DIR / "gold" / filename)
    }
    treatment = "domain_prompt" if args.domain_prompt else "without_reasoning"
    options = (
        {"prompt_suffix": DOMAIN_INSTRUCTION}
        if args.domain_prompt
        else {"reasoning_enabled": False}
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [
            (
                case_id,
                variant,
                pool.submit(
                    answer_case,
                    questions[case_id],
                    gold[case_id],
                    runs[case_id]["search"],
                    True,
                    None,
                    3072,
                    **settings,
                ),
            )
            for case_id in case_ids
            for variant, settings in (("control", {}), (treatment, options))
        ]
        results = {case_id: {} for case_id in case_ids}
        for case_id, variant, job in jobs:
            results[case_id][variant] = job.result()
    summaries = {}
    strict_cases = {}
    for variant in ("control", treatment):
        rows = [pair[variant] for pair in results.values()]
        strict_cases[variant] = {row["id"] for row in rows if case_outcome(row) == "strict_pass"}
        summaries[variant] = {
            "quality": answer_metrics(rows),
            "generation": usage_summary(rows, "generation"),
            "judge": usage_summary(rows, "judge"),
        }
    control = strict_cases["control"]
    variant = strict_cases[treatment]
    report = {
        "summary": summaries,
        "strict_wins": sorted(variant - control),
        "strict_losses": sorted(control - variant),
        "pairs": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
