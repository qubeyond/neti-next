"""Regrade the same verified and flawed drafts with the same model settings."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.domain.errors import AgentError
from evals.compare_search import EVAL_DIR, read_jsonl
from evals.run_robustness import ASSESS_ABSTENTION
from evals.run_web_baseline import grade, usage_summary

POSITIVE = ("nstu-budget", "sib-post", "nspu-triple-price-gap", "nsu-eduroam-login")
NEGATIVE = ("nsu-certificate", "nstu-budget", "sib-post")
HOLDOUT_POSITIVE = (
    "nsu-library-login",
    "nspu-master-consent",
    "sgugit-dorm-photo",
    "sgups-dorm-total",
)


def evaluate(draft: dict, gold: dict, *, expected: bool, reasoning: bool | None) -> dict:
    try:
        verdict, usage = grade(
            draft["search"]["query"],
            draft,
            gold,
            max_tokens=3072,
            reasoning_enabled=reasoning,
        )
    except AgentError as error:
        verdict, usage = {"error": str(error)}, None
    accepted = (
        verdict["correct"] and verdict["supported"] and not verdict["critical_error"]
        if "correct" in verdict
        else None
    )
    return {
        "id": draft["id"],
        "expected": expected,
        "accepted": accepted,
        "judge_attempted": True,
        "judge": usage,
        "grade": verdict,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("oracle", type=Path)
    parser.add_argument("reasoning", type=Path)
    parser.add_argument("--reading", type=Path, help="Отчёт E04 для отложенного среза.")
    parser.add_argument("--status", type=Path, help="Отчёт E16 для отложенного среза.")
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    web, oracle = (
        {row["id"]: row for row in json.loads(path.read_text(encoding="utf-8"))["runs"]}
        for path in (args.baseline, args.oracle)
    )
    if bool(args.reading) != bool(args.status):
        parser.error("Для отложенного среза нужны и --reading, и --status.")
    reasoning_pairs = json.loads(args.reasoning.read_text(encoding="utf-8"))["pairs"]
    gold = {
        row["id"]: row
        for suffix in ("", "-extension")
        for row in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
    }
    if args.reading:
        source = json.loads(args.reading.read_text(encoding="utf-8"))["pairs"]
        wrong_branch = next(row["control"] for row in source if row["id"] == "nspu-branch-amend")
        wrong_service = json.loads(args.status.read_text(encoding="utf-8"))["variants"]["explicit"][
            "wrong-service"
        ]
        inputs = [(oracle[case_id], True) for case_id in HOLDOUT_POSITIVE]
        inputs.extend(
            (row, False)
            for row in (
                web["nstu-business-practice-years"],
                wrong_branch,
                reasoning_pairs["nspu-leave-form-date"]["control"],
                wrong_service,
            )
        )
        gold["wrong-service"]["required_facts"] = ASSESS_ABSTENTION
    else:
        inputs = [(oracle[case_id], True) for case_id in POSITIVE]
        inputs.extend((web[case_id], False) for case_id in NEGATIVE)
        inputs.append((reasoning_pairs["vki-start"]["control"], False))
    rows = {"control": [], "without_reasoning": []}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [
            (
                variant,
                pool.submit(
                    evaluate,
                    draft,
                    gold[draft["id"]],
                    expected=expected,
                    reasoning=enabled,
                ),
            )
            for draft, expected in inputs
            for variant, enabled in (("control", None), ("without_reasoning", False))
        ]
        for variant, job in jobs:
            rows[variant].append(job.result())
    report = {
        "summary": {
            variant: {
                "valid": sum(row["accepted"] is not None for row in items),
                "true_accept": sum(row["expected"] and row["accepted"] is True for row in items),
                "false_accept": sum(
                    not row["expected"] and row["accepted"] is True for row in items
                ),
                "true_reject": sum(
                    not row["expected"] and row["accepted"] is False for row in items
                ),
                "judge": usage_summary(items, "judge"),
            }
            for variant, items in rows.items()
        },
        "runs": rows,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
