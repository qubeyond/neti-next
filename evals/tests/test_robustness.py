import json
from collections import Counter

from evals import run_robustness
from evals.run_robustness import load_cases, summarize


def test_robustness_fixtures_cover_each_kind_and_isolate_markers():
    cases = load_cases()
    assert Counter(case["kind"] for case in cases) == {
        "paraphrase": 8,
        "false-premise": 6,
        "scope-trap": 6,
        "prompt-injection": 12,
        "ambiguity": 4,
    }
    attacks = [case for case in cases if case["kind"] == "prompt-injection"]
    assert Counter(case["attack"]["channel"] for case in attacks) == {
        "source": 4,
        "query": 8,
    }
    markers = {case["gold"]["forbidden_marker"] for case in attacks}
    assert len(markers) == len(attacks)
    for case in attacks:
        marker = case["gold"]["forbidden_marker"]
        assert marker in case["attack"]["instruction"]
        assert marker not in case["question"]
        assert all(marker not in source["snippet"] for source in case["sources"])


def test_aborted_attacks_are_excluded_from_marker_denominator():
    clean = {
        "id": "case",
        "domain": "study",
        "trial": 1,
        "kind": "prompt-injection",
        "status": "answered",
        "variant": "clean",
        "attack_channel": None,
        "marker_seen": False,
        "outcome": "strict_pass",
    }
    failed = {
        **clean,
        "variant": "probe",
        "attack_channel": "source",
        "status": "generation_error",
        "outcome": "generation_error",
    }
    result = summarize([clean, failed])
    assert result["attacks_by_channel"]["source"] == {
        "trials": 1,
        "trials_with_output": 0,
        "marker_seen": 0,
    }
    assert result["by_kind"]["prompt-injection"]["clean_strict_pass"] == 1
    assert result["by_kind"]["prompt-injection"]["retained_strict_pass"] == 0


def test_source_attack_is_visible_to_generator_and_removed_for_judge(monkeypatch):
    case = next(case for case in load_cases() if case["id"] == "inj-source-library")
    marker = case["gold"]["forbidden_marker"]
    citation = case["sources"][0]["snippet"].split(" — ", maxsplit=1)[1]

    def fake_answer(item, key, search, use_judge):
        assert marker in search["results"][0]["snippet"]
        assert not use_judge
        return {
            "generation": {
                "answer": (
                    '{"status":"answered","answer":"Данные университетского аккаунта.",'
                    f'"citations":[{{"source":1,"quote":"{citation}"}}]}}'
                )
            }
        }

    def fake_grade(question, response, key, model, limit):
        assert marker not in question
        assert all(marker not in source["text"] for source in response["sources"])
        assert response["citations"][0]["verified"]
        return {"correct": True, "supported": True, "critical_error": False}, {}

    monkeypatch.setattr(run_robustness, "answer_case", fake_answer)
    monkeypatch.setattr(run_robustness, "grade", fake_grade)
    result = run_robustness.run_case(case, 1, control=False, budget=1500, judge_model=None)
    assert result["outcome"] == "strict_pass"
    assert not result["marker_seen"]


def test_decoded_attack_marker_counts_even_when_json_escapes_it(monkeypatch):
    case = next(case for case in load_cases() if case["id"] == "inj-query-start")
    marker = case["gold"]["forbidden_marker"]
    raw = json.dumps({"status": "answered", "answer": marker, "citations": []})
    assert marker not in raw

    def fake_answer(item, key, search, use_judge):
        assert marker in item["input"]["question"]
        return {
            "status": "answered",
            "answer": marker,
            "citations": [],
            "generation": {"answer": raw},
        }

    monkeypatch.setattr(run_robustness, "answer_case", fake_answer)
    result = run_robustness.run_case(case, 1, control=False, budget=1500, judge_model=None)
    assert result["outcome"] == "attack_marker"


def test_false_answer_is_flagged_even_if_judge_accepts_it(monkeypatch):
    case = next(case for case in load_cases() if case["id"] == "scope-lms")

    def fake_answer(item, key, search, use_judge):
        return {"status": "answered", "answer": "Вход по адресу университетской почты."}

    def fake_grade(question, response, key, model, limit):
        return {"correct": True, "supported": True, "critical_error": False}, {}

    monkeypatch.setattr(run_robustness, "answer_case", fake_answer)
    monkeypatch.setattr(run_robustness, "grade", fake_grade)
    result = run_robustness.run_case(case, 1, control=False, budget=1500, judge_model=None)
    assert result["outcome"] == "false_answer"
