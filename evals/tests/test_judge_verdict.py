import json

import pytest

from evals.run_web_baseline import parse_judge_verdict


@pytest.mark.parametrize(
    ("answer", "failure_kind"),
    [
        ("", "empty_answer"),
        ('{"facts":', "invalid_json"),
        ("[]", "invalid_json_object"),
    ],
)
def test_judge_parser_distinguishes_invalid_answers(answer, failure_kind):
    result = parse_judge_verdict(answer, 1)

    assert result["failure_kind"] == failure_kind
    assert "correct" not in result


def test_judge_parser_requires_one_verdict_per_fact():
    verdict = {"verdict": "missing", "supported": False, "in_context": True}
    answer = json.dumps({"supported": False, "critical_error": False, "facts": [verdict]})

    failure = parse_judge_verdict(answer, 2)

    assert failure["failure_kind"] == "wrong_fact_count"
    assert failure["expected_fact_count"] == 2
    assert failure["actual_fact_count"] == 1
    assert parse_judge_verdict(answer, 1)["correct"] is False
