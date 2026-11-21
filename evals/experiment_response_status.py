"""Check explicit answer-status instructions while holding inference mode fixed."""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from agent.shared.config import _local_settings, _setting
from evals.compare_search import EVAL_DIR, read_jsonl
from evals.metrics import answer_metrics, case_outcome
from evals.run_web_baseline import answer_case, usage_summary

WEB_CASES = (
    "nspu-master-docs",
    "nspu-schedule",
    "nstu-transfer",
    "nspu-overall-dorm",
    "sgugit-paid-contract",
    "nspu-master-post",
)
GROUNDING_CASES = ("missing-vacancies", "wrong-document", "wrong-service")
SUFFIX = (
    " Используй answered, когда фрагменты позволяют ответить на все явно запрошенные "
    "части вопроса и каждое существенное утверждение имеет применимую цитату. "
    "Отсутствие дополнительных сведений, которых пользователь не просил, не делает "
    "ответ частичным. Используй partial, только когда подтверждена часть явно "
    "запрошенного ответа, и укажи, какая именно часть не подтверждена. "
    "Используй unverified, если ни одна существенная часть не подтверждена. "
    "Совпадение по теме без нужного факта, вуза, периода или условий не подтверждает ответ."
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("grounding", type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    snapshots = {
        row["id"]: row["search"]
        for path in (args.baseline, args.grounding)
        for row in json.loads(path.read_text(encoding="utf-8"))["runs"]
    }
    questions = {}
    gold = {}
    for suffix in ("", "-extension"):
        questions.update(
            (row["id"], row) for row in read_jsonl(EVAL_DIR / f"questions{suffix}.jsonl")
        )
        gold.update(
            (row["id"], row) for row in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
        )
    system = _setting(_local_settings(), "WEB_SYSTEM_PROMPT")
    summaries = {}
    paired = {}
    for name, prompt in (("control", system), ("explicit", system + SUFFIX)):
        with (
            patch.dict(os.environ, {"WEB_SYSTEM_PROMPT": prompt}),
            ThreadPoolExecutor(max_workers=3) as pool,
        ):
            jobs = {
                case_id: pool.submit(
                    answer_case,
                    questions[case_id],
                    gold[case_id],
                    snapshots[case_id],
                    case_id in WEB_CASES,
                    None,
                    3072,
                    reasoning_enabled=False,
                )
                for case_id in (*WEB_CASES, *GROUNDING_CASES)
            }
            rows = {case_id: job.result() for case_id, job in jobs.items()}
        paired[name] = rows
        summaries[name] = {
            "quality": answer_metrics([rows[case_id] for case_id in WEB_CASES]),
            "grounding_statuses": {case_id: rows[case_id]["status"] for case_id in GROUNDING_CASES},
            "generation": usage_summary(list(rows.values()), "generation"),
            "judge": usage_summary(list(rows.values()), "judge"),
        }
    strict = {
        name: {case_id for case_id in WEB_CASES if case_outcome(rows[case_id]) == "strict_pass"}
        for name, rows in paired.items()
    }
    report = {
        "summary": summaries,
        "strict_wins": sorted(strict["explicit"] - strict["control"]),
        "strict_losses": sorted(strict["control"] - strict["explicit"]),
        "variants": paired,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
