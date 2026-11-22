"""Сравнивает составную и пофактную рубрику на сохранённых ответах."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.domain.errors import AgentError
from agent.domain.evidence import parse_answer
from evals.compare_search import EVAL_DIR, read_jsonl
from evals.run_web_baseline import grade

ATOMIC = {
    "dorm-nspu-sgugit": [
        "Заселение указанной группы НГПУ — с 24 августа.",
        "Заселение СГУГиТ по общему графику — с 26 августа.",
        "НГПУ раньше на два дня.",
    ],
    "dorm-nsu-sgugit": [
        "НГУ — 28 августа.",
        "СГУГиТ — 26 августа.",
        "НГУ позже на два дня.",
    ],
    "sgups-dorm-difference": [
        "Общежитие № 3 — 528 мест.",
        "Общежитие № 4 — 331 место.",
        "Разница составляет 197 мест.",
    ],
}


def fixed_format(size: int) -> dict:
    flag = {"type": "boolean"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "fact_verdict",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "facts": {
                        "type": "array",
                        "minItems": size,
                        "maxItems": size,
                        "items": {
                            "type": "object",
                            "properties": {
                                "verdict": {
                                    "type": "string",
                                    "enum": ["correct", "missing", "incorrect"],
                                },
                                "supported": flag,
                                "in_context": flag,
                            },
                            "required": ["verdict", "supported", "in_context"],
                            "additionalProperties": False,
                        },
                    },
                    "supported": flag,
                    "critical_error": flag,
                    "reason": {"type": "string"},
                },
                "required": ["facts", "supported", "critical_error", "reason"],
                "additionalProperties": False,
            },
        },
    }


def adjudicate(row: dict, rubric: dict, arm: str) -> dict:
    try:
        response_format = fixed_format(len(rubric["required_facts"])) if arm == "schema" else None
        verdict, call = grade(
            row["search"]["query"],
            row,
            rubric,
            max_tokens=3072,
            response_format=response_format,
        )
    except AgentError as failure:
        verdict, call = {"error": str(failure)}, {}
    accepted = (
        verdict["correct"] and verdict["supported"] and not verdict["critical_error"]
        if "correct" in verdict
        else None
    )
    return {"arm": arm, "accepted": accepted, "grade": verdict, "judge": call}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("combined", type=Path)
    parser.add_argument("oracle", type=Path)
    parser.add_argument("--live", required=True, action="store_true")
    parser.add_argument("--schema", action="store_true")
    args = parser.parse_args()
    runs = json.loads(args.combined.read_text(encoding="utf-8"))["runs"]
    oracle = {row["id"]: row for row in json.loads(args.oracle.read_text(encoding="utf-8"))["runs"]}
    gold = {row["id"]: row for row in read_jsonl(EVAL_DIR / "gold/answers.jsonl")}
    cases = [
        ("comparison_with_evidence", runs["combined"][0], True),
        ("comparison_without_evidence", runs["direct"][0], False),
        ("date_oracle", oracle["dorm-nsu-sgugit"], True),
        ("places_oracle", oracle["sgups-dorm-difference"], True),
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {}
        for label, saved, expected in cases:
            row = {
                **saved,
                **parse_answer(saved["generation"]["answer"], saved["sources"]),
            }
            grouped = gold[row["id"]]
            atomic = {**grouped, "required_facts": ATOMIC[row["id"]]}
            treatment = ("schema", grouped) if args.schema else ("atomic", atomic)
            jobs[label] = (
                expected,
                [
                    pool.submit(adjudicate, row, rubric, arm)
                    for arm, rubric in (("grouped", grouped), treatment)
                ],
            )
        report = {
            label: {"expected": expected, "arms": [job.result() for job in pair]}
            for label, (expected, pair) in jobs.items()
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
