"""Проверки знаменателей и пропусков при формировании отчёта."""

from evals.render_metrics import quality, usage


def test_worker_failure_does_not_change_conditional_accuracy_denominator():
    success = {
        "status": "answered",
        "judge_attempted": True,
        "search": {"status": "ok", "gold_urls": {"a": 1, "b": None}},
        "citations": [{"verified": True}],
        "grade": {
            "correct": True,
            "supported": True,
            "critical_error": False,
            "facts": [{"verdict": "correct", "in_context": True, "supported": True}],
        },
    }
    bad_grade = {
        "status": "partial",
        "judge_attempted": True,
        "search": {"status": "empty", "gold_urls": {"c": None}},
        "malformed_citations": 1,
    }
    blocked = {
        "status": "search_unavailable",
        "search": {"status": "blocked", "gold_urls": {"d": None}},
    }

    result = quality(
        {
            "mode": "e2e",
            "llm_judge": True,
            "search": {"limit": 5},
            "runs": [success, bad_grade, blocked],
        }
    )

    assert result["Hit Rate@5"] == "1/2 (50.0%)"
    assert result["Recall@5"] == "0.2500"
    assert result["Complete Retrieval Rate@5"] == "0/2 (0.0%)"
    assert result["Answer Coverage"] == "2/3 (66.7%)"
    assert result["Judge Coverage (среди полных и частичных ответов)"] == "1/2 (50.0%)"
    assert result["Conditional Answer Accuracy"] == "1/1 (100.0%)"
    assert result["Strict Case Pass Rate"] == "1/3 (33.3%)"
    assert result["Citation Verbatim Rate"] == "1/2 (50.0%)"


def test_cost_counts_exclude_missing_api_fields():
    failed = {"generation_attempted": True}
    first = {
        "generation_attempted": True,
        "generation": {
            "usage": {"total_tokens": 10, "cost": 0.1},
            "latency_ms": 250,
        },
    }
    second = {
        "generation_attempted": True,
        "generation": {
            "usage": {"total_tokens": 5, "cost": 0.2},
            "latency_ms": 500,
        },
    }
    assert usage({"runs": [failed, first, second]}, "generation") == (
        3,
        2,
        2,
        "—",
        "—",
        15,
        "0.3",
        "0.375",
        "0.500",
    )
