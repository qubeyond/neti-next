from collections import Counter
from math import log2
from statistics import mean


def _average(values: list[float]) -> float | None:
    return round(mean(values), 6) if values else None


def retrieval_metrics(cases: list[dict], limit: int) -> dict:
    values = {name: [] for name in ("hit_rate", "recall", "complete_retrieval_rate", "mrr", "ndcg")}
    for case in cases:
        if case["status"] not in {"ok", "empty"} or not case["gold_urls"]:
            continue
        ranks = sorted(
            {
                rank
                for rank in case["gold_urls"].values()
                if type(rank) is int and 1 <= rank <= limit
            }
        )
        expected = len(case["gold_urls"])
        values["hit_rate"].append(bool(ranks))
        values["recall"].append(len(ranks) / expected)
        values["complete_retrieval_rate"].append(len(ranks) == expected)
        values["mrr"].append(1 / ranks[0] if ranks else 0.0)
        discount = sum(1 / log2(rank + 1) for rank in ranks)
        ideal = sum(1 / log2(rank + 1) for rank in range(1, min(expected, limit) + 1))
        values["ndcg"].append(discount / ideal)
    return {
        "eligible": len(values["hit_rate"]),
        "scope": "known_gold_urls",
        "hits": sum(values["hit_rate"]),
        "complete_hits": sum(values["complete_retrieval_rate"]),
        **{f"{name}@{limit}": _average(scores) for name, scores in values.items()},
    }


def case_outcome(case: dict) -> str:
    status = case["status"]
    if status not in {"answered", "partial"}:
        return status
    grade = case.get("grade", {})
    if "correct" not in grade:
        return "judge_failed" if case["judge_attempted"] else "not_graded"
    if grade["critical_error"]:
        return "critical_error"
    if not grade["correct"]:
        return "incorrect_or_incomplete"
    if not grade["supported"]:
        return "correct_but_unsupported"
    if status != "answered":
        return "correct_but_marked_partial"
    citations = case.get("citations", [])
    if (
        not citations
        or case.get("malformed_citations")
        or not all(row["verified"] for row in citations)
    ):
        return "invalid_or_missing_citation"
    return "strict_pass"


def answer_metrics(cases: list[dict], *, expected_judge: bool = True) -> dict:
    outcomes = Counter(case_outcome(case) for case in cases)
    grades = [
        case["grade"]
        for case in cases
        if case["status"] in {"answered", "partial"} and "correct" in case.get("grade", {})
    ]
    citations = [citation for case in cases for citation in case.get("citations", [])]
    malformed = sum(case.get("malformed_citations", 0) for case in cases)
    fact_results = [fact for grade in grades for fact in grade.get("facts", [])]
    fact_correct = [fact for fact in fact_results if fact["verdict"] == "correct"]
    answered = sum(case["status"] in {"answered", "partial"} for case in cases)
    quote_count = len(citations) + malformed
    return {
        "cases": len(cases),
        "scored_answers": len(grades),
        "outcomes": dict(outcomes),
        "strict_case_pass_rate": (
            _average([outcomes["strict_pass"] / len(cases)]) if expected_judge and cases else None
        ),
        "answer_coverage": _average([answered / len(cases)]) if cases else None,
        "answer_accuracy_conditional": _average([grade["correct"] for grade in grades]),
        "answer_support_rate_conditional": _average([grade["supported"] for grade in grades]),
        "critical_error_rate_conditional": _average([grade["critical_error"] for grade in grades]),
        "citation_verbatim_rate": (
            _average([sum(citation["verified"] for citation in citations) / quote_count])
            if quote_count
            else None
        ),
        "citation_verbatim_count": sum(citation["verified"] for citation in citations),
        "citations_total": quote_count,
        "required_facts_graded": len(fact_results),
        "fact_recall_conditional": _average(
            [fact["verdict"] == "correct" for fact in fact_results]
        ),
        "fact_support_rate_on_correct": _average([fact["supported"] for fact in fact_correct]),
    }


def breakdown(cases: list[dict], limit: int, *, expected_judge: bool = True) -> dict:
    search = [case["search"] for case in cases]
    complete = [
        case
        for case in cases
        if case["search"]["status"] in {"ok", "empty"}
        and case["search"]["gold_urls"]
        and all(rank is not None and rank <= limit for rank in case["search"]["gold_urls"].values())
    ]
    contexts = [
        facts
        for case in cases
        if (facts := case.get("grade", {}).get("facts"))
        and all("in_context" in fact for fact in facts)
    ]
    context_facts = [fact["in_context"] for facts in contexts for fact in facts]
    return {
        "retrieval": retrieval_metrics(search, limit),
        "evidence": {
            "context_coverage_scored_cases": len(contexts),
            "context_fact_coverage": _average(context_facts),
            "complete_context_rate": _average(
                [all(fact["in_context"] for fact in facts) for facts in contexts]
            ),
        },
        "answers": answer_metrics(cases, expected_judge=expected_judge)
        if any(case["status"] != "not_generated" for case in cases)
        else None,
        "all_gold_retrieved": len(complete),
        "outcomes_with_all_gold": dict(Counter(case_outcome(case) for case in complete))
        if expected_judge
        else None,
        "all_gold_retrieved_but_not_strict": (
            sum(case_outcome(case) != "strict_pass" for case in complete)
            if expected_judge
            else None
        ),
        "strict_without_all_gold": (
            sum(case_outcome(case) == "strict_pass" for case in cases)
            - sum(case_outcome(case) == "strict_pass" for case in complete)
            if expected_judge
            else None
        ),
    }
