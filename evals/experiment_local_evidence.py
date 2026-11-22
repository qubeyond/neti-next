"""Check quote coverage before and after adding two local search results."""

import argparse
import json
from collections import Counter
from pathlib import Path

from agent.domain.evidence import prepare_sources
from evals.compare_search import EVAL_DIR, read_jsonl, url_key
from evals.experiment_local_index import evaluate
from evals.experiment_passage_coverage import quote_form


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    gold = {}
    evidence = {}
    for suffix in ("", "-extension"):
        gold.update(
            (case["id"], case["evidence_ids"])
            for case in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
        )
        evidence.update(
            json.loads((EVAL_DIR / "gold" / f"evidence{suffix}.json").read_text(encoding="utf-8"))
        )
    sets = evaluate(baseline["runs"], include_results=True)["candidate_sets"]
    rows = []
    totals = Counter()
    for web, expanded in zip(sets["web_5"], sets["web_5_local_2"], strict=True):
        if web["id"] != expanded["id"]:
            raise ValueError("Candidate sets are out of alignment.")
        case_id = web["id"]
        refs = gold[case_id]
        row = {"id": case_id, "total_refs": len(refs)}
        for name, payload in (("web", web), ("expanded", expanded)):
            sources = prepare_sources(payload["results"], 1500)
            normalized = [
                (url_key(source["url"]), " " + quote_form(source["text"])) for source in sources
            ]
            row[name] = [
                ref
                for ref in refs
                if any(
                    url_key(evidence[ref]["url"]) == url
                    and " " + quote_form(evidence[ref]["quote"]) in text
                    for url, text in normalized
                )
            ]
            totals[f"{name}_complete"] += len(row[name]) == len(refs)
            totals[f"{name}_quote_count"] += len(row[name])
            totals[f"{name}_context_chars"] += sum(len(source["text"]) for source in sources)
        totals["total_quotes"] += len(refs)
        rows.append(row)
    report = {
        "cases": len(rows),
        "totals": dict(totals),
        "gained_quote_ids": [row["id"] for row in rows if len(row["expanded"]) > len(row["web"])],
        "gained_complete_ids": [
            row["id"] for row in rows if len(row["web"]) < row["total_refs"] == len(row["expanded"])
        ],
        "runs": rows,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
