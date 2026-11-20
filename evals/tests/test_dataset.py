import json
from pathlib import Path

from evals.compare_search import read_jsonl

EVALS = Path(__file__).resolve().parents[1]


def test_every_eval_case_has_a_valid_answer_key():
    questions = read_jsonl(EVALS / "questions.jsonl") + read_jsonl(
        EVALS / "questions-extension.jsonl"
    )
    answers = read_jsonl(EVALS / "gold/answers.jsonl") + read_jsonl(
        EVALS / "gold/answers-extension.jsonl"
    )
    gold = {row["id"]: row for row in answers}
    assert (
        len(questions)
        == len(answers)
        == len(gold)
        == len({row["id"] for row in questions})
    )
    evidence = json.loads((EVALS / "gold/evidence.json").read_text(encoding="utf-8"))
    evidence.update(
        json.loads((EVALS / "gold/evidence-extension.json").read_text(encoding="utf-8"))
    )

    for item in questions:
        case = gold[item["id"]]
        assert item["input"]["question"].strip() and item["skills"], item["id"]
        if item["suite"] == "web":
            assert case["required_facts"] and case["evidence_ids"], item["id"]
        elif item["suite"] == "grounding":
            assert (
                case["expected_action"] == "unverified"
                and case["provided_evidence_ids"]
            ), item["id"]
        for ref in case["evidence_ids"] + case.get("provided_evidence_ids", []):
            assert evidence[ref]["quote"] and evidence[ref]["url"].startswith(
                "https://"
            ), ref
