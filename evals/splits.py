"""Фиксирует группы документов и отложенные наборы для веб-eval."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from evals.compare_search import EVAL_DIR, read_jsonl, url_key

DATASET_SUFFIXES = {
    "core": ("",),
    "extension": ("-extension",),
    "expanded": ("", "-extension"),
}


def load_cases(dataset: str) -> tuple[list[dict], dict, dict]:
    variants = DATASET_SUFFIXES[dataset]
    questions = [
        row
        for suffix in variants
        for row in read_jsonl(EVAL_DIR / f"questions{suffix}.jsonl")
        if row["suite"] == "web"
    ]
    answers = {
        row["id"]: row
        for suffix in variants
        for row in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
    }
    gold = {row["id"]: answers[row["id"]] for row in questions}
    # Дополнительные кейсы могут ссылаться на доказательства из основного набора.
    evidence = {
        key: ref
        for suffix in DATASET_SUFFIXES["expanded"]
        for key, ref in json.loads(
            (EVAL_DIR / "gold" / f"evidence{suffix}.json").read_text(encoding="utf-8")
        ).items()
    }
    used = {key for row in gold.values() for key in row["evidence_ids"]}
    return questions, gold, {key: evidence[key] for key in sorted(used)}


def document_groups(questions: list[dict], gold: dict, evidence: dict) -> list[list[str]]:
    owners: dict[tuple, str] = {}
    parents = {row["id"]: row["id"] for row in questions}
    if len(questions) != len(parents):
        raise ValueError("Повторяющиеся ID вопросов.")

    def find(case_id: str) -> str:
        while parents[case_id] != case_id:
            parents[case_id] = parents[parents[case_id]]
            case_id = parents[case_id]
        return case_id

    for row in sorted(questions, key=lambda item: item["id"]):
        case_id = row["id"]
        related = [("question", " ".join(row["input"]["question"].casefold().split()))]
        if row.get("base_id"):
            if row["base_id"] not in parents:
                raise ValueError(f"Не найден базовый вопрос для {case_id}.")
            parents[find(case_id)] = find(row["base_id"])
        for evidence_id in gold[case_id]["evidence_ids"]:
            ref = evidence[evidence_id]
            for url in (ref["url"], ref.get("linked_url")):
                if url:
                    normalized = url_key(url)
                    if normalized is None:
                        raise ValueError(f"Некорректный URL в {evidence_id}.")
                    related.append(("url", normalized))
            if family := ref.get("document_family"):
                related.append(("family", family))
        for key in related:
            other = owners.setdefault(key, case_id)
            parents[find(case_id)] = find(other)

    groups = defaultdict(list)
    for case_id in sorted(parents):
        groups[find(case_id)].append(case_id)
    return sorted(groups.values(), key=lambda group: (-len(group), group))


def make_manifest(dataset: str, fold_count: int) -> dict:
    questions, gold, evidence = load_cases(dataset)
    by_id = {row["id"]: row for row in questions}
    groups = document_groups(questions, gold, evidence)
    if not 2 <= fold_count <= len(groups):
        raise ValueError(f"Число фолдов должно быть от 2 до {len(groups)}.")

    folds: list[list[str]] = [[] for _ in range(fold_count)]
    domains: list[Counter] = [Counter() for _ in folds]
    for group in groups:
        topics = Counter(by_id[case_id]["domain"] for case_id in group)
        index = min(
            range(fold_count),
            key=lambda i: (
                len(folds[i]),
                sum(domains[i][name] * size for name, size in topics.items()),
                i,
            ),
        )
        folds[index].extend(group)
        domains[index].update(topics)

    institutions = sorted({name for row in questions for name in row["institutions"]})
    holdouts = {}
    for name in institutions:
        test = {
            case_id
            for case_id, row in by_id.items()
            if any(
                institution == name or institution.startswith(f"{name} (")
                for institution in row["institutions"]
            )
        }
        excluded = {case_id for group in groups if test.intersection(group) for case_id in group}
        holdouts[name] = {"test": sorted(test), "excluded": sorted(excluded - test)}

    dataset_bytes = json.dumps(
        [sorted(questions, key=lambda row: row["id"]), gold, evidence],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "version": 1,
        "dataset": dataset,
        "suite": "web",
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "document_groups": len(groups),
        "folds": [sorted(case_ids) for case_ids in folds],
        "institution_holdouts": holdouts,
    }


def select_split(path: Path, dataset: str, fold: int | None, institution: str | None) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest != make_manifest(dataset, len(manifest["folds"])):
        raise ValueError("Манифест не соответствует версии набора или правилам разбиения.")
    if fold is not None:
        if not 1 <= fold <= len(manifest["folds"]):
            raise ValueError(f"Номер фолда должен быть от 1 до {len(manifest['folds'])}.")
        selected = {"test": manifest["folds"][fold - 1], "excluded": []}
    else:
        if institution not in manifest["institution_holdouts"]:
            raise ValueError("В манифесте нет указанного вуза.")
        selected = manifest["institution_holdouts"][institution]
    return {
        "manifest": path.name,
        "dataset_sha256": manifest["dataset_sha256"],
        "fold": fold,
        "institution": institution,
        **selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=DATASET_SUFFIXES, default="expanded")
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()
    try:
        result = make_manifest(args.dataset, args.folds)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
