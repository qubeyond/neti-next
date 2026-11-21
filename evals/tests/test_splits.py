"""Проверяет, что связанные вопросы и документы не пересекают границу среза."""

import json

import pytest

from evals.compare_search import EVAL_DIR
from evals.splits import document_groups, load_cases, make_manifest, select_split


def test_grouping_links_mirrors_paraphrases_and_multiple_documents():
    questions = [
        {"id": key, "input": {"question": key}}
        for key in ("a", "alias", "b", "multi", "para", "separate")
    ]
    questions[4]["base_id"] = "b"
    evidence = {
        "a": {"url": "https://example.org/rules/", "document_family": "family"},
        "alias": {"url": "https://example.edu/mirror.pdf", "document_family": "family"},
        "same-a": {"url": "https://www.example.org/rules?utm_source=test"},
        "b": {"url": "https://example.org/other"},
        "separate": {"url": "https://example.org/other?year=2026"},
    }
    gold = {
        "a": {"evidence_ids": ["a"]},
        "alias": {"evidence_ids": ["alias"]},
        "b": {"evidence_ids": ["b"]},
        "multi": {"evidence_ids": ["same-a", "b"]},
        "para": {"evidence_ids": []},
        "separate": {"evidence_ids": ["separate"]},
    }
    assert document_groups(questions, gold, evidence) == [
        ["a", "alias", "b", "multi", "para"],
        ["separate"],
    ]


def test_checked_in_manifest_covers_dataset_and_keeps_all_groups_together():
    path = EVAL_DIR / "splits-web-v1.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest == make_manifest("expanded", 5)
    questions, gold, evidence = load_cases("expanded")
    groups = document_groups(questions, gold, evidence)
    fold_by_id = {cid: index for index, group in enumerate(manifest["folds"]) for cid in group}
    ids = {row["id"] for row in questions}
    assert set(fold_by_id) == ids
    assert sum(map(len, manifest["folds"])) == len(ids)
    assert all(len({fold_by_id[cid] for cid in group}) == 1 for group in groups)

    for name, split in manifest["institution_holdouts"].items():
        test = set(split["test"])
        excluded = set(split["excluded"])
        train = ids - test - excluded
        assert test and train and not test & excluded
        assert all(not (test.intersection(group) and train.intersection(group)) for group in groups)
        assert all(
            row["id"] in test
            for row in questions
            if any(i == name or i.startswith(f"{name} (") for i in row["institutions"])
        )


def test_stale_or_wrong_dataset_manifest_is_rejected(tmp_path):
    path = tmp_path / "split.json"
    manifest = make_manifest("expanded", 5)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    selection = select_split(path, "expanded", 1, None)
    assert selection["test"] == manifest["folds"][0]
    with pytest.raises(ValueError, match="не соответствует"):
        select_split(path, "core", 1, None)
    with pytest.raises(ValueError, match="Номер фолда"):
        select_split(path, "expanded", 6, None)

    manifest["folds"][0].pop()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="не соответствует"):
        select_split(path, "expanded", 1, None)
