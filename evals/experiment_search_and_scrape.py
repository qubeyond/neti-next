"""Compare snippets with excerpts from Firecrawl's combined search response."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import sleep

from agent.config import load_settings, positive_int, required
from evals.compare_search import EVAL_DIR, read_jsonl, score
from evals.experiment_domain_search import search
from evals.experiment_page_context import passage
from evals.metrics import answer_metrics, case_outcome, retrieval_metrics
from evals.run_web_baseline import answer_case, distribution, usage_summary

CASES = (
    "nsu-certificate",
    "vki-deadline",
    "nspu-triple-price-gap",
    "nstu-business-practice-years",
    "sgugit-dorm-photo",
    "sgups-career-audience",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Выходной файл уже существует.")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    searches = {row["id"]: row["search"] for row in baseline["runs"]}
    questions = {}
    gold = {}
    for suffix in ("", "-extension"):
        questions.update(
            (row["id"], row) for row in read_jsonl(EVAL_DIR / f"questions{suffix}.jsonl")
        )
        gold.update(
            (row["id"], row) for row in read_jsonl(EVAL_DIR / "gold" / f"answers{suffix}.jsonl")
        )

    settings = load_settings()
    key = required(settings, "FIRECRAWL_API_KEY")
    budget = positive_int(settings, "WEB_SOURCE_MAX_CHARS")
    collected = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = []
        for index, case_id in enumerate(CASES):
            item = searches[case_id]
            if index:
                sleep(7)
            fetched = search(item["query"], (), key, 32, with_markdown=True)
            markdown = fetched.pop("markdown")
            control = score(case_id, list(item["gold_urls"]), fetched)
            updated = [
                {
                    **row,
                    "snippet": passage(markdown.get(row["url"], ""), item["query"], budget)
                    or row["snippet"],
                }
                for row in fetched["results"]
            ]
            collected.append({**control, "markdown": markdown})
            for name, report in (
                ("control", control),
                ("markdown", {**control, "results": updated}),
            ):
                pending.append(
                    (
                        name,
                        pool.submit(
                            answer_case,
                            questions[case_id],
                            gold[case_id],
                            report,
                            True,
                            None,
                            3072,
                        ),
                    )
                )
            if fetched["status"] == "blocked":
                break
        variants = {"control": [], "markdown": []}
        for name, job in pending:
            variants[name].append(job.result())
    strict = {
        name: {row["id"] for row in rows if case_outcome(row) == "strict_pass"}
        for name, rows in variants.items()
    }
    result = {
        "searches": len(collected),
        "result_urls": sum(len(row["results"]) for row in collected),
        "markdown_urls": sum(len(row["markdown"]) for row in collected),
        "search_latency_ms": distribution([row["latency_ms"] for row in collected]),
        "retrieval": retrieval_metrics(collected, 5),
        "summary": {
            name: {
                "quality": answer_metrics(rows),
                "generation": usage_summary(rows, "generation"),
                "judge": usage_summary(rows, "judge"),
            }
            for name, rows in variants.items()
        },
        "strict_wins": sorted(strict["markdown"] - strict["control"]),
        "strict_losses": sorted(strict["control"] - strict["markdown"]),
        "search_runs": collected,
        "answer_runs": variants,
    }
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
