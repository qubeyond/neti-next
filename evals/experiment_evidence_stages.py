"""Сверяет эталонные выдержки с URL, сниппетами и контекстом из отчёта e2e."""

import argparse
import json
from collections import Counter
from pathlib import Path

from evals.compare_search import EVAL_DIR, read_jsonl, url_key
from evals.experiment_passage_coverage import quote_form


def evidence_stages(row: dict, references: list[tuple[str, dict]]) -> list[dict]:
    results = [
        (url_key(source["url"]), " " + quote_form(source["snippet"]))
        for source in row["search"]["results"]
    ]
    context = [
        (url_key(source["url"]), " " + quote_form(source["text"]))
        for source in row.get("sources", [])
    ]
    stages = []
    for evidence_id, reference in references:
        url = url_key(reference["url"])
        quote = " " + quote_form(reference["quote"])
        stages.append(
            {
                "case": row["id"],
                "domain": row["domain"],
                "evidence": evidence_id,
                "has_url": any(candidate == url for candidate, _ in results),
                "raw_match": any(candidate == url and quote in text for candidate, text in results),
                "context_match": any(
                    candidate == url and quote in text for candidate, text in context
                ),
                "other_context_match": any(
                    candidate != url and quote in text for candidate, text in context
                ),
            }
        )
    return stages


def summarize(rows: list[dict]) -> dict:
    counts = Counter()
    by_case: dict[str, list[dict]] = {}
    for row in rows:
        counts.update(
            name
            for name in ("has_url", "raw_match", "context_match", "other_context_match")
            if row[name]
        )
        counts["raw_lost"] += row["raw_match"] and not row["context_match"]
        counts["other_only"] += row["other_context_match"] and not row["context_match"]
        by_case.setdefault(row["case"], []).append(row)
    return {
        "instances": len(rows),
        "counts": dict(counts),
        "cases": len(by_case),
        "all_quotes_in_context": sum(
            all(row["context_match"] for row in group) for group in by_case.values()
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    data = json.loads(args.report.read_text(encoding="utf-8"))
    if data.get("mode") != "e2e":
        parser.error("Нужен отчёт e2e с исходной поисковой выдачей.")
    gold = {}
    evidence = {}
    for suffix in ("", "-extension"):
        gold.update(
            (row["id"], row["evidence_ids"])
            for row in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
        )
        evidence.update(
            json.loads((EVAL_DIR / "gold" / f"evidence{suffix}.json").read_text(encoding="utf-8"))
        )
    rows = [
        stage
        for row in data["runs"]
        for stage in evidence_stages(row, [(key, evidence[key]) for key in gold[row["id"]]])
    ]
    domains = sorted({row["domain"] for row in rows})
    summary = {"all": summarize(rows)}
    summary.update(
        (domain, summarize([row for row in rows if row["domain"] == domain])) for domain in domains
    )
    print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
